"""Tests for screen_mapper StaticText surfacing and system-overlay heuristic.

Background: v1.5.3 only categorised Buttons and TextFields. Error banners like
"Login failed: Could not connect to the server." sat in the a11y tree as
StaticText and never reached the summary — agents had to take a screenshot to
read them. Separately, when a system dialog (save-password, location prompt,
share sheet) was up, the app-scoped tree came back near-empty with no signal
to the agent that an overlay was the cause.

These tests pin both behaviours.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screen_mapper import ScreenMapper


def _tree(*children: dict, screen_name: str = "LoginVC") -> dict:
    """Build a minimal a11y tree root with the given children."""
    return {
        "type": "Application",
        "AXLabel": "App",
        "AXUniqueId": screen_name + "ViewController",
        "enabled": True,
        "children": list(children),
    }


def _node(type_: str, label: str = "", value: str = "", enabled: bool = True) -> dict:
    return {
        "type": type_,
        "AXLabel": label,
        "AXValue": value,
        "AXUniqueId": "",
        "enabled": enabled,
        "children": [],
    }


def test_static_text_is_collected_in_analysis():
    tree = _tree(
        _node("Button", "Log in"),
        _node("StaticText", "Login failed: Could not connect to the server."),
        _node("StaticText", "Forgot password?"),
    )
    analysis = ScreenMapper().analyze_tree(tree)
    assert "Login failed: Could not connect to the server." in analysis["static_texts"]
    assert "Forgot password?" in analysis["static_texts"]


def test_summary_surfaces_static_text_line():
    tree = _tree(
        _node("Button", "Log in"),
        _node("StaticText", "Login failed: Could not connect to the server."),
    )
    mapper = ScreenMapper()
    summary = mapper.format_summary(mapper.analyze_tree(tree))
    # Must appear on its own line, quoted, with the full sentence intact.
    text_lines = [line for line in summary.split("\n") if line.startswith("Text:")]
    assert len(text_lines) == 1
    assert "Login failed: Could not connect to the server." in text_lines[0]


def test_static_text_does_not_inflate_interactive_count():
    """Static labels are diagnostic only — they must not pretend to be tappable."""
    tree = _tree(
        _node("Button", "Log in"),
        _node("StaticText", "Email"),
        _node("StaticText", "Password"),
    )
    analysis = ScreenMapper().analyze_tree(tree)
    assert analysis["interactive_elements"] == 1
    assert analysis["buttons"] == ["Log in"]


def test_verbose_includes_other_labelled_elements_section():
    tree = _tree(
        _node("Button", "Log in"),
        _node("StaticText", "Welcome back"),
        _node("Image", "Company logo"),
    )
    mapper = ScreenMapper()
    summary = mapper.format_summary(mapper.analyze_tree(tree), verbose=True)
    assert "Other labelled elements:" in summary
    assert "StaticText" in summary
    assert "Image" in summary
    assert "Welcome back" in summary
    assert "Company logo" in summary


def test_sparse_tree_flags_possible_system_overlay():
    """Save-password / location prompts leave the app's tree near-empty."""
    tree = _tree()  # No children — the app is fully obscured.
    analysis = ScreenMapper().analyze_tree(tree)
    assert analysis["possible_system_overlay"] is True


def test_sparse_tree_summary_includes_overlay_note():
    tree = _tree()
    mapper = ScreenMapper()
    summary = mapper.format_summary(mapper.analyze_tree(tree))
    assert "Note:" in summary
    assert "system dialog" in summary.lower()
    assert "screenshot" in summary.lower()


def test_rich_tree_does_not_flag_overlay():
    tree = _tree(
        _node("Button", "Log in"),
        _node("Button", "Sign up"),
        _node("TextField", "Email"),
        _node("SecureTextField", "Password"),
        _node("Button", "Forgot password?"),
        _node("StaticText", "Welcome"),
    )
    analysis = ScreenMapper().analyze_tree(tree)
    assert analysis["possible_system_overlay"] is False


def test_overlay_hint_appears_in_navigation_hints():
    tree = _tree()
    mapper = ScreenMapper()
    hints = mapper.get_navigation_hints(mapper.analyze_tree(tree))
    assert any("system dialog" in h.lower() for h in hints)
    assert any("vanish" in h.lower() for h in hints)


def test_truncate_helper_caps_long_labels():
    long = "x" * 200
    assert len(ScreenMapper._truncate(long, 80)) == 80
    assert ScreenMapper._truncate(long, 80).endswith("…")
    assert ScreenMapper._truncate("short", 80) == "short"


if __name__ == "__main__":
    import traceback

    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except Exception:
                failures += 1
                print(f"  FAIL  {name}")
                traceback.print_exc()
    sys.exit(1 if failures else 0)
