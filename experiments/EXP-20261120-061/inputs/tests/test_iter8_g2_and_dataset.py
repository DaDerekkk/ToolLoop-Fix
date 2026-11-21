import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.adjudicate_g2_silver_and_human import (
    CONSENSUS_PROVENANCE,
    DATASET_GOVERNANCE_TIER,
    HUMAN_TIEBREAKER_TYPE,
    SILVER_REVIEWER_TYPE,
    TIEBREAKER_PROVENANCE,
    _contains_forbidden,
    compare_silver_packs,
    compute_cohens_kappa,
    finalize_g2_verdict,
)
from scripts.build_g2_audit_packs import ALLOWED_SEMANTIC_LABELS, build_audit_packs
from scripts.build_g3_preference_dataset import (
    GROUP_1,
    GROUP_2,
    GROUP_3,
    GROUP_4,
    StdlibXmlTestTokenizer,
    _load_and_verify_isolated_pool,
    build_g3_datasets,
    compute_token_divergence,
    format_qwen_tool_call,
    sha256_file,
    verify_cloud_safe_artifacts,
    verify_decision_point_in_snapshot,
)
from toolloop_fix.calibrated_protocol import CALIBRATED_SYSTEM_PROMPT
from toolloop_fix.harness import Environment, detect_loops, run_case, tool_call
from toolloop_fix.loop_detector_v2 import (
    DETECTOR_V2_VERSION,
    NONRETRYABLE_ERROR_CODES,
    detect_loops_v2,
    has_invalid_loop_v2,
)
from toolloop_fix.prefix_run import EXPLICIT_TASK_PREFIX


def _resolve_archive_root(root):
    if (root / "experiments").is_dir():
        return root
    parent_manifest = root.parent / "manifest.json"
    if parent_manifest.is_file():
        src = json.loads(parent_manifest.read_text(encoding="utf-8")).get("source_workspace")
        if src and (Path(src) / "experiments").is_dir():
            return Path(src)
    return root


ARCHIVE_ROOT = _resolve_archive_root(WORKSPACE_ROOT)
HAS_TRANSFORMERS = importlib.util.find_spec("transformers") is not None
QWEN_MODEL_DIR = ARCHIVE_ROOT / "models/Qwen3.5-0.8B/2fc06364715b967f1860aea9cf38778875588b17"


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class TestLoopDetectorV2(unittest.TestCase):
    def setUp(self):
        self.pilot_cases = _read_jsonl(WORKSPACE_ROOT / "data/dev/pilot_cases.jsonl")
        self.grouped_cases = _read_jsonl(WORKSPACE_ROOT / "data/dev/grouped_dev_suite_iter7.jsonl")

    def _get_pilot(self, domain="orders", category="normal"):
        return copy.deepcopy(
            next(c for c in self.pilot_cases if c["domain"] == domain and c["category"] == category)
        )

    def test_version_and_preserves_aa_abab_and_retry_limit(self):
        self.assertEqual(DETECTOR_V2_VERSION, "nonretryable_repeat_without_progress_v2")

        # 1. Adjacent AA equivalent path repeat
        env_eq = Environment(self._get_pilot("files", "recoverable"))
        env_eq.execute(tool_call("read_file", identifier=" /records/../old/report.txt "))
        env_eq.execute(tool_call("read_file", identifier="/old/report.txt"))
        flags_eq = detect_loops_v2(env_eq.public_view()["history"])
        self.assertEqual(
            flags_eq,
            [{"index": 1, "type": "identical_or_equivalent_repeat", "matched_prior_index": 0}],
        )

        # 2. Short cycle ABAB (deduplicated cleanly at index=3; ABA caught at index=2)
        env_abab = Environment(self._get_pilot("orders", "normal"))
        a = tool_call("get_order", identifier="ORD-BAD")
        b = tool_call("find_order", target="wrong_target")
        for act in (a, b, a, b):
            env_abab.execute(act)
        flags_abab = detect_loops_v2(env_abab.public_view()["history"])
        self.assertEqual(
            flags_abab,
            [
                {
                    "index": 2,
                    "type": "nonadjacent_or_multistep_repeat_without_progress",
                    "matched_prior_index": 0,
                },
                {
                    "index": 3,
                    "type": "short_cycle_abab",
                    "matched_prior_index": 1,
                },
            ],
        )

        # 3. Retry-limit violations on 4th timeout attempt and 4th poll_job
        c_tcap = self._get_pilot("orders", "retry_required")
        c_tcap["hidden"]["transient_remaining"] = 5
        env_tcap = Environment(c_tcap)
        for _ in range(4):
            env_tcap.execute(tool_call("get_order", identifier="ORD-101"))
        flags_tcap = detect_loops_v2(env_tcap.public_view()["history"])
        self.assertEqual([f["index"] for f in flags_tcap], [3])
        self.assertEqual(flags_tcap[0]["type"], "retry_limit_violation")
        self.assertEqual(flags_tcap[0]["matched_prior_index"], 2)

        c_pcap = self._get_pilot("jobs", "retry_required")
        c_pcap["hidden"]["polls_remaining"] = 5
        env_pcap = Environment(c_pcap)
        for _ in range(4):
            env_pcap.execute(tool_call("poll_job", identifier="JOB-101"))
        flags_pcap = detect_loops_v2(env_pcap.public_view()["history"])
        self.assertEqual([f["index"] for f in flags_pcap], [3])
        self.assertEqual(flags_pcap[0]["type"], "retry_limit_violation")
        self.assertEqual(flags_pcap[0]["matched_prior_index"], 2)

    def test_catches_abcabc_3cycle_without_progress(self):
        # Exact INV-CB2-6071 style 3-cycle: ask_user(identifier), ask_user(domain), ask_user(target) x 2
        env = Environment(self._get_pilot("orders", "recoverable"))
        cycle = [
            tool_call("ask_user", field="identifier"),
            tool_call("ask_user", field="domain"),
            tool_call("ask_user", field="target"),
        ]
        for act in cycle + cycle:
            env.execute(act)
        hist = env.public_view()["history"]
        self.assertEqual(detect_loops(hist), [])  # v1 misses ABCABC completely
        flags_v2 = detect_loops_v2(hist)
        self.assertEqual(
            flags_v2,
            [
                {
                    "index": 3,
                    "type": "nonadjacent_or_multistep_repeat_without_progress",
                    "matched_prior_index": 0,
                },
                {
                    "index": 4,
                    "type": "nonadjacent_or_multistep_repeat_without_progress",
                    "matched_prior_index": 1,
                },
                {
                    "index": 5,
                    "type": "nonadjacent_or_multistep_repeat_without_progress",
                    "matched_prior_index": 2,
                },
            ],
        )
        self.assertTrue(has_invalid_loop_v2(hist))

    def test_zero_false_positives_on_legal_timeout_retries_and_job_polls(self):
        # 1st & 2nd legal TIMEOUT retries (both succeeding with OK and hitting 3rd-attempt TIMEOUT cap)
        for transient_n in (1, 2, 4):
            c = self._get_pilot("orders", "retry_required")
            c["hidden"]["transient_remaining"] = transient_n
            env = Environment(c)
            attempts = min(transient_n + 1, 3)  # up to 2 legal retries (indices 0, 1, 2)
            for _ in range(attempts):
                env.execute(tool_call("get_order", identifier="ORD-101"))
            self.assertEqual(detect_loops_v2(env.public_view()["history"]), [])

        # 1, 2, and 3 legal poll_job calls + PRECONDITION -> READY -> get_result(OK)
        for polls_n in (1, 2, 3, 4):
            c = self._get_pilot("jobs", "retry_required")
            c["hidden"]["polls_remaining"] = polls_n
            env = Environment(c)
            env.execute(tool_call("get_result", identifier="JOB-101"))
            for _ in range(min(polls_n, 3)):
                env.execute(tool_call("poll_job", identifier="JOB-101"))
            if polls_n <= 3:
                env.execute(tool_call("get_result", identifier="JOB-101"))
            self.assertEqual(detect_loops_v2(env.public_view()["history"]), [])

        # Zero false positives across all 15 pilot + 180 grouped suite reference trajectories
        for c in self.pilot_cases + self.grouped_cases:
            _, traj = run_case(c, "scripted_reference", "end_to_end")
            tool_hist = [
                {"action": e["action"], "response": e["response"]}
                for e in traj["events"]
                if e["action"].get("type") == "tool"
            ]
            self.assertEqual(detect_loops_v2(tool_hist), [])


class TestAdjudicateG2SilverAndHuman(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        exp058_dir = ARCHIVE_ROOT / "experiments/EXP-20261119-058R1"
        if (exp058_dir / "blind_semantic_review_pack.json").is_file():
            cls.blank_sem = json.loads(
                (exp058_dir / "blind_semantic_review_pack.json").read_text(encoding="utf-8")
            )
            cls.sem_key = json.loads(
                (exp058_dir / "blind_semantic_key.json").read_text(encoding="utf-8")
            )
            cls.blank_det = json.loads(
                (exp058_dir / "blind_detector_audit_pack.json").read_text(encoding="utf-8")
            )
            cls.det_key = json.loads(
                (exp058_dir / "blind_detector_key.json").read_text(encoding="utf-8")
            )
            cls.is_full_358 = True
        else:
            built = build_audit_packs(
                {
                    "shuffle_seed": 20261119,
                    "model_runs": [str(ARCHIVE_ROOT / "experiments/EXP-20261116-016")],
                    "synthetic_runs": [str(ARCHIVE_ROOT / "experiments/EXP-20261116-004")],
                },
                base_dir=ARCHIVE_ROOT,
            )
            cls.blank_sem = built["semantic_pack"]
            cls.sem_key = built["semantic_key"]
            cls.blank_det = built["detector_pack"]
            cls.det_key = built["detector_key"]
            cls.is_full_358 = False

    def _make_silver_packs(self):
        sem_a = copy.deepcopy(self.blank_sem)
        sem_b = copy.deepcopy(self.blank_sem)
        for idx, (ia, ib) in enumerate(zip(sem_a["items"], sem_b["items"])):
            bid = ia["blind_id"]
            inc = self.sem_key["items"][bid]["inclusion_category"]
            base_lab = (
                "grounded_correct_delivery"
                if inc == "grounded_structured_positive_control"
                else "truthful_inability_or_timeout_stop"
                if idx % 2 == 0
                else "error_code_as_record_value"
            )
            ia["reviewer_type"] = SILVER_REVIEWER_TYPE
            ia["reviewer_id"] = "blind_silver_annotator_A"
            ia["factual_grounding_label"] = base_lab
            ia["notes"] = "silver A note"

            ib["reviewer_type"] = SILVER_REVIEWER_TYPE
            ib["reviewer_id"] = "blind_silver_annotator_B"
            ib["factual_grounding_label"] = base_lab
            ib["notes"] = "silver B note"

        # Inject 2 genuine A!=B semantic disagreements on non-positive-controls
        dis_count = 0
        for ia, ib in zip(sem_a["items"], sem_b["items"]):
            if self.sem_key["items"][ia["blind_id"]]["inclusion_category"] != "grounded_structured_positive_control":
                ib["factual_grounding_label"] = "unsupported_absence_or_cause_claim"
                dis_count += 1
                if dis_count == 2:
                    break

        det_a = copy.deepcopy(self.blank_det)
        det_b = copy.deepcopy(self.blank_det)
        for idx, (da, db) in enumerate(zip(det_a["items"], det_b["items"])):
            hist = list(da["history_prefix"]) + [da["focal_step_k"]]
            k = len(hist) - 1
            v2_bool = any(f["index"] == k for f in detect_loops_v2(hist))
            da["reviewer_type"] = SILVER_REVIEWER_TYPE
            da["reviewer_id"] = "blind_silver_annotator_A"
            da["human_is_avoidable_invalid_loop"] = v2_bool
            da["notes"] = "silver det A"

            db["reviewer_type"] = SILVER_REVIEWER_TYPE
            db["reviewer_id"] = "blind_silver_annotator_B"
            db["human_is_avoidable_invalid_loop"] = v2_bool
            db["notes"] = "silver det B"

        # Inject 1 explicit A!=B disagreement on detector
        det_b["items"][0]["human_is_avoidable_invalid_loop"] = not det_a["items"][0][
            "human_is_avoidable_invalid_loop"
        ]
        return sem_a, sem_b, det_a, det_b

    def test_exact_cohens_kappa_math(self):
        # 2x2 table: TT=40, TF=10, FT=5, FF=45 -> N=100, p_o=0.85
        # A_true=50, A_false=50; B_true=45, B_false=55 -> p_e = 0.5*0.45 + 0.5*0.55 = 0.50
        # kappa = (0.85 - 0.50) / (1 - 0.50) = 0.70 exact!
        pairs_2x2 = (
            [(True, True)] * 40
            + [(True, False)] * 10
            + [(False, True)] * 5
            + [(False, False)] * 45
        )
        res_2x2 = compute_cohens_kappa(pairs_2x2, [True, False])
        self.assertAlmostEqual(res_2x2["p_o"], 0.85, places=12)
        self.assertAlmostEqual(res_2x2["p_e"], 0.50, places=12)
        self.assertAlmostEqual(res_2x2["cohens_kappa"], 0.70, places=12)

        # 7-class multi-category check
        cats = ALLOWED_SEMANTIC_LABELS
        pairs_7 = [(cats[0], cats[0])] * 10 + [(cats[1], cats[1])] * 10 + [(cats[0], cats[2])] * 5
        # N=25, p_o = 20/25 = 0.80
        # A: c0=15, c1=10; B: c0=10, c1=10, c2=5
        # p_e = (15*10 + 10*10 + 0) / 625 = 250 / 625 = 0.40 -> kappa = (0.80 - 0.40) / 0.60 = 2/3
        res_7 = compute_cohens_kappa(pairs_7, cats)
        self.assertAlmostEqual(res_7["p_o"], 0.80, places=12)
        self.assertAlmostEqual(res_7["p_e"], 0.40, places=12)
        self.assertAlmostEqual(res_7["cohens_kappa"], 2.0 / 3.0, places=12)

    def test_anti_spoofing_rejects_human_claim_and_same_reviewer_and_tampering(self):
        sem_a, sem_b, det_a, det_b = self._make_silver_packs()

        # 1. Spoofing reviewer_type == "human" in silver MUST fail closed!
        spoof_sem = copy.deepcopy(sem_a)
        spoof_sem["items"][0]["reviewer_type"] = "human"
        with self.assertRaisesRegex(ValueError, "Anti-spoofing fail-closed rejection"):
            compare_silver_packs(
                self.blank_sem, spoof_sem, sem_b, self.blank_det, det_a, det_b, self.sem_key, self.det_key
            )

        spoof_det = copy.deepcopy(det_b)
        spoof_det["items"][0]["reviewer_type"] = "human"
        with self.assertRaisesRegex(ValueError, "Anti-spoofing fail-closed rejection"):
            compare_silver_packs(
                self.blank_sem, sem_a, sem_b, self.blank_det, det_a, spoof_det, self.sem_key, self.det_key
            )

        # 2. Identical reviewer_id across A and B MUST fail closed!
        dup_b = copy.deepcopy(sem_b)
        for it in dup_b["items"]:
            it["reviewer_id"] = "blind_silver_annotator_A"
        with self.assertRaisesRegex(ValueError, "share reviewer_id"):
            compare_silver_packs(
                self.blank_sem, sem_a, dup_b, self.blank_det, det_a, det_b, self.sem_key, self.det_key
            )

        # 3. Tampered public payload MUST fail closed!
        tampered = copy.deepcopy(det_a)
        tampered["items"][0]["public_request"]["target"] = "tampered_target"
        with self.assertRaisesRegex(ValueError, "public_payload_sha256 mismatch"):
            compare_silver_packs(
                self.blank_sem, sem_a, sem_b, self.blank_det, tampered, det_b, self.sem_key, self.det_key
            )

    def test_compare_silver_and_finalize_g2_cli_roundtrip(self):
        sem_a, sem_b, det_a, det_b = self._make_silver_packs()

        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            p_blank_sem = tdir / "blank_sem.json"
            p_sem_a = tdir / "sem_a.json"
            p_sem_b = tdir / "sem_b.json"
            p_blank_det = tdir / "blank_det.json"
            p_det_a = tdir / "det_a.json"
            p_det_b = tdir / "det_b.json"
            p_sem_key = tdir / "sem_key.json"
            p_det_key = tdir / "det_key.json"

            for path, obj in (
                (p_blank_sem, self.blank_sem),
                (p_sem_a, sem_a),
                (p_sem_b, sem_b),
                (p_blank_det, self.blank_det),
                (p_det_a, det_a),
                (p_det_b, det_b),
            ):
                path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            sem_key_local = copy.deepcopy(self.sem_key)
            det_key_local = copy.deepcopy(self.det_key)
            sem_key_local["blank_pack_sha256"] = sha256_file(p_blank_sem)
            det_key_local["blank_pack_sha256"] = sha256_file(p_blank_det)
            p_sem_key.write_text(json.dumps(sem_key_local, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            p_det_key.write_text(json.dumps(det_key_local, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            comp_out = tdir / "comparison_out"
            subprocess.run(
                [
                    sys.executable,
                    str(WORKSPACE_ROOT / "scripts/adjudicate_g2_silver_and_human.py"),
                    "--mode",
                    "compare-silver",
                    "--blank-semantic",
                    str(p_blank_sem),
                    "--silver-sem-a",
                    str(p_sem_a),
                    "--silver-sem-b",
                    str(p_sem_b),
                    "--blank-detector",
                    str(p_blank_det),
                    "--silver-det-a",
                    str(p_det_a),
                    "--silver-det-b",
                    str(p_det_b),
                    "--semantic-key",
                    str(p_sem_key),
                    "--detector-key",
                    str(p_det_key),
                    "--output",
                    str(comp_out),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            dis_obj = json.loads((comp_out / "iter8_disagreements_for_human.json").read_text(encoding="utf-8"))
            dis_md = (comp_out / "iter8_disagreements_for_human.md").read_text(encoding="utf-8")
            self.assertEqual(dis_obj["semantic_disagreement_count"], 2)
            if self.is_full_358:
                # 18 v1!=v2 segments + item 0 disagreement (18 or 19 total)
                self.assertGreaterEqual(dis_obj["detector_disagreement_or_upgrade_count"], 18)
            for forbidden in ("B0", "B1", "B2", "EXP-", "comparison_arm"):
                self.assertFalse(_contains_forbidden(json.dumps(dis_obj, ensure_ascii=False), forbidden))
                self.assertFalse(_contains_forbidden(dis_md, forbidden))

            # Reject agent-named or incomplete human rulings
            bad_rulings = {
                "reviewer_type": HUMAN_TIEBREAKER_TYPE,
                "reviewer_id": "claude_agent",
                "approve_detector_v2_freeze": True,
                "semantic_rulings": {it["blind_id"]: it["annotator_a"]["label"] for it in dis_obj["semantic_disagreements"]},
                "detector_rulings": {it["blind_id"]: it["annotator_a"]["is_avoidable_invalid_loop"] for it in dis_obj["detector_disagreements"]},
            }
            p_bad = tdir / "bad_rulings.json"
            p_bad.write_text(json.dumps(bad_rulings), encoding="utf-8")
            proc_bad = subprocess.run(
                [
                    sys.executable,
                    str(WORKSPACE_ROOT / "scripts/adjudicate_g2_silver_and_human.py"),
                    "--mode",
                    "finalize-g2",
                    "--comparison-dir",
                    str(comp_out),
                    "--human-rulings",
                    str(p_bad),
                    "--semantic-key",
                    str(p_sem_key),
                    "--detector-key",
                    str(p_det_key),
                    "--output",
                    str(tdir / "bad_out"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc_bad.returncode, 0)

            # Valid human tie-breaker rulings
            good_rulings = copy.deepcopy(bad_rulings)
            good_rulings["reviewer_id"] = "derek_pi"
            p_good = tdir / "good_rulings.json"
            p_good.write_text(json.dumps(good_rulings, ensure_ascii=False, indent=2), encoding="utf-8")

            final_out = tdir / "final_g2"
            subprocess.run(
                [
                    sys.executable,
                    str(WORKSPACE_ROOT / "scripts/adjudicate_g2_silver_and_human.py"),
                    "--mode",
                    "finalize-g2",
                    "--comparison-dir",
                    str(comp_out),
                    "--human-rulings",
                    str(p_good),
                    "--semantic-key",
                    str(p_sem_key),
                    "--detector-key",
                    str(p_det_key),
                    "--output",
                    str(final_out),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            verdict = json.loads((final_out / "g2_frozen_verdict.json").read_text(encoding="utf-8"))
            self.assertEqual(verdict["dataset_governance_tier"], DATASET_GOVERNANCE_TIER)
            self.assertFalse(verdict["pure_manual_human_gold_claimed"])
            self.assertEqual(
                verdict["label_provenance_counts"]["semantic_unique_items"][TIEBREAKER_PROVENANCE],
                2,
            )
            self.assertGreater(
                verdict["label_provenance_counts"]["semantic_unique_items"][CONSENSUS_PROVENANCE],
                0,
            )
            if self.is_full_358:
                self.assertTrue(verdict["g2_passed"])
                self.assertEqual(verdict["frozen_detector_version"], DETECTOR_V2_VERSION)
                self.assertEqual(
                    verdict["g2_freeze_criteria"]["observed"]["real_model_segments"], 212
                )
                self.assertEqual(
                    verdict["g2_freeze_criteria"]["observed"]["detector_v2_legal_retry_and_poll_fpr"],
                    0.0,
                )


class TestBuildG3PreferenceDataset(unittest.TestCase):
    def _make_passing_verdict(self, path):
        verdict = {
            "schema_version": 1,
            "evidence_class": "analysis",
            "mode": "finalize-g2",
            "dataset_governance_tier": DATASET_GOVERNANCE_TIER,
            "pure_manual_human_gold_claimed": False,
            "g2_passed": True,
            "frozen_detector_version": DETECTOR_V2_VERSION,
        }
        path.write_text(json.dumps(verdict, indent=2) + "\n", encoding="utf-8")
        return sha256_file(path)

    def test_requires_passing_g2_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            vpath = tdir / "failed_verdict.json"
            vpath.write_text(
                json.dumps({"g2_passed": False, "frozen_detector_version": None}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "g2_passed != True"):
                build_g3_datasets(
                    {
                        "g2_verdict_path": str(vpath),
                        "tokenizer_mode": "stdlib_xml_mock_for_tests",
                    },
                    root_dir=ARCHIVE_ROOT,
                )

    def test_snapshot_validator_rejects_hidden_leak_and_valid_rejected(self):
        pool = _load_and_verify_isolated_pool(WORKSPACE_ROOT)
        rec_case = pool["by_group"]["iter7-orders-recoverable-f3"][0]
        env = Environment(rec_case)
        env.execute(tool_call("get_order", identifier=rec_case["request"]["identifier"]))
        snap = env.snapshot()

        # Hidden valid_identifier has NOT been disclosed yet -> must reject!
        hidden_id = rec_case["hidden"]["valid_identifier"]
        with self.assertRaisesRegex(ValueError, "non-public argument"):
            verify_decision_point_in_snapshot(
                rec_case,
                snap,
                format_qwen_tool_call("get_order", identifier=hidden_id),
                format_qwen_tool_call("get_order", identifier=rec_case["request"]["identifier"]),
            )

        # Passing a valid chosen call as `rejected_text` must fail closed!
        good_locate = format_qwen_tool_call("find_order", target=rec_case["request"]["target"])
        with self.assertRaisesRegex(ValueError, "unexpectedly passed locally_validated"):
            verify_decision_point_in_snapshot(rec_case, snap, good_locate, good_locate)

    def test_full_dataset_build_isolation_divergence_and_cloud_gate(self):
        tok_mode = (
            "transformers"
            if (HAS_TRANSFORMERS and QWEN_MODEL_DIR.is_dir())
            else "stdlib_xml_mock_for_tests"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tdir = Path(tmp)
            vpath = tdir / "g2_frozen_verdict.json"
            vsha = self._make_passing_verdict(vpath)
            cfg = {
                "g2_verdict_path": str(vpath),
                "g2_verdict_sha256": vsha,
                "tokenizer_mode": tok_mode,
                "model_path": str(QWEN_MODEL_DIR),
                "shuffle_seed": 20261119,
            }
            out_dir = tdir / "g3_out"
            built = build_g3_datasets(cfg, output_dir=out_dir, root_dir=ARCHIVE_ROOT)

            bundle = built["cloud_bundle"]
            ds = built["jsonl_datasets"]
            self.assertEqual(bundle["g3_dataset_ready"], tok_mode == "transformers")
            self.assertEqual(bundle["base_prompt_arm"], "B0")
            self.assertFalse(bundle["external_guard_enabled_for_P"])

            # Exact counts & group proportions (N=400: 180 / 80 / 60 / 80)
            p_rows = ds["p_toolloop_fix_dpo.jsonl"]
            b3_rows = ds["b3_error_sft.jsonl"]
            b4_rows = ds["b4_single_token_ftpo_style.jsonl"]
            b5_rows = ds["b5_general_expert_dpo.jsonl"]
            ab_no_retry = ds["p_ablation_no_legal_retry.jsonl"]
            ab_no_norm = ds["p_ablation_no_normal_retention.jsonl"]

            self.assertEqual(len(p_rows), 400)
            self.assertEqual(len(b3_rows), 400)
            self.assertEqual(len(b4_rows), 400)
            self.assertEqual(len(b5_rows), 400)
            self.assertEqual(len(ab_no_retry), 320)
            self.assertEqual(len(ab_no_norm), 320)
            self.assertEqual(
                bundle["group_counts"],
                {GROUP_1: 180, GROUP_2: 80, GROUP_3: 60, GROUP_4: 80},
            )

            # Verify exact alignment across P, B3, B4 and exact B0 prompt + explicit prefix
            same_tool_diff_arg_seen = False
            for pr, b3r, b4r in zip(p_rows, b3_rows, b4_rows):
                self.assertEqual(pr["example_id"], b3r["example_id"])
                self.assertEqual(pr["example_id"], b4r["example_id"])
                self.assertEqual(pr["messages"], b3r["messages"])
                self.assertEqual(pr["messages"], b4r["messages"])
                self.assertEqual(pr["chosen_text"], b3r["chosen_text"])
                self.assertEqual(pr["chosen_text"], b4r["chosen_text"])
                self.assertEqual(pr["rejected_text"], b4r["rejected_text"])
                self.assertEqual(pr["messages"][0]["content"], CALIBRATED_SYSTEM_PROMPT)
                self.assertTrue(pr["messages"][1]["content"].startswith(EXPLICIT_TASK_PREFIX))

                offset = b4r["first_divergence_offset"]
                self.assertEqual(offset, b4r["shared_prefix_token_count"])
                self.assertGreaterEqual(offset, 0)
                self.assertLess(offset, min(b4r["chosen_token_count"], b4r["rejected_token_count"]))
                self.assertNotEqual(
                    b4r["divergence_chosen_token_id"], b4r["divergence_rejected_token_id"]
                )

                if pr["chosen_text"].startswith("<tool_call>") and pr["rejected_text"].startswith(
                    "<tool_call>"
                ):
                    self.assertGreater(b4r["shared_prefix_token_count"], 0)
                    if tok_mode == "transformers":
                        self.assertGreaterEqual(b4r["shared_prefix_token_count"], 4)
                        self.assertNotEqual(b4r["divergence_chosen_token_id"], 248058)
                        self.assertNotEqual(b4r["divergence_rejected_token_id"], 248058)
                    if b4r["subtype"] == "after_invalid_identifier_locate_with_stale_id_as_target":
                        same_tool_diff_arg_seen = True
                        if tok_mode == "transformers":
                            self.assertGreaterEqual(b4r["shared_prefix_token_count"], 12)
            self.assertTrue(same_tool_diff_arg_seen)

            # Verify B5 contains zero error codes in tool histories
            for r in b5_rows:
                for m in r["messages"]:
                    if m["role"] == "tool":
                        code = json.loads(m["content"]).get("code")
                        self.assertNotIn(code, NONRETRYABLE_ERROR_CODES | {"TIMEOUT"})

            # Negative tests on verify_cloud_safe_artifacts
            pool_info = _load_and_verify_isolated_pool(ARCHIVE_ROOT)
            clean_bundle_text = (out_dir / "public_colab_training_bundle.json").read_text(
                encoding="utf-8"
            )
            for injected in (
                "ORD-CA1-4011",  # dev_InDomain_selection entity
                "poll_job",  # cross-domain jobs tool
                "gdev7-orders-normal-f2-01",  # case_id metadata
                "iter7-orders-normal-f2",  # template_group metadata
                "/Users/derek/secret",  # local path
                '"hidden": {}',  # hidden key
            ):
                with self.assertRaisesRegex(ValueError, "Cloud zero-leakage gate FAILED"):
                    verify_cloud_safe_artifacts(
                        {"tampered.json": clean_bundle_text + "\n" + injected},
                        pool_info,
                        ARCHIVE_ROOT,
                    )

    def test_stdlib_fallback_tokenizer_divergence(self):
        tok = StdlibXmlTestTokenizer()
        msgs = [
            {"role": "system", "content": CALIBRATED_SYSTEM_PROMPT},
            {"role": "user", "content": EXPLICIT_TASK_PREFIX + '{"domain":"orders","target":"t"}'},
        ]
        div = compute_token_divergence(
            tok,
            msgs,
            [],
            format_qwen_tool_call("find_order", target="lookup_a"),
            format_qwen_tool_call("find_order", target="lookup_b"),
        )
        self.assertGreater(div["shared_prefix_token_count"], 4)
        self.assertNotEqual(div["divergence_chosen_token_id"], div["divergence_rejected_token_id"])


if __name__ == "__main__":
    unittest.main()
