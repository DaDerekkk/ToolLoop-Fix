import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from scripts.build_g2_audit_packs import (
    ALLOWED_SEMANTIC_LABELS,
    DETECTOR_EXTRA_FORBIDDEN,
    FORBIDDEN_BLIND_SUBSTRINGS,
    MANDATORY_DETECTOR_STRATA,
    build_audit_packs,
    sha256_file,
    verify_zero_leakage,
)
from scripts.score_g2_human_audits import (
    compute_detector_confusion,
    main as score_cli_main,
    score_detector_audit,
    score_semantic_audit,
)

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]


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


def _default_archived_config(seed=20261119):
    return {
        "shuffle_seed": seed,
        "model_runs": [
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-016"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-017"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-018"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-025"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-026"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-027"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-028"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-029"),
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-030"),
        ],
        "cpu_first_step_runs": [
            str(ARCHIVE_ROOT / "experiments/EXP-20261119-043"),
        ],
        "synthetic_runs": [
            str(ARCHIVE_ROOT / "experiments/EXP-20261116-004"),
        ],
    }


class TestG2AuditPacks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.built = build_audit_packs(_default_archived_config(20261119), base_dir=ARCHIVE_ROOT)

    def test_archived_pack_counts_and_strata_coverage(self):
        summary = self.built["summary"]
        self.assertFalse(summary["human_review_completed"])
        self.assertFalse(summary["training_eligible"])
        self.assertTrue(summary["zero_leakage_check"]["passed"])

        sem_info = summary["semantic_pack"]
        self.assertEqual(sem_info["total_source_occurrences"], 93)
        self.assertEqual(sem_info["unique_blind_items"], 66)
        self.assertEqual(
            sem_info["occurrence_counts_by_comparison_arm"],
            {"B0": 39, "B1": 27, "B2": 27},
        )
        self.assertEqual(
            sem_info["occurrence_counts_by_inclusion_category"]["grounded_structured_positive_control"],
            9,
        )

        det_info = summary["detector_pack"]
        self.assertGreaterEqual(det_info["unique_blind_segments"], 220)
        for stratum in MANDATORY_DETECTOR_STRATA:
            self.assertIn(stratum, det_info["counts_by_stratum"])
            self.assertGreaterEqual(det_info["counts_by_stratum"][stratum], 15)
        self.assertEqual(det_info["counts_by_source_evidence_class"]["model_inference"], 49)

    def test_zero_leakage_on_all_blind_outputs(self):
        sem_text = json.dumps(self.built["semantic_pack"], ensure_ascii=False)
        ws_text = self.built["semantic_worksheet"]
        det_text = json.dumps(self.built["detector_pack"], ensure_ascii=False)

        for forbidden in FORBIDDEN_BLIND_SUBSTRINGS:
            self.assertNotIn(forbidden, sem_text)
            self.assertNotIn(forbidden, ws_text)
            self.assertNotIn(forbidden, det_text)

        for det_forbidden in DETECTOR_EXTRA_FORBIDDEN:
            self.assertNotIn(det_forbidden, det_text)

        guard_flagged_in_key = sum(
            1
            for entry in self.built["semantic_key"]["items"].values()
            for occ in entry["occurrences"]
            if occ["guard_notice_occurred"]
        )
        self.assertGreater(guard_flagged_in_key, 0)

        bad_sem = copy.deepcopy(self.built["semantic_pack"])
        bad_sem["items"][0]["notes"] = "leaked B2 metadata"
        with self.assertRaises(ValueError):
            verify_zero_leakage(
                bad_sem,
                self.built["semantic_key"],
                self.built["semantic_worksheet"],
                self.built["detector_pack"],
                self.built["detector_key"],
            )

        bad_oracle_det = copy.deepcopy(self.built["detector_pack"])
        injected = False
        for item in bad_oracle_det["items"]:
            k_entry = self.built["detector_key"]["items"][item["blind_id"]]
            ovals = k_entry["oracle_values_for_leakage_audit"]
            vis = list(item["history_prefix"]) + [item["focal_step_k"]]
            disclosed = {e["response"].get("value") for e in vis if "value" in e["response"]}
            if ovals and ovals[0] not in disclosed:
                item["focal_step_k"]["response"]["leaked_hint"] = ovals[0]
                injected = True
                break
        self.assertTrue(injected)
        with self.assertRaises(ValueError):
            verify_zero_leakage(
                self.built["semantic_pack"],
                self.built["semantic_key"],
                self.built["semantic_worksheet"],
                bad_oracle_det,
                self.built["detector_key"],
            )

    def test_cli_determinism_and_without_synthetic_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_path = tmp_path / "cfg.json"
            cfg_path.write_text(json.dumps(_default_archived_config(20261119)), encoding="utf-8")

            out1 = tmp_path / "out1"
            out2 = tmp_path / "out2"
            for out_dir in (out1, out2):
                subprocess.run(
                    [
                        sys.executable,
                        str(WORKSPACE_ROOT / "scripts/build_g2_audit_packs.py"),
                        "--config",
                        str(cfg_path),
                        "--output",
                        str(out_dir),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            expected_files = (
                "blind_semantic_review_pack.json",
                "blind_semantic_worksheet.md",
                "blind_semantic_key.json",
                "blind_detector_audit_pack.json",
                "blind_detector_key.json",
                "g2_pack_summary.json",
            )
            for fname in expected_files:
                self.assertEqual((out1 / fname).read_bytes(), (out2 / fname).read_bytes())

            summary = json.loads((out1 / "g2_pack_summary.json").read_text(encoding="utf-8"))
            sem_key = json.loads((out1 / "blind_semantic_key.json").read_text(encoding="utf-8"))
            det_key = json.loads((out1 / "blind_detector_key.json").read_text(encoding="utf-8"))
            self.assertEqual(
                sha256_file(out1 / "blind_semantic_review_pack.json"),
                sem_key["blind_semantic_review_pack_sha256"],
            )
            self.assertEqual(
                sha256_file(out1 / "blind_detector_audit_pack.json"),
                det_key["blind_detector_audit_pack_sha256"],
            )
            for fname, digest in summary["artifacts"].items():
                self.assertEqual(sha256_file(out1 / fname), digest)

            # Passing unannotated blank pack directly to score CLI must fail closed
            proc = subprocess.run(
                [
                    sys.executable,
                    str(WORKSPACE_ROOT / "scripts/score_g2_human_audits.py"),
                    "--mode",
                    "semantic",
                    "--annotated",
                    str(out1 / "blind_semantic_review_pack.json"),
                    "--key",
                    str(out1 / "blind_semantic_key.json"),
                    "--output",
                    str(tmp_path / "rejected_out"),
                ],
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Fail-closed rejection", proc.stderr)

            built_alt = build_audit_packs(_default_archived_config(20261120), base_dir=ARCHIVE_ROOT)
            self.assertNotEqual(
                [it["terminal_response"]["raw_text"] for it in built_alt["semantic_pack"]["items"]],
                [it["terminal_response"]["raw_text"] for it in self.built["semantic_pack"]["items"]],
            )

            cfg_no_syn = _default_archived_config(20261119)
            cfg_no_syn["synthetic_runs"] = []
            built_no_syn = build_audit_packs(cfg_no_syn, base_dir=ARCHIVE_ROOT)
            self.assertGreaterEqual(
                built_no_syn["summary"]["detector_pack"]["unique_blind_segments"], 220
            )
            for st in MANDATORY_DETECTOR_STRATA:
                self.assertGreaterEqual(
                    built_no_syn["summary"]["detector_pack"]["counts_by_stratum"][st], 15
                )

    def test_source_run_tampering_rejected_across_all_run_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            # 1. Model run: tampered prefix_contexts.jsonl
            tmp_model = tpath / "EXP-025-copy"
            shutil.copytree(
                ARCHIVE_ROOT / "experiments/EXP-20261116-025",
                tmp_model,
                ignore=shutil.ignore_patterns("inputs"),
            )
            with (tmp_model / "prefix_contexts.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                build_audit_packs(
                    {"shuffle_seed": 1, "model_runs": [str(tmp_model)]},
                    base_dir=ARCHIVE_ROOT,
                )

            # 2. Model run: evidence_valid == False
            shutil.rmtree(tmp_model)
            shutil.copytree(
                ARCHIVE_ROOT / "experiments/EXP-20261116-025",
                tmp_model,
                ignore=shutil.ignore_patterns("inputs"),
            )
            manifest = json.loads((tmp_model / "manifest.json").read_text(encoding="utf-8"))
            manifest["evidence_valid"] = False
            (tmp_model / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "evidence_valid"):
                build_audit_packs(
                    {"shuffle_seed": 1, "model_runs": [str(tmp_model)]},
                    base_dir=ARCHIVE_ROOT,
                )

            # 3. CPU first-step run: tampered public_payloads.jsonl
            tmp_cpu = tpath / "EXP-043-copy"
            src_cpu = ARCHIVE_ROOT / "experiments/EXP-20261119-043"
            shutil.copytree(
                src_cpu,
                tmp_cpu,
                ignore=lambda directory, names: (
                    ["inputs"] if Path(directory) == src_cpu and "inputs" in names else []
                ),
            )
            with (tmp_cpu / "operation_sources/public_payloads.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                build_audit_packs(
                    {
                        "shuffle_seed": 1,
                        "model_runs": [str(ARCHIVE_ROOT / "experiments/EXP-20261116-016")],
                        "cpu_first_step_runs": [str(tmp_cpu)],
                    },
                    base_dir=ARCHIVE_ROOT,
                )

            # 4. Synthetic run: tampered trajectories.jsonl
            tmp_syn = tpath / "EXP-004-copy"
            shutil.copytree(
                ARCHIVE_ROOT / "experiments/EXP-20261116-004",
                tmp_syn,
                ignore=shutil.ignore_patterns("inputs"),
            )
            with (tmp_syn / "trajectories.jsonl").open("a", encoding="utf-8") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                build_audit_packs(
                    {
                        "shuffle_seed": 1,
                        "model_runs": [str(ARCHIVE_ROOT / "experiments/EXP-20261116-016")],
                        "synthetic_runs": [str(tmp_syn)],
                    },
                    base_dir=ARCHIVE_ROOT,
                )

    def test_fail_closed_reviewer_and_tamper_gates(self):
        det_pack = copy.deepcopy(self.built["detector_pack"])
        det_key = self.built["detector_key"]

        with self.assertRaisesRegex(ValueError, "reviewer_type must be 'human'"):
            score_detector_audit(det_pack, det_key)

        for item in det_pack["items"]:
            item["reviewer_type"] = "human"
            item["reviewer_id"] = "annotator_alice_01"
            item["human_is_avoidable_invalid_loop"] = bool(
                det_key["items"][item["blind_id"]]["detector_flagged_bool"]
            )

        forbidden_ids = (
            "",
            "   ",
            "agent",
            "checker",
            "checker6",
            "protocol",
            "source_audit",
            "root",
            "llm_judge",
            "auto_annotator",
            "claude-3-7",
            "gpt-4o",
            "gemini-2.5",
            "qwen3.5",
            "auditpack7",
            "runner7",
        )
        for bad_id in forbidden_ids:
            mutated = copy.deepcopy(det_pack)
            mutated["items"][0]["reviewer_id"] = bad_id
            with self.assertRaisesRegex(ValueError, "Fail-closed rejection"):
                score_detector_audit(mutated, det_key)

        for bad_type in (None, "agent", "Human", "auto"):
            mutated = copy.deepcopy(det_pack)
            mutated["items"][0]["reviewer_type"] = bad_type
            with self.assertRaisesRegex(ValueError, "reviewer_type must be 'human'"):
                score_detector_audit(mutated, det_key)

        for bad_label in (None, 1, 0, "true", "false"):
            mutated = copy.deepcopy(det_pack)
            mutated["items"][0]["human_is_avoidable_invalid_loop"] = bad_label
            with self.assertRaisesRegex(ValueError, "must be boolean"):
                score_detector_audit(mutated, det_key)

        tampered = copy.deepcopy(det_pack)
        tampered["items"][0]["public_request"]["target"] = "tampered target"
        with self.assertRaisesRegex(ValueError, "public payload hash tampered"):
            score_detector_audit(tampered, det_key)

        dropped = copy.deepcopy(det_pack)
        dropped["items"].pop()
        with self.assertRaisesRegex(ValueError, "blind_id set mismatch"):
            score_detector_audit(dropped, det_key)

    def test_zero_denominator_null_and_detector_g2_thresholds(self):
        empty_cm = compute_detector_confusion([])
        self.assertIsNone(empty_cm["precision"]["value"])
        self.assertIsNone(empty_cm["recall"]["value"])
        self.assertIsNone(empty_cm["fpr"]["value"])
        self.assertIsNone(empty_cm["fnr"]["value"])

        only_tn = compute_detector_confusion([(False, False), (False, False)])
        self.assertIsNone(only_tn["precision"]["value"])
        self.assertIsNone(only_tn["recall"]["value"])
        self.assertIsNone(only_tn["fnr"]["value"])
        self.assertEqual(only_tn["fpr"]["value"], 0.0)

        only_tp = compute_detector_confusion([(True, True)])
        self.assertEqual(only_tp["precision"]["value"], 1.0)
        self.assertEqual(only_tp["recall"]["value"], 1.0)
        self.assertIsNone(only_tp["fpr"]["value"])
        self.assertEqual(only_tp["fnr"]["value"], 0.0)

        det_pack = copy.deepcopy(self.built["detector_pack"])
        det_key = self.built["detector_key"]
        for item in det_pack["items"]:
            item["reviewer_type"] = "human"
            item["reviewer_id"] = "annotator_bob_02"
            item["human_is_avoidable_invalid_loop"] = bool(
                det_key["items"][item["blind_id"]]["detector_flagged_bool"]
            )

        perfect = score_detector_audit(det_pack, det_key)
        self.assertTrue(perfect["g2_gate"]["passed"])
        self.assertFalse(perfect["training_eligible"])
        self.assertEqual(perfect["legal_retry_and_poll_fpr"]["value"], 0.0)
        self.assertEqual(perfect["overall"]["precision"]["value"], 1.0)
        self.assertEqual(perfect["overall"]["recall"]["value"], 1.0)
        self.assertEqual(perfect["by_source_evidence_class"]["model_inference"]["total"], 49)
        self.assertGreater(
            perfect["by_source_evidence_class"]["all_synthetic_combined"]["total"], 150
        )
        self.assertIsNone(
            perfect["by_stratum"]["unflagged_legal_timeout_retry"]["precision"]["value"]
        )

        # Sub-200 sample size must fail G2 gate even with 100% precision/recall and 0% legal FPR
        small_pack = copy.deepcopy(det_pack)
        small_key = copy.deepcopy(det_key)
        keep_ids = []
        seen_strata = set()
        for item in small_pack["items"]:
            st = small_key["items"][item["blind_id"]]["stratum"]
            if st not in seen_strata:
                seen_strata.add(st)
                keep_ids.append(item["blind_id"])
        small_pack["items"] = [it for it in small_pack["items"] if it["blind_id"] in set(keep_ids)]
        small_key["items"] = {k: v for k, v in small_key["items"].items() if k in set(keep_ids)}
        res_small = score_detector_audit(small_pack, small_key)
        self.assertEqual(res_small["overall"]["total"], 6)
        self.assertFalse(res_small["g2_gate"]["checks"]["total_reviewed_gte_200"])
        self.assertFalse(res_small["g2_gate"]["passed"])

        # Inject 10 FPs -> precision < 0.95 -> G2 gate fails
        low_prec = copy.deepcopy(det_pack)
        flipped_fp = 0
        for item in low_prec["items"]:
            entry = det_key["items"][item["blind_id"]]
            if entry["detector_flagged_bool"] and flipped_fp < 10:
                item["human_is_avoidable_invalid_loop"] = False
                flipped_fp += 1
        res_low_prec = score_detector_audit(low_prec, det_key)
        self.assertEqual(res_low_prec["overall"]["FP"], 10)
        self.assertLess(res_low_prec["overall"]["precision"]["value"], 0.95)
        self.assertFalse(res_low_prec["g2_gate"]["passed"])

        # Inject 20 FNs -> recall < 0.90 -> G2 gate fails
        low_rec = copy.deepcopy(det_pack)
        flipped_fn = 0
        for item in low_rec["items"]:
            entry = det_key["items"][item["blind_id"]]
            if (
                entry["stratum"] == "unflagged_genuine_progress_or_first_error"
                and not entry["detector_flagged_bool"]
                and flipped_fn < 20
            ):
                item["human_is_avoidable_invalid_loop"] = True
                flipped_fn += 1
        res_low_rec = score_detector_audit(low_rec, det_key)
        self.assertEqual(res_low_rec["overall"]["FN"], 20)
        self.assertLess(res_low_rec["overall"]["recall"]["value"], 0.90)
        self.assertFalse(res_low_rec["g2_gate"]["passed"])

        # Simulate 1 FP in legal retry/poll strata -> legal_retry_and_poll_fpr > 0 -> G2 gate fails
        legal_fp_key = copy.deepcopy(det_key)
        legal_fp_pack = copy.deepcopy(det_pack)
        for item in legal_fp_pack["items"]:
            entry = legal_fp_key["items"][item["blind_id"]]
            if entry["stratum"] == "unflagged_legal_timeout_retry":
                entry["detector_flagged_bool"] = True
                item["human_is_avoidable_invalid_loop"] = False
                break
        res_legal_fp = score_detector_audit(legal_fp_pack, legal_fp_key)
        self.assertGreater(res_legal_fp["legal_retry_and_poll_fpr"]["value"], 0.0)
        self.assertFalse(res_legal_fp["g2_gate"]["passed"])

    def test_semantic_unblinding_positive_controls_and_cli_roundtrip(self):
        sem_pack = copy.deepcopy(self.built["semantic_pack"])
        sem_key = self.built["semantic_key"]
        det_pack = copy.deepcopy(self.built["detector_pack"])
        det_key = self.built["detector_key"]

        bad_sem = copy.deepcopy(sem_pack)
        for it in bad_sem["items"]:
            it["reviewer_type"] = "human"
            it["reviewer_id"] = "annotator_carol_03"
            it["factual_grounding_label"] = "grounded_correct_delivery"
        bad_sem["items"][0]["factual_grounding_label"] = "not_in_enum"
        with self.assertRaisesRegex(ValueError, "invalid or null factual_grounding_label"):
            score_semantic_audit(bad_sem, sem_key)

        non_control_labels = [
            lab for lab in ALLOWED_SEMANTIC_LABELS if lab != "grounded_correct_delivery"
        ]
        for idx, it in enumerate(sem_pack["items"]):
            it["reviewer_type"] = "human"
            it["reviewer_id"] = "annotator_carol_03"
            inc = sem_key["items"][it["blind_id"]]["inclusion_category"]
            if inc == "grounded_structured_positive_control":
                it["factual_grounding_label"] = "grounded_correct_delivery"
            else:
                it["factual_grounding_label"] = non_control_labels[idx % len(non_control_labels)]

        sem_only = score_semantic_audit(sem_pack, sem_key)
        self.assertFalse(sem_only["g2_gate"]["passed"])
        self.assertFalse(sem_only["training_eligible"])
        self.assertEqual(sem_only["overall_occurrences"]["denominator"], 93)
        self.assertEqual(sem_only["by_comparison_arm"]["B0"]["denominator"], 39)
        self.assertEqual(sem_only["by_comparison_arm"]["B1"]["denominator"], 27)
        self.assertEqual(sem_only["by_comparison_arm"]["B2"]["denominator"], 27)
        self.assertEqual(sem_only["by_source_cohort"]["cpu_first_step"]["denominator"], 12)

        for it in det_pack["items"]:
            it["reviewer_type"] = "human"
            it["reviewer_id"] = "annotator_carol_03"
            it["human_is_avoidable_invalid_loop"] = bool(
                det_key["items"][it["blind_id"]]["detector_flagged_bool"]
            )

        # Positive control mismatch must keep training_eligible False even with passing detector score
        det_res = score_detector_audit(det_pack, det_key)
        sem_bad_ctrl = copy.deepcopy(sem_pack)
        for it in sem_bad_ctrl["items"]:
            if sem_key["items"][it["blind_id"]]["inclusion_category"] == "grounded_structured_positive_control":
                it["factual_grounding_label"] = "unsupported_success_claim"
                break
        res_bad_ctrl = score_semantic_audit(sem_bad_ctrl, sem_key, detector_score=det_res)
        self.assertFalse(res_bad_ctrl["positive_control_audit"]["consistent"])
        self.assertFalse(res_bad_ctrl["g2_gate"]["passed"])
        self.assertFalse(res_bad_ctrl["training_eligible"])

        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            ann_det_path = tpath / "ann_det.json"
            key_det_path = tpath / "key_det.json"
            ann_sem_path = tpath / "ann_sem.json"
            key_sem_path = tpath / "key_sem.json"
            ann_det_path.write_text(json.dumps(det_pack), encoding="utf-8")
            key_det_path.write_text(json.dumps(det_key), encoding="utf-8")
            ann_sem_path.write_text(json.dumps(sem_pack), encoding="utf-8")
            key_sem_path.write_text(json.dumps(sem_key), encoding="utf-8")

            det_out_dir = tpath / "det_score"
            with contextlib.redirect_stdout(io.StringIO()):
                score_cli_main(
                    [
                        "--mode",
                        "detector",
                        "--annotated",
                        str(ann_det_path),
                        "--key",
                        str(key_det_path),
                        "--output",
                        str(det_out_dir),
                    ]
                )
            det_score_file = det_out_dir / "g2_detector_score.json"
            self.assertTrue(det_score_file.is_file())
            self.assertTrue((det_out_dir / "g2_detector_score.md").is_file())

            sem_out_dir = tpath / "sem_score"
            with contextlib.redirect_stdout(io.StringIO()):
                score_cli_main(
                    [
                        "--mode",
                        "semantic",
                        "--annotated",
                        str(ann_sem_path),
                        "--key",
                        str(key_sem_path),
                        "--detector-score",
                        str(det_score_file),
                        "--output",
                        str(sem_out_dir),
                    ]
                )
            sem_scored = json.loads(
                (sem_out_dir / "g2_semantic_score.json").read_text(encoding="utf-8")
            )
            self.assertTrue(sem_scored["g2_gate"]["passed"])
            self.assertTrue(sem_scored["training_eligible"])
            self.assertFalse(sem_scored["training_execution_authorized"])

    def test_arm_token_boundaries_and_iter7_full_config(self):
        # 1. Neutral public identifiers containing CB1 / CB2 must NOT false-trigger arm leakage
        for safe_id in ("ORD-CB1-6061", "ORDWAYBILL_CB2_V1"):
            safe_sem = copy.deepcopy(self.built["semantic_pack"])
            safe_det = copy.deepcopy(self.built["detector_pack"])
            safe_sem["items"][0]["public_user_request"]["user_message"] += f" [{safe_id}]"
            safe_det["items"][0]["public_request"]["identifier"] = safe_id
            ws_safe = self.built["semantic_worksheet"] + f"\nRef: {safe_id}\n"
            report = verify_zero_leakage(
                safe_sem,
                self.built["semantic_key"],
                ws_safe,
                safe_det,
                self.built["detector_key"],
            )
            self.assertTrue(report["passed"])

        # 2. Standalone or underscore-delimited arm tokens MUST still fail closed
        for leaked_arm in ("arm B1", "explicit_B2", "B0"):
            leaked_sem = copy.deepcopy(self.built["semantic_pack"])
            leaked_sem["items"][0]["terminal_response"]["raw_text"] += f" ({leaked_arm})"
            with self.assertRaisesRegex(ValueError, "Zero-leakage check failed"):
                verify_zero_leakage(
                    leaked_sem,
                    self.built["semantic_key"],
                    self.built["semantic_worksheet"],
                    self.built["detector_pack"],
                    self.built["detector_key"],
                )

            leaked_det = copy.deepcopy(self.built["detector_pack"])
            leaked_det["items"][0]["public_request"]["target"] = f"leak {leaked_arm}"
            with self.assertRaisesRegex(ValueError, "Zero-leakage check failed"):
                verify_zero_leakage(
                    self.built["semantic_pack"],
                    self.built["semantic_key"],
                    self.built["semantic_worksheet"],
                    leaked_det,
                    self.built["detector_key"],
                )

        # 3. Full ITER-007 config (including EXP-047 and EXP-049..056 with CB1/CB2 IDs) succeeds cleanly
        cfg_path = WORKSPACE_ROOT / "configs/iter7_g2_audit_packs.json"
        if not cfg_path.is_file():
            cfg_path = ARCHIVE_ROOT / "configs/iter7_g2_audit_packs.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "iter7_packs"
            built_full = build_audit_packs(cfg, output_dir=out_dir, base_dir=ARCHIVE_ROOT)
            summary = built_full["summary"]
            self.assertTrue(summary["zero_leakage_check"]["passed"])
            self.assertFalse(summary["human_review_completed"])
            self.assertFalse(summary["training_eligible"])
            self.assertGreaterEqual(summary["detector_pack"]["unique_blind_segments"], 220)
            for st in MANDATORY_DETECTOR_STRATA:
                self.assertGreaterEqual(summary["detector_pack"]["counts_by_stratum"][st], 15)
            for fname, digest in summary["artifacts"].items():
                self.assertEqual(sha256_file(out_dir / fname), digest)


if __name__ == "__main__":
    unittest.main()
