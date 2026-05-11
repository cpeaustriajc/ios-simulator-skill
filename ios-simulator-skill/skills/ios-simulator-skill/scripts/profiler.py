#!/usr/bin/env python3
"""
xctrace profiling helper.

Wraps `xcrun xctrace` to record an Instruments trace against a sim app and
emit a terse summary. The detailed XML schemas vary by template, so this
script is opinionated about a small set of well-known templates and falls
back to "trace recorded; open in Instruments" for anything else.

Usage:
    # List all templates available on this Xcode install
    python3 profiler.py --list-templates

    # 5-second Time Profiler attached to a running app by name
    python3 profiler.py --attach MyApp --template "Time Profiler" --duration 5

    # Launch a fresh app and profile it for 10s with Allocations
    python3 profiler.py --launch com.example.MyApp --template Allocations --duration 10

    # Keep the .trace file (default: cleaned up after summarise)
    python3 profiler.py --attach MyApp --template "Time Profiler" --keep-trace

Refs upstream issue #32.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from common import resolve_udid
from common.errors import SkillError, emit_error, emit_success

# Templates whose XML export we know how to summarise. Any template name not
# in this dict still records fine; the summary step degrades to
# "trace recorded; open in Instruments".
SUPPORTED_SUMMARISE = {"Time Profiler", "Allocations", "Leaks"}


class Profiler:
    """Drive `xctrace record` + `xctrace export` against a simulator."""

    def __init__(self, udid: str) -> None:
        self.udid = udid

    def list_templates(self) -> list[str]:
        """Parse `xctrace list templates` output."""
        try:
            res = subprocess.run(
                ["xcrun", "xctrace", "list", "templates"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise SkillError(
                "ENV_MISSING",
                "xctrace not available",
                hint="Install Xcode (not just CLT) to get Instruments / xctrace.",
            ) from exc

        templates: list[str] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            # Skip headers ("== Standard Templates ==") and blank lines.
            if not line or line.startswith("=="):
                continue
            templates.append(line)
        return templates

    def record(
        self,
        template: str,
        attach: str | None,
        launch: str | None,
        duration: int,
        output: Path,
    ) -> Path:
        """Execute xctrace record. Returns the resulting .trace path."""
        cmd = [
            "xcrun",
            "xctrace",
            "record",
            "--device",
            self.udid,
            "--template",
            template,
            "--time-limit",
            f"{duration}s",
            "--no-prompt",
            "--output",
            str(output),
        ]
        if attach:
            cmd += ["--attach", attach]
        elif launch:
            cmd += ["--launch", "--", launch]
        else:
            raise SkillError(
                "INVALID_ARGS",
                "Either --attach or --launch is required",
            )

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise SkillError(
                "BUILD_FAILED",
                "xctrace record failed",
                hint=(exc.stderr.strip() or exc.stdout.strip() or str(exc))[:500],
            ) from exc

        # xctrace sometimes prints info on stdout (paths, deprecation notices).
        # Keep them out of our return value; the path is what we asked for.
        _ = res
        return output

    def summarise(self, trace_path: Path, template: str) -> dict | None:
        """Best-effort summary for known templates. Returns None if unsupported."""
        if template not in SUPPORTED_SUMMARISE:
            return None

        # xctrace's TOC tells us which schemas are exportable from this trace.
        # We use it to pick a relevant xpath rather than guessing across
        # Xcode versions where schema names shift.
        toc = self._export_toc(trace_path)
        if toc is None:
            return None

        # Try a generic "top rows by sample-count" query that works for both
        # Time Profiler and Allocations summary tables. The actual schema
        # name differs by template — we walk the TOC looking for the first
        # leaf node with rows and project the first 5.
        for schema in toc:
            try:
                xml_path = trace_path.parent / f"{trace_path.stem}-{schema}.xml"
                subprocess.run(
                    [
                        "xcrun",
                        "xctrace",
                        "export",
                        "--input",
                        str(trace_path),
                        "--xpath",
                        f"/trace-toc/run/data/table[@schema='{schema}']/row[position()<=5]",
                        "--output",
                        str(xml_path),
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=30,
                )
                rows = self._parse_rows(xml_path)
                if rows:
                    return {"schema": schema, "rows": rows}
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ET.ParseError):
                continue
        return None

    @staticmethod
    def _export_toc(trace_path: Path) -> list[str] | None:
        """Run `xctrace export --toc` and pull the schema names out."""
        try:
            res = subprocess.run(
                ["xcrun", "xctrace", "export", "--input", str(trace_path), "--toc"],
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return None
        try:
            root = ET.fromstring(res.stdout)
        except ET.ParseError:
            return None
        # Look for any element exposing a `schema` attribute. xctrace's TOC
        # is deeply nested and the exact tag names vary by template, but the
        # schema attribute is the stable identifier we need.
        return [el.get("schema") for el in root.iter() if el.get("schema")]

    @staticmethod
    def _parse_rows(xml_path: Path) -> list[str]:
        """Pluck the first useful text from each row as a 1-line preview."""
        try:
            tree = ET.parse(xml_path)
        except (ET.ParseError, FileNotFoundError):
            return []
        rows: list[str] = []
        for row in tree.getroot().iter("row"):
            cells = [c.text for c in row.iter() if c.text and c.text.strip()]
            if cells:
                rows.append(" | ".join(cells[:4]))
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record an Instruments trace and emit a terse summary.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    target = parser.add_mutually_exclusive_group()
    target.add_argument("--attach", metavar="NAME|PID", help="Attach to running process")
    target.add_argument("--launch", metavar="BUNDLE_ID", help="Launch a fresh app and profile it")

    parser.add_argument(
        "--template",
        default="Time Profiler",
        help='Instruments template name (default: "Time Profiler")',
    )
    parser.add_argument("--duration", type=int, default=10, help="Recording length in seconds")
    parser.add_argument("--udid", help="Device UDID (auto-detects booted simulator)")
    parser.add_argument(
        "--keep-trace",
        action="store_true",
        help="Keep the .trace bundle after summarising (default: clean up)",
    )
    parser.add_argument(
        "--output",
        help="Output .trace path (default: a fresh tempdir; ignored unless --keep-trace)",
    )
    parser.add_argument(
        "--list-templates",
        action="store_true",
        help="List available Instruments templates and exit",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    try:
        if args.list_templates:
            # No UDID needed for `list templates` — xctrace is a host tool.
            templates = Profiler.list_templates(Profiler.__new__(Profiler))
            if args.json:
                print(json.dumps({"ok": True, "data": templates}))
            else:
                for t in templates:
                    print(t)
            return 0

        if not args.attach and not args.launch:
            raise SkillError(
                "INVALID_ARGS",
                "--attach or --launch is required (or --list-templates)",
            )

        try:
            udid = resolve_udid(args.udid)
        except RuntimeError as exc:
            raise SkillError(
                "NO_BOOTED_SIM",
                str(exc),
                hint="Boot a simulator first or set $SIMCTL_UDID.",
                recovery_cmd='xcrun simctl boot "iPhone 16 Pro"',
            ) from exc

        profiler = Profiler(udid)

        workdir: Path | None = None
        if args.output:
            output = Path(args.output).expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
        else:
            workdir = Path(tempfile.mkdtemp(prefix="ios-profiler-"))
            output = workdir / "recording.trace"

        try:
            trace_path = profiler.record(
                template=args.template,
                attach=args.attach,
                launch=args.launch,
                duration=args.duration,
                output=output,
            )
            summary = profiler.summarise(trace_path, args.template)

            data = {
                "trace": str(trace_path),
                "template": args.template,
                "duration_s": args.duration,
                "summary": summary,
            }

            if args.verbose and summary:
                human = f"Trace recorded: {trace_path}\nTemplate: {args.template}\n"
                human += f"Top rows from schema={summary['schema']}:\n"
                human += "\n".join(f"  {r}" for r in summary["rows"])
            elif summary:
                top = summary["rows"][0] if summary["rows"] else "(no rows)"
                human = f"{args.template}: top={top}  [{trace_path.name}]"
            else:
                human = (
                    f"{args.template}: recorded {args.duration}s — "
                    f"summary unavailable for this template, open {trace_path} in Instruments"
                )

            return emit_success(data, json_mode=args.json, summary=human)

        finally:
            # Auto-cleanup unless the user asked to keep it. .trace bundles
            # are 10-100MB, easy to fill a disk over many runs.
            if not args.keep_trace and workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)

    except SkillError as e:
        return emit_error(e, json_mode=args.json)


if __name__ == "__main__":
    sys.exit(main())
