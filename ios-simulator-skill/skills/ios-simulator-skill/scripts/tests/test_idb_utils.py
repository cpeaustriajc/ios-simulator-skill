"""Contract tests for idb_utils — FileNotFoundError must produce a structured envelope.

Background: agents that invoke screen_mapper / navigator / gesture without idb
installed used to get a raw 20-line Python traceback. We now translate the
FileNotFoundError into a SkillError with code IDB_NOT_INSTALLED plus a
recovery_cmd, so this test pins that translation in place.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

# Make `scripts/` importable so `common.idb_utils` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common import idb_utils  # noqa: E402
from common.errors import SkillError  # noqa: E402


def test_run_idb_raises_skillerror_when_binary_missing():
    with mock.patch.object(idb_utils.subprocess, "run", side_effect=FileNotFoundError(2, "no idb")):
        try:
            idb_utils.run_idb(["ui", "describe-all"])
        except SkillError as e:
            assert e.code == "IDB_NOT_INSTALLED"
            assert e.recovery_cmd and "idb-companion" in e.recovery_cmd
            assert e.hint and "fb-idb" in e.hint
            return
    raise AssertionError("expected SkillError")


def test_get_accessibility_tree_raises_skillerror_when_binary_missing():
    with mock.patch.object(idb_utils.subprocess, "run", side_effect=FileNotFoundError(2, "no idb")):
        try:
            idb_utils.get_accessibility_tree("anyudid")
        except SkillError as e:
            assert e.code == "IDB_NOT_INSTALLED"
            return
    raise AssertionError("expected SkillError")


def test_get_accessibility_tree_detects_python_314_incompatibility():
    fake_result = mock.MagicMock(
        returncode=1,
        stderr="RuntimeError: There is no current event loop in thread 'MainThread'.",
        stdout="",
    )
    with mock.patch.object(idb_utils.subprocess, "run", return_value=fake_result):
        try:
            idb_utils.get_accessibility_tree("anyudid")
        except SkillError as e:
            assert e.code == "ENV_MISSING"
            assert e.recovery_cmd and "python3.13" in e.recovery_cmd
            return
    raise AssertionError("expected SkillError")


def test_idb_not_installed_error_envelope_shape():
    err = idb_utils.idb_not_installed_error()
    payload = err.to_dict()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "IDB_NOT_INSTALLED"
    assert "recovery_cmd" in payload["error"]
    assert "hint" in payload["error"]


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
