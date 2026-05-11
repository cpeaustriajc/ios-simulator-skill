#!/usr/bin/env python3
"""
Swift Package Manager helper.

Thin wrapper around `swift package ...` so an agent can inspect, resolve, and
update SPM dependencies without copy-pasting boilerplate or parsing free-form
output. v1 scope is intentionally narrow:

  - Standalone Package.swift only. Xcode-integrated SPM (where Package.resolved
    lives under <project>.xcodeproj/project.xcworkspace/xcshareddata/swiftpm/)
    is deferred — the layout varies per Xcode version and isn't worth the
    surface area in v1.
  - State-mutating operations (`resolve`, `update`) are gated behind explicit
    flags. Default invocations are read-only.

Usage:
    # List the dependency graph for the package in cwd
    python3 spm_manager.py

    # Describe a specific package directory, JSON
    python3 spm_manager.py --package-path ./Vendor/MyLib --json

    # Resolve dependencies (no version updates, just record current state)
    python3 spm_manager.py --resolve

    # Update a specific dependency
    python3 spm_manager.py --update --package Alamofire

    # Verbose: full dependency tree instead of one-line summary
    python3 spm_manager.py --verbose

Refs upstream issue #35.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from common.errors import SkillError, emit_error, emit_success


class SPMManager:
    """Wrapper over `swift package`."""

    def __init__(self, package_path: Path) -> None:
        self.path = package_path
        self.manifest = package_path / "Package.swift"

    def ensure_package(self) -> None:
        """Raise if there's no Package.swift at the configured path."""
        if not self.manifest.is_file():
            raise SkillError(
                "INVALID_ARGS",
                f"No Package.swift at {self.path}",
                hint="Pass --package-path pointing at a directory containing Package.swift.",
            )

    def show_dependencies(self) -> dict:
        """Return the swift package dependency graph as a dict."""
        out = self._run(["show-dependencies", "--format", "json"])
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError) as exc:
            raise SkillError(
                "UNKNOWN",
                "swift package show-dependencies returned non-JSON output",
                hint="Run with --verbose to see the raw output.",
            ) from exc

    def describe(self) -> dict:
        """Describe the package itself (targets, products, source paths)."""
        out = self._run(["describe", "--type", "json"])
        try:
            return json.loads(out)
        except (json.JSONDecodeError, ValueError) as exc:
            raise SkillError(
                "UNKNOWN",
                "swift package describe returned non-JSON output",
            ) from exc

    def resolve(self) -> str:
        """Run swift package resolve. Returns stdout for surfacing."""
        return self._run(["resolve"]).strip()

    def update(self, package: str | None = None) -> str:
        """Run swift package update [--package NAME]."""
        cmd = ["update"]
        if package:
            cmd += ["--package", package]
        return self._run(cmd).strip()

    def _run(self, args: list[str]) -> str:
        cmd = ["swift", "package", "--package-path", str(self.path), *args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError as exc:
            raise SkillError(
                "ENV_MISSING",
                "swift CLI not found",
                hint="Install Xcode command-line tools: xcode-select --install",
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise SkillError(
                "BUILD_FAILED",
                f"swift package {' '.join(args)} failed",
                hint=(exc.stderr.strip() or exc.stdout.strip() or str(exc))[:500],
            ) from exc
        return proc.stdout


def _flatten_tree(node: dict, depth: int = 0) -> list[tuple[int, str, str]]:
    """Flatten the dependency tree to (depth, name, version) tuples."""
    out: list[tuple[int, str, str]] = []
    name = node.get("name") or node.get("identity") or "?"
    version = node.get("version") or ""
    out.append((depth, name, version))
    for child in node.get("dependencies") or []:
        out.extend(_flatten_tree(child, depth + 1))
    return out


def _one_line(tree: dict) -> str:
    """Render a 1-line summary: 'N deps: a, b, c (+more)'."""
    flat = _flatten_tree(tree)
    # Skip the root (it's the package itself).
    deps = flat[1:]
    if not deps:
        return f"{flat[0][1]}: no dependencies"
    names = [n for _, n, _ in deps]
    preview = ", ".join(names[:5])
    extra = f" (+{len(names) - 5} more)" if len(names) > 5 else ""
    return f"{flat[0][1]}: {len(names)} dep(s) — {preview}{extra}"


def _verbose_tree(tree: dict) -> str:
    """Indented tree render."""
    return "\n".join(f"{'  ' * d}- {n} {v}".rstrip() for d, n, v in _flatten_tree(tree))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect or modify a Swift Package Manager package.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--package-path",
        default=".",
        help="Directory containing Package.swift (default: cwd)",
    )

    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--describe",
        action="store_true",
        help="Run `swift package describe` instead of `show-dependencies`",
    )
    action.add_argument(
        "--resolve",
        action="store_true",
        help="Run `swift package resolve` (state-mutating)",
    )
    action.add_argument(
        "--update",
        action="store_true",
        help="Run `swift package update` (state-mutating; use --package to scope)",
    )

    parser.add_argument(
        "--package",
        help="Limit `--update` to a single dependency (e.g. Alamofire)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print the full dependency tree instead of a one-line summary",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        path = Path(args.package_path).expanduser().resolve()
        manager = SPMManager(path)
        manager.ensure_package()

        if args.resolve:
            out = manager.resolve()
            return emit_success(
                {"action": "resolve", "path": str(path), "output": out},
                json_mode=args.json,
                summary=f"Resolved dependencies for {path.name}",
            )

        if args.update:
            out = manager.update(args.package)
            return emit_success(
                {"action": "update", "package": args.package, "output": out},
                json_mode=args.json,
                summary=f"Updated {args.package or 'all dependencies'} for {path.name}",
            )

        if args.describe:
            desc = manager.describe()
            if args.json:
                print(json.dumps({"ok": True, "data": desc}))
            else:
                targets = [t.get("name") for t in desc.get("targets") or []]
                products = [p.get("name") for p in desc.get("products") or []]
                print(f"{desc.get('name', path.name)}")
                print(f"  Targets:  {', '.join(filter(None, targets)) or '(none)'}")
                print(f"  Products: {', '.join(filter(None, products)) or '(none)'}")
            return 0

        # Default: show-dependencies
        tree = manager.show_dependencies()
        if args.json:
            print(json.dumps({"ok": True, "data": tree}))
            return 0
        if args.verbose:
            print(_verbose_tree(tree))
        else:
            print(_one_line(tree))
        return 0

    except SkillError as e:
        return emit_error(e, json_mode=args.json)


if __name__ == "__main__":
    sys.exit(main())
