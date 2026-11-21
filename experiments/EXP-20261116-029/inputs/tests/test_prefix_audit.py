"""Synthetic six-cell archives only; no model, tokenizer, or GPU is loaded."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import test_prefix_run as fixtures
from scripts.audit_prefix_runs import (CELLS, CORE_FILES, ROOT, audit_run, audit_runs,
    digest, verify_accounting, verify_metrics)
from toolloop_fix.baselines import GUARD_VERSION
from toolloop_fix.calibrated_protocol import CALIBRATED_PROTOCOL_VERSION, CALIBRATED_SCORE_VERSION
from toolloop_fix.harness import state_hash
from toolloop_fix.prefix_run import (PREFIX_RUNTIME_VERSION, PREFIX_SCHEMA_VERSION,
    TASK_WORDING_VERSION, prepare_prefix, prefix_metrics, run_prefix_case)


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n")


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


class Backend:
    def __init__(self, row):
        if row["role"] == "normal_completion_control":
            self.raw = [fixtures.xml("finish_task", answer="shipped", identifier="ORD-101")]
        elif row["role"] == "unavailable_stop_control":
            self.raw = [fixtures.xml("ask_user", field="identifier"), "Unable to proceed.<|im_end|>"]
        else:
            self.raw = [fixtures.xml("get_order", identifier="ORD-OLD"), "<tool_call>", "No result.<|im_end|>"]

    def generate(self, messages, tools, seed):
        prompt = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
        return {"raw_text": self.raw.pop(0), "prompt_text": prompt,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "prompt_token_ids": [1], "generated_token_ids": [2, 3], "input_tokens": 1,
            "output_tokens": 2, "elapsed_seconds": 0.25, "token_limit_reached": False,
            "seed": seed, "effective_generation": {"unit_test_double": True}}


class PrefixAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="prefix-audit-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        helper = fixtures.PrefixRuntimeTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.rows = [helper.make_prefix(),
            helper.make_prefix(category="normal", role="normal_completion_control"),
            helper.make_prefix(category="unrecoverable", role="unavailable_stop_control", calls=[
                fixtures.xml("get_order", identifier="ORD-101"), fixtures.xml("ask_user", field="identifier")])]
        self.refs = []
        external_text = '{"unit_test_double":true}\n'
        external_sha = hashlib.sha256(external_text.encode()).hexdigest()
        for wording, arm in CELLS:
            name = wording + "-" + arm
            path = self.base / name
            cfg = helper.config(self.rows[0], arm, wording)
            cfg.update(cases_path="data/dev/prefix-fixture.jsonl", case_ids=[r["case_id"] for r in self.rows],
                case_seeds={r["case_id"]: i * 16 for i, r in enumerate(self.rows)},
                policy_name="fixture-" + name,
                external_inputs=[{"name": "model_manifest", "path": str(self.base / "never-loaded.json"), "sha256": external_sha}])
            for source in CORE_FILES:
                target = path / "inputs" / source
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / source, target)
            write_rows(path / "inputs" / cfg["cases_path"], self.rows)
            write_json(path / "inputs/configs/fixture.json", cfg)
            write_json(path / "config.json", cfg)
            generations, events, results, traces, contexts = [], [], [], [], []
            for row in self.rows:
                result, trace = run_prefix_case(row, Backend(row), cfg,
                    emit_generation=generations.append, emit_event=events.append, emit_prefix_context=contexts.append)
                results.append(result)
                traces.append(trace)
            for artifact, values in (("generations.jsonl", generations), ("execution_events.jsonl", events),
                    ("case_results.jsonl", results), ("trajectories.jsonl", traces), ("prefix_contexts.jsonl", contexts)):
                write_rows(path / artifact, values)
            write_json(path / "metrics.json", prefix_metrics(results))
            (path / "external_inputs").mkdir()
            (path / "external_inputs/model_manifest.json").write_text(external_text)
            prepared = prepare_prefix(self.rows[0], cfg)
            provenance = {"evidence_class": "model_inference", "model": cfg["model"], "sampling": cfg["sampling"],
                "runtime": {"executable": sys.executable, "packages": {"unit_test_double": "1"}},
                "model_files_verified": True, "model_manifest_sha256": external_sha,
                "chat_template_sha256": "a" * 64, "effective_generation": {"unit_test_double": True},
                "actual_model_class": "NotLoadedTestDouble", "actual_parameter_device": "fixture",
                "actual_parameter_dtype": "fixture", "case_seeds": cfg["case_seeds"], "comparison_arm": arm,
                "protocol_version": CALIBRATED_PROTOCOL_VERSION, "score_version": CALIBRATED_SCORE_VERSION,
                "guard_version": GUARD_VERSION, "mode": "fixed_failure_prefix",
                "prefix_runtime_version": PREFIX_RUNTIME_VERSION, "prefix_schema_version": PREFIX_SCHEMA_VERSION,
                "task_wording_version": TASK_WORDING_VERSION, "wording_profile": wording,
                "system_prompt_sha256": hashlib.sha256(prepared["messages"][0]["content"].encode()).hexdigest(),
                "prefix_dataset_sha256": digest(path / "inputs" / cfg["cases_path"]),
                "prefix_source_rows": {r["case_id"]: state_hash(r) for r in self.rows}}
            write_json(path / "model_provenance.json", provenance)
            write_json(path / "manifest.json", {"run_id": name, "kind": "model_inference", "status": "completed",
                "exit_code": 0, "evidence_valid": True, "contract_errors": [], "command": [sys.executable],
                "config_sha256": digest(path / "config.json"), "inputs": {}, "outputs": {}})
            self.refs.append({"path": str(path), "manifest_sha256": ""})
            self.refresh(name)

    def refresh(self, name):
        path = self.base / name
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["config_sha256"] = digest(path / "config.json")
        manifest["inputs"] = {str(p.relative_to(path / "inputs")): digest(p) for p in (path / "inputs").rglob("*") if p.is_file()}
        manifest["outputs"] = {str(p.relative_to(path)): digest(p) for p in path.rglob("*") if p.is_file()
            and p.relative_to(path).parts[0] != "inputs" and p.name != "manifest.json"}
        write_json(path / "manifest.json", manifest)
        next(ref for ref in self.refs if ref["path"] == str(path))["manifest_sha256"] = digest(path / "manifest.json")

    def mutate_rows(self, name, artifact, mutation):
        path = self.base / name / artifact
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        mutation(rows)
        write_rows(path, rows)
        self.refresh(name)

    def test_valid_six_cells_preserve_prefix_and_separate_costs(self):
        result = audit_runs({"run_dirs": self.refs})
        self.assertTrue(result["accepted"])
        self.assertEqual(result["case_runs_replayed"], 18)
        self.assertEqual(result["fresh_generations_replayed"], 36)
        b2 = next(r for r in result["runs"] if r["arm"] == "B2" and r["wording_profile"] == "explicit")
        self.assertEqual(b2["metrics"]["guard_blocked_proposals"], 2)
        self.assertEqual(b2["metrics"]["actual_tool_attempts"], 4)
        self.assertEqual(b2["metrics"]["input_tokens"], 6)
        self.assertEqual(b2["metrics"]["historical_input_tokens"], 12)
        self.assertEqual(b2["metrics"]["new_goal_evidence_recovery"]["denominator"], 1)

    def test_requires_six_unique_cells(self):
        with self.assertRaisesRegex(ValueError, "six wording/arm"):
            audit_runs({"run_dirs": self.refs[:-1]})
        with self.assertRaisesRegex(ValueError, "exactly one"):
            audit_runs({"run_dirs": self.refs[:-1] + self.refs[:1]})

    def test_manifest_pin_tampering_rejected(self):
        self.refs[0]["manifest_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "pin mismatch"):
            audit_run(self.refs[0])

    def test_rehashed_fake_block_response_rejected(self):
        self.mutate_rows("legacy-B2", "trajectories.jsonl",
            lambda rows: rows[0]["events"][1].update(response={"code": "FAKE"}))
        with self.assertRaisesRegex(ValueError, "raw replay result/state mismatch"):
            audit_run(self.refs[2])

    def test_rehashed_prefix_context_budget_rejected(self):
        self.mutate_rows("legacy-B0", "prefix_contexts.jsonl",
            lambda rows: rows[0].update(remaining_tool_budget=99))
        with self.assertRaisesRegex(ValueError, "prefix context differs"):
            audit_run(self.refs[0])

    def test_rehashed_fresh_seed_rejected(self):
        self.mutate_rows("legacy-B0", "generations.jsonl", lambda rows: rows[0].update(seed=999))
        with self.assertRaisesRegex(ValueError, "seed mismatch"):
            audit_run(self.refs[0])

    def test_rehashed_turn_offset_rejected(self):
        self.mutate_rows("legacy-B0", "generations.jsonl", lambda rows: rows[0].update(turn=1))
        with self.assertRaisesRegex(ValueError, "generation turn mismatch"):
            audit_run(self.refs[0])

    def test_rehashed_prompt_digest_and_effective_settings_rejected(self):
        self.mutate_rows("legacy-B0", "generations.jsonl", lambda rows: rows[0].update(prompt_sha256="0" * 64))
        with self.assertRaisesRegex(ValueError, "prompt hash mismatch"):
            audit_run(self.refs[0])
        self.mutate_rows("explicit-B0", "generations.jsonl", lambda rows: rows[0].update(effective_generation={"tampered": True}))
        with self.assertRaisesRegex(ValueError, "settings mismatch"):
            audit_run(self.refs[3])

    def test_rehashed_unseen_hidden_value_in_prompt_rejected(self):
        def mutate(rows):
            rows[0]["prompt_text"] += " shipped"
            rows[0]["prompt_sha256"] = hashlib.sha256(rows[0]["prompt_text"].encode()).hexdigest()
        self.mutate_rows("legacy-B0", "generations.jsonl", mutate)
        with self.assertRaisesRegex(ValueError, "unseen target value"):
            audit_run(self.refs[0])

    def test_uncontrolled_unused_configuration_difference_rejected(self):
        path = self.base / "explicit-B2/config.json"
        config = json.loads(path.read_text())
        config["uncontrolled_note"] = "different"
        write_json(path, config)
        self.refresh("explicit-B2")
        with self.assertRaisesRegex(ValueError, "six-cell configuration difference"):
            audit_runs({"run_dirs": self.refs})

    def test_independent_checks_reject_inherited_goal_and_cost_misattribution(self):
        path = self.base / "legacy-B0"
        config = json.loads((path / "config.json").read_text())
        results = [json.loads(x) for x in (path / "case_results.jsonl").read_text().splitlines()]
        traces = [json.loads(x) for x in (path / "trajectories.jsonl").read_text().splitlines()]
        contexts = [json.loads(x) for x in (path / "prefix_contexts.jsonl").read_text().splitlines()]
        generations = [json.loads(x) for x in (path / "generations.jsonl").read_text().splitlines()]
        raw = [r for r in generations if r["case_id"] == self.rows[1]["case_id"]]
        for field, value, message in (("new_goal_evidence", True, "inherited goal"),
                ("historical_input_tokens", 0, "prefix/total accounting")):
            bad = {**results[1], field: value}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                verify_accounting(bad, traces[1], raw, contexts[1], self.rows[1], config)

    def test_independent_metrics_reject_recovery_denominator_and_historical_cost_mix(self):
        path = self.base / "legacy-B0"
        results = [json.loads(x) for x in (path / "case_results.jsonl").read_text().splitlines()]
        metrics = json.loads((path / "metrics.json").read_text())
        bad = copy.deepcopy(metrics)
        bad["metrics"]["new_goal_evidence_recovery"]["denominator"] = 3
        with self.assertRaisesRegex(ValueError, "metric denominator"):
            verify_metrics(bad, results)
        bad = copy.deepcopy(metrics)
        bad["metrics"]["input_tokens"] += bad["metrics"]["historical_input_tokens"]
        with self.assertRaisesRegex(ValueError, "aggregate counter"):
            verify_metrics(bad, results)


if __name__ == "__main__":
    unittest.main()
