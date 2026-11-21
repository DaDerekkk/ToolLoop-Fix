import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.record_run import execute_run
from scripts.validate_records import validate


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        (self.root / "configs/test.json").write_text('{"seed": 1}')

    def tearDown(self):
        self.temp.cleanup()

    def run_command(self, run_id, code):
        with contextlib.redirect_stdout(io.StringIO()):
            return execute_run(self.root, run_id, "analysis", "configs/test.json", "test",
                               "exit code observed", "test", [sys.executable, "-c", code])

    def test_success_and_integrity(self):
        self.assertEqual(self.run_command("TEST-001", "print('observed')"), 0)
        errors, rows = validate(self.root)
        self.assertEqual(errors, [])
        self.assertTrue(rows[0]["evidence_valid"])
        self.assertEqual(rows[0]["acceptance_status"], "not_reviewed")
        (self.root / "experiments/TEST-001/stdout.log").write_text("tampered")
        self.assertTrue(validate(self.root)[0])

    def test_failure_retained(self):
        self.assertEqual(self.run_command("TEST-002", "raise SystemExit(7)"), 7)
        errors, rows = validate(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["status"], "failed")
        self.assertFalse(rows[0]["evidence_valid"])

    def test_no_overwrite(self):
        self.run_command("TEST-003", "pass")
        with self.assertRaises(FileExistsError):
            self.run_command("TEST-003", "pass")

    def test_bad_id_and_external_config_rejected(self):
        with self.assertRaises(ValueError):
            self.run_command("../oops", "pass")
        with self.assertRaises(ValueError):
            execute_run(self.root, "TEST-004", "analysis", "/tmp/outside.json", "x", "y", "z", ["true"])

    def test_concurrent_input_change_invalidates_evidence(self):
        code = "from pathlib import Path; Path('configs/test.json').write_text('{}')"
        self.run_command("TEST-005", code)
        value = json.loads((self.root / "experiments/TEST-005/manifest.json").read_text())
        self.assertFalse(value["evidence_valid"])
        self.assertEqual(value["inputs_changed_during_run"], ["configs/test.json"])
        self.assertEqual(validate(self.root)[0], [])  # Frozen snapshot remains valid.


if __name__ == "__main__":
    unittest.main()
