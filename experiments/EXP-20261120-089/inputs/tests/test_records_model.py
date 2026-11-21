"""Model record contract tests use tiny files and fake outputs, never a model."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

from scripts.record_run import execute_run
from scripts.validate_records import config_errors, model_manifest_errors, validate


FAKE_RUNNER = """
import json,sys
from pathlib import Path
out=Path(sys.argv[1]); config=json.loads(Path(sys.argv[2]).read_text())
provenance={"evidence_class":"model_inference","model":config["model"],
 "sampling":config["sampling"],"runtime":{"executable":sys.executable,
 "python_version":sys.version,"packages":{"torch":"fixture-only","transformers":"fixture-only"}},
 "model_manifest_sha256":next(ref["sha256"] for ref in config["external_inputs"] if ref["name"]=="model_manifest"),
 "model_files_verified":True,"chat_template_sha256":"b"*64}
(out/"model_provenance.json").write_text(json.dumps(provenance))
for name in ("generations.jsonl","trajectories.jsonl","case_results.jsonl"):
 (out/name).write_text(json.dumps({"evidence_class":"model_inference","fixture_only":True})+"\\n")
(out/"metrics.json").write_text(json.dumps({"evidence_class":"model_inference","fixture_only":True}))
"""


class ModelRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="toolloop-model-contract-")
        self.root = Path(self.temp.name)
        (self.root / "configs").mkdir()
        (self.root / "data/dev").mkdir(parents=True)
        (self.root / "data/dev/cases.jsonl").write_text('{"fixture_only":true}\n')
        self.models = self.root / "models"
        self.models.mkdir()
        blob = self.models / "blob"
        blob.write_bytes(b"not actual model weights")
        (self.models / "model.safetensors").symlink_to(blob)
        self.model = {"model_id": "fixture/model", "revision": "a" * 40,
                      "backend": "transformers", "model_path": str(self.models),
                      "device": "cpu", "dtype": "float32"}
        self.external = self.models / "model_manifest.json"
        self.external.write_text(json.dumps({**{key: self.model[key] for key in
                                               ("model_id", "revision", "model_path")},
            "files": [{"path": "model.safetensors", "size_bytes": blob.stat().st_size,
                       "sha256": hashlib.sha256(blob.read_bytes()).hexdigest()}]}))
        self.config = {"evidence_kind": "model_inference", "evidence_class": "model_inference",
                       "model": self.model,
                       "sampling": {"do_sample": True, "temperature": 1, "top_p": 1,
                                    "top_k": 20, "max_new_tokens": 64, "seed": 0,
                                    "enable_thinking": False},
                       "cases_path": "data/dev/cases.jsonl",
                       "external_inputs": [{"name": "model_manifest", "path": str(self.external),
                                            "sha256": hashlib.sha256(self.external.read_bytes()).hexdigest()}]}
        self.write_config()

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self):
        (self.root / "configs/model.json").write_text(json.dumps(self.config))

    def run_fixture(self, code=FAKE_RUNNER, run_id="MODEL-TEST"):
        with contextlib.redirect_stdout(io.StringIO()):
            result = execute_run(self.root, run_id, "model_inference", "configs/model.json",
                                 "fixture contract test, not model evidence", "validate contract", "test",
                                 [sys.executable, "-c", code, "{run_dir}", "{config}"])
        directory = self.root / "experiments" / run_id
        return result, directory, json.loads((directory / "manifest.json").read_text())

    def test_complete_fake_contract_validates_and_never_copies_weights(self):
        code, run, manifest = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(validate(self.root)[0], [])
        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(manifest["artifact_contract"], "model_inference")
        self.assertTrue(manifest["evidence_valid"])
        self.assertEqual((run / "external_inputs/model_manifest.json").read_bytes(), self.external.read_bytes())
        self.assertFalse(list(run.rglob("*.safetensors")))
        self.assertEqual(manifest["inputs"][manifest["config_input_path"]], manifest["config_sha256"])

    def test_kind_alignment_and_required_model_config(self):
        for mutate in (lambda c: c.update(evidence_kind="analysis"),
                       lambda c: c["model"].update(revision="main"),
                       lambda c: c["model"].pop("device"),
                       lambda c: c["sampling"].pop("max_new_tokens"),
                       lambda c: c["sampling"].update(temperature=float("nan")),
                       lambda c: c.update(cases_path="/tmp/live.jsonl")):
            config = copy.deepcopy(self.config)
            mutate(config)
            self.assertTrue(config_errors(config, "model_inference"))
        self.assertTrue(config_errors({"evidence_class": "model_inference"}, "analysis"))

    def test_runtime_manifest_reference_is_copied_without_cloning_environment(self):
        runtime = self.root / "runtime_install.json"
        runtime.write_text(json.dumps({"status": "completed", "packages": {"fixture": "1"}}))
        self.config["external_inputs"].insert(0, {"name": "runtime_install_manifest", "path": str(runtime),
                "sha256": hashlib.sha256(runtime.read_bytes()).hexdigest()})
        self.write_config()
        code, run, manifest = self.run_fixture()
        self.assertEqual(code, 0)
        self.assertEqual(len(manifest["external_inputs"]), 2)
        self.assertEqual((run / "external_inputs/runtime_install_manifest.json").read_bytes(), runtime.read_bytes())
        self.assertEqual(validate(self.root)[0], [])

    def test_external_names_must_be_unique_and_safe(self):
        config = copy.deepcopy(self.config)
        config["external_inputs"].append(copy.deepcopy(config["external_inputs"][0]))
        self.assertTrue(config_errors(config, "model_inference"))
        config["external_inputs"][-1]["name"] = "../escape"
        self.assertTrue(config_errors(config, "model_inference"))

    def test_invalid_config_rejected_before_execution(self):
        self.config["evidence_kind"] = "analysis"
        self.write_config()
        with self.assertRaises(ValueError):
            self.run_fixture("raise AssertionError('must not execute')")
        self.assertFalse((self.root / "experiments/MODEL-TEST").exists())

    def test_kind_alone_does_not_certify_model_execution(self):
        code, _, manifest = self.run_fixture("pass")
        self.assertEqual(code, 2)
        self.assertEqual(manifest["exit_code"], 0)  # Child succeeded, evidence contract did not.
        self.assertEqual(manifest["status"], "completed")
        self.assertFalse(manifest["evidence_valid"])
        self.assertTrue(manifest["contract_errors"])
        self.assertTrue(validate(self.root)[0])

    def test_failed_model_attempt_retained_without_complete_outputs(self):
        code, _, manifest = self.run_fixture("raise SystemExit(7)")
        self.assertEqual(code, 7)
        self.assertFalse(manifest["evidence_valid"])
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(validate(self.root)[0], [])

    def test_provenance_mismatch_and_wrong_output_label_rejected(self):
        code = FAKE_RUNNER + "\nprovenance['model']['revision']='c'*40\n(out/'model_provenance.json').write_text(json.dumps(provenance))\n(out/'generations.jsonl').write_text('{\"evidence_class\":\"synthetic_harness\"}\\n')"
        result, _, manifest = self.run_fixture(code)
        self.assertEqual(result, 2)
        self.assertFalse(manifest["evidence_valid"])
        self.assertTrue(any("provenance" in item for item in manifest["contract_errors"]))
        self.assertTrue(any("generations" in item for item in manifest["contract_errors"]))

    def test_actual_interpreter_and_package_versions_required(self):
        code = FAKE_RUNNER + "\nprovenance['runtime']={'executable':'/wrong/python','python_version':'fixture','packages':{}}\n(out/'model_provenance.json').write_text(json.dumps(provenance))"
        result, _, manifest = self.run_fixture(code)
        self.assertEqual(result, 2)
        self.assertTrue(any("interpreter" in item for item in manifest["contract_errors"]))
        self.assertTrue(any("package" in item for item in manifest["contract_errors"]))

    def test_empty_generation_file_is_not_model_evidence(self):
        code, _, manifest = self.run_fixture(FAKE_RUNNER + "\n(out/'generations.jsonl').write_text('')")
        self.assertEqual(code, 2)
        self.assertFalse(manifest["evidence_valid"])
        self.assertTrue(any("generations" in item for item in manifest["contract_errors"]))

    def test_external_manifest_identity_and_path_validation(self):
        value = json.loads(self.external.read_text())
        value["revision"] = "c" * 40
        value["files"][0]["path"] = "../escape.safetensors"
        errors = model_manifest_errors(value, self.config)
        self.assertTrue(any("revision" in item for item in errors))
        self.assertTrue(any("path" in item for item in errors))

    def test_external_manifest_bad_hash_stops_child(self):
        self.external.write_text('{}')
        code, _, manifest = self.run_fixture("raise AssertionError('must not execute')")
        self.assertNotEqual(code, 0)
        self.assertEqual(manifest["status"], "failed")
        self.assertIn("hash mismatch", manifest["error"])

    def test_model_case_file_must_be_in_snapshot_inventory(self):
        self.config["cases_path"] = "data/raw/unfrozen.jsonl"
        self.write_config()
        code, _, manifest = self.run_fixture("raise AssertionError('must not execute')")
        self.assertNotEqual(code, 0)
        self.assertIn("not in the frozen", manifest["error"])

    def test_external_manifest_change_during_run_invalidates_evidence(self):
        code = FAKE_RUNNER + "\nPath(config['external_inputs'][0]['path']).write_text('{}')"
        _, _, manifest = self.run_fixture(code)
        self.assertFalse(manifest["evidence_valid"])
        self.assertEqual(manifest["external_inputs_changed_during_run"], ["model_manifest"])
        self.assertTrue(validate(self.root)[0])

    def test_archived_model_manifest_checked_without_requiring_live_weights(self):
        _, run, _ = self.run_fixture()
        self.external.unlink()
        (self.models / "model.safetensors").unlink()
        self.assertEqual(validate(self.root)[0], [])
        (run / "external_inputs/model_manifest.json").write_text('{}')
        self.assertTrue(validate(self.root)[0])


if __name__ == "__main__":
    unittest.main()
