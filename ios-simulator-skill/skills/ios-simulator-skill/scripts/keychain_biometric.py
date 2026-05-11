#!/usr/bin/env python3
"""
iOS Keychain & Biometric Simulator

Two related concerns that come up constantly when testing auth flows on the
simulator and aren't covered by other scripts:

1. Keychain — install root/intermediate certs, reset the keychain so a test
   starts from a clean slate.

2. Biometrics — trigger Face ID / Touch ID match/no-match events from the host
   so a test that's sitting at a biometric prompt doesn't require manual menu
   clicks in Simulator.app.

Both are thin wrappers over `xcrun simctl` subcommands; the value here is a
single tool with auto-UDID, structured error envelopes, and a help text that
documents which notifyutil keys correspond to which gestures (Apple's docs are
scattered).

Usage examples:
    # Install an internal CA so HTTPS requests don't fail in the sim
    python3 keychain_biometric.py --add-root-cert ./certs/internal-ca.pem

    # Reset the keychain between tests
    python3 keychain_biometric.py --reset-keychain

    # Trigger a successful Face ID match (app must be at a biometric prompt)
    python3 keychain_biometric.py --biometric face-match

    # Toggle whether the simulated device has biometrics enrolled
    python3 keychain_biometric.py --biometric enroll-toggle

Refs upstream issue #34.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import build_simctl_command, resolve_udid
from common.errors import SkillError, emit_error, emit_success

# notifyutil keys recognised by CoreSimulator's BiometricKit_Sim. The
# `pearl.*` keys drive Face ID, `fingerTouch.*` drive Touch ID, and
# `enroll.toggle` flips whether the device is considered enrolled at all.
# Apple has never documented these in one place — they're collated here so a
# user adding a new gesture doesn't have to dig through forum posts.
BIOMETRIC_ACTIONS = {
    "face-match": "com.apple.BiometricKit_Sim.pearl.match",
    "face-nomatch": "com.apple.BiometricKit_Sim.pearl.nomatch",
    "touch-match": "com.apple.BiometricKit_Sim.fingerTouch.match",
    "touch-nomatch": "com.apple.BiometricKit_Sim.fingerTouch.nomatch",
    "enroll-toggle": "com.apple.BiometricKit_Sim.enrollmentChanged",
}


class KeychainBiometricManager:
    """Wraps `xcrun simctl keychain` and biometric notifyutil events."""

    def __init__(self, udid: str) -> None:
        self.udid = udid

    # --- Keychain ---------------------------------------------------------

    def add_root_cert(self, cert_path: Path) -> str:
        """Install `cert_path` as a trusted root cert on the device."""
        return self._run_keychain(["add-root-cert", str(cert_path)], cert_path)

    def add_cert(self, cert_path: Path) -> str:
        """Install `cert_path` as a regular (intermediate/leaf) cert."""
        return self._run_keychain(["add-cert", str(cert_path)], cert_path)

    def reset_keychain(self) -> str:
        """Wipe the simulator's keychain to its post-fresh-install state."""
        import subprocess

        cmd = build_simctl_command("keychain", self.udid, "reset")
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise SkillError(
                "PERMISSION_DENIED",
                f"keychain reset failed: {exc.stderr.strip() or exc}",
                hint="Make sure the simulator is booted and not in the middle of a reset.",
            ) from exc
        return f"Keychain reset on {self.udid}"

    def _run_keychain(self, subargs: list[str], cert_path: Path) -> str:
        import subprocess

        if not cert_path.exists():
            raise SkillError(
                "INVALID_ARGS",
                f"Certificate not found: {cert_path}",
                hint="Pass an absolute or correctly-resolved relative path.",
            )

        cmd = build_simctl_command("keychain", self.udid, *subargs)
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise SkillError(
                "PERMISSION_DENIED",
                f"simctl keychain {' '.join(subargs[:1])} failed: " f"{exc.stderr.strip() or exc}",
                hint="Confirm the certificate is in a PEM/DER format simctl accepts.",
            ) from exc
        return f"Installed {cert_path.name} on {self.udid}"

    # --- Biometrics -------------------------------------------------------

    def simulate_biometric(self, action: str) -> str:
        """Post a biometric notification on the booted simulator.

        The app must already be presenting a biometric prompt for `face-match`
        / `touch-match` etc. to have an observable effect. `enroll-toggle`
        flips device-level enrollment state and persists until toggled back.
        """
        import subprocess

        notification = BIOMETRIC_ACTIONS.get(action)
        if not notification:
            raise SkillError(
                "INVALID_ARGS",
                f"Unknown biometric action: {action}",
                hint=f"Choose one of: {', '.join(BIOMETRIC_ACTIONS)}",
            )

        cmd = build_simctl_command(
            "spawn",
            self.udid,
            "notifyutil",
            "-p",
            notification,
        )
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
        except subprocess.CalledProcessError as exc:
            raise SkillError(
                "PERMISSION_DENIED",
                f"notifyutil failed for {action}: {exc.stderr.strip() or exc}",
                hint="Ensure the simulator is booted; notifyutil only works on a running device.",
            ) from exc
        return f"Sent {action} ({notification}) to {self.udid}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage simulator keychain and trigger biometric events.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    # Mutually exclusive: one action per invocation. Keychain-cert installs
    # and biometric triggers are conceptually distinct enough that we don't
    # let them compose in a single call — easier to read in test scripts.
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--add-root-cert", metavar="PATH", help="Install as a trusted root cert")
    action.add_argument("--add-cert", metavar="PATH", help="Install as a regular cert")
    action.add_argument(
        "--reset-keychain",
        action="store_true",
        help="Wipe the simulator's keychain",
    )
    action.add_argument(
        "--biometric",
        choices=sorted(BIOMETRIC_ACTIONS),
        help="Post a biometric notification to the booted simulator",
    )
    action.add_argument(
        "--list-biometric",
        action="store_true",
        help="Show supported biometric actions and the notifyutil keys they map to",
    )

    parser.add_argument("--udid", help="Device UDID (auto-detects booted simulator)")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.list_biometric:
        if args.json:
            import json as _json

            print(_json.dumps({"ok": True, "data": BIOMETRIC_ACTIONS}))
        else:
            print("Biometric actions:")
            for key, notif in BIOMETRIC_ACTIONS.items():
                print(f"  {key:<14} → notifyutil -p {notif}")
        return 0

    try:
        try:
            udid = resolve_udid(args.udid)
        except RuntimeError as exc:
            raise SkillError(
                "NO_BOOTED_SIM",
                str(exc),
                hint="Boot a simulator first or set $SIMCTL_UDID.",
                recovery_cmd='xcrun simctl boot "iPhone 16 Pro"',
            ) from exc

        manager = KeychainBiometricManager(udid)

        if args.add_root_cert:
            summary = manager.add_root_cert(Path(args.add_root_cert).expanduser())
            data = {"action": "add-root-cert", "path": args.add_root_cert, "udid": udid}
        elif args.add_cert:
            summary = manager.add_cert(Path(args.add_cert).expanduser())
            data = {"action": "add-cert", "path": args.add_cert, "udid": udid}
        elif args.reset_keychain:
            summary = manager.reset_keychain()
            data = {"action": "reset-keychain", "udid": udid}
        else:  # --biometric
            summary = manager.simulate_biometric(args.biometric)
            data = {
                "action": "biometric",
                "biometric": args.biometric,
                "notification": BIOMETRIC_ACTIONS[args.biometric],
                "udid": udid,
            }

        return emit_success(data, json_mode=args.json, summary=summary)

    except SkillError as e:
        return emit_error(e, json_mode=args.json)


if __name__ == "__main__":
    sys.exit(main())
