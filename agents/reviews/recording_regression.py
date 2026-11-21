"""Independent regression reproductions; all run artifacts live in /tmp.

Run from any directory with Python 3. No dependencies, model calls, or network.
Tests assert desired integrity behavior and intentionally fail on audited v1.
"""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from scripts.record_run import execute_run
from scripts.validate_records import validate


class RecordingRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="toolloop-audit-", dir="/tmp")
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        (self.root / "configs/test.json").write_text('{"seed": 1}')

    def tearDown(self):
        self.temp.cleanup()

    def run_fixture(self, code="pass"):
        with contextlib.redirect_stdout(io.StringIO()):
            execute_run(self.root, "AUDIT-001", "analysis", "configs/test.json",
                        "audit only", "test expected integrity behavior", "source_audit",
                        [sys.executable, "-c", code])
        return self.root / "experiments/AUDIT-001"

    def rewrite_manifest(self, run, modify):
        path = run / "manifest.json"
        value = json.loads(path.read_text())
        modify(value)
        path.write_text(json.dumps(value))

    def test_baseline_archive_validates(self):
        self.run_fixture()
        self.assertEqual(validate(self.root)[0], [])

    def test_configuration_cannot_escape_integrity_check(self):
        run = self.run_fixture()
        self.rewrite_manifest(run, lambda m: m["outputs"].pop("config.json"))
        (run / "config.json").write_text('{"seed": 999}')
        self.assertTrue(validate(self.root)[0], "unhashed/tampered config was accepted")

    def test_invalid_schema_is_rejected(self):
        run = self.run_fixture()

        def modify(value):
            value.update(status="unknown", kind="unknown", evidence_valid=True)
            for name in ("command", "cwd", "ended_at_utc"):
                value.pop(name)

        self.rewrite_manifest(run, modify)
        self.assertTrue(validate(self.root)[0], "invalid status/kind/provenance accepted")

    def test_nonobject_manifest_returns_errors(self):
        run = self.run_fixture()
        (run / "manifest.json").write_text("[]")
        errors, _ = validate(self.root)
        self.assertTrue(errors, "non-object manifest must be rejected without crashing")

    def test_live_edit_restore_cannot_falsely_certify_snapshot(self):
        (self.root / "toolloop_fix").mkdir()
        task = self.root / "toolloop_fix/task.py"
        task.write_text("print('snapshot-A')\n")
        # Mutate the live workspace, representing another agent editing during a run.
        # The executed relative path should come from a frozen cwd, or be invalidated.
        code = (
            "from pathlib import Path; import runpy; "
            "live=Path({!r}); original=live.read_text(); "
            "live.write_text(\"print('executed-B')\\n\"); "
            "runpy.run_path('toolloop_fix/task.py'); live.write_text(original)"
        ).format(str(task))
        run = self.run_fixture(code)
        value = json.loads((run / "manifest.json").read_text())
        observed = (run / "stdout.log").read_text().strip()
        self.assertTrue(
            not value["evidence_valid"] or observed == "snapshot-A",
            "valid=True while archived code is A and executed output is {!r}".format(observed),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
