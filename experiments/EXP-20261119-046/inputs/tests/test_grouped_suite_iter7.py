import json
import posixpath
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.build_grouped_dev_suite import (
    CATEGORIES,
    DOMAINS,
    EVAL24_SEED_START,
    EVAL24_SEED_STEP,
    generate_suite_and_manifest,
    sha256_file,
)
from toolloop_fix.baselines import B1_GUIDANCE, guard_action, prompt_for
from toolloop_fix.calibrated_protocol import (
    CALIBRATED_PROTOCOL_VERSION,
    CALIBRATED_SYSTEM_PROMPT,
    messages_for_calibrated_view,
)
from toolloop_fix.calibrated_run import validate_config as validate_calibrated_config
from toolloop_fix.harness import (
    DOMAIN_TOOLS,
    EVIDENCE_CLASS,
    Environment,
    canonical_action,
    run_case,
    scripted_policy,
    summarize,
    tool_call,
    validate_alternative,
)
from toolloop_fix.model_protocol import tool_schemas
from toolloop_fix.prefix_run import EXPLICIT_TASK_PREFIX

ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = ROOT / "data/dev/grouped_dev_suite_iter7.jsonl"
MANIFEST_PATH = ROOT / "data/processed/grouped_split_manifest_ITER007.json"
PILOT_PATH = ROOT / "data/dev/pilot_cases.jsonl"
ITER6_PATH = ROOT / "data/dev/timeout_crossed_iter6.jsonl"


def _load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


class GroupedSuiteIter7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = _load_jsonl(SUITE_PATH)
        cls.manifest = json.loads(MANIFEST_PATH.read_text())
        cls.pilot_cases = _load_jsonl(PILOT_PATH)
        cls.iter6_cases = _load_jsonl(ITER6_PATH)
        cls.by_id = {c["case_id"]: c for c in cls.cases}

    def test_suite_reproducibility_and_cli_check(self):
        built_cases, built_manifest, jsonl_bytes, manifest_bytes = generate_suite_and_manifest()
        self.assertEqual(built_cases, self.cases)
        self.assertEqual(built_manifest, self.manifest)
        self.assertEqual(SUITE_PATH.read_bytes(), jsonl_bytes)
        self.assertEqual(MANIFEST_PATH.read_bytes(), manifest_bytes)
        self.assertEqual(sha256_file(SUITE_PATH), self.manifest["dataset_sha256"])

        check_cmd = [sys.executable, str(ROOT / "scripts/build_grouped_dev_suite.py"), "--check"]
        checked = subprocess.run(check_cmd, cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_cases = Path(tmp) / "suite.jsonl"
            tmp_manifest = Path(tmp) / "manifest.json"
            build_cmd = [
                sys.executable,
                str(ROOT / "scripts/build_grouped_dev_suite.py"),
                "--cases-out",
                str(tmp_cases),
                "--manifest-out",
                str(tmp_manifest),
            ]
            res = subprocess.run(build_cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(tmp_cases.read_bytes(), jsonl_bytes)

            refused = subprocess.run(build_cmd, cwd=ROOT, capture_output=True, text=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("refusing to overwrite", refused.stderr)

    def test_shape_balance_and_g1_total(self):
        self.assertEqual(len(self.cases), 180)
        self.assertEqual(len(self.cases) + len(self.pilot_cases), 195)
        self.assertEqual(self.manifest["gate_status"]["g1_dev_fixture_total_with_pilot15"], 195)

        self.assertEqual(len({c["case_id"] for c in self.cases}), 180)
        groups = {c["template_group"] for c in self.cases}
        self.assertEqual(len(groups), 45)

        for case in self.cases:
            self.assertEqual(case["schema_version"], 1)
            self.assertEqual(case["evidence_class"], EVIDENCE_CLASS)
            self.assertEqual(case["split"], "dev")
            self.assertIn(case["domain"], DOMAINS)
            self.assertIn(case["category"], CATEGORIES)
            self.assertTrue(isinstance(case["fault_subtype"], str) and case["fault_subtype"])

        for domain in DOMAINS:
            for category in CATEGORIES:
                cell = [c for c in self.cases if c["domain"] == domain and c["category"] == category]
                self.assertEqual(len(cell), 12, f"unbalanced cell {(domain, category)}")
                cell_groups = {c["template_group"] for c in cell}
                self.assertEqual(len(cell_groups), 3)
                for tg in cell_groups:
                    members = [c for c in cell if c["template_group"] == tg]
                    self.assertEqual(len(members), 4)
                    # All 4 variants in a template_group share identical fault mechanics.
                    mechanics = {
                        (
                            m["fault_subtype"],
                            m["hidden"]["transient_remaining"],
                            m["hidden"]["polls_remaining"],
                            m["hidden"]["unavailable"],
                            bool(m.get("fixed_prefix")),
                            "identifier" in m["request"],
                        )
                        for m in members
                    }
                    self.assertEqual(len(mechanics), 1)

    def test_disjointness_from_pilot_and_iter6_crossed(self):
        forbidden = {
            "ORD-101",
            "ORD-OLD",
            "/records/report.txt",
            "/old/report.txt",
            "JOB-101",
            "JOB-OLD",
            "shipped",
            "verified report contents",
            "processed 25 records",
            "latest order",
            "report.txt",
            "daily report",
            "R-17",
            "R-42",
            "amber",
            "neutral-read-v1",
            "DEMO-001",
        }
        for row in self.pilot_cases:
            forbidden.add(row["case_id"])
            forbidden.add(row["template_group"])
            forbidden.add(row["request"]["target"])
            if "identifier" in row["request"]:
                forbidden.add(row["request"]["identifier"])
            forbidden.add(row["hidden"]["valid_identifier"])
            forbidden.add(row["hidden"]["value"])
        for row in self.iter6_cases:
            forbidden.add(row["case_id"])
            forbidden.add(row["identifier"])
            forbidden.add(row["template_group"])

        for case in self.cases:
            candidate_strings = [
                case["case_id"],
                case["template_group"],
                case["request"]["target"],
                case["hidden"]["valid_identifier"],
                case["hidden"]["value"],
            ]
            if "identifier" in case["request"]:
                candidate_strings.append(case["request"]["identifier"])
            for cand in candidate_strings:
                self.assertNotIn(cand, forbidden)
                for old_str in forbidden:
                    self.assertNotIn(
                        old_str,
                        cand,
                        f"legacy string {old_str!r} leaked as substring of {cand!r}",
                    )

    def test_posix_paths_literal_targets_and_prefix_contracts(self):
        targets = set()
        valid_ids = set()
        values = set()

        for case in self.cases:
            target = case["request"]["target"]
            self.assertTrue(target.startswith("lookup_key:"), target)
            self.assertEqual(target.strip(), target)
            self.assertNotIn(" ", target)
            targets.add(target)
            valid_ids.add(case["hidden"]["valid_identifier"])
            values.add(case["hidden"]["value"])

            if case["category"] == "clarification":
                self.assertNotIn("identifier", case["request"])
            else:
                self.assertIn("identifier", case["request"])
                self.assertTrue(case["request"]["identifier"])

            primary, _ = DOMAIN_TOOLS[case["domain"]]
            if case["category"] in {"recoverable", "retry_required"}:
                self.assertEqual(
                    case.get("fixed_prefix"),
                    [tool_call(primary, identifier=case["request"]["identifier"])],
                )
            else:
                self.assertNotIn("fixed_prefix", case)

            if case["domain"] == "files":
                paths = [case["hidden"]["valid_identifier"]]
                if "identifier" in case["request"]:
                    paths.append(case["request"]["identifier"])
                for p in paths:
                    self.assertTrue(p.startswith("/"))
                    self.assertFalse(p.endswith("/"))
                    self.assertNotIn("..", p)
                    self.assertEqual(posixpath.normpath(p), p)
                    norm_call = json.loads(canonical_action(tool_call("read_file", identifier=p)))
                    self.assertEqual(norm_call["arguments"]["identifier"], p)

        # Every case has unique target, valid_identifier, and value across all 180 cases.
        self.assertEqual(len(targets), 180)
        self.assertEqual(len(valid_ids), 180)
        self.assertEqual(len(values), 180)

    def test_partition_manifest_zero_leakage_and_gates(self):
        self.assertIs(self.manifest["training_eligible"], False)
        self.assertIs(self.manifest["formal_closed_holdout_unsealed"], False)

        expected_counts = {
            "dev_InDomain_candidate_pool": (14, 56, {"orders", "files"}),
            "dev_InDomain_selection": (16, 64, {"orders", "files"}),
            "dev_CrossDomain_diagnostic": (8, 32, {"jobs"}),
            "dev_CrossDomain_reserved": (7, 28, {"jobs"}),
        }
        partitions = self.manifest["partitions"]
        self.assertEqual(set(partitions), set(expected_counts))

        seen_groups = set()
        seen_cases = set()
        part_sets = {}

        for name, (exp_groups, exp_cases, exp_domains) in expected_counts.items():
            part = partitions[name]
            self.assertIs(part["training_eligible"], False)
            self.assertEqual(part["template_group_count"], exp_groups)
            self.assertEqual(part["case_count"], exp_cases)
            self.assertEqual(len(part["template_groups"]), exp_groups)
            self.assertEqual(len(part["case_ids"]), exp_cases)

            rows = [self.by_id[cid] for cid in part["case_ids"]]
            self.assertEqual({r["domain"] for r in rows}, exp_domains)
            self.assertEqual({r["template_group"] for r in rows}, set(part["template_groups"]))
            if exp_domains == {"jobs"}:
                self.assertIs(part["excluded_from_in_domain_training_pool"], True)

            ids_in_part = set()
            for r in rows:
                ids_in_part.add(r["hidden"]["valid_identifier"])
                if "identifier" in r["request"]:
                    ids_in_part.add(r["request"]["identifier"])
            part_sets[name] = {
                "groups": set(part["template_groups"]),
                "cases": set(part["case_ids"]),
                "identifiers": ids_in_part,
                "targets": {r["request"]["target"] for r in rows},
                "values": {r["hidden"]["value"] for r in rows},
            }
            seen_groups |= part_sets[name]["groups"]
            seen_cases |= part_sets[name]["cases"]

        self.assertEqual(len(seen_groups), 45)
        self.assertEqual(len(seen_cases), 180)

        names = list(part_sets)
        for i, left in enumerate(names):
            for right in names[i + 1:]:
                for field in ("groups", "cases", "identifiers", "targets", "values"):
                    self.assertEqual(
                        part_sets[left][field] & part_sets[right][field],
                        set(),
                        f"overlap in {field} between {left} and {right}",
                    )

    def test_mps_unseen_eval24_slice_and_seeds(self):
        eval24_ids = self.manifest["mps_unseen_eval24_case_ids"]
        eval24_seeds = self.manifest["mps_unseen_eval24_case_seeds"]
        eval24_slice = self.manifest["mps_unseen_eval24_slice"]

        self.assertEqual(len(eval24_ids), 24)
        self.assertEqual(len(set(eval24_ids)), 24)
        self.assertEqual(set(eval24_ids), set(eval24_seeds))

        sel_ids = set(self.manifest["partitions"]["dev_InDomain_selection"]["case_ids"])
        diag_ids = set(self.manifest["partitions"]["dev_CrossDomain_diagnostic"]["case_ids"])
        pool_ids = set(self.manifest["partitions"]["dev_InDomain_candidate_pool"]["case_ids"])
        res_ids = set(self.manifest["partitions"]["dev_CrossDomain_reserved"]["case_ids"])

        eval24_cases = [self.by_id[cid] for cid in eval24_ids]
        self.assertEqual(len({c["template_group"] for c in eval24_cases}), 24)
        self.assertEqual(set(eval24_ids) & (pool_ids | res_ids), set())

        for domain in DOMAINS:
            dom_cases = [c for c in eval24_cases if c["domain"] == domain]
            self.assertEqual(len(dom_cases), 8)
            if domain in {"orders", "files"}:
                self.assertTrue(all(c["case_id"] in sel_ids for c in dom_cases))
            else:
                self.assertTrue(all(c["case_id"] in diag_ids for c in dom_cases))

        expected_slots = {
            "normal": 3,
            "recoverable_stale_id_locate": 3,
            "recoverable_compound": 3,
            "retry_required_1_step": 3,
            "retry_required_boundary": 3,
            "clarification_direct": 3,
            "clarification_compound": 3,
            "unrecoverable": 3,
        }
        self.assertEqual(eval24_slice["slot_counts"], expected_slots)
        for slot in expected_slots:
            domains_for_slot = {
                row["domain"] for row in eval24_slice["cases"] if row["eval24_slot"] == slot
            }
            self.assertEqual(domains_for_slot, set(DOMAINS))

        expected_seed_list = [EVAL24_SEED_START + i * EVAL24_SEED_STEP for i in range(24)]
        self.assertEqual(expected_seed_list[0], 6000)
        self.assertEqual(expected_seed_list[-1], 6736)
        self.assertEqual([eval24_seeds[cid] for cid in eval24_ids], expected_seed_list)

        prior_seeds = set(range(0, 6000))
        turn_windows = [set(range(seed, seed + 16)) for seed in eval24_seeds.values()]
        for i, window in enumerate(turn_windows):
            self.assertEqual(window & prior_seeds, set())
            for other in turn_windows[i + 1:]:
                self.assertEqual(window & other, set())

    def test_prompt_and_public_view_non_leakage_rules(self):
        static_prompt_text = "\n".join(
            (CALIBRATED_SYSTEM_PROMPT, B1_GUIDANCE, EXPLICIT_TASK_PREFIX)
        )
        forbidden_prompt_keys = (
            '"category"',
            '"hidden"',
            '"template_group"',
            '"valid_identifier"',
            '"fault_subtype"',
            "normal",
            "recoverable",
            "retry_required",
            "clarification",
            "unrecoverable",
        )
        unrevealed_count = 0

        for case in self.cases:
            env = Environment(case)
            view = env.public_view()
            self.assertEqual(set(view), {"request", "tools", "history"})

            msgs_b0 = messages_for_calibrated_view(view, [], prompt_for("B0"))
            msgs_b1 = messages_for_calibrated_view(view, [], prompt_for("B1"))
            explicit_user = EXPLICIT_TASK_PREFIX + json.dumps(view["request"], ensure_ascii=False)
            tools = tool_schemas(view)
            rendered_public = json.dumps(
                {"view": view, "b0": msgs_b0, "b1": msgs_b1, "explicit": explicit_user, "tools": tools},
                ensure_ascii=False,
            )

            case_id = case["case_id"]
            value = case["hidden"]["value"]
            valid_id = case["hidden"]["valid_identifier"]
            req_id = case["request"].get("identifier", "")
            target = case["request"]["target"]

            self.assertNotIn(case_id, req_id)
            self.assertNotIn(case_id, target)
            self.assertNotIn(case_id, valid_id)
            self.assertNotIn(case_id, value)
            self.assertNotIn(case_id, rendered_public)

            self.assertNotIn(value, req_id)
            self.assertNotIn(value, target)
            self.assertNotIn(value, valid_id)
            self.assertNotIn(value, static_prompt_text)
            self.assertNotIn(value, rendered_public)

            for label in forbidden_prompt_keys:
                self.assertNotIn(label, rendered_public)

            if case["request"].get("identifier") != valid_id:
                unrevealed_count += 1
                self.assertNotIn(valid_id, json.dumps(case["request"], ensure_ascii=False))
                self.assertNotIn(valid_id, rendered_public)
                self.assertNotIn(valid_id, static_prompt_text)

        # 36 recoverable + 36 clarification + 12 unrecoverable alt_id_f2 = 84 cases
        self.assertEqual(unrevealed_count, 84)

    def test_fault_subtypes_and_exact_harness_trajectories(self):
        expected_codes = {
            "direct_ok_f1": ["OK", "FINAL_RECORDED"],
            "direct_ok_f2": ["OK", "FINAL_RECORDED"],
            "direct_ok_f3": ["OK", "FINAL_RECORDED"],
            "stale_id_locate": ["INVALID_IDENTIFIER", "LOCATED", "OK", "FINAL_RECORDED"],
            "stale_id_locate_plus_1timeout": [
                "INVALID_IDENTIFIER",
                "LOCATED",
                "TIMEOUT",
                "OK",
                "FINAL_RECORDED",
            ],
            "stale_id_locate_plus_2timeout": [
                "INVALID_IDENTIFIER",
                "LOCATED",
                "TIMEOUT",
                "TIMEOUT",
                "OK",
                "FINAL_RECORDED",
            ],
            "stale_id_locate_plus_1poll": [
                "INVALID_IDENTIFIER",
                "LOCATED",
                "PRECONDITION",
                "READY",
                "OK",
                "FINAL_RECORDED",
            ],
            "transient_1_retry": ["TIMEOUT", "OK", "FINAL_RECORDED"],
            "transient_1_retry_alt_schema": ["TIMEOUT", "OK", "FINAL_RECORDED"],
            "transient_2_retries_boundary": ["TIMEOUT", "TIMEOUT", "OK", "FINAL_RECORDED"],
            "job_poll_1_step": ["PRECONDITION", "READY", "OK", "FINAL_RECORDED"],
            "job_poll_2_steps": ["PRECONDITION", "RUNNING", "READY", "OK", "FINAL_RECORDED"],
            "job_poll_3_steps_boundary": [
                "PRECONDITION",
                "RUNNING",
                "RUNNING",
                "READY",
                "OK",
                "FINAL_RECORDED",
            ],
            "missing_id_direct": ["USER_INFORMATION", "OK", "FINAL_RECORDED"],
            "missing_id_plus_1timeout": ["USER_INFORMATION", "TIMEOUT", "OK", "FINAL_RECORDED"],
            "missing_id_plus_2timeout": [
                "USER_INFORMATION",
                "TIMEOUT",
                "TIMEOUT",
                "OK",
                "FINAL_RECORDED",
            ],
            "missing_id_plus_1poll": [
                "USER_INFORMATION",
                "PRECONDITION",
                "READY",
                "OK",
                "FINAL_RECORDED",
            ],
            "unavailable_valid_id_f1": ["RESOURCE_UNAVAILABLE", "STOP_RECORDED"],
            "unavailable_alt_id_f2": ["RESOURCE_UNAVAILABLE", "STOP_RECORDED"],
            "unavailable_archive_id_f3": ["RESOURCE_UNAVAILABLE", "STOP_RECORDED"],
        }

        seen_subtypes = set()
        for case in self.cases:
            subtype = case["fault_subtype"]
            seen_subtypes.add(subtype)
            res, trace = run_case(case, "scripted_reference", "end_to_end")
            codes = [ev["response"]["code"] for ev in trace["events"]]
            self.assertEqual(codes, expected_codes[subtype], case["case_id"])
            self.assertEqual(res["success"], case["category"] != "unrecoverable")
            self.assertEqual(res["correct_termination"], case["category"] == "unrecoverable")
            self.assertFalse(res["has_invalid_loop"])
            self.assertFalse(res["budget_exhausted"])

            if case.get("fixed_prefix"):
                pfx_res, pfx_trace = run_case(case, "scripted_reference", "fixed_failure_prefix")
                self.assertEqual(
                    [ev["response"]["code"] for ev in pfx_trace["events"]],
                    expected_codes[subtype],
                )
                self.assertTrue(pfx_res["success"])
                self.assertFalse(pfx_res["has_invalid_loop"])

        self.assertEqual(seen_subtypes, set(expected_codes))

    def test_summarize_metrics_scripted_policies_b2_guard_and_runner_cli(self):
        e2e_ref = [run_case(c, "scripted_reference", "end_to_end")[0] for c in self.cases]
        e2e_naive = [run_case(c, "scripted_naive", "end_to_end")[0] for c in self.cases]
        pfx_cases = [c for c in self.cases if c.get("fixed_prefix")]
        self.assertEqual(len(pfx_cases), 72)
        pfx_ref = [run_case(c, "scripted_reference", "fixed_failure_prefix")[0] for c in pfx_cases]
        pfx_naive = [run_case(c, "scripted_naive", "fixed_failure_prefix")[0] for c in pfx_cases]

        all_results = e2e_ref + e2e_naive + pfx_ref + pfx_naive
        groups = {(g["policy"], g["mode"]): g for g in summarize(all_results)["groups"]}

        ref_e2e = groups[("scripted_reference", "end_to_end")]
        self.assertEqual(ref_e2e["case_count"], 180)
        self.assertEqual(ref_e2e["task_completion"], {"numerator": 144, "denominator": 144, "value": 1.0})
        self.assertEqual(ref_e2e["normal_completion"], {"numerator": 36, "denominator": 36, "value": 1.0})
        self.assertEqual(ref_e2e["necessary_retry_success"], {"numerator": 36, "denominator": 36, "value": 1.0})
        self.assertEqual(ref_e2e["correct_termination"], {"numerator": 36, "denominator": 36, "value": 1.0})
        self.assertEqual(ref_e2e["invalid_loop_rate"], {"numerator": 0, "denominator": 180, "value": 0.0})
        self.assertEqual(ref_e2e["premature_abandonment"], {"numerator": 0, "denominator": 144, "value": 0.0})
        self.assertEqual(ref_e2e["unnecessary_clarification"], {"numerator": 0, "denominator": 180, "value": 0.0})
        self.assertEqual(ref_e2e["false_success"], {"numerator": 0, "denominator": 180, "value": 0.0})
        self.assertEqual(ref_e2e["budget_exhaustion"], {"numerator": 0, "denominator": 180, "value": 0.0})

        ref_pfx = groups[("scripted_reference", "fixed_failure_prefix")]
        self.assertEqual(ref_pfx["case_count"], 72)
        self.assertEqual(ref_pfx["task_completion"], {"numerator": 72, "denominator": 72, "value": 1.0})
        self.assertEqual(ref_pfx["error_recovery"], {"numerator": 36, "denominator": 36, "value": 1.0})
        self.assertEqual(ref_pfx["necessary_retry_success"], {"numerator": 36, "denominator": 36, "value": 1.0})
        self.assertEqual(ref_pfx["invalid_loop_rate"], {"numerator": 0, "denominator": 72, "value": 0.0})

        naive_e2e = groups[("scripted_naive", "end_to_end")]
        self.assertEqual(naive_e2e["task_completion"], {"numerator": 60, "denominator": 144, "value": 60 / 144})
        self.assertEqual(naive_e2e["normal_completion"], {"numerator": 36, "denominator": 36, "value": 1.0})
        self.assertEqual(naive_e2e["necessary_retry_success"], {"numerator": 24, "denominator": 36, "value": 24 / 36})
        self.assertEqual(naive_e2e["correct_termination"], {"numerator": 0, "denominator": 36, "value": 0.0})
        self.assertEqual(naive_e2e["invalid_loop_rate"], {"numerator": 120, "denominator": 180, "value": 120 / 180})
        self.assertEqual(naive_e2e["budget_exhaustion"], {"numerator": 120, "denominator": 180, "value": 120 / 180})

        naive_pfx = groups[("scripted_naive", "fixed_failure_prefix")]
        self.assertEqual(naive_pfx["task_completion"], {"numerator": 24, "denominator": 72, "value": 24 / 72})
        self.assertEqual(naive_pfx["error_recovery"], {"numerator": 0, "denominator": 36, "value": 0.0})
        self.assertEqual(naive_pfx["necessary_retry_success"], {"numerator": 24, "denominator": 36, "value": 24 / 36})
        self.assertEqual(naive_pfx["invalid_loop_rate"], {"numerator": 48, "denominator": 72, "value": 48 / 72})

        # Verify B2 guard_action never false-blocks scripted_reference on any of the 180 cases.
        for case in self.cases:
            env = Environment(case)
            while env.snapshot()["terminal"] is None:
                view = env.public_view()
                action = scripted_policy(view, "scripted_reference")
                verdict = guard_action(view, action)
                self.assertFalse(verdict["blocked"], f"B2 false block on {case['case_id']}: {action}")
                env.execute(action)

        # Verify CLI toolloop_fix.run executes cleanly on grouped_dev_suite_iter7.jsonl (504 rows).
        with tempfile.TemporaryDirectory() as tmp:
            cfg_path = Path(tmp) / "cfg.json"
            out_dir = Path(tmp) / "out"
            cfg_path.write_text(
                json.dumps(
                    {
                        "evidence_class": "synthetic_harness",
                        "cases_path": "data/dev/grouped_dev_suite_iter7.jsonl",
                        "modes": ["end_to_end", "fixed_failure_prefix"],
                        "policies": ["scripted_naive", "scripted_reference"],
                        "tool_budget": 12,
                        "turn_budget": 16,
                    }
                )
            )
            proc = subprocess.run(
                [sys.executable, "-m", "toolloop_fix.run", "--config", str(cfg_path), "--output", str(out_dir)],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(len(_load_jsonl(out_dir / "case_results.jsonl")), 504)
            self.assertEqual(len(_load_jsonl(out_dir / "trajectories.jsonl")), 504)

    def test_calibrated_config_seed_validation_and_alternative_validation(self):
        base_cfg = json.loads((ROOT / "configs/calibrated_b0.json").read_text())
        base_cfg["cases_path"] = "data/dev/grouped_dev_suite_iter7.jsonl"
        base_cfg["case_ids"] = self.manifest["mps_unseen_eval24_case_ids"]
        base_cfg["case_seeds"] = self.manifest["mps_unseen_eval24_case_seeds"]
        for arm in ("B0", "B1", "B2"):
            cfg = {**base_cfg, "comparison_arm": arm, "protocol_version": CALIBRATED_PROTOCOL_VERSION}
            validate_calibrated_config(cfg)

        # Verify validate_alternative works cleanly on every recoverable case after prefix failure.
        for case in self.cases:
            if case["category"] != "recoverable":
                continue
            primary, locate = DOMAIN_TOOLS[case["domain"]]
            env = Environment(case)
            env.execute(case["fixed_prefix"][0])
            chosen = validate_alternative(env, tool_call(locate, target=case["request"]["target"]))
            self.assertTrue(chosen["public_arguments_only"], case["case_id"])
            self.assertTrue(chosen["locally_validated"], case["case_id"])

            hidden_guess = validate_alternative(
                env, tool_call(primary, identifier=case["hidden"]["valid_identifier"])
            )
            self.assertFalse(hidden_guess["public_arguments_only"], case["case_id"])
            self.assertFalse(hidden_guess["locally_validated"], case["case_id"])


if __name__ == "__main__":
    unittest.main()
