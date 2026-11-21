import json
import tempfile
import unittest
from pathlib import Path

from scripts import colab_inference_probe as smoke


class ColabSmokeTests(unittest.TestCase):
    def test_known_allowlist_pinned(self):
        self.assertEqual(len(smoke.FILES), 9)
        self.assertEqual(len(smoke.REVISION), 40)
        for k, v in smoke.FILES.items():
            self.assertEqual(k, Path(k).name)
            self.assertEqual(len(bytes.fromhex(v)), 32)

    def test_file_integrity_mismatch_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a").write_bytes(b"x")
            with self.assertRaises(ValueError):
                smoke.verify_files(d, {"a": "0"*64})

    def test_only_vision_keys_may_be_unused(self):
        smoke.verify_loading({"unexpected_keys": {"model.visual.projection"}})
        for info in ({"missing_keys": ["x"]}, {"unexpected_keys": ["text.x"]}):
            with self.assertRaises(ValueError):
                smoke.verify_loading(info)

    def test_output_is_exclusive_and_failure_sanitized(self):
        def fail(report):
            report["stage"] = "example_stage"
            raise RuntimeError("fake secret value")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "result.json"
            self.assertEqual(smoke.main(target, fail), 1)
            record = json.loads(target.read_text())
            self.assertFalse(record["tool_executed"])
            self.assertEqual(record["stage"], "example_stage")
            self.assertNotIn("fake secret", target.read_text())
            with self.assertRaises(FileExistsError):
                smoke.main(target, lambda _: self.fail("unexpected execution"))

    def test_success_does_not_claim_task_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "result.json"
            self.assertEqual(smoke.main(target, lambda r: r.update(raw_text="fixture")), 0)
            record = json.loads(target.read_text())
            self.assertFalse(record["training_performed"])
            self.assertEqual(record["scope"], "single_public_tool_call_proposal_only")
            self.assertNotIn("task_success", record)


if __name__ == "__main__":
    unittest.main()
