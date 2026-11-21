"""Tiny fake archives exercise audit checks; no tokenizer/model is loaded."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from scripts.audit_calibrated_runs import CORE_FILES, ROOT, audit_runs, digest
from toolloop_fix.baselines import GUARD_VERSION, prompt_for
from toolloop_fix.calibrated_protocol import CALIBRATED_PROTOCOL_VERSION, CALIBRATED_SCORE_VERSION, CALIBRATED_SYSTEM_PROMPT
from toolloop_fix.calibrated_run import run_calibrated_case, calibrated_metrics


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")


class FakeBackend:
    def __init__(self):
        self.index = 0

    def generate(self, messages, tools, seed):
        raw = ("<tool_call><function=get_order><parameter=identifier>BAD</parameter></function></tool_call><|im_end|>"
               if self.index < 2 else "Unable to proceed.<|im_end|>")
        self.index += 1
        prompt = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
        return {"raw_text": raw, "prompt_text": prompt,
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
                "prompt_token_ids": [1], "generated_token_ids": [2], "input_tokens": 1,
                "output_tokens": 1, "elapsed_seconds": 0.1, "token_limit_reached": False,
                "seed": seed, "effective_generation": {"fixture_only": True}}


class CalibratedAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="calibrated-audit-fixture-")
        self.base = Path(self.temp.name)
        self.refs = []
        case = {"case_id": "fixture-recovery", "domain": "orders", "category": "recoverable",
                "template_group": "fixture", "request": {"domain": "orders", "target": "latest order", "identifier": "BAD"},
                "hidden": {"valid_identifier": "ORD-101", "value": "shipped", "polls_remaining": 0,
                           "transient_remaining": 0, "unavailable": False}}
        external_text = '{"fixture_only":true}\n'
        external_sha = hashlib.sha256(external_text.encode()).hexdigest()
        base_config = json.loads((ROOT / "configs/calibrated_b0.json").read_text())
        base_config.update(cases_path="data/dev/fixture.jsonl", case_ids=[case["case_id"]], case_seeds={case["case_id"]: 0},
                           external_inputs=[{"name": "model_manifest", "path": str(self.base / "not_loaded.json"), "sha256": external_sha}])
        for arm in ("B0", "B1", "B2"):
            path = self.base / arm
            config = copy.deepcopy(base_config)
            config.update(comparison_arm=arm, policy_name="fixture_" + arm)
            for name in CORE_FILES:
                target = path / "inputs" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / name, target)
            write_json(path / "inputs" / config["cases_path"], case)
            write_json(path / "inputs/configs/fixture.json", config)
            write_json(path / "config.json", config)
            generations, events = [], []
            result, trace = run_calibrated_case(case, FakeBackend(), config,
                emit_generation=generations.append, emit_event=events.append)
            for name, values in (("generations.jsonl", generations), ("execution_events.jsonl", events),
                                 ("case_results.jsonl", [result]), ("trajectories.jsonl", [trace])):
                (path / name).write_text("".join(json.dumps(row) + "\n" for row in values))
            write_json(path / "metrics.json", calibrated_metrics([result]))
            (path / "external_inputs").mkdir()
            (path / "external_inputs/model_manifest.json").write_text(external_text)
            prompt = CALIBRATED_SYSTEM_PROMPT + ("\n\n" + prompt_for(arm) if prompt_for(arm) else "")
            provenance = {"model": config["model"], "sampling": config["sampling"],
                "runtime": {"executable": sys.executable, "packages": {"fixture_only": "1"}},
                "model_files_verified": True, "model_manifest_sha256": external_sha,
                "chat_template_sha256": "a" * 64, "effective_generation": {"fixture_only": True},
                "actual_model_class": "FakeNotLoaded", "actual_parameter_device": "fixture",
                "actual_parameter_dtype": "fixture", "checkpoint_loading_info": {},
                "case_seeds": config["case_seeds"], "comparison_arm": arm,
                "protocol_version": CALIBRATED_PROTOCOL_VERSION, "score_version": CALIBRATED_SCORE_VERSION,
                "guard_version": GUARD_VERSION, "system_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
            write_json(path / "model_provenance.json", provenance)
            write_json(path / "manifest.json", {"run_id": arm, "kind": "model_inference", "status": "completed",
                "exit_code": 0, "evidence_valid": True, "contract_errors": [], "command": [sys.executable],
                "inputs": {}, "outputs": {}})
            self.refs.append({"path": str(path), "manifest_sha256": ""})
            self.refresh(arm)

    def tearDown(self):
        self.temp.cleanup()

    def refresh(self, arm):
        path = self.base / arm
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["inputs"] = {str(p.relative_to(path / "inputs")): digest(p) for p in (path / "inputs").rglob("*") if p.is_file()}
        manifest["outputs"] = {str(p.relative_to(path)): digest(p) for p in path.rglob("*") if p.is_file()
                               and p.relative_to(path).parts[0] != "inputs" and p.name != "manifest.json"}
        write_json(path / "manifest.json", manifest)
        next(ref for ref in self.refs if ref["path"] == str(path))["manifest_sha256"] = digest(path / "manifest.json")

    def test_valid_fake_triplet_replays_and_counts_b2_block(self):
        result = audit_runs({"run_dirs": self.refs})
        self.assertTrue(result["accepted"])
        b2 = result["runs"][2]
        self.assertEqual(b2["generation_count"], 3)
        self.assertEqual(b2["metrics"]["guard_blocked_proposals"], 1)
        self.assertEqual(b2["metrics"]["actual_tool_attempts"], 1)

    def test_wrong_manifest_pin_rejected(self):
        self.refs[0]["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "pin mismatch"):
            audit_runs({"run_dirs": self.refs})

    def test_rehashed_fabricated_block_response_is_rejected(self):
        path = self.base / "B2" / "trajectories.jsonl"
        trace = json.loads(path.read_text())
        trace["events"][1]["response"] = {"code": "FAKE_TOOL_RESULT"}
        write_json(path, trace)
        self.refresh("B2")
        with self.assertRaisesRegex(ValueError, "replay result/state mismatch"):
            audit_runs({"run_dirs": self.refs})

    def test_rehashed_wrong_seed_is_rejected(self):
        path = self.base / "B0" / "generations.jsonl"
        generations = [json.loads(line) for line in path.read_text().splitlines()]
        generations[0]["seed"] = 999
        path.write_text("".join(json.dumps(row) + "\n" for row in generations))
        self.refresh("B0")
        with self.assertRaisesRegex(ValueError, "seed mismatch"):
            audit_runs({"run_dirs": self.refs})

    def test_rehashed_wrong_prompt_hash_is_rejected(self):
        path = self.base / "B0" / "generations.jsonl"
        generations = [json.loads(line) for line in path.read_text().splitlines()]
        generations[0]["prompt_sha256"] = "0" * 64
        path.write_text("".join(json.dumps(row) + "\n" for row in generations))
        self.refresh("B0")
        with self.assertRaisesRegex(ValueError, "prompt hash mismatch"):
            audit_runs({"run_dirs": self.refs})

    def test_missing_arm_rejected(self):
        with self.assertRaisesRegex(ValueError, "triplet"):
            audit_runs({"run_dirs": self.refs[:2]})


if __name__ == "__main__":
    unittest.main()
