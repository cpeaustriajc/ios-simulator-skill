#!/usr/bin/env python3
"""
Shared IDB utility functions.

This module provides common IDB operations used across multiple scripts.
Follows Jackson's Law - only shared code that's truly reused, not speculative.

Used by:
- navigator.py - Accessibility tree navigation
- screen_mapper.py - UI element analysis
- accessibility_audit.py - WCAG compliance checking
- test_recorder.py - Test documentation
- app_state_capture.py - State snapshots
- gesture.py - Touch gesture operations
"""

import json
import subprocess
import sys

from .errors import SkillError

IDB_NOT_INSTALLED_HINT = (
    "fb-idb client is not on PATH. Install BOTH the brew companion daemon and "
    "the pipx CLI client; see README Prerequisites."
)
IDB_RECOVERY_CMD = (
    "brew tap facebook/fb && brew install idb-companion && "
    "pipx install --python python3.13 fb-idb"
)


def idb_not_installed_error(cause: Exception | None = None) -> SkillError:
    """Build a SkillError for a missing `idb` binary, with a consistent recovery hint."""
    err = SkillError(
        "IDB_NOT_INSTALLED",
        "idb (fb-idb client) is required for this operation but was not found on PATH.",
        hint=IDB_NOT_INSTALLED_HINT,
        recovery_cmd=IDB_RECOVERY_CMD,
    )
    if cause is not None:
        err.__cause__ = cause
    return err


def run_idb(
    args: list[str],
    *,
    capture_output: bool = True,
    text: bool = True,
    check: bool = False,
    timeout: float | None = None,
    udid: str | None = None,
) -> subprocess.CompletedProcess:
    """
    Run an `idb` subcommand, raising a structured SkillError if idb is missing.

    `args` should NOT include the leading "idb" — this wrapper prepends it and,
    when `udid` is given, appends `--udid <udid>`.

    Why: every script that shells out to idb hit a raw FileNotFoundError traceback
    when the binary wasn't installed. Centralising the FileNotFoundError → SkillError
    translation keeps the agent-facing envelope consistent.
    """
    cmd = ["idb", *args]
    if udid:
        cmd.extend(["--udid", udid])
    try:
        return subprocess.run(
            cmd,
            capture_output=capture_output,
            text=text,
            check=check,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise idb_not_installed_error(exc) from exc


def get_accessibility_tree(udid: str | None = None, nested: bool = True) -> dict:
    """
    Fetch accessibility tree from IDB.

    The accessibility tree represents the complete UI hierarchy of the current
    screen, with all element properties needed for semantic navigation.

    Args:
        udid: Device UDID (uses booted simulator if None)
        nested: Include nested structure (default True). If False, returns flat array.

    Returns:
        Root element of accessibility tree as dict.

    Raises:
        SkillError: with code IDB_NOT_INSTALLED (no idb binary), IDB_CONNECT_FAILED
            (idb ran but failed), or UNKNOWN (JSON decode failed).
    """
    sub_args = ["ui", "describe-all", "--json"]
    if nested:
        sub_args.append("--nested")

    result = run_idb(sub_args, udid=udid)
    if result.returncode != 0:
        stderr = result.stderr or ""
        # Detect Python 3.14 incompatibility (asyncio.get_event_loop) — issue #16.
        if "no current event loop" in stderr or "There is no current event loop" in stderr:
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            raise SkillError(
                "ENV_MISSING",
                f"fb-idb is incompatible with Python {py_ver} "
                "(asyncio.get_event_loop raises RuntimeError on 3.14+).",
                hint="Reinstall fb-idb against Python 3.13 or 3.12.",
                recovery_cmd="pipx install --force --python python3.13 fb-idb",
            )
        raise SkillError(
            "IDB_CONNECT_FAILED",
            f"idb describe-all failed: {stderr.strip() or 'no stderr'}",
            hint="Confirm idb-companion is running and the simulator is booted.",
        )

    try:
        tree_data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SkillError(
            "UNKNOWN",
            "idb returned invalid JSON for accessibility tree.",
            hint="Re-run with the simulator focused and ready.",
        ) from exc

    # IDB returns array format, extract first element (root)
    if isinstance(tree_data, list) and len(tree_data) > 0:
        return tree_data[0]
    return tree_data


def flatten_tree(node: dict, depth: int = 0, elements: list[dict] | None = None) -> list[dict]:
    """
    Flatten nested accessibility tree into list of elements.

    Converts the hierarchical accessibility tree into a flat list where each
    element includes its depth for context.

    Used by:
    - navigator.py - Element finding
    - screen_mapper.py - Element analysis
    - accessibility_audit.py - Audit scanning

    Args:
        node: Root node of tree (typically from get_accessibility_tree)
        depth: Current depth (used internally, start at 0)
        elements: Accumulator list (used internally, start as None)

    Returns:
        Flat list of elements, each with "depth" key indicating nesting level.
    """
    if elements is None:
        elements = []

    # Add current node with depth tracking
    node_copy = node.copy()
    node_copy["depth"] = depth
    elements.append(node_copy)

    # Process children recursively
    for child in node.get("children", []):
        flatten_tree(child, depth + 1, elements)

    return elements


def count_elements(node: dict) -> int:
    """
    Count total elements in tree (recursive).

    Traverses entire tree counting all elements for reporting purposes.

    Used by:
    - test_recorder.py - Element counting per step
    - screen_mapper.py - Summary statistics

    Args:
        node: Root node of tree

    Returns:
        Total element count including root and all descendants
    """
    count = 1
    for child in node.get("children", []):
        count += count_elements(child)
    return count


def get_screen_size(udid: str | None = None) -> tuple[int, int]:
    """
    Get screen dimensions from accessibility tree.

    Used by gesture.py to position swipes. Falls back to iPhone 14 defaults
    if the tree cannot be retrieved (e.g., idb missing) — callers that need
    a hard failure should call get_accessibility_tree directly.

    Args:
        udid: Device UDID (uses booted if None)

    Returns:
        (width, height) tuple. Defaults to (390, 844) if detection fails.
    """
    DEFAULT_WIDTH = 390  # iPhone 14
    DEFAULT_HEIGHT = 844

    try:
        tree = get_accessibility_tree(udid, nested=False)
        frame = tree.get("frame", {})
        width = int(frame.get("width", DEFAULT_WIDTH))
        height = int(frame.get("height", DEFAULT_HEIGHT))
        return (width, height)
    except (SkillError, Exception):
        # Silently fall back to defaults if tree access fails.
        return (DEFAULT_WIDTH, DEFAULT_HEIGHT)
