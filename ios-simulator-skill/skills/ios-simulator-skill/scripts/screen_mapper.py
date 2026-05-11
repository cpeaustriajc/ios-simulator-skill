#!/usr/bin/env python3
"""
iOS Screen Mapper - Current Screen Analyzer

Maps the current screen's UI elements for navigation decisions.
Provides token-efficient summaries of available interactions.

This script analyzes the iOS simulator screen using IDB's accessibility tree
and provides a compact, actionable summary of what's currently visible and
interactive on the screen. Perfect for AI agents making navigation decisions.

Key Features:
- Token-efficient output (5-7 lines by default)
- Identifies buttons, text fields, navigation elements
- Counts interactive and focusable elements
- Progressive detail with --verbose flag
- Navigation hints with --hints flag

Usage Examples:
    # Quick summary (default)
    python scripts/screen_mapper.py --udid <device-id>

    # Detailed element breakdown
    python scripts/screen_mapper.py --udid <device-id> --verbose

    # Include navigation suggestions
    python scripts/screen_mapper.py --udid <device-id> --hints

    # Full JSON output for parsing
    python scripts/screen_mapper.py --udid <device-id> --json

Output Format (default):
    Screen: LoginViewController (45 elements, 7 interactive)
    Buttons: "Login", "Cancel", "Forgot Password"
    TextFields: 2 (0 filled)
    Text: "Login failed: Could not connect to the server."
    Navigation: NavBar: "Sign In"
    Focusable: 7 elements

Technical Details:
- Uses IDB's accessibility tree via `idb ui describe-all --json --nested`
- Parses IDB's array format: [{ root element with children }]
- Identifies element types: Button, TextField, NavigationBar, TabBar, etc.
- Extracts labels from AXLabel, AXValue, and AXUniqueId fields

Caveat — system dialogs are invisible here:
- idb's tree is app-scoped. System overlays (keychain "Save Password",
  Location/Notification prompts, share sheet) are owned by SpringBoard and
  never appear in this output. When a tap appears to "vanish" or the tree
  looks unexpectedly sparse, suspect a system overlay and screenshot to
  confirm. The summary surfaces a `Note:` line when the tree is sparse
  enough to suggest an overlay.
"""

import argparse
import json
import subprocess
import sys
from collections import defaultdict

from common import get_accessibility_tree, resolve_udid
from common.errors import SkillError, emit_error


class ScreenMapper:
    """
    Analyzes current screen for navigation decisions.

    This class fetches the iOS accessibility tree from IDB and analyzes it
    to provide actionable summaries for navigation. It categorizes elements
    by type, counts interactive elements, and identifies key UI patterns.

    Attributes:
        udid (Optional[str]): Device UDID to target, or None for booted device
        INTERACTIVE_TYPES (Set[str]): Element types that users can interact with

    Design Philosophy:
        - Token efficiency: Provide minimal but complete information
        - Progressive disclosure: Summary by default, details on request
        - Navigation-focused: Highlight elements relevant for automation
    """

    # Element types we care about for navigation
    # These are the accessibility element types that indicate user interaction points
    INTERACTIVE_TYPES = {
        "Button",
        "Link",
        "TextField",
        "SecureTextField",
        "Cell",
        "Switch",
        "Slider",
        "Stepper",
        "SegmentedControl",
        "TabBar",
        "NavigationBar",
        "Toolbar",
    }

    # Non-interactive types whose labels are still useful for diagnosing UI state
    # (error banners, status labels, image captions). Kept separate from
    # INTERACTIVE_TYPES so we don't accidentally claim they're tappable.
    LABELLED_TYPES = {
        "StaticText",
        "Text",
        "Image",
        "Heading",
    }

    # Heuristic threshold for "tree looks suspiciously empty — system overlay
    # may be obscuring the app". Tuned against a few real apps; the cost of a
    # false positive is just an extra Note line.
    SPARSE_TREE_TOTAL = 5
    SPARSE_TREE_INTERACTIVE = 1

    def __init__(self, udid: str | None = None):
        """
        Initialize screen mapper.

        Args:
            udid: Optional device UDID. If None, uses booted simulator.

        Example:
            mapper = ScreenMapper(udid="656DC652-1C9F-4AB2-AD4F-F38E65976BDA")
            mapper = ScreenMapper()  # Uses booted device
        """
        self.udid = udid

    def get_accessibility_tree(self) -> dict:
        """
        Fetch accessibility tree from iOS simulator via IDB.

        Delegates to shared utility for consistent tree fetching across all scripts.
        """
        return get_accessibility_tree(self.udid, nested=True)

    def analyze_tree(self, node: dict, depth: int = 0) -> dict:
        """Analyze accessibility tree for navigation info."""
        analysis = {
            "elements_by_type": defaultdict(list),
            "labels_by_type": defaultdict(list),
            "total_elements": 0,
            "interactive_elements": 0,
            "text_fields": [],
            "buttons": [],
            "static_texts": [],
            "navigation": {},
            "screen_name": None,
            "focusable": 0,
            "possible_system_overlay": False,
        }

        self._analyze_recursive(node, analysis, depth)

        # Post-process for clean output
        analysis["elements_by_type"] = dict(analysis["elements_by_type"])
        analysis["labels_by_type"] = dict(analysis["labels_by_type"])

        # Sparse-tree heuristic: when the app's a11y tree comes back nearly
        # empty, a system overlay (keychain save-password, location/notification
        # prompt, share sheet) is the most common cause — those dialogs are
        # owned by SpringBoard and never appear in the app's tree.
        if (
            analysis["total_elements"] <= self.SPARSE_TREE_TOTAL
            or analysis["interactive_elements"] <= self.SPARSE_TREE_INTERACTIVE
        ):
            analysis["possible_system_overlay"] = True

        return analysis

    def _analyze_recursive(self, node: dict, analysis: dict, depth: int):
        """Recursively analyze tree nodes."""
        elem_type = node.get("type")
        label = node.get("AXLabel", "")
        value = node.get("AXValue", "")
        identifier = node.get("AXUniqueId", "")

        # Count element
        if elem_type:
            analysis["total_elements"] += 1

            # Track by type
            if elem_type in self.INTERACTIVE_TYPES:
                analysis["interactive_elements"] += 1

                # Store concise info (label only, not full node)
                elem_info = label or value or identifier or "Unnamed"
                analysis["elements_by_type"][elem_type].append(elem_info)

                # Special handling for common types
                if elem_type == "Button":
                    analysis["buttons"].append(elem_info)
                elif elem_type in ("TextField", "SecureTextField"):
                    analysis["text_fields"].append(
                        {"type": elem_type, "label": elem_info, "has_value": bool(value)}
                    )
                elif elem_type == "NavigationBar":
                    analysis["navigation"]["nav_title"] = label or "Navigation"
                elif elem_type == "TabBar":
                    # Count tab items
                    tab_count = len(node.get("children", []))
                    analysis["navigation"]["tab_count"] = tab_count

            # Track non-interactive but labelled elements (StaticText, Image,
            # Heading). These hold error banners, status text, captions —
            # critical for diagnosing UI state without taking a screenshot.
            elif elem_type in self.LABELLED_TYPES:
                text = label or value
                if text:
                    analysis["labels_by_type"][elem_type].append(text)
                    if elem_type in ("StaticText", "Text"):
                        analysis["static_texts"].append(text)

            # Track focusable elements
            if node.get("enabled", False) and elem_type in self.INTERACTIVE_TYPES:
                analysis["focusable"] += 1

        # Try to identify screen name from view controller
        if not analysis["screen_name"] and identifier:
            if "ViewController" in identifier or "Screen" in identifier:
                analysis["screen_name"] = identifier

        # Process children
        for child in node.get("children", []):
            self._analyze_recursive(child, analysis, depth + 1)

    def format_summary(self, analysis: dict, verbose: bool = False) -> str:
        """Format analysis as token-efficient summary."""
        lines = []

        # Screen identification (1 line)
        screen = analysis["screen_name"] or "Unknown Screen"
        total = analysis["total_elements"]
        interactive = analysis["interactive_elements"]
        lines.append(f"Screen: {screen} ({total} elements, {interactive} interactive)")

        # Buttons summary (1 line)
        if analysis["buttons"]:
            button_list = ", ".join(f'"{b}"' for b in analysis["buttons"][:5])
            if len(analysis["buttons"]) > 5:
                button_list += f" +{len(analysis['buttons']) - 5} more"
            lines.append(f"Buttons: {button_list}")

        # Text fields summary (1 line)
        if analysis["text_fields"]:
            field_count = len(analysis["text_fields"])
            [f["type"] for f in analysis["text_fields"]]
            filled = sum(1 for f in analysis["text_fields"] if f["has_value"])
            lines.append(f"TextFields: {field_count} ({filled} filled)")

        # Static text summary (1 line) — surface error banners, status labels,
        # any non-interactive copy that helps diagnose the screen without a
        # screenshot. Top 5, deduped to keep the line short.
        if analysis["static_texts"]:
            seen = []
            for t in analysis["static_texts"]:
                if t not in seen:
                    seen.append(t)
                if len(seen) >= 5:
                    break
            text_list = ", ".join(f'"{self._truncate(t, 80)}"' for t in seen)
            extra = len(analysis["static_texts"]) - len(seen)
            if extra > 0:
                text_list += f" +{extra} more"
            lines.append(f"Text: {text_list}")

        # Navigation summary (1 line)
        nav_parts = []
        if "nav_title" in analysis["navigation"]:
            nav_parts.append(f"NavBar: \"{analysis['navigation']['nav_title']}\"")
        if "tab_count" in analysis["navigation"]:
            nav_parts.append(f"TabBar: {analysis['navigation']['tab_count']} tabs")
        if nav_parts:
            lines.append(f"Navigation: {', '.join(nav_parts)}")

        # Focusable count (1 line)
        lines.append(f"Focusable: {analysis['focusable']} elements")

        # System-overlay note — always shown (not gated on --hints) because
        # if the tree is this sparse, the agent's next move is almost
        # certainly wrong without this context.
        if analysis.get("possible_system_overlay"):
            lines.append(
                "Note: tree is sparse — a system dialog (keychain save-password, "
                "location/notification prompt, share sheet) may be obscuring the app. "
                "System overlays are not in idb's app-scoped tree; "
                "screenshot to confirm."
            )

        # Verbose mode adds element type breakdown
        if verbose:
            lines.append("\nInteractive elements by type:")
            for elem_type, items in analysis["elements_by_type"].items():
                if items:  # Only show types that exist
                    lines.append(f"  {elem_type}: {len(items)}")
                    for item in items[:3]:  # Show first 3
                        lines.append(f"    - {item}")
                    if len(items) > 3:
                        lines.append(f"    ... +{len(items) - 3} more")

            if analysis["labels_by_type"]:
                lines.append("\nOther labelled elements:")
                for elem_type, items in analysis["labels_by_type"].items():
                    if items:
                        lines.append(f"  {elem_type}: {len(items)}")
                        for item in items[:5]:
                            lines.append(f"    - {self._truncate(item, 120)}")
                        if len(items) > 5:
                            lines.append(f"    ... +{len(items) - 5} more")

        return "\n".join(lines)

    @staticmethod
    def _truncate(s: str, n: int) -> str:
        """Truncate long labels for one-line output."""
        s = s.replace("\n", " ").strip()
        return s if len(s) <= n else s[: n - 1] + "…"

    def get_navigation_hints(self, analysis: dict) -> list[str]:
        """Generate navigation hints based on screen analysis."""
        hints = []

        # Check for common patterns
        if "Login" in str(analysis.get("buttons", [])):
            hints.append("Login screen detected - find TextFields for credentials")

        if analysis["text_fields"]:
            unfilled = [f for f in analysis["text_fields"] if not f["has_value"]]
            if unfilled:
                hints.append(f"{len(unfilled)} empty text field(s) - may need input")

        if not analysis["buttons"] and not analysis["text_fields"]:
            hints.append("No interactive elements - try swiping or going back")

        if "tab_count" in analysis.get("navigation", {}):
            hints.append(f"Tab bar available with {analysis['navigation']['tab_count']} tabs")

        # Surface the system-overlay hypothesis prominently — this is the most
        # common cause of a tap "vanishing" or a tree looking empty.
        if analysis.get("possible_system_overlay"):
            hints.append(
                "If a recent tap appears to have vanished, a system dialog "
                "(save-password, location, notifications, share sheet) is likely "
                "obscuring the app. Screenshot to confirm, then dismiss it before "
                "retrying the tap."
            )

        return hints


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Map current screen UI elements")
    parser.add_argument("--verbose", action="store_true", help="Show detailed element breakdown")
    parser.add_argument("--json", action="store_true", help="Output raw JSON analysis")
    parser.add_argument("--hints", action="store_true", help="Include navigation hints")
    parser.add_argument(
        "--udid",
        help="Device UDID (auto-detects booted simulator if not provided)",
    )

    args = parser.parse_args()

    try:
        try:
            udid = resolve_udid(args.udid)
        except RuntimeError as e:
            raise SkillError(
                "NO_BOOTED_SIM",
                str(e),
                hint="Boot a simulator first or set $SIMCTL_UDID.",
                recovery_cmd='xcrun simctl boot "iPhone 16 Pro"',
            ) from e

        mapper = ScreenMapper(udid=udid)
        tree = mapper.get_accessibility_tree()
        analysis = mapper.analyze_tree(tree)

        if args.json:
            print(json.dumps(analysis, indent=2, default=str))
        else:
            summary = mapper.format_summary(analysis, verbose=args.verbose)
            print(summary)

            if args.hints:
                hints = mapper.get_navigation_hints(analysis)
                if hints:
                    print("\nHints:")
                    for hint in hints:
                        print(f"  - {hint}")
    except SkillError as e:
        sys.exit(emit_error(e, json_mode=args.json))


if __name__ == "__main__":
    main()
