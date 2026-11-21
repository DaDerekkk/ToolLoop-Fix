#!/usr/bin/env python3
"""ITER-008 honest dual-blind subagent silver comparator & human tie-breaker G2 adjudicator.

Stdlib only. Preserves `scripts/score_g2_human_audits.py` untouched for 100%-human gold packs,
while providing:
  1. `compare-silver`: verifies public payload SHA256 against blank packs & sealed keys,
     fails closed if any silver item claims `reviewer_type == "human"`, computes exact agreement
     and unweighted Cohen's kappa (7x7 semantic + 2x2 detector), evaluates both `detect_loops` (v1)
     and `detect_loops_v2`, and exports arm-blinded `iter8_disagreements_for_human.{json,md}`.
  2. `finalize-g2`: verifies `human-rulings` (`reviewer_type == "human_tiebreaker"`, non-agent ID,
     100% coverage of extracted disagreements), merges silver consensus + human tie-breaker rulings
     with explicit provenance tags (`dual_blind_silver_plus_human_disagreement_adjudication_v1`),
     unblinds against sealed keys (`B0/B1/B2` semantic rates + v1/v2 detector metrics overall,
     per stratum, and on the 212 real `model_inference` segments), and writes `g2_frozen_verdict.{json,md}`.
"""
import argparse
import copy
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
from scripts.score_g2_human_audits import (
    FORBIDDEN_REVIEWER_SUBSTRINGS,
    FORBIDDEN_REVIEWER_TOKENS,
    GROUNDED_OR_TRUTHFUL_LABELS,
    LEGAL_RETRY_AND_POLL_STRATA,
    UNSUPPORTED_CLAIM_LABELS,
    compute_detector_confusion,
    rate,
    sha256_file,
)
from toolloop_fix.harness import detect_loops
from toolloop_fix.loop_detector_v2 import DETECTOR_V2_VERSION, detect_loops_v2

SILVER_REVIEWER_TYPE = "blind_subagent_silver"
HUMAN_TIEBREAKER_TYPE = "human_tiebreaker"
CONSENSUS_PROVENANCE = "dual_blind_subagent_silver_consensus"
TIEBREAKER_PROVENANCE = "human_tiebreaker_adjudicated"
DATASET_GOVERNANCE_TIER = "dual_blind_silver_plus_human_disagreement_adjudication_v1"
DETECTOR_V1_VERSION = "prototype_adjacent_abab_retry_limit_v1"

EXTRA_TIEBREAKER_FORBIDDEN_SUBSTRINGS = FORBIDDEN_REVIEWER_SUBSTRINGS + (
    "silver",
    "subagent",
    "g2_freeze",
    "blind_silver",
)
EXTRA_TIEBREAKER_FORBIDDEN_TOKENS = FORBIDDEN_REVIEWER_TOKENS | {
    "silver",
    "subagent",
}

_ARM_PATTERNS = {
    tok: re.compile(rf"(?<![A-Za-z0-9]){tok}(?![A-Za-z0-9])")
    for tok in ("B0", "B1", "B2")
}

STRICT_PUBLIC_FORBIDDEN_TOKENS = (
    "B0",
    "B1",
    "B2",
    "EXP-",
    "comparison_arm",
    "template_group",
    "Runtime guard notice",
    "guard_blocked",
    "synthetic_harness",
    "model_inference",
)


def _contains_forbidden(text, token):
    pat = _ARM_PATTERNS.get(token)
    if pat is not None:
        return bool(pat.search(text))
    return token in text


def _write_exclusive_json(path, obj):
    payload = (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with Path(path).open("xb") as handle:
        handle.write(payload)
    return sha256_file(path)


def _write_exclusive_text(path, text):
    if not text.endswith("\n"):
        text += "\n"
    payload = text.encode("utf-8")
    with Path(path).open("xb") as handle:
        handle.write(payload)
    return sha256_file(path)


def _fmt_rate(r):
    if r["value"] is None:
        return f"N/A ({r['numerator']}/{r['denominator']})"
    return f"{r['value']:.4f} ({r['numerator']}/{r['denominator']})"


def _fmt_float(val):
    if val is None:
        return "N/A"
    return f"{val:.4f}"


def validate_silver_reviewer(item, pack_label):
    """Fail closed if any silver item spoofs `human` or lacks `blind_subagent_silver`."""
    bid = item.get("blind_id", "<unknown>")
    rtype = item.get("reviewer_type")
    if rtype == "human":
        raise ValueError(
            f"Anti-spoofing fail-closed rejection on {pack_label}:{bid}: "
            "subagent silver pack MUST NOT claim reviewer_type='human'"
        )
    if type(rtype) is not str or rtype != SILVER_REVIEWER_TYPE:
        raise ValueError(
            f"Fail-closed rejection on {pack_label}:{bid}: "
            f"reviewer_type must be {SILVER_REVIEWER_TYPE!r}, got {rtype!r}"
        )
    rid = item.get("reviewer_id")
    if type(rid) is not str or not rid.strip():
        raise ValueError(
            f"Fail-closed rejection on {pack_label}:{bid}: reviewer_id must be a non-empty string"
        )
    notes = item.get("notes")
    if type(notes) is not str:
        raise ValueError(
            f"Fail-closed rejection on {pack_label}:{bid}: notes must be a string"
        )
    return rid.strip()


def validate_human_tiebreaker(rulings_obj, forbidden_silver_ids=()):
    """Fail closed if human_tiebreaker lacks valid human identity or matches agent/silver names."""
    if not isinstance(rulings_obj, dict):
        raise ValueError("human-rulings must be a JSON object")
    rtype = rulings_obj.get("reviewer_type")
    if type(rtype) is not str or rtype != HUMAN_TIEBREAKER_TYPE:
        raise ValueError(
            f"Fail-closed rejection: human-rulings reviewer_type must be {HUMAN_TIEBREAKER_TYPE!r}, got {rtype!r}"
        )
    rid = rulings_obj.get("reviewer_id")
    if type(rid) is not str or not rid.strip():
        raise ValueError("Fail-closed rejection: human-rulings reviewer_id must be a non-empty string")
    clean_rid = rid.strip()
    lowered = clean_rid.lower()
    if clean_rid in set(forbidden_silver_ids) or lowered in {s.lower() for s in forbidden_silver_ids}:
        raise ValueError(
            f"Fail-closed rejection: tiebreaker reviewer_id {clean_rid!r} matches a silver annotator ID"
        )
    for sub in EXTRA_TIEBREAKER_FORBIDDEN_SUBSTRINGS:
        if sub in lowered:
            raise ValueError(
                f"Fail-closed rejection: tiebreaker reviewer_id {clean_rid!r} matches forbidden pattern {sub!r}"
            )
    tokens = [tok for tok in re.split(r"[^a-z0-9]+", lowered) if tok]
    if not tokens:
        raise ValueError(
            f"Fail-closed rejection: tiebreaker reviewer_id {clean_rid!r} contains no alphanumeric token"
        )
    for tok in tokens:
        base_tok = re.sub(r"\d+$", "", tok)
        if tok in EXTRA_TIEBREAKER_FORBIDDEN_TOKENS or (
            base_tok and base_tok in EXTRA_TIEBREAKER_FORBIDDEN_TOKENS
        ):
            raise ValueError(
                f"Fail-closed rejection: tiebreaker reviewer_id {clean_rid!r} matches forbidden role token {tok!r}"
            )
    return clean_rid


def compute_cohens_kappa(pairs, categories):
    """Exact agreement rate and unweighted Cohen's kappa: (p_o - p_e) / (1 - p_e)."""
    cat_list = list(categories)
    cat_set = set(cat_list)
    key_fn = lambda c: ("true" if c is True else "false") if type(c) is bool else str(c)
    cat_keys = [key_fn(c) for c in cat_list]

    matrix = {ck1: {ck2: 0 for ck2 in cat_keys} for ck1 in cat_keys}
    marginals_a = {ck: 0 for ck in cat_keys}
    marginals_b = {ck: 0 for ck in cat_keys}

    for a_val, b_val in pairs:
        if a_val not in cat_set or b_val not in cat_set:
            raise ValueError(f"label pair ({a_val!r}, {b_val!r}) outside allowed categories")
        ka, kb = key_fn(a_val), key_fn(b_val)
        matrix[ka][kb] += 1
        marginals_a[ka] += 1
        marginals_b[kb] += 1

    total = len(pairs)
    agreed = sum(matrix[ck][ck] for ck in cat_keys)
    disagreed = total - agreed
    if total == 0:
        p_o = None
        p_e = None
        kappa = None
    else:
        p_o = agreed / total
        p_e = sum((marginals_a[ck] / total) * (marginals_b[ck] / total) for ck in cat_keys)
        if abs(1.0 - p_e) < 1e-15:
            kappa = 1.0 if agreed == total else None
        else:
            kappa = (p_o - p_e) / (1.0 - p_e)

    return {
        "total": total,
        "agreed": agreed,
        "disagreed": disagreed,
        "exact_agreement_rate": rate(agreed, total),
        "p_o": p_o,
        "p_e": p_e,
        "cohens_kappa": kappa,
        "categories": cat_keys,
        "marginals_a": marginals_a,
        "marginals_b": marginals_b,
        "confusion_matrix": matrix,
    }


def _verify_pack_structure(pack_obj, blank_obj, key_obj, expected_pack_type, expected_key_type, expected_keys, label):
    if not isinstance(pack_obj, dict) or not isinstance(blank_obj, dict) or not isinstance(key_obj, dict):
        raise ValueError(f"{label}: pack, blank pack, and sealed key must be JSON objects")
    if blank_obj.get("pack_type") != expected_pack_type or pack_obj.get("pack_type") != expected_pack_type:
        raise ValueError(f"{label}: pack_type mismatch (expected {expected_pack_type})")
    if key_obj.get("key_type") != expected_key_type:
        raise ValueError(f"{label}: key_type mismatch (expected {expected_key_type})")
    pack_id = key_obj.get("pack_id")
    if blank_obj.get("pack_id") != pack_id or pack_obj.get("pack_id") != pack_id:
        raise ValueError(f"{label}: pack_id mismatch against blank pack / sealed key")

    blank_items = blank_obj.get("items")
    pack_items = pack_obj.get("items")
    key_items = key_obj.get("items")
    if not isinstance(blank_items, list) or not isinstance(pack_items, list) or not isinstance(key_items, dict):
        raise ValueError(f"{label}: malformed items container")
    if not blank_items or len(pack_items) != len(blank_items) or len(key_items) != len(blank_items):
        raise ValueError(f"{label}: item count mismatch against blank pack / sealed key")

    by_id = {}
    for item in pack_items:
        if not isinstance(item, dict) or set(item.keys()) != expected_keys:
            raise ValueError(f"{label}: unexpected item keys on {item.get('blind_id') if isinstance(item, dict) else item!r}")
        bid = item["blind_id"]
        if bid in by_id:
            raise ValueError(f"{label}: duplicate blind_id {bid}")
        by_id[bid] = item

    blank_by_id = {it["blind_id"]: it for it in blank_items}
    if set(by_id.keys()) != set(blank_by_id.keys()) or set(by_id.keys()) != set(key_items.keys()):
        raise ValueError(f"{label}: blind_id set mismatch against blank pack / sealed key")
    return by_id, blank_by_id


def _verify_blind_disagreement_export(disagreements_obj, disagreements_md, semantic_key, detector_key):
    """Ensure human tie-breaker files stay strictly arm-blinded and free of undisclosed oracle values."""
    sem_map = semantic_key["items"]
    det_map = detector_key["items"]

    for item in disagreements_obj["semantic_disagreements"]:
        bid = item["blind_id"]
        pub_only = {
            "blind_id": bid,
            "public_user_request": item["public_user_request"],
            "public_tool_names": item["public_tool_names"],
            "observed_public_tool_history": item["observed_public_tool_history"],
            "terminal_response": item["terminal_response"],
        }
        if object_sha256(pub_only) != sem_map[bid]["public_payload_sha256"]:
            raise ValueError(f"disagreement semantic payload hash mismatch on {bid}")
        item_json = json.dumps(item, ensure_ascii=False)
        for tok in STRICT_PUBLIC_FORBIDDEN_TOKENS:
            if _contains_forbidden(item_json, tok):
                raise ValueError(f"blindness violation on {bid}: contains {tok!r}")
        disclosed_vals = {
            ev.get("response", {}).get("value")
            for ev in item["observed_public_tool_history"]
            if isinstance(ev.get("response"), dict) and "value" in ev["response"]
        }
        req_str = json.dumps(item["public_user_request"], ensure_ascii=False)
        raw_str = item["terminal_response"].get("raw_text", "")
        notes_str = json.dumps([item["annotator_a"], item["annotator_b"]], ensure_ascii=False)
        for oval in sem_map[bid].get("oracle_values_for_leakage_audit", []):
            if oval and oval not in disclosed_vals and oval not in req_str and oval not in raw_str:
                if oval in notes_str:
                    raise ValueError(f"undisclosed oracle value leaked in notes on {bid}")

    for item in disagreements_obj["detector_disagreements"]:
        bid = item["blind_id"]
        pub_only = {
            "blind_id": bid,
            "public_request": item["public_request"],
            "public_tool_docs": item["public_tool_docs"],
            "history_prefix": item["history_prefix"],
            "focal_step_k": item["focal_step_k"],
        }
        if object_sha256(pub_only) != det_map[bid]["public_payload_sha256"]:
            raise ValueError(f"disagreement detector payload hash mismatch on {bid}")
        item_json = json.dumps(item, ensure_ascii=False)
        for tok in STRICT_PUBLIC_FORBIDDEN_TOKENS + MANDATORY_DETECTOR_STRATA:
            if _contains_forbidden(item_json, tok):
                raise ValueError(f"blindness violation on {bid}: contains {tok!r}")
        vis = list(item["history_prefix"]) + [item["focal_step_k"]]
        disclosed_vals = {
            ev.get("response", {}).get("value")
            for ev in vis
            if isinstance(ev.get("response"), dict) and "value" in ev["response"]
        }
        req_str = json.dumps(item["public_request"], ensure_ascii=False)
        for oval in det_map[bid].get("oracle_values_for_leakage_audit", []):
            if oval and oval not in disclosed_vals and oval not in req_str:
                if oval in item_json:
                    raise ValueError(f"undisclosed oracle value leaked on {bid}")

    full_dis_json = json.dumps(disagreements_obj, ensure_ascii=False)
    for tok in ("B0", "B1", "B2", "EXP-", "comparison_arm", "template_group"):
        if _contains_forbidden(full_dis_json, tok):
            raise ValueError(f"disagreements JSON leaked forbidden token {tok!r}")
        if _contains_forbidden(disagreements_md, tok):
            raise ValueError(f"disagreements markdown leaked forbidden token {tok!r}")


def compare_silver_packs(
    blank_semantic,
    silver_sem_a,
    silver_sem_b,
    blank_detector,
    silver_det_a,
    silver_det_b,
    semantic_key,
    detector_key,
    input_hashes=None,
):
    sem_expected_keys = {
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
    det_expected_keys = {
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

    sem_a_by_id, blank_sem_by_id = _verify_pack_structure(
        silver_sem_a, blank_semantic, semantic_key, SEMANTIC_PACK_TYPE, SEMANTIC_KEY_TYPE, sem_expected_keys, "silver_sem_a"
    )
    sem_b_by_id, _ = _verify_pack_structure(
        silver_sem_b, blank_semantic, semantic_key, SEMANTIC_PACK_TYPE, SEMANTIC_KEY_TYPE, sem_expected_keys, "silver_sem_b"
    )
    det_a_by_id, blank_det_by_id = _verify_pack_structure(
        silver_det_a, blank_detector, detector_key, DETECTOR_PACK_TYPE, DETECTOR_KEY_TYPE, det_expected_keys, "silver_det_a"
    )
    det_b_by_id, _ = _verify_pack_structure(
        silver_det_b, blank_detector, detector_key, DETECTOR_PACK_TYPE, DETECTOR_KEY_TYPE, det_expected_keys, "silver_det_b"
    )

    sem_rids_a, sem_rids_b = set(), set()
    sem_pairs = []
    verified_sem_items = []
    semantic_disagreements = []
    positive_control_silver_flags = 0

    for blank_item in blank_semantic["items"]:
        bid = blank_item["blind_id"]
        item_a = sem_a_by_id[bid]
        item_b = sem_b_by_id[bid]
        key_entry = semantic_key["items"][bid]

        blank_sha = object_sha256(semantic_public_payload(blank_item))
        sha_a = object_sha256(semantic_public_payload(item_a))
        sha_b = object_sha256(semantic_public_payload(item_b))
        if len({blank_sha, sha_a, sha_b, key_entry["public_payload_sha256"]}) != 1:
            raise ValueError(f"public_payload_sha256 mismatch on semantic item {bid}")

        rid_a = validate_silver_reviewer(item_a, "silver_sem_a")
        rid_b = validate_silver_reviewer(item_b, "silver_sem_b")
        sem_rids_a.add(rid_a)
        sem_rids_b.add(rid_b)

        lab_a = item_a.get("factual_grounding_label")
        lab_b = item_b.get("factual_grounding_label")
        if type(lab_a) is not str or lab_a not in ALLOWED_SEMANTIC_LABELS:
            raise ValueError(f"invalid factual_grounding_label {lab_a!r} on silver_sem_a:{bid}")
        if type(lab_b) is not str or lab_b not in ALLOWED_SEMANTIC_LABELS:
            raise ValueError(f"invalid factual_grounding_label {lab_b!r} on silver_sem_b:{bid}")

        sem_pairs.append((lab_a, lab_b))
        is_pos_ctrl = key_entry["inclusion_category"] == "grounded_structured_positive_control"
        pos_ctrl_miss = is_pos_ctrl and (
            lab_a != "grounded_correct_delivery" or lab_b != "grounded_correct_delivery"
        )
        if pos_ctrl_miss:
            positive_control_silver_flags += 1
        needs_human = (lab_a != lab_b) or pos_ctrl_miss

        verified_sem_items.append(
            {
                "blind_id": bid,
                "public_payload_sha256": blank_sha,
                "reviewer_id_a": rid_a,
                "reviewer_id_b": rid_b,
                "label_a": lab_a,
                "notes_a": item_a["notes"],
                "label_b": lab_b,
                "notes_b": item_b["notes"],
                "requires_human_ruling": needs_human,
            }
        )
        if needs_human:
            semantic_disagreements.append(
                {
                    "blind_id": bid,
                    "public_user_request": copy.deepcopy(blank_item["public_user_request"]),
                    "public_tool_names": copy.deepcopy(blank_item["public_tool_names"]),
                    "observed_public_tool_history": copy.deepcopy(
                        blank_item["observed_public_tool_history"]
                    ),
                    "terminal_response": copy.deepcopy(blank_item["terminal_response"]),
                    "annotator_a": {"label": lab_a, "notes": item_a["notes"]},
                    "annotator_b": {"label": lab_b, "notes": item_b["notes"]},
                }
            )

    if sem_rids_a & sem_rids_b:
        raise ValueError(
            f"Anti-spoofing rejection: silver_sem_a and silver_sem_b share reviewer_id(s) {sorted(sem_rids_a & sem_rids_b)}"
        )

    det_rids_a, det_rids_b = set(), set()
    det_pairs = []
    verified_det_items = []
    detector_disagreements = []
    v1_flagged_count = 0
    v2_flagged_count = 0
    v1_v2_diff_count = 0

    for blank_item in blank_detector["items"]:
        bid = blank_item["blind_id"]
        item_a = det_a_by_id[bid]
        item_b = det_b_by_id[bid]
        key_entry = detector_key["items"][bid]

        blank_sha = object_sha256(detector_public_payload(blank_item))
        sha_a = object_sha256(detector_public_payload(item_a))
        sha_b = object_sha256(detector_public_payload(item_b))
        if len({blank_sha, sha_a, sha_b, key_entry["public_payload_sha256"]}) != 1:
            raise ValueError(f"public_payload_sha256 mismatch on detector item {bid}")

        rid_a = validate_silver_reviewer(item_a, "silver_det_a")
        rid_b = validate_silver_reviewer(item_b, "silver_det_b")
        det_rids_a.add(rid_a)
        det_rids_b.add(rid_b)

        bool_a = item_a.get("human_is_avoidable_invalid_loop")
        bool_b = item_b.get("human_is_avoidable_invalid_loop")
        if type(bool_a) is not bool:
            raise ValueError(f"invalid boolean label {bool_a!r} on silver_det_a:{bid}")
        if type(bool_b) is not bool:
            raise ValueError(f"invalid boolean label {bool_b!r} on silver_det_b:{bid}")

        det_pairs.append((bool_a, bool_b))

        history = list(blank_item["history_prefix"]) + [blank_item["focal_step_k"]]
        k = len(history) - 1
        v1_flags = [f for f in detect_loops(history) if f["index"] == k]
        v2_flags = [f for f in detect_loops_v2(history) if f["index"] == k]
        v1_bool = bool(v1_flags)
        v2_bool = bool(v2_flags)
        if v1_bool != bool(key_entry["detector_flagged_bool"]):
            raise ValueError(f"sealed detector key v1 flag mismatch against live detect_loops on {bid}")

        v1_flagged_count += int(v1_bool)
        v2_flagged_count += int(v2_bool)
        v1_ne_v2 = v1_bool != v2_bool
        v1_v2_diff_count += int(v1_ne_v2)

        silver_diff = bool_a != bool_b
        needs_human = silver_diff or v1_ne_v2
        if silver_diff and v1_ne_v2:
            reason = "silver_disagreement_and_v1_vs_v2_upgrade"
        elif silver_diff:
            reason = "silver_disagreement"
        elif v1_ne_v2:
            reason = "detector_v1_vs_v2_upgrade"
        else:
            reason = None

        verified_det_items.append(
            {
                "blind_id": bid,
                "public_payload_sha256": blank_sha,
                "reviewer_id_a": rid_a,
                "reviewer_id_b": rid_b,
                "bool_a": bool_a,
                "notes_a": item_a["notes"],
                "bool_b": bool_b,
                "notes_b": item_b["notes"],
                "detector_v1_flagged": v1_bool,
                "detector_v1_flags_at_k": v1_flags,
                "detector_v2_flagged": v2_bool,
                "detector_v2_flags_at_k": v2_flags,
                "requires_human_ruling": needs_human,
                "review_reason": reason,
            }
        )
        if needs_human:
            detector_disagreements.append(
                {
                    "blind_id": bid,
                    "review_reason": reason,
                    "public_request": copy.deepcopy(blank_item["public_request"]),
                    "public_tool_docs": copy.deepcopy(blank_item["public_tool_docs"]),
                    "history_prefix": copy.deepcopy(blank_item["history_prefix"]),
                    "focal_step_k": copy.deepcopy(blank_item["focal_step_k"]),
                    "annotator_a": {
                        "is_avoidable_invalid_loop": bool_a,
                        "notes": item_a["notes"],
                    },
                    "annotator_b": {
                        "is_avoidable_invalid_loop": bool_b,
                        "notes": item_b["notes"],
                    },
                    "detector_rule_comparison": {
                        "v1_flagged": v1_bool,
                        "v2_flagged": v2_bool,
                        "v2_matched_prior_indices": [
                            f.get("matched_prior_index") for f in v2_flags
                        ],
                    },
                }
            )

    if det_rids_a & det_rids_b:
        raise ValueError(
            f"Anti-spoofing rejection: silver_det_a and silver_det_b share reviewer_id(s) {sorted(det_rids_a & det_rids_b)}"
        )

    sem_kappa = compute_cohens_kappa(sem_pairs, ALLOWED_SEMANTIC_LABELS)
    det_kappa = compute_cohens_kappa(det_pairs, [True, False])
    det_kappa["table_2x2"] = {
        "true_true": det_kappa["confusion_matrix"]["true"]["true"],
        "true_false": det_kappa["confusion_matrix"]["true"]["false"],
        "false_true": det_kappa["confusion_matrix"]["false"]["true"],
        "false_false": det_kappa["confusion_matrix"]["false"]["false"],
    }

    consensus_det_pairs_v1 = [
        (it["detector_v1_flagged"], it["bool_a"])
        for it in verified_det_items
        if it["bool_a"] == it["bool_b"]
    ]
    consensus_det_pairs_v2 = [
        (it["detector_v2_flagged"], it["bool_a"])
        for it in verified_det_items
        if it["bool_a"] == it["bool_b"]
    ]

    disagreements_payload = {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": "analysis",
        "governance_note": (
            "Arm-blinded human tie-breaker adjudication packet. Contains ONLY public context and "
            "dual-blind subagent silver annotations (plus v1->v2 rule delta segments). "
            "Zero arm labels, run identifiers, detector strata, or oracle values are exposed."
        ),
        "semantic_pack_id": blank_semantic["pack_id"],
        "detector_pack_id": blank_detector["pack_id"],
        "semantic_disagreement_count": len(semantic_disagreements),
        "detector_disagreement_or_upgrade_count": len(detector_disagreements),
        "semantic_disagreements": semantic_disagreements,
        "detector_disagreements": detector_disagreements,
        "human_rulings_template": {
            "reviewer_type": HUMAN_TIEBREAKER_TYPE,
            "reviewer_id": "<fill_personal_non_agent_human_id>",
            "approve_detector_v2_freeze": None,
            "semantic_rulings": {it["blind_id"]: None for it in semantic_disagreements},
            "detector_rulings": {it["blind_id"]: None for it in detector_disagreements},
        },
    }

    disagreements_md = render_disagreements_markdown(disagreements_payload)
    _verify_blind_disagreement_export(
        disagreements_payload, disagreements_md, semantic_key, detector_key
    )

    verified_state = {
        "schema_version": SCHEMA_VERSION,
        "semantic_pack_id": blank_semantic["pack_id"],
        "detector_pack_id": blank_detector["pack_id"],
        "silver_reviewer_ids": {
            "semantic_a": sorted(sem_rids_a),
            "semantic_b": sorted(sem_rids_b),
            "detector_a": sorted(det_rids_a),
            "detector_b": sorted(det_rids_b),
        },
        "semantic_items": verified_sem_items,
        "detector_items": verified_det_items,
    }

    agreement_report = {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": "analysis",
        "mode": "compare-silver",
        "dataset_governance_tier": DATASET_GOVERNANCE_TIER,
        "pure_manual_human_gold_claimed": False,
        "anti_spoofing_checks": {
            "passed": True,
            "required_silver_reviewer_type": SILVER_REVIEWER_TYPE,
            "zero_human_impersonation_in_silver": True,
            "distinct_reviewer_ids_semantic": True,
            "distinct_reviewer_ids_detector": True,
            "all_public_payload_sha256_verified": True,
        },
        "input_hashes": input_hashes or {},
        "silver_reviewer_ids": verified_state["silver_reviewer_ids"],
        "semantic_agreement": {
            "pack_id": blank_semantic["pack_id"],
            **sem_kappa,
            "positive_control_silver_mismatch_count": positive_control_silver_flags,
            "extracted_for_human_count": len(semantic_disagreements),
        },
        "detector_agreement": {
            "pack_id": blank_detector["pack_id"],
            **det_kappa,
            "detector_v1_version": DETECTOR_V1_VERSION,
            "detector_v2_version": DETECTOR_V2_VERSION,
            "detector_v1_flagged_count": v1_flagged_count,
            "detector_v2_flagged_count": v2_flagged_count,
            "detector_v1_vs_v2_delta_count": v1_v2_diff_count,
            "preliminary_on_silver_consensus_only": {
                "consensus_segment_count": len(consensus_det_pairs_v1),
                "detector_v1": compute_detector_confusion(consensus_det_pairs_v1),
                "detector_v2": compute_detector_confusion(consensus_det_pairs_v2),
            },
            "extracted_for_human_count": len(detector_disagreements),
        },
        "human_tiebreaker_completed": False,
        "g2_passed": False,
    }
    agreement_md = render_silver_agreement_markdown(agreement_report)

    return {
        "agreement_report": agreement_report,
        "agreement_markdown": agreement_md,
        "disagreements": disagreements_payload,
        "disagreements_markdown": disagreements_md,
        "verified_state": verified_state,
    }


def render_disagreements_markdown(disagreements_payload):
    lines = [
        "# ITER-008 G2 双盲银标分歧与检测器 v1→v2 升级人工裁决表（盲态）",
        "",
        f"- Semantic Pack ID: `{disagreements_payload['semantic_pack_id']}` | 需人工裁决数: `{disagreements_payload['semantic_disagreement_count']}`",
        f"- Detector Pack ID: `{disagreements_payload['detector_pack_id']}` | 需人工裁决数（含 A!=B 与 v1!=v2）: `{disagreements_payload['detector_disagreement_or_upgrade_count']}`",
        "- **盲审说明**: 本文件不含实验臂（无比较组标签）、运行编号、检测器分层名或未披露隐藏值。请仅依据公开上下文裁决。",
        "",
        "## 一、终止语义分歧项 (`semantic_rulings`)",
        "",
        "可选标签枚举：",
    ]
    for lab in ALLOWED_SEMANTIC_LABELS:
        lines.append(f"- `{lab}`")
    lines.append("")

    if not disagreements_payload["semantic_disagreements"]:
        lines.append("_无语义分歧项（双盲银标 100% 一致且正对照全部吻合）。_")
        lines.append("")
    for item in disagreements_payload["semantic_disagreements"]:
        lines.extend(
            [
                f"### {item['blind_id']}",
                "",
                f"- **公开请求**: `{item['public_user_request']['user_message']}`",
                f"- **公开工具**: `{', '.join(item['public_tool_names'])}`",
                "- **公开工具执行历史**:",
                "```json",
                json.dumps(item["observed_public_tool_history"], ensure_ascii=False, indent=2),
                "```",
                f"- **终止形式**: `{item['terminal_response']['terminal_form']}` | **截断**: `{item['terminal_response']['token_limit_reached']}`",
                "- **模型最终输出 (`raw_text`)**:",
                "```text",
                item["terminal_response"]["raw_text"],
                "```",
                f"- **Annotator A**: `{item['annotator_a']['label']}` — {item['annotator_a']['notes']}",
                f"- **Annotator B**: `{item['annotator_b']['label']}` — {item['annotator_b']['notes']}",
                "",
            ]
        )

    lines.extend(
        [
            "## 二、循环检测分歧与 v1→v2 规则升级待裁决段 (`detector_rulings`)",
            "",
            "请判定 `focal_step_k` 是否属于**本可避免的无效循环** (`true` / `false`)，并在回执顶层确认 `approve_detector_v2_freeze` (`true` / `false`)。",
            "",
        ]
    )
    if not disagreements_payload["detector_disagreements"]:
        lines.append("_无检测器分歧或规则差异项。_")
        lines.append("")
    for item in disagreements_payload["detector_disagreements"]:
        rc = item["detector_rule_comparison"]
        lines.extend(
            [
                f"### {item['blind_id']} (`{item['review_reason']}`)",
                "",
                f"- **公开请求**: `{json.dumps(item['public_request'], ensure_ascii=False)}`",
                "- **前置已执行步骤 (`0..k-1`)**:",
                "```json",
                json.dumps(item["history_prefix"], ensure_ascii=False, indent=2),
                "```",
                "- **当前步骤 (`focal_step_k`)**:",
                "```json",
                json.dumps(item["focal_step_k"], ensure_ascii=False, indent=2),
                "```",
                f"- **Annotator A**: `{item['annotator_a']['is_avoidable_invalid_loop']}` — {item['annotator_a']['notes']}",
                f"- **Annotator B**: `{item['annotator_b']['is_avoidable_invalid_loop']}` — {item['annotator_b']['notes']}",
                f"- **规则对比**: `v1_flagged={rc['v1_flagged']}` vs `v2_flagged={rc['v2_flagged']}` (matched prior indices: `{rc['v2_matched_prior_indices']}`)",
                "",
            ]
        )
    return "\n".join(lines) + "\n"


def render_silver_agreement_markdown(report):
    sem = report["semantic_agreement"]
    det = report["detector_agreement"]
    t2 = det["table_2x2"]
    lines = [
        "# ITER-008 G2 双盲银标一致性报告（解盲前）",
        "",
        f"- 治理层级 (`dataset_governance_tier`): `{report['dataset_governance_tier']}`",
        f"- 声称 100% 纯手工金标 (`pure_manual_human_gold_claimed`): `{report['pure_manual_human_gold_claimed']}`",
        f"- 防冒充校验 (`zero_human_impersonation_in_silver`): `{report['anti_spoofing_checks']['zero_human_impersonation_in_silver']}`",
        "",
        "## 1. 终止语义盲包一致性 (`166` 项，7 类)",
        "",
        f"- Pack ID: `{sem['pack_id']}`",
        f"- Exact Agreement: `{_fmt_rate(sem['exact_agreement_rate'])}` (`p_o={_fmt_float(sem['p_o'])}`, `p_e={_fmt_float(sem['p_e'])}`)",
        f"- Unweighted Cohen's κ: `{_fmt_float(sem['cohens_kappa'])}`",
        f"- 需人类裁决条数: `{sem['extracted_for_human_count']}`",
        "",
        "## 2. 循环检测盲审包一致性 (`358` 段，2×2)",
        "",
        f"- Pack ID: `{det['pack_id']}`",
        f"- Exact Agreement: `{_fmt_rate(det['exact_agreement_rate'])}` (`p_o={_fmt_float(det['p_o'])}`, `p_e={_fmt_float(det['p_e'])}`)",
        f"- Unweighted Cohen's κ: `{_fmt_float(det['cohens_kappa'])}`",
        f"- 2×2 表: `(A=True,B=True)={t2['true_true']}`, `(A=True,B=False)={t2['true_false']}`, `(A=False,B=True)={t2['false_true']}`, `(A=False,B=False)={t2['false_false']}`",
        f"- `detect_loops` v1 检出段数: `{det['detector_v1_flagged_count']}` | `detect_loops_v2` 检出段数: `{det['detector_v2_flagged_count']}` | `v1 != v2` 差异段数: `{det['detector_v1_vs_v2_delta_count']}`",
        f"- 需人类裁决总段数（`A != B` ∪ `v1 != v2`）: `{det['extracted_for_human_count']}`",
    ]
    return "\n".join(lines) + "\n"


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


def _evaluate_detector_against_reference(records, pred_field, version_name):
    overall = compute_detector_confusion(
        [(r[pred_field], r["adjudicated_is_avoidable_invalid_loop"]) for r in records]
    )
    by_stratum = {}
    for st in MANDATORY_DETECTOR_STRATA:
        sub = [
            (r[pred_field], r["adjudicated_is_avoidable_invalid_loop"])
            for r in records
            if r["stratum"] == st
        ]
        by_stratum[st] = compute_detector_confusion(sub)
    for extra_st in sorted({r["stratum"] for r in records} - set(MANDATORY_DETECTOR_STRATA)):
        sub = [
            (r[pred_field], r["adjudicated_is_avoidable_invalid_loop"])
            for r in records
            if r["stratum"] == extra_st
        ]
        by_stratum[extra_st] = compute_detector_confusion(sub)

    legal_sub = [
        (r[pred_field], r["adjudicated_is_avoidable_invalid_loop"])
        for r in records
        if r["stratum"] in LEGAL_RETRY_AND_POLL_STRATA
    ]
    legal_cm = compute_detector_confusion(legal_sub)

    by_evidence_class = {}
    for ev_cls in ("model_inference", "synthetic_harness", "synthetic_harness_boundary"):
        sub = [
            (r[pred_field], r["adjudicated_is_avoidable_invalid_loop"])
            for r in records
            if r["source_evidence_class"] == ev_cls
        ]
        by_evidence_class[ev_cls] = compute_detector_confusion(sub)
    by_evidence_class["all_synthetic_combined"] = compute_detector_confusion(
        [
            (r[pred_field], r["adjudicated_is_avoidable_invalid_loop"])
            for r in records
            if r["source_evidence_class"] in {"synthetic_harness", "synthetic_harness_boundary"}
        ]
    )

    by_provenance = {}
    for prov in (CONSENSUS_PROVENANCE, TIEBREAKER_PROVENANCE):
        sub = [
            (r[pred_field], r["adjudicated_is_avoidable_invalid_loop"])
            for r in records
            if r["label_provenance"] == prov
        ]
        by_provenance[prov] = compute_detector_confusion(sub)

    errors = [
        {
            "blind_id": r["blind_id"],
            "stratum": r["stratum"],
            "source_evidence_class": r["source_evidence_class"],
            "predicted": r[pred_field],
            "reference": r["adjudicated_is_avoidable_invalid_loop"],
            "outcome": "FP" if r[pred_field] else "FN",
            "label_provenance": r["label_provenance"],
        }
        for r in records
        if r[pred_field] != r["adjudicated_is_avoidable_invalid_loop"]
    ]

    return {
        "detector_version": version_name,
        "overall": overall,
        "legal_retry_and_poll_fpr": legal_cm["fpr"],
        "legal_retry_and_poll_confusion": legal_cm,
        "by_stratum": by_stratum,
        "by_source_evidence_class": by_evidence_class,
        "by_label_provenance": by_provenance,
        "error_count": len(errors),
        "errors": errors,
    }


def finalize_g2_verdict(
    agreement_report,
    disagreements,
    verified_state,
    human_rulings,
    semantic_key,
    detector_key,
    artifact_hashes=None,
):
    if semantic_key.get("key_type") != SEMANTIC_KEY_TYPE or detector_key.get("key_type") != DETECTOR_KEY_TYPE:
        raise ValueError("invalid sealed key types passed to finalize-g2")
    if verified_state.get("semantic_pack_id") != semantic_key.get("pack_id"):
        raise ValueError("semantic pack_id mismatch between comparison-dir and semantic-key")
    if verified_state.get("detector_pack_id") != detector_key.get("pack_id"):
        raise ValueError("detector pack_id mismatch between comparison-dir and detector-key")

    all_silver_ids = set()
    for group_ids in verified_state.get("silver_reviewer_ids", {}).values():
        all_silver_ids.update(group_ids)
    tiebreaker_id = validate_human_tiebreaker(human_rulings, forbidden_silver_ids=all_silver_ids)

    approve_v2 = human_rulings.get("approve_detector_v2_freeze")
    if type(approve_v2) is not bool:
        raise ValueError("Fail-closed rejection: approve_detector_v2_freeze must be boolean true/false")

    sem_rulings = human_rulings.get("semantic_rulings")
    det_rulings = human_rulings.get("detector_rulings")
    if not isinstance(sem_rulings, dict) or not isinstance(det_rulings, dict):
        raise ValueError("Fail-closed rejection: semantic_rulings and detector_rulings must be JSON objects")

    required_sem_ids = {it["blind_id"] for it in disagreements["semantic_disagreements"]}
    required_det_ids = {it["blind_id"] for it in disagreements["detector_disagreements"]}
    missing_sem = sorted(required_sem_ids - set(sem_rulings.keys()))
    missing_det = sorted(required_det_ids - set(det_rulings.keys()))
    if missing_sem:
        raise ValueError(f"Fail-closed rejection: missing human semantic rulings for {missing_sem}")
    if missing_det:
        raise ValueError(f"Fail-closed rejection: missing human detector rulings for {missing_det}")

    valid_sem_ids = set(semantic_key["items"].keys())
    valid_det_ids = set(detector_key["items"].keys())
    unknown_sem = sorted(set(sem_rulings.keys()) - valid_sem_ids)
    unknown_det = sorted(set(det_rulings.keys()) - valid_det_ids)
    if unknown_sem:
        raise ValueError(f"unknown semantic blind_ids in human_rulings: {unknown_sem}")
    if unknown_det:
        raise ValueError(f"unknown detector blind_ids in human_rulings: {unknown_det}")

    for bid, lab in sem_rulings.items():
        if type(lab) is not str or lab not in ALLOWED_SEMANTIC_LABELS:
            raise ValueError(f"invalid human semantic ruling {lab!r} on {bid}")
    for bid, val in det_rulings.items():
        if type(val) is not bool:
            raise ValueError(f"invalid human detector ruling {val!r} on {bid} (must be bool)")

    # Merge & unblind semantic items
    unblinded_sem_items = []
    sem_occurrence_rows = []
    for item in verified_state["semantic_items"]:
        bid = item["blind_id"]
        key_entry = semantic_key["items"][bid]
        if item["public_payload_sha256"] != key_entry["public_payload_sha256"]:
            raise ValueError(f"semantic public_payload_sha256 mismatch on {bid}")
        if bid in sem_rulings:
            final_label = sem_rulings[bid]
            prov = TIEBREAKER_PROVENANCE
        else:
            if item["label_a"] != item["label_b"]:
                raise ValueError(f"unresolved semantic disagreement on {bid}")
            final_label = item["label_a"]
            prov = CONSENSUS_PROVENANCE

        inc_cat = key_entry["inclusion_category"]
        unblinded_sem_items.append(
            {
                "blind_id": bid,
                "adjudicated_factual_grounding_label": final_label,
                "label_provenance": prov,
                "silver_label_a": item["label_a"],
                "silver_label_b": item["label_b"],
                "human_tiebreaker_id": tiebreaker_id if prov == TIEBREAKER_PROVENANCE else None,
                "inclusion_category": inc_cat,
                "occurrence_count": key_entry["occurrence_count"],
                "occurrences": key_entry["occurrences"],
            }
        )
        for occ in key_entry["occurrences"]:
            sem_occurrence_rows.append(
                {
                    "blind_id": bid,
                    "factual_grounding_label": final_label,
                    "label_provenance": prov,
                    "inclusion_category": inc_cat,
                    **occ,
                }
            )

    arms = sorted({"B0", "B1", "B2"} | {r["comparison_arm"] for r in sem_occurrence_rows})
    cohorts = sorted({r["source_cohort"] for r in sem_occurrence_rows})
    by_arm = {
        arm: _summarize_semantic_labels(
            [r["factual_grounding_label"] for r in sem_occurrence_rows if r["comparison_arm"] == arm]
        )
        for arm in arms
    }
    by_cohort = {
        cohort: _summarize_semantic_labels(
            [r["factual_grounding_label"] for r in sem_occurrence_rows if r["source_cohort"] == cohort]
        )
        for cohort in cohorts
    }
    by_cohort_and_arm = {
        cohort: {
            arm: _summarize_semantic_labels(
                [
                    r["factual_grounding_label"]
                    for r in sem_occurrence_rows
                    if r["source_cohort"] == cohort and r["comparison_arm"] == arm
                ]
            )
            for arm in arms
        }
        for cohort in cohorts
    }
    by_guard = {
        "guard_notice_occurred": _summarize_semantic_labels(
            [
                r["factual_grounding_label"]
                for r in sem_occurrence_rows
                if r.get("guard_notice_occurred") is True
            ]
        ),
        "no_guard_notice": _summarize_semantic_labels(
            [
                r["factual_grounding_label"]
                for r in sem_occurrence_rows
                if not r.get("guard_notice_occurred")
            ]
        ),
    }

    pos_controls = [
        it
        for it in unblinded_sem_items
        if it["inclusion_category"] == "grounded_structured_positive_control"
    ]
    pos_matched = sum(
        1
        for it in pos_controls
        if it["adjudicated_factual_grounding_label"] == "grounded_correct_delivery"
    )
    pos_controls_ok = (len(pos_controls) == 0) or (pos_matched == len(pos_controls))

    # Merge & unblind detector segments
    unblinded_det_records = []
    for item in verified_state["detector_items"]:
        bid = item["blind_id"]
        key_entry = detector_key["items"][bid]
        if item["public_payload_sha256"] != key_entry["public_payload_sha256"]:
            raise ValueError(f"detector public_payload_sha256 mismatch on {bid}")
        if bid in det_rulings:
            final_bool = det_rulings[bid]
            prov = TIEBREAKER_PROVENANCE
        else:
            if item["bool_a"] != item["bool_b"]:
                raise ValueError(f"unresolved detector disagreement on {bid}")
            final_bool = item["bool_a"]
            prov = CONSENSUS_PROVENANCE

        unblinded_det_records.append(
            {
                "blind_id": bid,
                "stratum": key_entry["stratum"],
                "source_evidence_class": key_entry["source_evidence_class"],
                "adjudicated_is_avoidable_invalid_loop": final_bool,
                "label_provenance": prov,
                "silver_bool_a": item["bool_a"],
                "silver_bool_b": item["bool_b"],
                "human_tiebreaker_id": tiebreaker_id if prov == TIEBREAKER_PROVENANCE else None,
                "detector_v1_flagged": item["detector_v1_flagged"],
                "detector_v1_flags_at_k": item["detector_v1_flags_at_k"],
                "detector_v2_flagged": item["detector_v2_flagged"],
                "detector_v2_flags_at_k": item["detector_v2_flags_at_k"],
                "provenance": key_entry["provenance"],
            }
        )

    v1_eval = _evaluate_detector_against_reference(
        unblinded_det_records, "detector_v1_flagged", DETECTOR_V1_VERSION
    )
    v2_eval = _evaluate_detector_against_reference(
        unblinded_det_records, "detector_v2_flagged", DETECTOR_V2_VERSION
    )

    total_segments = v2_eval["overall"]["total"]
    real_model_segments = v2_eval["by_source_evidence_class"]["model_inference"]["total"]
    all_six_strata = all(
        v2_eval["by_stratum"][st]["total"] > 0 for st in MANDATORY_DETECTOR_STRATA
    )
    v2_legal_fpr = v2_eval["legal_retry_and_poll_fpr"]["value"]
    v2_prec = v2_eval["overall"]["precision"]["value"]
    v2_rec = v2_eval["overall"]["recall"]["value"]

    checks = {
        "total_segments_gte_200": total_segments >= 200,
        "real_model_segments_gte_200": real_model_segments >= 200,
        "all_six_mandatory_strata_covered": all_six_strata,
        "detector_v2_legal_retry_and_poll_fpr_eq_0": (
            v2_legal_fpr is not None and v2_legal_fpr == 0.0
        ),
        "detector_v2_precision_gte_0_95": (v2_prec is not None and v2_prec >= 0.95),
        "detector_v2_recall_gte_0_90": (v2_rec is not None and v2_rec >= 0.90),
        "semantic_positive_controls_consistent": pos_controls_ok,
        "approve_detector_v2_freeze": approve_v2 is True,
    }
    g2_passed = all(checks.values())

    sem_prov_counts = {
        CONSENSUS_PROVENANCE: sum(
            1 for it in unblinded_sem_items if it["label_provenance"] == CONSENSUS_PROVENANCE
        ),
        TIEBREAKER_PROVENANCE: sum(
            1 for it in unblinded_sem_items if it["label_provenance"] == TIEBREAKER_PROVENANCE
        ),
    }
    det_prov_counts = {
        CONSENSUS_PROVENANCE: sum(
            1 for it in unblinded_det_records if it["label_provenance"] == CONSENSUS_PROVENANCE
        ),
        TIEBREAKER_PROVENANCE: sum(
            1 for it in unblinded_det_records if it["label_provenance"] == TIEBREAKER_PROVENANCE
        ),
    }

    verdict = {
        "schema_version": SCHEMA_VERSION,
        "evidence_class": "analysis",
        "mode": "finalize-g2",
        "dataset_governance_tier": DATASET_GOVERNANCE_TIER,
        "pure_manual_human_gold_claimed": False,
        "governance_disclosure": (
            "Consensus items come from two independent blind subagents (reviewer_type='blind_subagent_silver') "
            "with quantified Cohen's kappa; all disagreements and all v1->v2 detector delta segments were "
            "blindly adjudicated by a human tie-breaker (reviewer_type='human_tiebreaker'). Pure-human gold "
            "scorer scripts/score_g2_human_audits.py remains unmodified."
        ),
        "g2_passed": g2_passed,
        "frozen_detector_version": DETECTOR_V2_VERSION if g2_passed else None,
        "candidate_detector_version": DETECTOR_V2_VERSION,
        "human_tiebreaker_id": tiebreaker_id,
        "approve_detector_v2_freeze": approve_v2,
        "silver_reviewer_ids": verified_state["silver_reviewer_ids"],
        "artifact_hashes": artifact_hashes or {},
        "label_provenance_counts": {
            "semantic_unique_items": sem_prov_counts,
            "detector_unique_segments": det_prov_counts,
        },
        "silver_inter_annotator_agreement": {
            "semantic_cohens_kappa": agreement_report["semantic_agreement"]["cohens_kappa"],
            "semantic_exact_agreement_rate": agreement_report["semantic_agreement"][
                "exact_agreement_rate"
            ],
            "detector_cohens_kappa": agreement_report["detector_agreement"]["cohens_kappa"],
            "detector_exact_agreement_rate": agreement_report["detector_agreement"][
                "exact_agreement_rate"
            ],
        },
        "g2_freeze_criteria": {
            "thresholds": {
                "min_total_segments": 200,
                "min_real_model_segments": 200,
                "require_all_six_mandatory_strata": True,
                "max_detector_v2_legal_retry_and_poll_fpr": 0.0,
                "min_detector_v2_precision": 0.95,
                "min_detector_v2_recall": 0.90,
                "require_semantic_positive_controls_consistent": True,
                "require_human_approve_detector_v2_freeze": True,
            },
            "observed": {
                "total_segments": total_segments,
                "real_model_segments": real_model_segments,
                "detector_v1_precision": v1_eval["overall"]["precision"]["value"],
                "detector_v1_recall": v1_eval["overall"]["recall"]["value"],
                "detector_v1_legal_retry_and_poll_fpr": v1_eval["legal_retry_and_poll_fpr"]["value"],
                "detector_v2_precision": v2_prec,
                "detector_v2_recall": v2_rec,
                "detector_v2_legal_retry_and_poll_fpr": v2_legal_fpr,
                "approve_detector_v2_freeze": approve_v2,
            },
            "checks": checks,
            "passed": g2_passed,
        },
        "semantic_unblinded": {
            "pack_id": semantic_key["pack_id"],
            "unique_items_summary": _summarize_semantic_labels(
                [it["adjudicated_factual_grounding_label"] for it in unblinded_sem_items]
            ),
            "overall_occurrences": _summarize_semantic_labels(
                [r["factual_grounding_label"] for r in sem_occurrence_rows]
            ),
            "by_comparison_arm": by_arm,
            "by_source_cohort": by_cohort,
            "by_cohort_and_arm": by_cohort_and_arm,
            "by_guard_notice": by_guard,
            "positive_control_audit": {
                "grounded_structured_controls_matched": rate(pos_matched, len(pos_controls)),
                "consistent": pos_controls_ok,
            },
            "unblinded_items": unblinded_sem_items,
        },
        "detector_unblinded": {
            "pack_id": detector_key["pack_id"],
            "sampling_warning": (
                "Stratified non-uniform audit sample across 6 mandatory strata; "
                "overall precision/recall/FPR/FNR measure detector classification accuracy "
                "and MUST NOT be reported as natural population loop rates."
            ),
            "detector_v1": v1_eval,
            "detector_v2": v2_eval,
            "v1_to_v2_comparison": {
                "tp_delta": v2_eval["overall"]["TP"] - v1_eval["overall"]["TP"],
                "fp_delta": v2_eval["overall"]["FP"] - v1_eval["overall"]["FP"],
                "fn_delta": v2_eval["overall"]["FN"] - v1_eval["overall"]["FN"],
                "tn_delta": v2_eval["overall"]["TN"] - v1_eval["overall"]["TN"],
                "real_model_tp_delta": (
                    v2_eval["by_source_evidence_class"]["model_inference"]["TP"]
                    - v1_eval["by_source_evidence_class"]["model_inference"]["TP"]
                ),
                "real_model_fn_delta": (
                    v2_eval["by_source_evidence_class"]["model_inference"]["FN"]
                    - v1_eval["by_source_evidence_class"]["model_inference"]["FN"]
                ),
            },
            "unblinded_segments": unblinded_det_records,
        },
    }
    verdict_md = render_frozen_verdict_markdown(verdict)
    return verdict, verdict_md


def render_frozen_verdict_markdown(verdict):
    sem_arm = verdict["semantic_unblinded"]["by_comparison_arm"]
    v1_ov = verdict["detector_unblinded"]["detector_v1"]["overall"]
    v2_ov = verdict["detector_unblinded"]["detector_v2"]["overall"]
    v1_mi = verdict["detector_unblinded"]["detector_v1"]["by_source_evidence_class"][
        "model_inference"
    ]
    v2_mi = verdict["detector_unblinded"]["detector_v2"]["by_source_evidence_class"][
        "model_inference"
    ]
    iaa = verdict["silver_inter_annotator_agreement"]
    prov = verdict["label_provenance_counts"]

    lines = [
        "# ITER-008 G2 冻结裁决报告（双盲银标 + 人类分歧裁决）",
        "",
        f"- **G2 门禁通过 (`g2_passed`)**: `{verdict['g2_passed']}`",
        f"- **冻结检测器版本 (`frozen_detector_version`)**: `{verdict['frozen_detector_version']}`",
        f"- **数据治理层级 (`dataset_governance_tier`)**: `{verdict['dataset_governance_tier']}`",
        f"- **声称 100% 纯手工金标 (`pure_manual_human_gold_claimed`)**: `{verdict['pure_manual_human_gold_claimed']}`",
        f"- **人类裁决者 (`human_tiebreaker_id`)**: `{verdict['human_tiebreaker_id']}` (`approve_detector_v2_freeze={verdict['approve_detector_v2_freeze']}`)",
        f"- **银标双盲 κ**: Semantic κ=`{_fmt_float(iaa['semantic_cohens_kappa'])}` (`{_fmt_rate(iaa['semantic_exact_agreement_rate'])}`) | Detector κ=`{_fmt_float(iaa['detector_cohens_kappa'])}` (`{_fmt_rate(iaa['detector_exact_agreement_rate'])}`)",
        f"- **来源构成**: Semantic 共识 `{prov['semantic_unique_items'][CONSENSUS_PROVENANCE]}` + 人类裁决 `{prov['semantic_unique_items'][TIEBREAKER_PROVENANCE]}` | Detector 共识 `{prov['detector_unique_segments'][CONSENSUS_PROVENANCE]}` + 人类裁决 `{prov['detector_unique_segments'][TIEBREAKER_PROVENANCE]}`",
        "",
        "## 1. 终止语义按实验臂解盲统计 (`B0` / `B1` / `B2`)",
        "",
        "| Label | B0 | B1 | B2 |",
        "|---|---:|---:|---:|",
    ]
    for lab in ALLOWED_SEMANTIC_LABELS:
        cells = [_fmt_rate(sem_arm[arm]["by_label"][lab]) for arm in ("B0", "B1", "B2")]
        lines.append(f"| `{lab}` | {' | '.join(cells)} |")
    lines.append(
        "| **Any Unsupported Factual Claim** | "
        + " | ".join(
            _fmt_rate(sem_arm[arm]["any_unsupported_factual_claim"])
            for arm in ("B0", "B1", "B2")
        )
        + " |"
    )

    lines.extend(
        [
            "",
            "## 2. 循环检测器 `v1` vs `v2` 解盲对比",
            "",
            "| Scope / Detector | Total | TP | FP | FN | TN | Precision | Recall | Legal Retry/Poll FPR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            f"| Overall (`358`) — `v1` | {v1_ov['total']} | {v1_ov['TP']} | {v1_ov['FP']} | {v1_ov['FN']} | {v1_ov['TN']} | {_fmt_rate(v1_ov['precision'])} | {_fmt_rate(v1_ov['recall'])} | {_fmt_rate(verdict['detector_unblinded']['detector_v1']['legal_retry_and_poll_fpr'])} |",
            f"| Overall (`358`) — `v2` | {v2_ov['total']} | {v2_ov['TP']} | {v2_ov['FP']} | {v2_ov['FN']} | {v2_ov['TN']} | {_fmt_rate(v2_ov['precision'])} | {_fmt_rate(v2_ov['recall'])} | {_fmt_rate(verdict['detector_unblinded']['detector_v2']['legal_retry_and_poll_fpr'])} |",
            f"| Real `model_inference` (`212`) — `v1` | {v1_mi['total']} | {v1_mi['TP']} | {v1_mi['FP']} | {v1_mi['FN']} | {v1_mi['TN']} | {_fmt_rate(v1_mi['precision'])} | {_fmt_rate(v1_mi['recall'])} | — |",
            f"| Real `model_inference` (`212`) — `v2` | {v2_mi['total']} | {v2_mi['TP']} | {v2_mi['FP']} | {v2_mi['FN']} | {v2_mi['TN']} | {_fmt_rate(v2_mi['precision'])} | {_fmt_rate(v2_mi['recall'])} | — |",
            "",
            "## 3. `detector_v2` 六层必选分层明细",
            "",
            "| Stratum | Total | TP | FP | FN | TN | Precision | Recall | FPR |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for st, cm in verdict["detector_unblinded"]["detector_v2"]["by_stratum"].items():
        lines.append(
            f"| `{st}` | {cm['total']} | {cm['TP']} | {cm['FP']} | {cm['FN']} | {cm['TN']} | "
            f"{_fmt_rate(cm['precision'])} | {_fmt_rate(cm['recall'])} | {_fmt_rate(cm['fpr'])} |"
        )

    lines.extend(["", "## 4. G2 冻结门禁逐项核验", ""])
    for check_name, passed in verdict["g2_freeze_criteria"]["checks"].items():
        lines.append(f"- `{check_name}`: `{passed}`")
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("compare-silver", "finalize-g2"))
    parser.add_argument("--blank-semantic", default=None)
    parser.add_argument("--silver-sem-a", default=None)
    parser.add_argument("--silver-sem-b", default=None)
    parser.add_argument("--blank-detector", default=None)
    parser.add_argument("--silver-det-a", default=None)
    parser.add_argument("--silver-det-b", default=None)
    parser.add_argument("--comparison-dir", default=None)
    parser.add_argument("--human-rulings", default=None)
    parser.add_argument("--semantic-key", required=True)
    parser.add_argument("--detector-key", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    sem_key_path = Path(args.semantic_key).resolve()
    det_key_path = Path(args.detector_key).resolve()
    sem_key = json.loads(sem_key_path.read_text(encoding="utf-8"))
    det_key = json.loads(det_key_path.read_text(encoding="utf-8"))
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.mode == "compare-silver":
        req_flags = (
            args.blank_semantic,
            args.silver_sem_a,
            args.silver_sem_b,
            args.blank_detector,
            args.silver_det_a,
            args.silver_det_b,
        )
        if any(x is None for x in req_flags):
            raise ValueError("compare-silver requires --blank-semantic, --silver-sem-a/b, --blank-detector, --silver-det-a/b")

        paths = {
            "blank_semantic": Path(args.blank_semantic).resolve(),
            "silver_sem_a": Path(args.silver_sem_a).resolve(),
            "silver_sem_b": Path(args.silver_sem_b).resolve(),
            "blank_detector": Path(args.blank_detector).resolve(),
            "silver_det_a": Path(args.silver_det_a).resolve(),
            "silver_det_b": Path(args.silver_det_b).resolve(),
            "semantic_key": sem_key_path,
            "detector_key": det_key_path,
        }
        hashes = {k: sha256_file(p) for k, p in paths.items()}
        if sem_key.get("blank_pack_sha256") and hashes["blank_semantic"] != sem_key["blank_pack_sha256"]:
            raise ValueError("blank-semantic SHA256 does not match semantic-key blank_pack_sha256")
        if det_key.get("blank_pack_sha256") and hashes["blank_detector"] != det_key["blank_pack_sha256"]:
            raise ValueError("blank-detector SHA256 does not match detector-key blank_pack_sha256")
        for s_key, b_key in (
            ("silver_sem_a", "blank_semantic"),
            ("silver_sem_b", "blank_semantic"),
            ("silver_det_a", "blank_detector"),
            ("silver_det_b", "blank_detector"),
        ):
            if hashes[s_key] == hashes[b_key]:
                raise ValueError(f"Fail-closed rejection: {s_key} is identical to blank pack")

        compared = compare_silver_packs(
            blank_semantic=json.loads(paths["blank_semantic"].read_text(encoding="utf-8")),
            silver_sem_a=json.loads(paths["silver_sem_a"].read_text(encoding="utf-8")),
            silver_sem_b=json.loads(paths["silver_sem_b"].read_text(encoding="utf-8")),
            blank_detector=json.loads(paths["blank_detector"].read_text(encoding="utf-8")),
            silver_det_a=json.loads(paths["silver_det_a"].read_text(encoding="utf-8")),
            silver_det_b=json.loads(paths["silver_det_b"].read_text(encoding="utf-8")),
            semantic_key=sem_key,
            detector_key=det_key,
            input_hashes=hashes,
        )

        dis_json_sha = _write_exclusive_json(
            out_dir / "iter8_disagreements_for_human.json", compared["disagreements"]
        )
        dis_md_sha = _write_exclusive_text(
            out_dir / "iter8_disagreements_for_human.md", compared["disagreements_markdown"]
        )
        state_sha = _write_exclusive_json(
            out_dir / "iter8_verified_silver_state.json", compared["verified_state"]
        )
        compared["agreement_report"]["comparison_artifacts"] = {
            "iter8_disagreements_for_human.json": dis_json_sha,
            "iter8_disagreements_for_human.md": dis_md_sha,
            "iter8_verified_silver_state.json": state_sha,
        }
        rep_json_sha = _write_exclusive_json(
            out_dir / "iter8_silver_agreement_report.json", compared["agreement_report"]
        )
        _write_exclusive_text(
            out_dir / "iter8_silver_agreement_report.md", compared["agreement_markdown"]
        )

        print(
            json.dumps(
                {
                    "mode": "compare-silver",
                    "output_dir": str(out_dir),
                    "agreement_report_sha256": rep_json_sha,
                    "semantic_cohens_kappa": compared["agreement_report"]["semantic_agreement"][
                        "cohens_kappa"
                    ],
                    "detector_cohens_kappa": compared["agreement_report"]["detector_agreement"][
                        "cohens_kappa"
                    ],
                    "semantic_disagreements_for_human": compared["disagreements"][
                        "semantic_disagreement_count"
                    ],
                    "detector_disagreements_for_human": compared["disagreements"][
                        "detector_disagreement_or_upgrade_count"
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if args.comparison_dir is None or args.human_rulings is None:
        raise ValueError("finalize-g2 requires --comparison-dir and --human-rulings")

    comp_dir = Path(args.comparison_dir).resolve()
    rulings_path = Path(args.human_rulings).resolve()
    rep_path = comp_dir / "iter8_silver_agreement_report.json"
    dis_path = comp_dir / "iter8_disagreements_for_human.json"
    state_path = comp_dir / "iter8_verified_silver_state.json"

    agreement_report = json.loads(rep_path.read_text(encoding="utf-8"))
    pinned = agreement_report.get("comparison_artifacts", {})
    if sha256_file(dis_path) != pinned.get("iter8_disagreements_for_human.json"):
        raise ValueError("tampered iter8_disagreements_for_human.json in comparison-dir")
    if sha256_file(state_path) != pinned.get("iter8_verified_silver_state.json"):
        raise ValueError("tampered iter8_verified_silver_state.json in comparison-dir")

    verdict, verdict_md = finalize_g2_verdict(
        agreement_report=agreement_report,
        disagreements=json.loads(dis_path.read_text(encoding="utf-8")),
        verified_state=json.loads(state_path.read_text(encoding="utf-8")),
        human_rulings=json.loads(rulings_path.read_text(encoding="utf-8")),
        semantic_key=sem_key,
        detector_key=det_key,
        artifact_hashes={
            "iter8_silver_agreement_report.json": sha256_file(rep_path),
            "iter8_disagreements_for_human.json": sha256_file(dis_path),
            "iter8_verified_silver_state.json": sha256_file(state_path),
            "human_rulings.json": sha256_file(rulings_path),
            "semantic_key.json": sha256_file(sem_key_path),
            "detector_key.json": sha256_file(det_key_path),
        },
    )
    verdict_sha = _write_exclusive_json(out_dir / "g2_frozen_verdict.json", verdict)
    _write_exclusive_text(out_dir / "g2_frozen_verdict.md", verdict_md)
    print(
        json.dumps(
            {
                "mode": "finalize-g2",
                "output_dir": str(out_dir),
                "g2_frozen_verdict_sha256": verdict_sha,
                "g2_passed": verdict["g2_passed"],
                "frozen_detector_version": verdict["frozen_detector_version"],
                "dataset_governance_tier": verdict["dataset_governance_tier"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
