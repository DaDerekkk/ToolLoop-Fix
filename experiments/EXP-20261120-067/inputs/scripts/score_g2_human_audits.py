#!/usr/bin/env python3
"""Fail-closed verifier, unblinder, and G2 gate scorer for human-annotated audit packs.

Stdlib only; rejects non-human, empty, agent-impersonated, incomplete, or tampered packs.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from scripts.build_g2_audit_packs import (
    ALLOWED_SEMANTIC_LABELS,
    DETECTOR_KEY_TYPE,
    DETECTOR_PACK_TYPE,
    MANDATORY_DETECTOR_STRATA,
    SCHEMA_VERSION,
    SEMANTIC_KEY_TYPE,
    SEMANTIC_PACK_TYPE,
    detector_public_payload,
    object_sha256,
    semantic_public_payload,
)

FORBIDDEN_REVIEWER_SUBSTRINGS = (
    "agent",
    "checker",
    "protocol",
    "source_audit",
    "source-audit",
    "root",
    "llm",
    "auto",
    "claude",
    "gpt",
    "gemini",
    "qwen",
    "auditpack",
    "simulator",
    "auditor",
    "antigravity",
    "openai",
    "anthropic",
    "deepseek",
    "mistral",
    "llama",
    "copilot",
)

FORBIDDEN_REVIEWER_TOKENS = frozenset(
    {
        "agent",
        "checker",
        "protocol",
        "root",
        "llm",
        "auto",
        "claude",
        "gpt",
        "gemini",
        "qwen",
        "runner",
        "dataset",
        "research",
        "runtime",
        "probe",
        "transport",
        "auditpack",
        "auditor",
        "simulator",
        "coordinator",
        "bot",
        "ai",
        "model",
        "script",
        "synthetic",
        "system",
    }
)

UNSUPPORTED_CLAIM_LABELS = frozenset(
    {
        "error_code_as_record_value",
        "unobserved_execution_claim",
        "unsupported_success_claim",
        "unsupported_absence_or_cause_claim",
    }
)

GROUNDED_OR_TRUTHFUL_LABELS = frozenset(
    {
        "grounded_correct_delivery",
        "truthful_inability_or_timeout_stop",
    }
)

LEGAL_RETRY_AND_POLL_STRATA = frozenset(
    {
        "unflagged_legal_timeout_retry",
        "unflagged_legal_job_polling",
    }
)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rate(numerator, denominator):
    """Exact numerator/denominator/value; zero denominator yields None (null / N/A)."""
    return {
        "numerator": int(numerator),
        "denominator": int(denominator),
        "value": (numerator / denominator) if denominator > 0 else None,
    }


def validate_human_reviewer(item):
    """Hard fail-closed check: only genuine human reviewer IDs are accepted."""
    bid = item.get("blind_id", "<unknown>")
    rtype = item.get("reviewer_type")
    if type(rtype) is not str or rtype != "human":
        raise ValueError(
            f"Fail-closed rejection on {bid}: reviewer_type must be 'human', got {rtype!r}"
        )
    rid = item.get("reviewer_id")
    if type(rid) is not str or not rid.strip():
        raise ValueError(
            f"Fail-closed rejection on {bid}: reviewer_id must be a non-empty string"
        )
    lowered = rid.strip().lower()
    for sub in FORBIDDEN_REVIEWER_SUBSTRINGS:
        if sub in lowered:
            raise ValueError(
                f"Fail-closed rejection on {bid}: reviewer_id {rid!r} matches forbidden agent/model pattern {sub!r}"
            )
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", lowered) if tok]
    if not tokens:
        raise ValueError(
            f"Fail-closed rejection on {bid}: reviewer_id {rid!r} contains no alphanumeric identifier"
        )
    for tok in tokens:
        base_tok = re.sub(r"\d+$", "", tok)
        if tok in FORBIDDEN_REVIEWER_TOKENS or (base_tok and base_tok in FORBIDDEN_REVIEWER_TOKENS):
            raise ValueError(
                f"Fail-closed rejection on {bid}: reviewer_id {rid!r} matches forbidden role token {tok!r}"
            )
    notes = item.get("notes")
    if type(notes) is not str:
        raise ValueError(f"Fail-closed rejection on {bid}: notes must be a string")
    return rid.strip()


def _verify_common_pack_and_key(annotated, key, expected_pack_type, expected_key_type, expected_item_keys):
    if not isinstance(annotated, dict) or not isinstance(key, dict):
        raise ValueError("annotated pack and key must be JSON objects")
    if annotated.get("pack_type") != expected_pack_type:
        raise ValueError(
            f"pack_type mismatch: expected {expected_pack_type}, got {annotated.get('pack_type')!r}"
        )
    if key.get("key_type") != expected_key_type:
        raise ValueError(
            f"key_type mismatch: expected {expected_key_type}, got {key.get('key_type')!r}"
        )
    if annotated.get("pack_id") != key.get("pack_id"):
        raise ValueError(
            f"pack_id mismatch between annotated pack ({annotated.get('pack_id')!r}) and sealed key ({key.get('pack_id')!r})"
        )
    items = annotated.get("items")
    key_items = key.get("items")
    if not isinstance(items, list) or not items or not isinstance(key_items, dict) or not key_items:
        raise ValueError("annotated pack items and sealed key items must be non-empty")
    seen_ids = set()
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("each blind item must be a JSON object")
        if set(item.keys()) != expected_item_keys:
            raise ValueError(
                f"tampered or unexpected item keys on {item.get('blind_id')!r}: {sorted(item.keys())}"
            )
        bid = item["blind_id"]
        if bid in seen_ids:
            raise ValueError(f"duplicate blind_id in annotated pack: {bid}")
        seen_ids.add(bid)
    if seen_ids != set(key_items.keys()):
        missing = sorted(set(key_items.keys()) - seen_ids)
        extra = sorted(seen_ids - set(key_items.keys()))
        raise ValueError(
            f"blind_id set mismatch against sealed key (missing={missing[:5]}, extra={extra[:5]})"
        )


def compute_detector_confusion(pairs):
    """Compute TP/FP/FN/TN and nullable precision/recall/FPR/FNR from (pred_bool, human_bool) pairs."""
    tp = sum(1 for pred, gold in pairs if pred and gold)
    fp = sum(1 for pred, gold in pairs if pred and (not gold))
    fn = sum(1 for pred, gold in pairs if (not pred) and gold)
    tn = sum(1 for pred, gold in pairs if (not pred) and (not gold))
    return {
        "total": len(pairs),
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "TN": tn,
        "precision": rate(tp, tp + fp),
        "recall": rate(tp, tp + fn),
        "fpr": rate(fp, fp + tn),
        "fnr": rate(fn, fn + tp),
    }


def score_detector_audit(annotated, key):
    expected_keys = {
        "blind_id",
        "public_request",
        "public_tool_docs",
        "history_prefix",
        "focal_step_k",
        "reviewer_type",
        "reviewer_id",
        "human_is_avoidable_invalid_loop",
        "notes",
    }
    _verify_common_pack_and_key(
        annotated, key, DETECTOR_PACK_TYPE, DETECTOR_KEY_TYPE, expected_keys
    )

    reviewer_ids = set()
    records = []
    for item in annotated["items"]:
        bid = item["blind_id"]
        key_entry = key["items"][bid]
        actual_pub_sha = object_sha256(detector_public_payload(item))
        if actual_pub_sha != key_entry["public_payload_sha256"]:
            raise ValueError(f"public payload hash tampered for {bid}")

        rid = validate_human_reviewer(item)
        reviewer_ids.add(rid)
        label = item.get("human_is_avoidable_invalid_loop")
        if type(label) is not bool:
            raise ValueError(
                f"Fail-closed rejection on {bid}: human_is_avoidable_invalid_loop must be boolean true/false, got {label!r}"
            )
        pred = bool(key_entry["detector_flagged_bool"])
        records.append(
            {
                "blind_id": bid,
                "reviewer_id": rid,
                "stratum": key_entry["stratum"],
                "source_evidence_class": key_entry["source_evidence_class"],
                "detector_flagged_bool": pred,
                "detector_flags_at_k": key_entry["detector_flags_at_k"],
                "human_is_avoidable_invalid_loop": label,
                "notes": item["notes"],
                "outcome": (
                    "TP"
                    if (pred and label)
                    else "FP"
                    if (pred and not label)
                    else "FN"
                    if (not pred and label)
                    else "TN"
                ),
                "provenance": key_entry["provenance"],
            }
        )

    overall_cm = compute_detector_confusion(
        [(r["detector_flagged_bool"], r["human_is_avoidable_invalid_loop"]) for r in records]
    )

    by_stratum = {}
    for stratum in MANDATORY_DETECTOR_STRATA:
        sub = [
            (r["detector_flagged_bool"], r["human_is_avoidable_invalid_loop"])
            for r in records
            if r["stratum"] == stratum
        ]
        by_stratum[stratum] = compute_detector_confusion(sub)

    for extra_st in sorted({r["stratum"] for r in records} - set(MANDATORY_DETECTOR_STRATA)):
        sub = [
            (r["detector_flagged_bool"], r["human_is_avoidable_invalid_loop"])
            for r in records
            if r["stratum"] == extra_st
        ]
        by_stratum[extra_st] = compute_detector_confusion(sub)

    legal_sub = [
        (r["detector_flagged_bool"], r["human_is_avoidable_invalid_loop"])
        for r in records
        if r["stratum"] in LEGAL_RETRY_AND_POLL_STRATA
    ]
    legal_cm = compute_detector_confusion(legal_sub)
    legal_retry_and_poll_fpr = legal_cm["fpr"]

    by_evidence_class = {}
    for ev_cls in ("model_inference", "synthetic_harness", "synthetic_harness_boundary"):
        sub = [
            (r["detector_flagged_bool"], r["human_is_avoidable_invalid_loop"])
            for r in records
            if r["source_evidence_class"] == ev_cls
        ]
        by_evidence_class[ev_cls] = compute_detector_confusion(sub)

    synthetic_combined_sub = [
        (r["detector_flagged_bool"], r["human_is_avoidable_invalid_loop"])
        for r in records
        if r["source_evidence_class"] in {"synthetic_harness", "synthetic_harness_boundary"}
    ]
    by_evidence_class["all_synthetic_combined"] = compute_detector_confusion(
        synthetic_combined_sub
    )

    total_reviewed = overall_cm["total"]
    all_six_strata_covered = all(
        by_stratum[s]["total"] > 0 for s in MANDATORY_DETECTOR_STRATA
    )
    legal_fpr_val = legal_retry_and_poll_fpr["value"]
    prec_val = overall_cm["precision"]["value"]
    rec_val = overall_cm["recall"]["value"]

    checks = {
        "total_reviewed_gte_200": total_reviewed >= 200,
        "all_six_mandatory_strata_covered": all_six_strata_covered,
        "legal_retry_and_poll_fpr_eq_0": (legal_fpr_val is not None and legal_fpr_val == 0.0),
        "precision_gte_0_95": (prec_val is not None and prec_val >= 0.95),
        "recall_gte_0_90": (rec_val is not None and rec_val >= 0.90),
    }
    gate_passed = all(checks.values())

    disagreements = [r for r in records if r["outcome"] in {"FP", "FN"}]

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": "analysis",
        "mode": "detector",
        "pack_id": annotated["pack_id"],
        "human_review_verified": True,
        "human_reviewer_ids": sorted(reviewer_ids),
        "sampling_warning": (
            "Stratified non-uniform audit sample across 6 mandatory strata; "
            "overall precision/recall/FPR/FNR measure detector classification on the audit pack "
            "and MUST NOT be reported as natural population loop rates."
        ),
        "overall": overall_cm,
        "legal_retry_and_poll_fpr": legal_retry_and_poll_fpr,
        "legal_retry_and_poll_confusion": legal_cm,
        "by_stratum": by_stratum,
        "by_source_evidence_class": by_evidence_class,
        "g2_gate": {
            "thresholds": {
                "min_total_reviewed": 200,
                "require_all_six_strata": True,
                "max_legal_retry_and_poll_fpr": 0.0,
                "min_precision": 0.95,
                "min_recall": 0.90,
            },
            "observed": {
                "total_reviewed": total_reviewed,
                "legal_retry_and_poll_fpr": legal_fpr_val,
                "precision": prec_val,
                "recall": rec_val,
            },
            "checks": checks,
            "passed": gate_passed,
        },
        "detector_freeze_eligible": gate_passed,
        "training_eligible": False,
        "training_note": (
            "Detector audit alone never enables model training; grouped split freeze, "
            "semantic human audit, and explicit user authorization remain required."
        ),
        "disagreement_count": len(disagreements),
        "disagreements": disagreements,
        "unblinded_segments": records,
    }


def _summarize_semantic_labels(labels):
    total = len(labels)
    by_label = {
        lab: rate(sum(1 for x in labels if x == lab), total)
        for lab in ALLOWED_SEMANTIC_LABELS
    }
    return {
        "denominator": total,
        "by_label": by_label,
        "any_unsupported_factual_claim": rate(
            sum(1 for x in labels if x in UNSUPPORTED_CLAIM_LABELS), total
        ),
        "grounded_or_truthful_stop": rate(
            sum(1 for x in labels if x in GROUNDED_OR_TRUTHFUL_LABELS), total
        ),
    }


def score_semantic_audit(annotated, key, detector_score=None):
    expected_keys = {
        "blind_id",
        "public_user_request",
        "public_tool_names",
        "observed_public_tool_history",
        "terminal_response",
        "reviewer_type",
        "reviewer_id",
        "factual_grounding_label",
        "notes",
    }
    _verify_common_pack_and_key(
        annotated, key, SEMANTIC_PACK_TYPE, SEMANTIC_KEY_TYPE, expected_keys
    )

    reviewer_ids = set()
    unblinded_items = []
    occurrence_rows = []
    for item in annotated["items"]:
        bid = item["blind_id"]
        key_entry = key["items"][bid]
        actual_pub_sha = object_sha256(semantic_public_payload(item))
        if actual_pub_sha != key_entry["public_payload_sha256"]:
            raise ValueError(f"public payload hash tampered for {bid}")

        rid = validate_human_reviewer(item)
        reviewer_ids.add(rid)
        label = item.get("factual_grounding_label")
        if type(label) is not str or label not in ALLOWED_SEMANTIC_LABELS:
            raise ValueError(
                f"Fail-closed rejection on {bid}: invalid or null factual_grounding_label {label!r}"
            )

        inc_cat = key_entry["inclusion_category"]
        unblinded_items.append(
            {
                "blind_id": bid,
                "reviewer_id": rid,
                "factual_grounding_label": label,
                "notes": item["notes"],
                "inclusion_category": inc_cat,
                "occurrence_count": key_entry["occurrence_count"],
                "occurrences": key_entry["occurrences"],
            }
        )
        for occ in key_entry["occurrences"]:
            occurrence_rows.append(
                {
                    "blind_id": bid,
                    "reviewer_id": rid,
                    "factual_grounding_label": label,
                    "inclusion_category": inc_cat,
                    **occ,
                }
            )

    unique_summary = _summarize_semantic_labels(
        [it["factual_grounding_label"] for it in unblinded_items]
    )
    overall_occurrences = _summarize_semantic_labels(
        [row["factual_grounding_label"] for row in occurrence_rows]
    )

    arms = sorted({"B0", "B1", "B2"} | {row["comparison_arm"] for row in occurrence_rows})
    by_comparison_arm = {
        arm: _summarize_semantic_labels(
            [
                row["factual_grounding_label"]
                for row in occurrence_rows
                if row["comparison_arm"] == arm
            ]
        )
        for arm in arms
    }

    cohorts = sorted({row["source_cohort"] for row in occurrence_rows})
    by_source_cohort = {
        cohort: _summarize_semantic_labels(
            [
                row["factual_grounding_label"]
                for row in occurrence_rows
                if row["source_cohort"] == cohort
            ]
        )
        for cohort in cohorts
    }

    by_cohort_and_arm = {}
    for cohort in cohorts:
        cohort_arms = sorted(
            {"B0", "B1", "B2"}
            | {
                row["comparison_arm"]
                for row in occurrence_rows
                if row["source_cohort"] == cohort
            }
        )
        by_cohort_and_arm[cohort] = {
            arm: _summarize_semantic_labels(
                [
                    row["factual_grounding_label"]
                    for row in occurrence_rows
                    if row["source_cohort"] == cohort and row["comparison_arm"] == arm
                ]
            )
            for arm in cohort_arms
        }

    by_guard_notice = {
        "guard_notice_occurred": _summarize_semantic_labels(
            [
                row["factual_grounding_label"]
                for row in occurrence_rows
                if row.get("guard_notice_occurred") is True
            ]
        ),
        "no_guard_notice": _summarize_semantic_labels(
            [
                row["factual_grounding_label"]
                for row in occurrence_rows
                if not row.get("guard_notice_occurred")
            ]
        ),
    }

    pos_controls = [
        it
        for it in unblinded_items
        if it["inclusion_category"] == "grounded_structured_positive_control"
    ]
    pos_matched = sum(
        1 for it in pos_controls if it["factual_grounding_label"] == "grounded_correct_delivery"
    )
    pos_control_rate = rate(pos_matched, len(pos_controls))
    pos_controls_ok = (len(pos_controls) == 0) or (pos_matched == len(pos_controls))

    detector_gate_passed = False
    if detector_score is not None:
        if (
            not isinstance(detector_score, dict)
            or detector_score.get("mode") != "detector"
            or detector_score.get("human_review_verified") is not True
        ):
            raise ValueError("supplied detector_score is not a verified human detector audit report")
        detector_gate_passed = bool(detector_score.get("g2_gate", {}).get("passed") is True)

    g2_gate_passed = bool(pos_controls_ok and detector_gate_passed)

    return {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": "analysis",
        "mode": "semantic",
        "pack_id": annotated["pack_id"],
        "human_review_verified": True,
        "human_reviewer_ids": sorted(reviewer_ids),
        "unique_items_summary": unique_summary,
        "overall_occurrences": overall_occurrences,
        "by_comparison_arm": by_comparison_arm,
        "by_source_cohort": by_source_cohort,
        "by_cohort_and_arm": by_cohort_and_arm,
        "by_guard_notice": by_guard_notice,
        "positive_control_audit": {
            "grounded_structured_controls_matched": pos_control_rate,
            "consistent": pos_controls_ok,
        },
        "g2_gate": {
            "checks": {
                "all_semantic_items_human_reviewed": True,
                "positive_controls_consistent": pos_controls_ok,
                "detector_g2_gate_verified_and_passed": detector_gate_passed,
            },
            "passed": g2_gate_passed,
        },
        "training_eligible": g2_gate_passed,
        "training_execution_authorized": False,
        "training_note": (
            "training_eligible reflects G2 human audit gate status only; actual model training "
            "remains prohibited without grouped-split freeze and explicit user authorization."
        ),
        "unblinded_items": unblinded_items,
    }


def _fmt_rate(r):
    if r["value"] is None:
        return f"N/A ({r['numerator']}/{r['denominator']})"
    return f"{r['value']:.4f} ({r['numerator']}/{r['denominator']})"


def render_markdown_report(result):
    if result["mode"] == "detector":
        ov = result["overall"]
        gate = result["g2_gate"]
        lines = [
            "# G2 循环检测器人工盲审评分报告",
            "",
            f"- Pack ID: `{result['pack_id']}`",
            f"- 人类复核者: `{', '.join(result['human_reviewer_ids'])}`",
            f"- G2 检测器门禁通过: `{gate['passed']}`",
            f"- 训练资格 (`training_eligible`): `{result['training_eligible']}`",
            f"- 抽样说明: {result['sampling_warning']}",
            "",
            "## 总体混淆矩阵与指标",
            "",
            f"- Total: `{ov['total']}` | TP: `{ov['TP']}` | FP: `{ov['FP']}` | FN: `{ov['FN']}` | TN: `{ov['TN']}`",
            f"- Precision: `{_fmt_rate(ov['precision'])}`",
            f"- Recall: `{_fmt_rate(ov['recall'])}`",
            f"- FPR: `{_fmt_rate(ov['fpr'])}`",
            f"- FNR: `{_fmt_rate(ov['fnr'])}`",
            f"- Legal Retry & Poll FPR (Strata 4 & 5): `{_fmt_rate(result['legal_retry_and_poll_fpr'])}`",
            "",
            "## 六层必选分层结果",
            "",
            "| Stratum | Total | TP | FP | FN | TN | Precision | Recall | FPR | FNR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for st, cm in result["by_stratum"].items():
            lines.append(
                f"| `{st}` | {cm['total']} | {cm['TP']} | {cm['FP']} | {cm['FN']} | {cm['TN']} | "
                f"{_fmt_rate(cm['precision'])} | {_fmt_rate(cm['recall'])} | {_fmt_rate(cm['fpr'])} | {_fmt_rate(cm['fnr'])} |"
            )
        lines.extend(
            [
                "",
                "## 按证据来源类别拆分 (`source_evidence_class`)",
                "",
                "| Evidence Class | Total | TP | FP | FN | TN | Precision | Recall | FPR | FNR |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for cls_name, cm in result["by_source_evidence_class"].items():
            lines.append(
                f"| `{cls_name}` | {cm['total']} | {cm['TP']} | {cm['FP']} | {cm['FN']} | {cm['TN']} | "
                f"{_fmt_rate(cm['precision'])} | {_fmt_rate(cm['recall'])} | {_fmt_rate(cm['fpr'])} | {_fmt_rate(cm['fnr'])} |"
            )
        return "\n".join(lines) + "\n"

    lines = [
        "# G2 终止语义人工盲审解盲报告",
        "",
        f"- Pack ID: `{result['pack_id']}`",
        f"- 人类复核者: `{', '.join(result['human_reviewer_ids'])}`",
        f"- G2 联合门禁通过: `{result['g2_gate']['passed']}`",
        f"- 训练资格 (`training_eligible`): `{result['training_eligible']}`",
        "",
        "## 按 Comparison Arm 解盲统计",
        "",
        "| Label | B0 | B1 | B2 |",
        "|---|---:|---:|---:|",
    ]
    for lab in ALLOWED_SEMANTIC_LABELS:
        row_cells = [
            _fmt_rate(result["by_comparison_arm"][arm]["by_label"][lab])
            for arm in ("B0", "B1", "B2")
        ]
        lines.append(f"| `{lab}` | {' | '.join(row_cells)} |")
    lines.append(
        "| **Any Unsupported Claim** | "
        + " | ".join(
            _fmt_rate(result["by_comparison_arm"][arm]["any_unsupported_factual_claim"])
            for arm in ("B0", "B1", "B2")
        )
        + " |"
    )
    return "\n".join(lines) + "\n"


def _write_outputs(result, output_arg):
    out_path = Path(output_arg)
    json_payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    md_payload = render_markdown_report(result)
    if out_path.suffix.lower() == ".json" and not out_path.is_dir():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("x", encoding="utf-8") as handle:
            handle.write(json_payload)
        md_sibling = out_path.with_suffix(".md")
        if not md_sibling.exists():
            with md_sibling.open("x", encoding="utf-8") as handle:
                handle.write(md_payload)
        return out_path

    out_path.mkdir(parents=True, exist_ok=True)
    stem = f"g2_{result['mode']}_score"
    json_file = out_path / f"{stem}.json"
    md_file = out_path / f"{stem}.md"
    with json_file.open("x", encoding="utf-8") as handle:
        handle.write(json_payload)
    with md_file.open("x", encoding="utf-8") as handle:
        handle.write(md_payload)
    return json_file


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("semantic", "detector"))
    parser.add_argument("--annotated", required=True, help="Path to human-annotated blind pack JSON")
    parser.add_argument("--key", required=True, help="Path to sealed key JSON")
    parser.add_argument("--output", required=True, help="Output directory or .json file path")
    parser.add_argument(
        "--detector-score",
        default=None,
        help="Optional verified detector score JSON (required to pass full G2 gate in semantic mode)",
    )
    args = parser.parse_args(argv)

    annotated_path = Path(args.annotated).resolve()
    key_path = Path(args.key).resolve()
    annotated = json.loads(annotated_path.read_text(encoding="utf-8"))
    key = json.loads(key_path.read_text(encoding="utf-8"))
    if sha256_file(annotated_path) == key.get("blank_pack_sha256"):
        raise ValueError("Fail-closed rejection: annotated pack is identical to the blank unannotated pack")

    if args.mode == "detector":
        result = score_detector_audit(annotated, key)
    else:
        det_score = (
            json.loads(Path(args.detector_score).resolve().read_text(encoding="utf-8"))
            if args.detector_score
            else None
        )
        result = score_semantic_audit(annotated, key, detector_score=det_score)

    written = _write_outputs(result, args.output)
    print(
        json.dumps(
            {
                "mode": result["mode"],
                "output": str(written),
                "human_review_verified": result["human_review_verified"],
                "g2_gate_passed": result["g2_gate"]["passed"],
                "training_eligible": result["training_eligible"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
