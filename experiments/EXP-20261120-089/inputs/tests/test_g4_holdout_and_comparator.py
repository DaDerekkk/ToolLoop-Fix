import copy
import hashlib
import json
import math
import posixpath
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.build_g3_preference_dataset import _candidate_pool_within_group_variant
from scripts.build_g4_closed_holdout import (
    CATEGORIES,
    DOMAINS,
    FORBIDDEN_SUBSTRINGS_CASE_INSENSITIVE,
    SPLIT_NAME,
    _resolve_archive_root as resolve_build_archive_root,
    generate_g4_suite_and_manifest,
    sha256_file,
    verify_g4_suite_and_manifest,
)
from scripts.compare_g3_g4_results import (
    CANONICAL_ARM_ORDER,
    SHARED_CORE_FILES,
    _resolve_archive_root as resolve_cmp_archive_root,
    compare_g3_g4_results,
    render_markdown_report,
    sample_stats,
    t_crit_95,
)
from toolloop_fix.harness import (
    DOMAIN_TOOLS,
    EVIDENCE_CLASS,
    Environment,
    detect_loops,
    run_case,
    summarize,
    tool_call,
)
from toolloop_fix.loop_detector_v2 import detect_loops_v2, has_invalid_loop_v2

ARCHIVE_ROOT = resolve_build_archive_root(WORKSPACE_ROOT)
G4_SUITE_PATH = WORKSPACE_ROOT / "data/dev/g4_closed_holdout_iter9.jsonl"
G4_MANIFEST_PATH = ARCHIVE_ROOT / "data/processed/g4_holdout_manifest_ITER009.json"


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestG4ClosedHoldoutSuite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = _read_jsonl(G4_SUITE_PATH)
        cls.manifest = (
            json.loads(G4_MANIFEST_PATH.read_text(encoding="utf-8"))
            if G4_MANIFEST_PATH.is_file()
            else generate_g4_suite_and_manifest(workspace_root=WORKSPACE_ROOT)[1]
        )

    def test_byte_for_byte_reproducibility_and_cli_check(self):
        built_cases, built_manifest, jsonl_bytes, manifest_bytes = generate_g4_suite_and_manifest(
            workspace_root=WORKSPACE_ROOT
        )
        self.assertEqual(built_cases, self.cases)
        self.assertEqual(built_manifest, self.manifest)
        self.assertEqual(G4_SUITE_PATH.read_bytes(), jsonl_bytes)
        if G4_MANIFEST_PATH.is_file():
            self.assertEqual(G4_MANIFEST_PATH.read_bytes(), manifest_bytes)
        self.assertEqual(sha256_file(G4_SUITE_PATH), self.manifest["dataset_sha256"])

        if G4_MANIFEST_PATH.is_file():
            chk = subprocess.run(
                [
                    sys.executable,
                    str(WORKSPACE_ROOT / "scripts/build_g4_closed_holdout.py"),
                    "--check",
                    "--cases-out",
                    str(G4_SUITE_PATH),
                    "--manifest-out",
                    str(G4_MANIFEST_PATH),
                ],
                cwd=WORKSPACE_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(chk.returncode, 0, chk.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_cases = Path(tmp) / "g4_suite.jsonl"
            tmp_manifest = Path(tmp) / "g4_manifest.json"
            cmd = [
                sys.executable,
                str(WORKSPACE_ROOT / "scripts/build_g4_closed_holdout.py"),
                "--cases-out",
                str(tmp_cases),
                "--manifest-out",
                str(tmp_manifest),
            ]
            res = subprocess.run(cmd, cwd=WORKSPACE_ROOT, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(tmp_cases.read_bytes(), jsonl_bytes)
            self.assertEqual(tmp_manifest.read_bytes(), manifest_bytes)

            refused = subprocess.run(cmd, cwd=WORKSPACE_ROOT, capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("refusing to overwrite", refused.stderr)

    def test_exact_36_case_matrix_and_18_prefixes(self):
        self.assertEqual(len(self.cases), 36)
        self.assertEqual(len({c["case_id"] for c in self.cases}), 36)
        self.assertEqual(len({c["template_group"] for c in self.cases}), 36)
        self.assertTrue(all(c["template_group"].startswith("g4h9-") for c in self.cases))

        completable = [c for c in self.cases if c["category"] != "unrecoverable"]
        unrecoverable = [c for c in self.cases if c["category"] == "unrecoverable"]
        self.assertEqual(len(completable), 30)
        self.assertEqual(len(unrecoverable), 6)

        in_domain = [c for c in self.cases if c["domain"] in ("orders", "files")]
        cross_domain = [c for c in self.cases if c["domain"] == "jobs"]
        self.assertEqual(len(in_domain), 24)
        self.assertEqual(len(cross_domain), 12)

        pfx_cases = [c for c in self.cases if "fixed_prefix" in c]
        self.assertEqual(len(pfx_cases), 18)
        self.assertEqual(sum(c["category"] == "recoverable" for c in pfx_cases), 12)
        self.assertEqual(sum(c["category"] == "retry_required" for c in pfx_cases), 6)

        for c in self.cases:
            self.assertEqual(c["schema_version"], 1)
            self.assertEqual(c["evidence_class"], EVIDENCE_CLASS)
            self.assertEqual(c["split"], SPLIT_NAME)
            primary, _ = DOMAIN_TOOLS[c["domain"]]
            if c["category"] in ("recoverable", "retry_required"):
                self.assertEqual(
                    c["fixed_prefix"],
                    [tool_call(primary, identifier=c["request"]["identifier"])],
                )
            else:
                self.assertNotIn("fixed_prefix", c)
            if c["category"] == "clarification":
                self.assertNotIn("identifier", c["request"])
            else:
                self.assertTrue(c["request"]["identifier"])
            if c["domain"] == "files":
                for pval in filter(None, (c["hidden"]["valid_identifier"], c["request"].get("identifier"))):
                    self.assertEqual(posixpath.normpath(pval), pval)
                    self.assertTrue(pval.startswith("/"))

    def test_zero_leakage_against_all_prior_files_and_exp062_dpo(self):
        prior_cids = set()
        prior_groups = set()
        prior_ids = set()
        prior_targets = set()
        prior_values = set()

        for rel in ("data/dev/pilot_cases.jsonl", "data/dev/grouped_dev_suite_iter7.jsonl"):
            for c in _read_jsonl(WORKSPACE_ROOT / rel):
                prior_cids.add(c["case_id"])
                prior_groups.add(c["template_group"])
                prior_ids.add(c["hidden"]["valid_identifier"])
                if "identifier" in c["request"]:
                    prior_ids.add(c["request"]["identifier"])
                prior_targets.add(c["request"]["target"])
                prior_values.add(c["hidden"]["value"])

        # Also expand candidate_pool variants v5..v20 to ensure zero collision with expanded 14000..34000 pool.
        iter7_cases = _read_jsonl(WORKSPACE_ROOT / "data/dev/grouped_dev_suite_iter7.jsonl")
        for c in iter7_cases:
            if c["domain"] in ("orders", "files") and c["template_group"].endswith(("-f2", "-f3")):
                for vnum in range(5, 21):
                    exp_c = _candidate_pool_within_group_variant(c, vnum)
                    prior_cids.add(exp_c["case_id"])
                    prior_ids.add(exp_c["hidden"]["valid_identifier"])
                    if "identifier" in exp_c["request"]:
                        prior_ids.add(exp_c["request"]["identifier"])
                    prior_targets.add(exp_c["request"]["target"])
                    prior_values.add(exp_c["hidden"]["value"])

        for row in _read_jsonl(WORKSPACE_ROOT / "data/dev/fixed_prefix_cases_iter4.jsonl"):
            prior_cids.add(row["case_id"])
            oc = row["original_case"]
            prior_cids.add(oc["case_id"])
            prior_groups.add(oc["template_group"])
            prior_ids.add(oc["hidden"]["valid_identifier"])
            if "identifier" in oc["request"]:
                prior_ids.add(oc["request"]["identifier"])
            prior_targets.add(oc["request"]["target"])
            prior_values.add(oc["hidden"]["value"])

        for row in _read_jsonl(WORKSPACE_ROOT / "data/dev/timeout_crossed_iter6.jsonl"):
            prior_cids.add(row["case_id"])
            prior_groups.add(row["template_group"])
            prior_ids.add(row["identifier"])

        exp062 = ARCHIVE_ROOT / "experiments/EXP-20261120-062"
        p_text = (exp062 / "p_toolloop_fix_dpo.jsonl").read_text(encoding="utf-8")
        b5_text = (exp062 / "b5_general_expert_dpo.jsonl").read_text(encoding="utf-8")
        self.assertEqual(len(p_text.splitlines()), 400)
        self.assertEqual(len(b5_text.splitlines()), 400)

        g4_cids = {c["case_id"] for c in self.cases}
        g4_groups = {c["template_group"] for c in self.cases}
        g4_ids = {c["hidden"]["valid_identifier"] for c in self.cases} | {
            c["request"]["identifier"] for c in self.cases if "identifier" in c["request"]
        }
        g4_targets = {c["request"]["target"] for c in self.cases}
        g4_values = {c["hidden"]["value"] for c in self.cases}

        self.assertFalse(g4_cids & prior_cids)
        self.assertFalse(g4_groups & prior_groups)
        self.assertFalse(g4_ids & prior_ids)
        self.assertFalse(g4_targets & prior_targets)
        self.assertFalse(g4_values & prior_values)

        for probe in g4_cids | g4_groups | g4_ids | g4_targets | g4_values:
            self.assertNotIn(probe, p_text)
            self.assertNotIn(probe, b5_text)
            for bad in FORBIDDEN_SUBSTRINGS_CASE_INSENSITIVE:
                self.assertNotIn(bad, probe.lower())

        for c in self.cases:
            pub_json = json.dumps(Environment(c).public_view(), ensure_ascii=False)
            self.assertNotIn(c["case_id"], pub_json)
            self.assertNotIn(c["hidden"]["value"], pub_json)
            if c["request"].get("identifier") != c["hidden"]["valid_identifier"]:
                self.assertNotIn(c["hidden"]["valid_identifier"], pub_json)

    def test_scripted_reference_100pct_and_zero_loops_v1_v2(self):
        e2e_pairs = [run_case(c, "scripted_reference", "end_to_end") for c in self.cases]
        pfx_cases = [c for c in self.cases if c.get("fixed_prefix")]
        pfx_pairs = [run_case(c, "scripted_reference", "fixed_failure_prefix") for c in pfx_cases]

        groups = {
            (g["policy"], g["mode"]): g
            for g in summarize([p[0] for p in e2e_pairs] + [p[0] for p in pfx_pairs])["groups"]
        }
        e2e_g = groups[("scripted_reference", "end_to_end")]
        pfx_g = groups[("scripted_reference", "fixed_failure_prefix")]

        self.assertEqual(e2e_g["task_completion"], {"numerator": 30, "denominator": 30, "value": 1.0})
        self.assertEqual(e2e_g["normal_completion"], {"numerator": 6, "denominator": 6, "value": 1.0})
        self.assertEqual(e2e_g["necessary_retry_success"], {"numerator": 6, "denominator": 6, "value": 1.0})
        self.assertEqual(e2e_g["correct_termination"], {"numerator": 6, "denominator": 6, "value": 1.0})
        self.assertEqual(e2e_g["invalid_loop_rate"], {"numerator": 0, "denominator": 36, "value": 0.0})

        self.assertEqual(pfx_g["error_recovery"], {"numerator": 12, "denominator": 12, "value": 1.0})
        self.assertEqual(pfx_g["necessary_retry_success"], {"numerator": 6, "denominator": 6, "value": 1.0})
        self.assertEqual(pfx_g["task_completion"], {"numerator": 18, "denominator": 18, "value": 1.0})
        self.assertEqual(pfx_g["invalid_loop_rate"], {"numerator": 0, "denominator": 18, "value": 0.0})

        for res, trj in e2e_pairs + pfx_pairs:
            hist = [
                ev
                for ev in trj["final_snapshot"]["history"]
                if isinstance(ev.get("action"), dict) and ev["action"].get("type") == "tool"
            ]
            self.assertEqual(detect_loops(hist), [])
            self.assertEqual(detect_loops_v2(hist), [])
            self.assertFalse(has_invalid_loop_v2(hist, start_index=res["prefix_tool_attempts"]))

    def test_disjoint_seed_schedules_and_resolve_archive_root_snapshot(self):
        e2e_scheds = self.manifest["e2e_seed_schedules"]
        pfx_scheds = self.manifest["prefix_seed_schedules"]
        self.assertEqual([s["schedule_id"] for s in e2e_scheds], ["g4_e2e_s1", "g4_e2e_s2", "g4_e2e_s3"])
        self.assertEqual([s["seed_start"] for s in e2e_scheds], [80000, 82000, 84000])
        self.assertEqual([s["schedule_id"] for s in pfx_scheds], ["g4_pfx_s1", "g4_pfx_s2", "g4_pfx_s3"])
        self.assertEqual([s["seed_start"] for s in pfx_scheds], [90000, 92000, 94000])

        intervals = []
        for s in e2e_scheds + pfx_scheds:
            self.assertEqual(s["seed_step"], 32)
            for cid, seed in s["case_seeds"].items():
                intervals.append((seed, seed + 16, f"{cid}@{s['schedule_id']}"))
        self.assertEqual(len(intervals), 162)
        intervals.sort()
        for idx in range(len(intervals) - 1):
            self.assertLessEqual(intervals[idx][1], intervals[idx + 1][0])

        # Simulate record_run.py frozen snapshot directory (`EXP-SNAP/inputs/` without `experiments/` or `data/processed/`)
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "EXP-20261120-SNAP"
            snap_inputs = run_dir / "inputs"
            (snap_inputs / "data/dev").mkdir(parents=True)
            for jf in (WORKSPACE_ROOT / "data/dev").glob("*.jsonl"):
                shutil.copy2(jf, snap_inputs / "data/dev" / jf.name)
            (run_dir / "manifest.json").write_text(
                json.dumps({"source_workspace": str(ARCHIVE_ROOT)}),
                encoding="utf-8",
            )
            self.assertEqual(resolve_build_archive_root(snap_inputs), ARCHIVE_ROOT.resolve())
            self.assertEqual(resolve_cmp_archive_root(snap_inputs), ARCHIVE_ROOT.resolve())
            snap_cases, snap_manifest, _, _ = generate_g4_suite_and_manifest(workspace_root=snap_inputs)
            verify_g4_suite_and_manifest(snap_cases, snap_manifest, workspace_root=snap_inputs)
            self.assertEqual(snap_manifest["dataset_sha256"], self.manifest["dataset_sha256"])


def _build_mock_model_run(
    base_dir,
    run_id,
    arm,
    mode,
    cases_relpath,
    cases_list,
    schedules_spec,
    episode_builder,
    train_seed=101,
    multiseed_format=True,
    force_guard_blocks_on_non_b2=0,
):
    """Create a complete SHA256-sealed `model_inference` run directory for comparator unit testing."""
    run_dir = Path(base_dir) / run_id
    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=False)

    # Copy core python files + dataset into inputs/
    for rel in list(SHARED_CORE_FILES) + ["toolloop_fix/loop_detector_v2.py", cases_relpath]:
        src = WORKSPACE_ROOT / rel
        dst = inputs_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    model_manifest_obj = {
        "model_id": "Qwen/Qwen3.5-0.8B",
        "revision": "2fc06364715b967f1860aea9cf38778875588b17",
        "model_path": "/models/Qwen3.5-0.8B/2fc06364715b967f1860aea9cf38778875588b17",
        "files": [{"path": "config.json", "sha256": "b" * 64, "size_bytes": 100}],
    }
    ext_dir = run_dir / "external_inputs"
    ext_dir.mkdir(parents=True, exist_ok=True)
    ext_manifest_path = ext_dir / "model_manifest.json"
    ext_manifest_path.write_text(json.dumps(model_manifest_obj, sort_keys=True) + "\n", encoding="utf-8")
    ext_sha = sha256_file(ext_manifest_path)

    case_ids = [c["case_id"] for c in cases_list]
    config = {
        "schema_version": 1,
        "evidence_class": "model_inference",
        "evidence_kind": "model_inference",
        "policy_name": f"mock_{arm}_s{train_seed}",
        "comparison_arm": arm if arm in ("B0", "B1", "B2") else "B0",
        "training_arm": arm,
        "train_seed": train_seed,
        "mode": mode,
        "cases_path": cases_relpath,
        "case_ids": case_ids,
        "tool_budget": 12,
        "turn_budget": 16,
        "max_context_tokens": 8192,
        "model": {
            "model_id": "Qwen/Qwen3.5-0.8B",
            "revision": "2fc06364715b967f1860aea9cf38778875588b17",
            "backend": "transformers",
            "model_path": "/models/Qwen3.5-0.8B/2fc06364715b967f1860aea9cf38778875588b17",
            "device": "mps",
            "dtype": "bfloat16",
        },
        "sampling": {
            "enable_thinking": False,
            "do_sample": True,
            "temperature": 1.0,
            "top_p": 1.0,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 2.0,
            "repetition_penalty": 1.0,
            "max_new_tokens": 256,
            "seed": 0,
        },
        "external_inputs": [
            {
                "name": "model_manifest",
                "path": "/models/manifests/qwen.json",
                "sha256": ext_sha,
            }
        ],
    }
    if multiseed_format:
        config["seed_schedules"] = schedules_spec
    else:
        config["case_seeds"] = schedules_spec[0]["case_seeds"]

    cfg_rel = f"configs/{run_id}.json"
    cfg_in = inputs_dir / cfg_rel
    cfg_in.parent.mkdir(parents=True, exist_ok=True)
    cfg_bytes = (json.dumps(config, indent=2, sort_keys=True) + "\n").encode("utf-8")
    cfg_in.write_bytes(cfg_bytes)
    (run_dir / "config.json").write_bytes(cfg_bytes)

    provenance = {
        "evidence_class": "model_inference",
        "model": config["model"],
        "sampling": config["sampling"],
        "model_files_verified": True,
        "model_manifest_sha256": ext_sha,
        "chat_template_sha256": "c" * 64,
        "train_seed": train_seed,
    }
    (run_dir / "model_provenance.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    res_lines = []
    trj_lines = []
    gen_lines = []
    for sched in schedules_spec:
        sid = sched["schedule_id"]
        for idx, c in enumerate(cases_list):
            cid = c["case_id"]
            seed = sched["case_seeds"][cid]
            spec = episode_builder(arm, mode, c, idx, sid, train_seed)
            trial_cid = f"{cid}@{sid}" if multiseed_format else cid
            id_fields = (
                {"case_id": trial_cid, "trial_id": trial_cid, "base_case_id": cid, "schedule_id": sid}
                if multiseed_format
                else {"case_id": cid}
            )
            pfx_len = len(c.get("fixed_prefix", [])) if mode == "fixed_failure_prefix" else 0
            hist = []
            if pfx_len:
                env = Environment(c)
                for act in c["fixed_prefix"]:
                    hist.append(env.execute(act))
            hist.extend(spec.get("continuation_tool_events", []))

            flags_v1 = [f for f in detect_loops(hist) if f["index"] >= pfx_len]
            res_obj = {
                **id_fields,
                "domain": c["domain"],
                "category": c["category"],
                "template_group": c["template_group"],
                "fault_subtype": c["fault_subtype"],
                "mode": mode,
                "evidence_class": "model_inference",
                "case_seed": seed,
                "completable": c["category"] != "unrecoverable",
                "goal_at_prefix": False,
                "tool_goal_attainment": spec["goal"],
                "new_goal_evidence": spec["goal"],
                "exact_grounded_structured_completion": spec["exact"],
                "premature_stop_without_goal_evidence": spec.get("abandon", False),
                "unsupported_finish_wrapper": spec.get("false_success", False),
                "terminal_form": spec.get("terminal_form", "structured_finish" if spec["exact"] else "natural_text"),
                "natural_termination_semantics_unknown": spec.get("natural_unknown", False),
                "stopped_after_public_unavailable": spec.get("stopped_unavail", False),
                "exact_stop_reason_code": spec.get("exact_stop", False),
                "budget_exhausted": spec.get("exhausted", False),
                "has_invalid_loop": bool(flags_v1),
                "loop_types": sorted({f["type"] for f in flags_v1}),
                "prefix_turns": pfx_len,
                "prefix_tool_attempts": pfx_len,
                "prefix_actual_tool_attempts": pfx_len,
                "tool_attempts": pfx_len + spec.get("cont_attempts", 2),
                "continuation_proposal_attempts": spec.get("cont_attempts", 2),
                "actual_tool_attempts": len(hist),
                "guard_blocked_proposals": (
                    spec.get("guard_blocks", 0) if arm == "B2" else force_guard_blocks_on_non_b2
                ),
                "parser_error_turns": 0,
                "turns": pfx_len + spec.get("cont_attempts", 2) + 1,
                "input_tokens": spec.get("in_tokens", 900),
                "output_tokens": spec.get("out_tokens", 100),
                "generation_seconds": 1.25,
            }
            trj_obj = {
                **id_fields,
                "domain": c["domain"],
                "category": c["category"],
                "template_group": c["template_group"],
                "mode": mode,
                "evidence_class": "model_inference",
                "case_seed": seed,
                "actual_tool_history": hist,
            }
            gen_obj = {
                **id_fields,
                "evidence_class": "model_inference",
                "turn": 1,
                "seed": seed,
                "raw_text": "ok",
                "input_tokens": res_obj["input_tokens"],
                "output_tokens": res_obj["output_tokens"],
            }
            res_lines.append(json.dumps(res_obj, sort_keys=True))
            trj_lines.append(json.dumps(trj_obj, sort_keys=True))
            gen_lines.append(json.dumps(gen_obj, sort_keys=True))

    (run_dir / "case_results.jsonl").write_text("\n".join(res_lines) + "\n", encoding="utf-8")
    (run_dir / "trajectories.jsonl").write_text("\n".join(trj_lines) + "\n", encoding="utf-8")
    (run_dir / "generations.jsonl").write_text("\n".join(gen_lines) + "\n", encoding="utf-8")
    (run_dir / "metrics.json").write_text(
        json.dumps({"evidence_class": "model_inference"}, indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "stdout.log").write_text("ok\n", encoding="utf-8")
    (run_dir / "stderr.log").write_text("", encoding="utf-8")

    inputs_map = {
        str(p.relative_to(inputs_dir)): sha256_file(p)
        for p in sorted(inputs_dir.rglob("*"))
        if p.is_file()
    }
    outputs_map = {
        str(p.relative_to(run_dir)): sha256_file(p)
        for p in sorted(run_dir.rglob("*"))
        if p.is_file() and p.relative_to(run_dir).parts[0] != "inputs"
    }
    manifest = {
        "schema_version": 3,
        "run_id": run_id,
        "kind": "model_inference",
        "owner": "test",
        "status": "completed",
        "hypothesis": "mock",
        "acceptance_criteria": "mock",
        "inputs": inputs_map,
        "outputs": outputs_map,
        "command": ["python"],
        "cwd": str(inputs_dir),
        "environment": {},
        "started_at_utc": "2026-11-20T00:00:00+00:00",
        "ended_at_utc": "2026-11-20T00:01:00+00:00",
        "config_sha256": sha256_file(run_dir / "config.json"),
        "config_input_path": cfg_rel,
        "execution_mode": "frozen_snapshot",
        "artifact_contract": "model_inference",
        "exit_code": 0,
        "evidence_valid": True,
        "contract_errors": [],
        "inputs_changed_during_run": [],
        "external_inputs_changed_during_run": [],
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"path": str(run_dir), "manifest_sha256": sha256_file(manifest_path), "train_seed": train_seed}


def _loop_history_for_type(loop_kind):
    a_ev = {
        "action": tool_call("get_order", identifier="BAD-1"),
        "response": {"code": "INVALID_IDENTIFIER", "retryable": False},
    }
    b_ev = {
        "action": tool_call("find_order", target="bad_target"),
        "response": {"code": "NOT_FOUND", "retryable": False},
    }
    c_ev = {
        "action": tool_call("ask_user", field="domain"),
        "response": {"code": "INVALID_ARGUMENTS", "retryable": False},
    }
    if loop_kind == "aa":
        return [copy.deepcopy(a_ev), copy.deepcopy(a_ev)]
    if loop_kind == "abab":
        return [copy.deepcopy(a_ev), copy.deepcopy(b_ev), copy.deepcopy(a_ev), copy.deepcopy(b_ev)]
    if loop_kind == "abcabc":
        return [
            copy.deepcopy(a_ev),
            copy.deepcopy(b_ev),
            copy.deepcopy(c_ev),
            copy.deepcopy(a_ev),
        ]
    if loop_kind == "retry_limit":
        t_ok = {
            "action": tool_call("get_order", identifier="ORD-1"),
            "response": {"code": "TIMEOUT", "retryable": True},
        }
        t_cap = {
            "action": tool_call("get_order", identifier="ORD-1"),
            "response": {"code": "RETRY_LIMIT_EXCEEDED", "retryable": False, "limit_kind": "timeout_retries"},
        }
        return [copy.deepcopy(t_ok), copy.deepcopy(t_ok), copy.deepcopy(t_ok), copy.deepcopy(t_cap)]
    return []


class TestCompareG3G4Results(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g4_cases = _read_jsonl(G4_SUITE_PATH)
        cls.g4_manifest = json.loads(G4_MANIFEST_PATH.read_text(encoding="utf-8"))
        cls.dev_manifest = json.loads(
            (ARCHIVE_ROOT / "data/processed/grouped_split_manifest_ITER007.json").read_text(encoding="utf-8")
        )
        all_dev = {
            c["case_id"]: c
            for c in _read_jsonl(WORKSPACE_ROOT / "data/dev/grouped_dev_suite_iter7.jsonl")
        }
        cls.eval24_cases = [all_dev[cid] for cid in cls.dev_manifest["mps_unseen_eval24_case_ids"]]
        cls.eval24_sched = [
            {
                "schedule_id": "eval24_s0",
                "case_seeds": cls.dev_manifest["mps_unseen_eval24_case_seeds"],
            }
        ]
        cls.g4_pfx_cases = [c for c in cls.g4_cases if c.get("fixed_prefix")]

    def test_t_crit_95_and_sample_stats_exactness(self):
        self.assertAlmostEqual(t_crit_95(2), 4.3026527297, places=7)
        self.assertAlmostEqual(t_crit_95(11), 2.2009851601, places=7)
        self.assertAlmostEqual(t_crit_95(17), 2.1098155778, places=7)
        self.assertAlmostEqual(t_crit_95(29), 2.0452296421, places=7)
        self.assertAlmostEqual(t_crit_95(35), 2.0301079283, places=7)
        self.assertAlmostEqual(t_crit_95(25), 2.0595385528, places=6)

        st = sample_stats([0.60, 0.70, 0.80])
        self.assertAlmostEqual(st["mean"], 0.70)
        self.assertAlmostEqual(st["sample_sd"], 0.10)
        self.assertEqual(st["df"], 2)
        expected_half = t_crit_95(2) * (0.10 / math.sqrt(3.0))
        self.assertAlmostEqual(st["ci95"][0], 0.70 - expected_half, places=9)
        self.assertAlmostEqual(st["ci95"][1], 0.70 + expected_half, places=9)

    def test_full_pipeline_metrics_3train_seeds_clustered_ci_and_p_gt_b3(self):
        def builder(arm, mode, case, idx, sid, train_seed):
            completable = case["category"] != "unrecoverable"
            if not completable:
                is_good = arm in ("P", "B3", "P_no_retry")
                return {
                    "goal": False,
                    "exact": False,
                    "stopped_unavail": True,
                    "exact_stop": is_good,
                    "cont_attempts": 1,
                    "continuation_tool_events": [],
                }

            if arm == "B0":
                # Zero exact completions -> cost_per_success exact must be None/N/A!
                # Goal attained on 3 cases so goal != exact is strictly verified.
                # Emit all 4 v2 loop types on indices 0..3.
                lk = {0: "aa", 1: "abab", 2: "abcabc", 3: "retry_limit"}.get(idx)
                return {
                    "goal": idx < 3,
                    "exact": False,
                    "abandon": idx >= 4,
                    "false_success": idx == 0,
                    "terminal_form": "structured_finish" if idx == 0 else "natural_text",
                    "cont_attempts": 4,
                    "in_tokens": 1000,
                    "out_tokens": 200,
                    "continuation_tool_events": _loop_history_for_type(lk) if lk else [],
                }
            if arm == "B1":
                return {
                    "goal": idx < 4,
                    "exact": idx < 2,
                    "abandon": idx >= 4,
                    "false_success": False,
                    "cont_attempts": 3,
                    "continuation_tool_events": _loop_history_for_type("aa") if idx == 0 else [],
                }
            if arm == "B2":
                # Highest dev score among untrained (2 exact, 4 goal, 0 loops) -> selected as Strongest Untrained!
                return {
                    "goal": idx < 4,
                    "exact": idx < 2,
                    "abandon": idx >= 4,
                    "false_success": False,
                    "guard_blocks": 2 if idx < 2 else 0,
                    "cont_attempts": 3,
                    "continuation_tool_events": [],
                }
            if arm == "B3":
                # Moderate performance (~35-45%), clearly below P (~85%) across all seeds.
                cutoff = {101: 11, 202: 12, 303: 10}.get(train_seed, 11)
                ok = idx < (cutoff if mode == "end_to_end" else 5)
                return {
                    "goal": ok,
                    "exact": ok,
                    "abandon": not ok,
                    "cont_attempts": 2,
                    "continuation_tool_events": _loop_history_for_type("aa") if idx in (20, 21) else [],
                }
            if arm == "P":
                # Strong across all 3 sampling seeds and all 3 training seeds (s101, s202, s303),
                # with slight sampling-seed variation to verify mean-not-best-of-K!
                if sid.endswith("s3") and idx == 0:
                    ok = False
                elif train_seed == 202 and idx == 1:
                    ok = False
                elif train_seed == 303 and idx in (1, 2):
                    ok = False
                else:
                    ok = idx < 28
                return {
                    "goal": ok or idx == 28,
                    "exact": ok,
                    "abandon": False,
                    "false_success": False,
                    "cont_attempts": 2,
                    "in_tokens": 800,
                    "out_tokens": 100,
                    "continuation_tool_events": _loop_history_for_type("aa") if idx == 29 else [],
                }
            if arm == "P_no_retry":
                ok = case["category"] != "retry_required" and idx < 22
                return {
                    "goal": ok,
                    "exact": ok,
                    "abandon": case["category"] == "retry_required",
                    "cont_attempts": 2,
                    "continuation_tool_events": [],
                }
            # B4, B5, P_no_normal
            ok = idx < 8
            return {
                "goal": ok,
                "exact": ok and arm != "P_no_normal",
                "abandon": not ok,
                "false_success": arm == "P_no_normal" and ok,
                "terminal_form": "structured_finish" if (ok and arm == "P_no_normal") else "natural_text",
                "cont_attempts": 3,
                "continuation_tool_events": _loop_history_for_type("aa") if idx in (15, 16) else [],
            }

        with tempfile.TemporaryDirectory() as tmp:
            dev_refs = {}
            e2e_refs = {}
            pfx_refs = {}
            for arm in CANONICAL_ARM_ORDER:
                dev_refs[arm] = _build_mock_model_run(
                    tmp,
                    f"EXP-DEV-{arm}",
                    arm,
                    "end_to_end",
                    "data/dev/grouped_dev_suite_iter7.jsonl",
                    self.eval24_cases,
                    self.eval24_sched,
                    builder,
                    train_seed=101,
                    multiseed_format=False,
                )
                e2e_refs[arm] = _build_mock_model_run(
                    tmp,
                    f"EXP-G4E2E-{arm}-s101",
                    arm,
                    "end_to_end",
                    "data/dev/g4_closed_holdout_iter9.jsonl",
                    self.g4_cases,
                    self.g4_manifest["e2e_seed_schedules"],
                    builder,
                    train_seed=101,
                    multiseed_format=True,
                )
                pfx_refs[arm] = _build_mock_model_run(
                    tmp,
                    f"EXP-G4PFX-{arm}-s101",
                    arm,
                    "fixed_failure_prefix",
                    "data/dev/g4_closed_holdout_iter9.jsonl",
                    self.g4_pfx_cases,
                    self.g4_manifest["prefix_seed_schedules"],
                    builder,
                    train_seed=101,
                    multiseed_format=True,
                )

            multi_e2e = {"P": [e2e_refs["P"]], "B3": [e2e_refs["B3"]]}
            multi_pfx = {"P": [pfx_refs["P"]], "B3": [pfx_refs["B3"]]}
            for arm in ("P", "B3"):
                for tseed in (202, 303):
                    multi_e2e[arm].append(
                        _build_mock_model_run(
                            tmp,
                            f"EXP-G4E2E-{arm}-s{tseed}",
                            arm,
                            "end_to_end",
                            "data/dev/g4_closed_holdout_iter9.jsonl",
                            self.g4_cases,
                            self.g4_manifest["e2e_seed_schedules"],
                            builder,
                            train_seed=tseed,
                            multiseed_format=True,
                        )
                    )
                    multi_pfx[arm].append(
                        _build_mock_model_run(
                            tmp,
                            f"EXP-G4PFX-{arm}-s{tseed}",
                            arm,
                            "fixed_failure_prefix",
                            "data/dev/g4_closed_holdout_iter9.jsonl",
                            self.g4_pfx_cases,
                            self.g4_manifest["prefix_seed_schedules"],
                            builder,
                            train_seed=tseed,
                            multiseed_format=True,
                        )
                    )

            cfg = {
                "dev_selection_runs": dev_refs,
                "g4_e2e_runs": e2e_refs,
                "g4_prefix_runs": pfx_refs,
                "multi_train_seed_runs": multi_e2e,
                "multi_train_seed_prefix_runs": multi_pfx,
            }
            report = compare_g3_g4_results(cfg, workspace_root=WORKSPACE_ROOT)

            # 1. Dev strongest untrained selection
            self.assertEqual(report["dev_strongest_untrained"]["selected_arm"], "B2")

            # 2. Zero-denominator cost_per_success on B0 exact completion -> None, while goal cost is finite
            b0_ov = report["g4_e2e"]["arms"]["B0"]["overall"]
            self.assertEqual(b0_ov["task_completion_exact"]["numerator"], 0)
            self.assertEqual(b0_ov["task_completion_goal"]["numerator"], 9)  # 3 cases x 3 sampling seeds
            self.assertIsNone(b0_ov["cost_per_success"]["tool_attempts_per_exact_completion"]["value"])
            self.assertIsNone(b0_ov["cost_per_success"]["tokens_per_exact_completion"]["value"])
            self.assertIsNotNone(b0_ov["cost_per_success"]["tool_attempts_per_goal_attainment"]["value"])

            # 3. Detector v2 catches 4/36 per seed (= 12/108) on B0 whereas v1 misses abcabc (3/36 = 9/108)
            self.assertEqual(b0_ov["invalid_loop_rate_v2"], {"numerator": 12, "denominator": 108, "value": 12 / 108})
            self.assertEqual(b0_ov["invalid_loop_rate_v1"], {"numerator": 9, "denominator": 108, "value": 9 / 108})
            bd = b0_ov["loop_v2_breakdown_by_type"]
            self.assertEqual(bd["identical_or_equivalent_repeat"]["episode_rate"]["numerator"], 3)
            self.assertEqual(bd["short_cycle_abab"]["episode_rate"]["numerator"], 3)
            self.assertEqual(bd["nonadjacent_or_multistep_repeat_without_progress"]["episode_rate"]["numerator"], 6)
            self.assertEqual(bd["retry_limit_violation"]["episode_rate"]["numerator"], 3)

            # 4. Sampling-seed mean (NEVER best-of-K!): idx=0 failed on s3 only -> pooled exact < best-of-3
            p_arm = report["g4_e2e"]["arms"]["P"]
            self.assertEqual(p_arm["aggregation_rule"], "mean_over_sampling_seeds_never_best_of_k")
            s_vals = [
                p_arm["by_sampling_schedule"][sid]["task_completion_exact"]["value"]
                for sid in p_arm["sampling_schedule_ids"]
            ]
            self.assertAlmostEqual(
                p_arm["overall"]["task_completion_exact"]["value"],
                sum(s_vals) / 3.0,
                places=12,
            )
            self.assertIsNotNone(p_arm["sampling_seed_stats"]["task_completion_exact"]["sample_sd"])

            # 5. In-Domain (24 cases x 3 seeds = 72) vs Cross-Domain jobs (12 cases x 3 seeds = 36)
            self.assertEqual(p_arm["in_domain_orders_files"]["episode_count"], 72)
            self.assertEqual(p_arm["cross_domain_heldout_jobs"]["episode_count"], 36)

            # 6. Multi-train-seed (s101, s202, s303) aggregation
            ms_p = report["multi_train_seed"]["e2e"]["P"]
            self.assertEqual(ms_p["train_seeds"], [101, 202, 303])
            self.assertEqual(ms_p["overall"]["task_completion_exact"]["df"], 2)
            self.assertIsNotNone(ms_p["overall"]["task_completion_exact"]["sample_sd"])

            # 7. Clustered paired CIs check (`df=35` on 36 E2E groups, `df=29` on 30 completable, `df=11` on 12 prefix recoverable)
            e2e_c = report["paired_clustered_contrasts"]["g4_e2e"]
            pfx_c = report["paired_clustered_contrasts"]["g4_prefix"]
            for required_key in (
                "P_vs_B0",
                "P_vs_Strongest_Untrained",
                "P_vs_B3",
                "P_vs_B4",
                "P_vs_B5",
                "P_vs_P_no_retry",
                "P_vs_P_no_normal",
            ):
                self.assertIn(required_key, e2e_c)
                self.assertIn(required_key, pfx_c)
            self.assertEqual(e2e_c["P_vs_B0"]["overall"]["invalid_loop_rate_v2"]["df"], 35)
            self.assertEqual(e2e_c["P_vs_B0"]["overall"]["task_completion_exact"]["df"], 29)
            self.assertEqual(pfx_c["P_vs_B3"]["overall"]["invalid_loop_rate_v2"]["df"], 17)
            self.assertEqual(pfx_c["P_vs_B3"]["overall"]["error_recovery_exact"]["df"], 11)

            # 8. Honest Hypothesis Verdict -> P > B3
            hv = report["honest_hypothesis_verdicts"]
            self.assertTrue(
                hv["engineering_targets_section_8_1"]["invalid_loop_reduction_ge_30pct_vs_B0"]["target_met"]
            )
            self.assertTrue(
                hv["engineering_targets_section_8_1"]["error_recovery_gain_ge_5pp_vs_strongest_untrained"]["target_met"]
            )
            self.assertTrue(
                hv["engineering_targets_section_8_1"]["normal_and_retry_retention_within_minus_2pp_vs_B0"]["target_met"]
            )
            self.assertEqual(
                hv["research_attribution_section_8_2"]["p_vs_b3_error_sft"]["relation"],
                "P > B3",
            )

            # 9. CLI execution & overwrite guard
            cfg_path = Path(tmp) / "compare_cfg.json"
            cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
            out_dir = Path(tmp) / "out_report"
            cli_res = subprocess.run(
                [
                    sys.executable,
                    str(WORKSPACE_ROOT / "scripts/compare_g3_g4_results.py"),
                    "--config",
                    str(cfg_path),
                    "--output",
                    str(out_dir),
                ],
                cwd=WORKSPACE_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(cli_res.returncode, 0, cli_res.stderr)
            self.assertTrue((out_dir / "g3_g4_comparison.json").is_file())
            md_content = (out_dir / "g3_g4_comparison.md").read_text(encoding="utf-8")
            self.assertIn("N/A", md_content)
            self.assertIn("P > B3", md_content)

            refused = subprocess.run(
                [
                    sys.executable,
                    str(WORKSPACE_ROOT / "scripts/compare_g3_g4_results.py"),
                    "--config",
                    str(cfg_path),
                    "--output",
                    str(out_dir),
                ],
                cwd=WORKSPACE_ROOT,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(refused.returncode, 0)

    def test_honest_verdict_p_approx_b3_and_tamper_guards(self):
        """Verify Proposal §8.2 honest attribution when Error SFT (B3) matches P, plus zero-loop baseline & tamper guards."""

        def tied_builder(arm, mode, case, idx, sid, train_seed):
            completable = case["category"] != "unrecoverable"
            if not completable:
                return {"goal": False, "exact": False, "stopped_unavail": True, "exact_stop": arm != "B0"}
            if arm == "B0":
                # Zero invalid loops on B0 -> relative loop reduction must be None ("not_applicable_zero_baseline")!
                return {"goal": idx < 2, "exact": idx < 2, "abandon": idx >= 2, "continuation_tool_events": []}
            # P and B3 perform nearly identically (P beats B3 on 1 case and loses on 1 case -> mean_diff == 0.0)
            ok = idx < 24 if arm in ("P", "B3") else idx < 10
            if arm == "P" and idx == 24:
                ok = True
            if arm == "B3" and idx == 25:
                ok = True
            return {"goal": ok, "exact": ok, "abandon": not ok, "continuation_tool_events": []}

        with tempfile.TemporaryDirectory() as tmp:
            dev_refs = {}
            e2e_refs = {}
            pfx_refs = {}
            for arm in ("B0", "B3", "P"):
                dev_refs[arm] = _build_mock_model_run(
                    tmp,
                    f"TIED-DEV-{arm}",
                    arm,
                    "end_to_end",
                    "data/dev/grouped_dev_suite_iter7.jsonl",
                    self.eval24_cases,
                    self.eval24_sched,
                    tied_builder,
                    multiseed_format=False,
                )
                # Also exercise single-schedule G4 format (`multiseed_format=False`)!
                e2e_refs[arm] = _build_mock_model_run(
                    tmp,
                    f"TIED-E2E-{arm}",
                    arm,
                    "end_to_end",
                    "data/dev/g4_closed_holdout_iter9.jsonl",
                    self.g4_cases,
                    [self.g4_manifest["e2e_seed_schedules"][0]],
                    tied_builder,
                    multiseed_format=False,
                )
                pfx_refs[arm] = _build_mock_model_run(
                    tmp,
                    f"TIED-PFX-{arm}",
                    arm,
                    "fixed_failure_prefix",
                    "data/dev/g4_closed_holdout_iter9.jsonl",
                    self.g4_pfx_cases,
                    [self.g4_manifest["prefix_seed_schedules"][0]],
                    tied_builder,
                    multiseed_format=False,
                )

            cfg = {
                "dev_selection_runs": dev_refs,
                "g4_e2e_runs": e2e_refs,
                "g4_prefix_runs": pfx_refs,
            }
            report = compare_g3_g4_results(cfg, workspace_root=WORKSPACE_ROOT)
            hv = report["honest_hypothesis_verdicts"]
            loop_v = hv["engineering_targets_section_8_1"]["invalid_loop_reduction_ge_30pct_vs_B0"]
            self.assertIsNone(loop_v["relative_reduction"])
            self.assertEqual(loop_v["status"], "not_applicable_zero_baseline")

            att = hv["research_attribution_section_8_2"]["p_vs_b3_error_sft"]
            self.assertEqual(att["relation"], "P ≈ B3")
            self.assertIn("Proposal §8.2", att["honest_interpretation"])
            md = render_markdown_report(report)
            self.assertIn("P ≈ B3", md)

            # Proposal §6 guard: trained arm P must NOT use external LoopGuard blocks
            illegal_p = _build_mock_model_run(
                tmp,
                "ILLEGAL-GUARD-P",
                "P",
                "end_to_end",
                "data/dev/g4_closed_holdout_iter9.jsonl",
                self.g4_cases,
                [self.g4_manifest["e2e_seed_schedules"][0]],
                tied_builder,
                multiseed_format=False,
                force_guard_blocks_on_non_b2=1,
            )
            bad_cfg = copy.deepcopy(cfg)
            bad_cfg["g4_e2e_runs"]["P"] = illegal_p
            with self.assertRaisesRegex(ValueError, "Proposal §6 violation"):
                compare_g3_g4_results(bad_cfg, workspace_root=WORKSPACE_ROOT)

            # Artifact SHA256 tamper guard
            tampered_file = Path(e2e_refs["B0"]["path"]) / "stdout.log"
            tampered_file.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "artifact SHA256 mismatch"):
                compare_g3_g4_results(cfg, workspace_root=WORKSPACE_ROOT)

    def test_real_archived_iter7_eval24_runs_compatibility(self):
        """Verify _load_arm_bundle and select_strongest_untrained_on_dev work on real EXP-20261119-054..056 archives."""
        from scripts.compare_g3_g4_results import _load_arm_bundle, select_strongest_untrained_on_dev, summarize_arm_bundle

        real_runs = {
            "B0": ARCHIVE_ROOT / "experiments/EXP-20261119-054",
            "B1": ARCHIVE_ROOT / "experiments/EXP-20261119-055",
            "B2": ARCHIVE_ROOT / "experiments/EXP-20261119-056",
        }
        if not all((p / "manifest.json").is_file() for p in real_runs.values()):
            self.skipTest("Real EXP-20261119-054..056 archives not present")

        dev_summaries = {}
        for arm, run_path in real_runs.items():
            ref = {"path": str(run_path), "manifest_sha256": sha256_file(run_path / "manifest.json")}
            bundle = _load_arm_bundle(arm, ref, "end_to_end", WORKSPACE_ROOT)
            dev_summaries[arm] = summarize_arm_bundle(bundle)

        # On real EXP-054..056: v2 catches non-adjacent loops missed by v1 (B0: 5->7, B2: 0->3),
        # and B1 is uniquely selected on dev eval24 via exact_grounded_structured_completion (1/21 vs 0/21).
        self.assertEqual(dev_summaries["B0"]["overall"]["invalid_loop_rate_v1"]["numerator"], 5)
        self.assertEqual(dev_summaries["B0"]["overall"]["invalid_loop_rate_v2"]["numerator"], 7)
        self.assertEqual(dev_summaries["B2"]["overall"]["invalid_loop_rate_v1"]["numerator"], 0)
        self.assertEqual(dev_summaries["B2"]["overall"]["invalid_loop_rate_v2"]["numerator"], 3)
        sel = select_strongest_untrained_on_dev(dev_summaries)
        self.assertEqual(sel["selected_arm"], "B1")
        self.assertEqual(sel["ranking"], ["B1", "B2", "B0"])


if __name__ == "__main__":
    unittest.main()
