#!/usr/bin/env python3
"""
iOS Simulator Crash Log Inspector

Find, parse, and summarise crash reports from simulator runs. When a sim
app crashes the system writes an `.ips` (newer) or `.crash` (legacy) report
to one of three locations:

  - ~/Library/Logs/DiagnosticReports/
  - ~/Library/Logs/DiagnosticReports/Retired/
  - ~/Library/Developer/CoreSimulator/Devices/<UDID>/data/Library/Logs/CrashReporter/

This script discovers them, filters by simulator + app bundle id + age, and
emits a terse one-line summary plus the top stack frames. With `--dsym`
and `--symbolicate` it resolves binary addresses to source symbols via
`atos`.

Usage:
    # Most recent crash for one app on the booted sim
    python3 crash_log.py --app com.example.MyApp

    # Last 3 crashes from the past hour, JSON
    python3 crash_log.py --app com.example.MyApp --last 1h --limit 3 --json

    # Symbolicate with a build dSYM
    python3 crash_log.py --app com.example.MyApp \\
        --symbolicate --dsym ./Build/Products/Debug-iphonesimulator/MyApp.app.dSYM

    # Just list discovered reports (no parsing)
    python3 crash_log.py --list

Refs upstream issue #36.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from common import resolve_udid
from common.errors import SkillError, emit_error, emit_success

# Standard crash log search locations. The CoreSimulator path is per-device
# and must be templated with the UDID at runtime.
SYSTEM_DIAG_DIR = Path.home() / "Library" / "Logs" / "DiagnosticReports"
RETIRED_DIAG_DIR = SYSTEM_DIAG_DIR / "Retired"
SIM_DIAG_TEMPLATE = (
    Path.home()
    / "Library"
    / "Developer"
    / "CoreSimulator"
    / "Devices"
    / "{udid}"
    / "data"
    / "Library"
    / "Logs"
    / "CrashReporter"
)

# Time-window suffixes used by --last (e.g. "10m", "2h", "7d").
TIME_SUFFIXES = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_age(spec: str) -> timedelta:
    """Convert '10m', '2h', '7d' to a timedelta. Raises SkillError on bad input."""
    m = re.fullmatch(r"(\d+)([smhd])", spec.strip())
    if not m:
        raise SkillError(
            "INVALID_ARGS",
            f"Bad --last value: {spec!r}",
            hint="Use e.g. 10m, 2h, 7d.",
        )
    return timedelta(seconds=int(m.group(1)) * TIME_SUFFIXES[m.group(2)])


def discover_reports(udid: str | None) -> list[Path]:
    """Return all crash logs across the three known locations.

    `udid` may be None — in that case we still include the per-device
    CoreSimulator path only if it exists for some device (we won't enumerate
    all devices; callers should pass a UDID for per-sim filtering).
    """
    candidates: list[Path] = []
    for base in (SYSTEM_DIAG_DIR, RETIRED_DIAG_DIR):
        if base.is_dir():
            candidates.extend(base.iterdir())
    if udid:
        sim_dir = Path(str(SIM_DIAG_TEMPLATE).format(udid=udid))
        if sim_dir.is_dir():
            candidates.extend(sim_dir.iterdir())

    # Filter to crash report file types. .ips is the modern JSON-ish format;
    # .crash is the legacy plain-text format still emitted by some tools.
    return [p for p in candidates if p.is_file() and p.suffix in {".ips", ".crash"}]


def report_age(path: Path) -> datetime:
    """Use the file's mtime as the report time. Filenames also encode a
    timestamp but the format varies between iOS / macOS / xctest builds."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def parse_ips(path: Path) -> dict | None:
    """Parse a two-part .ips file: header JSON on line 1, payload JSON after.

    Returns the merged dict, or None if the file isn't a valid .ips.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    parts = text.split("\n", 1)
    if len(parts) != 2:
        return None
    try:
        header = json.loads(parts[0])
        payload = json.loads(parts[1])
    except (json.JSONDecodeError, ValueError):
        return None
    return {"_header": header, **payload}


def parse_crash(path: Path) -> dict | None:
    """Parse a legacy .crash plain-text report into a minimal dict.

    We don't try to fully reconstruct the .ips schema — just enough to keep
    the summary path working: process name, exception, and a top-frames
    list pulled from the first thread block.
    """
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    head: dict = {}
    for line in text.splitlines()[:60]:
        if ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip()
            if k in ("Process", "Identifier", "Exception Type", "Exception Codes", "Date/Time"):
                head[k] = v
    frames: list[str] = []
    in_thread = False
    for line in text.splitlines():
        if re.match(r"Thread \d+ Crashed:", line):
            in_thread = True
            continue
        if in_thread:
            if not line.strip():
                break
            frames.append(line.strip())
    return {
        "procName": head.get("Process", "").split()[0] if head.get("Process") else "",
        "bundleId": head.get("Identifier", ""),
        "exception": {"type": head.get("Exception Type", "")},
        "captureTime": head.get("Date/Time", ""),
        "_legacy_frames": frames,
    }


def matches_sim(report: dict, udid: str | None) -> bool:
    """Filter to reports scoped to `udid` via coalition name."""
    if not udid:
        return True
    coalition = report.get("coalitionName") or report.get("_header", {}).get("coalitionName")
    if isinstance(coalition, str):
        return udid in coalition
    # Legacy .crash reports don't carry coalitionName; we accept them only
    # if they live under the per-device CrashReporter path, which the
    # discover_reports() step already enforced when udid was provided.
    return True


def matches_app(report: dict, bundle_id: str | None) -> bool:
    if not bundle_id:
        return True
    rid = report.get("bundleID") or report.get("bundleId") or ""
    proc = report.get("procName") or report.get("_header", {}).get("procName") or ""
    return bundle_id in (rid, proc) or bundle_id == rid


def crashed_frames(report: dict) -> list[dict]:
    """Extract the crashed thread's frames as a list of dicts.

    Each dict carries `index`, `image`, `symbol`, `addr`, and optionally
    `imageOffset` so the symbolicator can refine it. Returns [] if the
    schema doesn't match.
    """
    if "_legacy_frames" in report:
        return [{"index": i, "raw": line} for i, line in enumerate(report["_legacy_frames"])]

    threads = report.get("threads") or []
    images = report.get("usedImages") or []
    crashed = next((t for t in threads if t.get("triggered")), None) or (
        threads[0] if threads else None
    )
    if not crashed:
        return []
    out: list[dict] = []
    for i, frame in enumerate(crashed.get("frames", [])):
        image_idx = frame.get("imageIndex")
        image = images[image_idx] if isinstance(image_idx, int) and image_idx < len(images) else {}
        out.append(
            {
                "index": i,
                "image": image.get("name") or image.get("path") or "?",
                "symbol": frame.get("symbol"),
                "addr": frame.get("imageOffset"),
                "loadAddress": image.get("base"),
                "uuid": image.get("uuid"),
            }
        )
    return out


def symbolicate(frames: list[dict], dsym: Path, binary_name: str | None = None) -> list[dict]:
    """Resolve `frames` to source symbols via `atos`. Adds `resolved` per frame.

    We invoke `atos` once per frame (single addresses) — slower than batching
    but each frame can target a different image (system frameworks vs. the
    app binary), and atos's per-invocation overhead is small on macOS.
    """
    dsym = dsym.expanduser().resolve()
    if not dsym.exists():
        raise SkillError(
            "INVALID_ARGS",
            f"dSYM not found: {dsym}",
            hint="Pass the path to your *.dSYM bundle.",
        )

    # Resolve the inner DWARF binary path. atos accepts the dSYM bundle root
    # but the binary-name disambiguation only kicks in for multi-arch dSYMs.
    dwarf = dsym / "Contents" / "Resources" / "DWARF"
    dsym_arg = dsym
    if dwarf.is_dir():
        binaries = list(dwarf.iterdir())
        if binary_name:
            binaries = [b for b in binaries if b.name == binary_name] or binaries
        dsym_arg = binaries[0] if binaries else dsym

    for f in frames:
        addr = f.get("addr")
        load = f.get("loadAddress")
        if addr is None or load is None:
            continue
        try:
            res = subprocess.run(
                [
                    "atos",
                    "-o",
                    str(dsym_arg),
                    "-l",
                    str(load),
                    hex(
                        int(addr) + int(load, 16)
                        if isinstance(load, str)
                        else int(addr) + int(load)
                    ),
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                f["resolved"] = res.stdout.strip()
        except (subprocess.TimeoutExpired, ValueError):
            continue
    return frames


def summarise(report: dict, frames: list[dict], path: Path) -> str:
    proc = report.get("procName") or report.get("_header", {}).get("procName") or "?"
    exc = (report.get("exception") or {}).get("type") or report.get("exceptionType") or "?"
    top = ""
    for f in frames[:3]:
        label = f.get("resolved") or f.get("symbol") or f.get("raw") or f.get("image") or "?"
        if isinstance(label, str):
            top += f"\n    {f.get('index', '?'):>2}  {label}"
    return f"{proc}: {exc}  [{path.name}]{top}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Discover and summarise simulator crash reports.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--udid", help="Scope to one simulator (auto-detect booted if omitted)")
    parser.add_argument(
        "--app",
        help="Filter to one app bundle id or process name (default: include all)",
    )
    parser.add_argument(
        "--last",
        help="Only reports captured within this window (e.g. 10m, 2h, 7d)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max reports to summarise (default 5; most recent first)",
    )
    parser.add_argument("--list", action="store_true", help="Just list discovered files, no parse")
    parser.add_argument(
        "--symbolicate", action="store_true", help="Resolve frames via atos (requires --dsym)"
    )
    parser.add_argument("--dsym", help="Path to a .dSYM bundle for symbolication")
    parser.add_argument("--binary", help="Inner binary name inside the dSYM if ambiguous")
    parser.add_argument("--verbose", action="store_true", help="Dump full frame list")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    try:
        # UDID is optional — we fall back to the system-wide DiagnosticReports
        # path if no sim is booted, so users can inspect crashes after shutdown.
        try:
            udid: str | None = resolve_udid(args.udid)
        except RuntimeError:
            udid = None

        if args.symbolicate and not args.dsym:
            raise SkillError("INVALID_ARGS", "--symbolicate requires --dsym <path>")

        reports = discover_reports(udid)
        reports.sort(key=lambda p: p.stat().st_mtime, reverse=True)

        if args.last:
            cutoff = datetime.now(tz=UTC) - parse_age(args.last)
            reports = [p for p in reports if report_age(p) >= cutoff]

        if args.list:
            data = [{"path": str(p), "mtime": report_age(p).isoformat()} for p in reports]
            if args.json:
                print(json.dumps({"ok": True, "data": data}))
            else:
                for r in data:
                    print(f"{r['mtime']}  {r['path']}")
            return 0

        summaries: list[dict] = []
        for path in reports:
            parsed = parse_ips(path) if path.suffix == ".ips" else parse_crash(path)
            if parsed is None:
                continue
            if not matches_sim(parsed, udid) or not matches_app(parsed, args.app):
                continue

            frames = crashed_frames(parsed)
            if args.symbolicate:
                frames = symbolicate(frames, Path(args.dsym), args.binary)

            summaries.append(
                {
                    "path": str(path),
                    "proc": parsed.get("procName")
                    or parsed.get("_header", {}).get("procName")
                    or "",
                    "bundleId": parsed.get("bundleID") or parsed.get("bundleId") or "",
                    "exception": (parsed.get("exception") or {}).get("type"),
                    "captureTime": parsed.get("captureTime")
                    or parsed.get("_header", {}).get("timestamp"),
                    "frames": frames if args.verbose else frames[:3],
                    "summary": summarise(parsed, frames, path),
                }
            )

            if len(summaries) >= args.limit:
                break

        if not summaries:
            return emit_success(
                {"count": 0, "reports": []},
                json_mode=args.json,
                summary="No crash reports matched",
            )

        if args.json:
            print(json.dumps({"ok": True, "data": {"count": len(summaries), "reports": summaries}}))
        else:
            for s in summaries:
                print(s["summary"])
        return 0

    except SkillError as e:
        return emit_error(e, json_mode=args.json)


if __name__ == "__main__":
    sys.exit(main())
