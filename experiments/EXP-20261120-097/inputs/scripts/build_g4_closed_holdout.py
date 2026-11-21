#!/usr/bin/env python3
"""Deterministic builder and zero-leakage verifier for ITER-009 36-case G4 Formal Closed Holdout Suite."""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from toolloop_fix.harness import (
    DOMAIN_TOOLS,
    EVIDENCE_CLASS,
    Environment,
    detect_loops,
    run_case,
    scripted_policy,
    state_hash,
    summarize,
    tool_call,
    validate_alternative,
)
from toolloop_fix.loop_detector_v2 import DETECTOR_V2_VERSION, detect_loops_v2, has_invalid_loop_v2

DOMAINS = ("orders", "files", "jobs")
IN_DOMAIN = ("orders", "files")
CROSS_DOMAIN = ("jobs",)
CATEGORIES = ("normal", "recoverable", "retry_required", "clarification", "unrecoverable")

SPLIT_NAME = "closed_holdout_g4"
TURN_BUDGET = 16
TOOL_BUDGET = 12
SEED_STEP = 32

E2E_SCHEDULE_STARTS = (
    ("g4_e2e_s1", 80000),
    ("g4_e2e_s2", 82000),
    ("g4_e2e_s3", 84000),
)
PREFIX_SCHEDULE_STARTS = (
    ("g4_pfx_s1", 90000),
    ("g4_pfx_s2", 92000),
    ("g4_pfx_s3", 94000),
)

FORBIDDEN_SUBSTRINGS_CASE_INSENSITIVE = (
    "b0",
    "b1",
    "b2",
    "b3",
    "b4",
    "b5",
    "cb1",
    "cb2",
    "exp-",
)

# 12 slots per domain -> 36 distinct template_groups (1 case per template_group).
_IN_DOMAIN_SLOTS = (
    # normal (2)
    ("normal", "h1", "direct_ok_h1", 0, 0, False, "valid", False, "GH1"),
    ("normal", "h2", "direct_ok_h2", 0, 0, False, "valid", False, "GH2"),
    # recoverable (4, all with 1-step fixed_prefix)
    ("recoverable", "h1", "stale_id_locate", 0, 0, False, "alt", True, "GH3"),
    ("recoverable", "h2", "stale_id_locate", 0, 0, False, "alt", True, "GH4"),
    ("recoverable", "h3", "stale_id_locate_plus_1timeout", 1, 0, False, "alt", True, "GH5"),
    ("recoverable", "h4", "stale_id_locate_plus_2timeout", 2, 0, False, "alt", True, "GH6"),
    # retry_required (2, both with 1-step fixed_prefix)
    ("retry_required", "h1", "transient_1_retry", 1, 0, False, "valid", True, "GH7"),
    ("retry_required", "h2", "transient_2_retries_boundary", 2, 0, False, "valid", True, "GH8"),
    # clarification (2, identifier omitted)
    ("clarification", "h1", "missing_id_direct", 0, 0, False, "omitted", False, "GH9"),
    ("clarification", "h2", "missing_id_plus_1timeout", 1, 0, False, "omitted", False, "GH2"),
    # unrecoverable (2, unavailable=True)
    ("unrecoverable", "h1", "unavailable_valid_id_h1", 0, 0, True, "valid", False, "GH5"),
    ("unrecoverable", "h2", "unavailable_alt_id_h2", 0, 0, True, "alt", False, "GH8"),
)

_CROSS_DOMAIN_JOBS_SLOTS = (
    # normal (2)
    ("normal", "h1", "direct_ok_h1", 0, 0, False, "valid", False, "GH1"),
    ("normal", "h2", "direct_ok_h2", 0, 0, False, "valid", False, "GH2"),
    # recoverable (4, all with 1-step fixed_prefix)
    ("recoverable", "h1", "stale_id_locate", 0, 0, False, "alt", True, "GH3"),
    ("recoverable", "h2", "stale_id_locate", 0, 0, False, "alt", True, "GH4"),
    ("recoverable", "h3", "stale_id_locate_plus_1poll", 0, 1, False, "alt", True, "GH5"),
    ("recoverable", "h4", "stale_id_locate_plus_1poll", 0, 1, False, "alt", True, "GH6"),
    # retry_required (2, both with 1-step fixed_prefix)
    ("retry_required", "h1", "job_poll_1_step", 0, 1, False, "valid", True, "GH7"),
    ("retry_required", "h2", "job_poll_2_steps", 0, 2, False, "valid", True, "GH8"),
    # clarification (2, identifier omitted)
    ("clarification", "h1", "missing_id_direct", 0, 0, False, "omitted", False, "GH9"),
    ("clarification", "h2", "missing_id_plus_1poll", 0, 1, False, "omitted", False, "GH2"),
    # unrecoverable (2, unavailable=True)
    ("unrecoverable", "h1", "unavailable_valid_id_h1", 0, 0, True, "valid", False, "GH5"),
    ("unrecoverable", "h2", "unavailable_alt_id_h2", 0, 0, True, "alt", False, "GH8"),
)

_ENTITY_SCHEMAS = {
    "orders": (
        ("ORD", "receipt", "cleared"),
        ("SO", "voucher", "posted"),
        ("ORD", "shipment", "dispatched"),
        ("RMA", "return", "credited"),
        ("INV", "parcel", "audited"),
        ("PO", "consign", "reconciled"),
        ("ORD", "ticket", "confirmed"),
        ("SO", "booking", "released"),
        ("ORD", "claim", "approved"),
        ("INV", "escrow", "funded"),
        ("ORD", "purged", "voided"),
        ("RMA", "sealed", "retired"),
    ),
    "files": (
        ("archive", "doc", "txt", "bundle", "digest"),
        ("cabinet", "memo", "json", "dossier", "checksum"),
        ("archive", "sheet", "csv", "register", "hash"),
        ("depot", "note", "md", "notice", "token"),
        ("locker", "audit", "log", "trace", "stamp"),
        ("shelf", "packet", "xml", "folio", "seal"),
        ("archive", "record", "txt", "snapshot", "verdict"),
        ("cabinet", "item", "json", "manifest", "signature"),
        ("archive", "clip", "txt", "excerpt", "anchor"),
        ("depot", "form", "log", "annex", "serial"),
        ("archive", "gone", "txt", "redacted", "absent"),
        ("locker", "cold", "csv", "offline", "locked"),
    ),
    "jobs": (
        ("JOB", "batch", "summary"),
        ("RUN", "pipeline", "metrics"),
        ("JOB", "worker", "counts"),
        ("DAG", "stage", "marker"),
        ("QUE", "rollup", "report"),
        ("ACT", "export", "artifact"),
        ("JOB", "compact", "delta"),
        ("RUN", "ingest", "frame"),
        ("JOB", "shard", "tally"),
        ("QUE", "sweep", "window"),
        ("JOB", "expired", "dropped"),
        ("DAG", "evicted", "pruned"),
    ),
}


def _resolve_archive_root(root):
    root = Path(root).resolve()
    if (root / "experiments").is_dir() and (root / "data/processed").is_dir():
        return root
    parent_manifest = root.parent / "manifest.json"
    if parent_manifest.is_file():
        src = json.loads(parent_manifest.read_text(encoding="utf-8")).get("source_workspace")
        if src and (Path(src) / "experiments").is_dir():
            return Path(src).resolve()
    if (root / "data/processed").is_dir():
        return root
    return root


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def _read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _g4_entity_strings(domain, slot_idx_1based, gh_tag):
    domain_idx = DOMAINS.index(domain)
    num = 75100 + domain_idx * 100 + slot_idx_1based
    snum = 76100 + domain_idx * 100 + slot_idx_1based
    vnum = 77100 + domain_idx * 100 + slot_idx_1based
    gh_low = gh_tag.lower()
    schema = _ENTITY_SCHEMAS[domain][slot_idx_1based - 1]

    if domain == "orders":
        prefix_code, target_noun, value_tag = schema
        valid_id = f"{prefix_code}-{gh_tag}-{num}"
        alt_id = f"{prefix_code}-{gh_tag}-{snum}"
        target = f"lookup_key:g4_ord_{target_noun}_{gh_low}_{slot_idx_1based:02d}"
        value = f"val_g4_ord_{value_tag}_{gh_low}_{vnum}"
    elif domain == "files":
        folder, stem, ext, target_noun, value_tag = schema
        valid_id = f"/{folder}_{gh_low}/{stem}_{num}.{ext}"
        alt_id = f"/{folder}_{gh_low}/{stem}_{snum}.{ext}"
        target = f"lookup_key:g4_fil_{target_noun}_{gh_low}_{slot_idx_1based:02d}"
        value = f"val_g4_fil_{value_tag}_{gh_low}_{vnum}"
    elif domain == "jobs":
        prefix_code, target_noun, value_tag = schema
        valid_id = f"{prefix_code}-{gh_tag}-{num}"
        alt_id = f"{prefix_code}-{gh_tag}-{snum}"
        target = f"lookup_key:g4_job_{target_noun}_{gh_low}_{slot_idx_1based:02d}"
        value = f"val_g4_job_{value_tag}_{gh_low}_{vnum}"
    else:
        raise ValueError(f"unsupported domain: {domain}")
    return valid_id, alt_id, target, value


def _build_seed_schedules(case_ids, schedule_starts):
    schedules = []
    by_id = {}
    for schedule_id, seed_start in schedule_starts:
        case_seeds = {cid: seed_start + idx * SEED_STEP for idx, cid in enumerate(case_ids)}
        seed_end = seed_start + (len(case_ids) - 1) * SEED_STEP
        entry = {
            "schedule_id": schedule_id,
            "seed_start": seed_start,
            "seed_step": SEED_STEP,
            "seed_end": seed_end,
            "turn_budget": TURN_BUDGET,
            "case_count": len(case_ids),
            "case_seeds": case_seeds,
        }
        schedules.append(entry)
        by_id[schedule_id] = entry
    return schedules, by_id


def _collect_historical_artifacts(workspace_root=ROOT):
    workspace_root = Path(workspace_root).resolve()
    archive_root = _resolve_archive_root(workspace_root)

    forbidden_case_ids = set()
    forbidden_groups = set()
    forbidden_identifiers = set()
    forbidden_targets = set()
    forbidden_values = set()

    pilot_path = workspace_root / "data/dev/pilot_cases.jsonl"
    iter4_path = workspace_root / "data/dev/fixed_prefix_cases_iter4.jsonl"
    iter6_path = workspace_root / "data/dev/timeout_crossed_iter6.jsonl"
    iter7_path = workspace_root / "data/dev/grouped_dev_suite_iter7.jsonl"

    for c in _read_jsonl(pilot_path):
        forbidden_case_ids.add(c["case_id"])
        forbidden_groups.add(c["template_group"])
        forbidden_identifiers.add(c["hidden"]["valid_identifier"])
        if "identifier" in c["request"]:
            forbidden_identifiers.add(c["request"]["identifier"])
        forbidden_targets.add(c["request"]["target"])
        forbidden_values.add(c["hidden"]["value"])

    for row in _read_jsonl(iter4_path):
        forbidden_case_ids.add(row["case_id"])
        oc = row["original_case"]
        forbidden_case_ids.add(oc["case_id"])
        forbidden_groups.add(oc["template_group"])
        forbidden_identifiers.add(oc["hidden"]["valid_identifier"])
        if "identifier" in oc["request"]:
            forbidden_identifiers.add(oc["request"]["identifier"])
        forbidden_targets.add(oc["request"]["target"])
        forbidden_values.add(oc["hidden"]["value"])

    for row in _read_jsonl(iter6_path):
        forbidden_case_ids.add(row["case_id"])
        forbidden_groups.add(row["template_group"])
        forbidden_identifiers.add(row["identifier"])

    for c in _read_jsonl(iter7_path):
        forbidden_case_ids.add(c["case_id"])
        forbidden_groups.add(c["template_group"])
        forbidden_identifiers.add(c["hidden"]["valid_identifier"])
        if "identifier" in c["request"]:
            forbidden_identifiers.add(c["request"]["identifier"])
        forbidden_targets.add(c["request"]["target"])
        forbidden_values.add(c["hidden"]["value"])

    # Also include all expanded candidate-pool entity strings and training records from EXP-20261120-062.
    exp062_dir = archive_root / "experiments/EXP-20261120-062"
    p_dpo_path = exp062_dir / "p_toolloop_fix_dpo.jsonl"
    b5_dpo_path = exp062_dir / "b5_general_expert_dpo.jsonl"
    if not p_dpo_path.is_file() or not b5_dpo_path.is_file():
        raise FileNotFoundError(
            f"missing frozen G3 training artifacts under {exp062_dir}; cannot certify G4 zero-leakage"
        )

    p_rows = _read_jsonl(p_dpo_path)
    b5_rows = _read_jsonl(b5_dpo_path)
    if len(p_rows) != 400 or len(b5_rows) != 400:
        raise ValueError(
            f"expected 400 P DPO and 400 B5 DPO records in EXP-20261120-062, got {len(p_rows)} and {len(b5_rows)}"
        )

    raw_training_blobs = [
        p_dpo_path.read_text(encoding="utf-8"),
        b5_dpo_path.read_text(encoding="utf-8"),
    ]
    sidecar_path = exp062_dir / "local_only_provenance_sidecar.json"
    if sidecar_path.is_file():
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        for section_key in ("p_b3_b4_provenance", "b5_provenance"):
            for item in sidecar.get(section_key, {}).values():
                if "source_case_id" in item:
                    forbidden_case_ids.add(item["source_case_id"])
                if "template_group" in item:
                    forbidden_groups.add(item["template_group"])

    for row in p_rows + b5_rows:
        for msg in row.get("messages", []):
            content = msg.get("content") or ""
            if msg.get("role") == "user" and "Request: {" in content:
                req_json = content.split("Request: ", 1)[1].strip()
                req_obj = json.loads(req_json)
                if "target" in req_obj:
                    forbidden_targets.add(req_obj["target"])
                if "identifier" in req_obj:
                    forbidden_identifiers.add(req_obj["identifier"])
            elif msg.get("role") == "tool" and content.startswith("{"):
                resp_obj = json.loads(content)
                if isinstance(resp_obj.get("identifier"), str):
                    forbidden_identifiers.add(resp_obj["identifier"])
                if isinstance(resp_obj.get("value"), str):
                    forbidden_values.add(resp_obj["value"])

    hashes = {
        "pilot_cases_sha256": sha256_file(pilot_path),
        "fixed_prefix_cases_iter4_sha256": sha256_file(iter4_path),
        "timeout_crossed_iter6_sha256": sha256_file(iter6_path),
        "grouped_dev_suite_iter7_sha256": sha256_file(iter7_path),
        "exp062_p_toolloop_fix_dpo_sha256": sha256_file(p_dpo_path),
        "exp062_b5_general_expert_dpo_sha256": sha256_file(b5_dpo_path),
    }
    return {
        "forbidden_case_ids": forbidden_case_ids,
        "forbidden_groups": forbidden_groups,
        "forbidden_identifiers": forbidden_identifiers,
        "forbidden_targets": forbidden_targets,
        "forbidden_values": forbidden_values,
        "raw_training_blobs": raw_training_blobs,
        "p_record_count": len(p_rows),
        "b5_record_count": len(b5_rows),
        "hashes": hashes,
    }


def generate_g4_suite_and_manifest(
    dataset_relpath="data/dev/g4_closed_holdout_iter9.jsonl",
    workspace_root=ROOT,
):
    workspace_root = Path(workspace_root).resolve()
    historical = _collect_historical_artifacts(workspace_root)

    cases = []
    group_records = []
    in_domain_case_ids = []
    cross_domain_case_ids = []
    in_domain_groups = []
    cross_domain_groups = []

    for domain in DOMAINS:
        primary, _ = DOMAIN_TOOLS[domain]
        slots = _IN_DOMAIN_SLOTS if domain in IN_DOMAIN else _CROSS_DOMAIN_JOBS_SLOTS
        partition = (
            "g4_in_domain_unseen_templates"
            if domain in IN_DOMAIN
            else "g4_cross_domain_heldout_jobs"
        )
        for slot_idx_1based, slot in enumerate(slots, start=1):
            (
                category,
                slot_code,
                fault_subtype,
                transient_remaining,
                polls_remaining,
                unavailable,
                id_mode,
                has_prefix,
                gh_tag,
            ) = slot
            template_group = f"g4h9-{domain}-{category}-{slot_code}"
            case_id = f"{template_group}-01"
            valid_id, alt_id, target, value = _g4_entity_strings(domain, slot_idx_1based, gh_tag)

            if domain == "files":
                for pval in (valid_id, alt_id):
                    if posixpath.normpath(pval) != pval or not pval.startswith("/"):
                        raise ValueError(f"unnormalized POSIX file path: {pval}")

            request = {"domain": domain, "target": target}
            if id_mode == "valid":
                request["identifier"] = valid_id
            elif id_mode == "alt":
                request["identifier"] = alt_id
            elif id_mode != "omitted":
                raise ValueError(f"unknown id_mode: {id_mode}")

            case = {
                "case_id": case_id,
                "category": category,
                "domain": domain,
                "evidence_class": EVIDENCE_CLASS,
                "fault_subtype": fault_subtype,
                "hidden": {
                    "polls_remaining": polls_remaining,
                    "transient_remaining": transient_remaining,
                    "unavailable": unavailable,
                    "valid_identifier": valid_id,
                    "value": value,
                },
                "request": request,
                "schema_version": 1,
                "split": SPLIT_NAME,
                "template_group": template_group,
            }
            if has_prefix:
                case["fixed_prefix"] = [tool_call(primary, identifier=request["identifier"])]

            cases.append(case)
            if domain in IN_DOMAIN:
                in_domain_case_ids.append(case_id)
                in_domain_groups.append(template_group)
            else:
                cross_domain_case_ids.append(case_id)
                cross_domain_groups.append(template_group)

            group_records.append(
                {
                    "template_group": template_group,
                    "case_id": case_id,
                    "case_ids": [case_id],
                    "domain": domain,
                    "category": category,
                    "slot_code": slot_code,
                    "fault_subtype": fault_subtype,
                    "partition": partition,
                    "has_fixed_prefix": has_prefix,
                    "completable": category != "unrecoverable",
                }
            )

    jsonl_bytes = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in cases
    ).encode("utf-8")
    dataset_sha256 = sha256_bytes(jsonl_bytes)

    e2e_case_ids = [c["case_id"] for c in cases]
    prefix_case_ids = [c["case_id"] for c in cases if c.get("fixed_prefix")]

    e2e_schedules, e2e_schedules_by_id = _build_seed_schedules(e2e_case_ids, E2E_SCHEDULE_STARTS)
    prefix_schedules, prefix_schedules_by_id = _build_seed_schedules(
        prefix_case_ids, PREFIX_SCHEDULE_STARTS
    )

    manifest = {
        "schema_version": 1,
        "manifest_id": "g4_holdout_manifest_ITER009",
        "iteration": "ITER-009",
        "evidence_class": "analysis",
        "dataset_evidence_class": EVIDENCE_CLASS,
        "dataset_path": dataset_relpath,
        "dataset_sha256": dataset_sha256,
        "split": SPLIT_NAME,
        "generator_script": "scripts/build_g4_closed_holdout.py",
        "detector_v2_version": DETECTOR_V2_VERSION,
        "training_eligible": False,
        "tuning_eligible": False,
        "governance": {
            "single_shot_closed_holdout": True,
            "jobs_domain_excluded_from_g3_training": True,
            "entity_namespace_floor": 75000,
            "forbidden_arm_substrings_excluded": list(FORBIDDEN_SUBSTRINGS_CASE_INSENSITIVE),
            "note": (
                "Formal G4 closed holdout suite under EVALUATION_SPEC.md §5 and Proposal §7-§8. "
                "36 pairwise-disjoint unseen template_groups (24 in-domain orders+files + "
                "12 cross-domain zero-shot held-out jobs), containing 36 E2E cases and 18 "
                "curated 1-step fixed_failure_prefix cases (12 recoverable + 6 retry_required). "
                "Strictly prohibited from training, prompt iteration, or hyperparameter selection."
            ),
        },
        "summary": {
            "total_e2e_cases": len(cases),
            "total_template_groups": len(group_records),
            "completable_e2e_cases": sum(c["category"] != "unrecoverable" for c in cases),
            "unrecoverable_e2e_cases": sum(c["category"] == "unrecoverable" for c in cases),
            "total_prefix_cases": len(prefix_case_ids),
            "prefix_recoverable_cases": sum(
                c["category"] == "recoverable" for c in cases if c.get("fixed_prefix")
            ),
            "prefix_retry_required_cases": sum(
                c["category"] == "retry_required" for c in cases if c.get("fixed_prefix")
            ),
            "in_domain_e2e_cases": len(in_domain_case_ids),
            "cross_domain_e2e_cases": len(cross_domain_case_ids),
            "in_domain_prefix_cases": sum(
                c["domain"] in IN_DOMAIN for c in cases if c.get("fixed_prefix")
            ),
            "cross_domain_prefix_cases": sum(
                c["domain"] in CROSS_DOMAIN for c in cases if c.get("fixed_prefix")
            ),
            "domains": list(DOMAINS),
            "in_domain_domains": list(IN_DOMAIN),
            "cross_domain_held_out_domain": "jobs",
            "categories": list(CATEGORIES),
            "domain_counts": {d: sum(c["domain"] == d for c in cases) for d in DOMAINS},
            "category_counts": {cat: sum(c["category"] == cat for c in cases) for cat in CATEGORIES},
            "per_domain_category_counts": {
                d: {cat: sum(c["domain"] == d and c["category"] == cat for c in cases) for cat in CATEGORIES}
                for d in DOMAINS
            },
        },
        "disjointness_checks": {
            "zero_case_id_overlap_with_all_prior": True,
            "zero_template_group_overlap_with_all_prior": True,
            "zero_identifier_overlap_with_all_prior": True,
            "zero_target_overlap_with_all_prior": True,
            "zero_value_overlap_with_all_prior": True,
            "zero_raw_substring_overlap_with_exp062_dpo_records": True,
            "exp062_p_dpo_records_checked": historical["p_record_count"],
            "exp062_b5_dpo_records_checked": historical["b5_record_count"],
            **historical["hashes"],
        },
        "partitions": {
            "g4_in_domain_unseen_templates": {
                "role": "formal_closed_holdout_in_domain_unseen_templates",
                "domains": ["orders", "files"],
                "training_eligible": False,
                "template_group_count": len(in_domain_groups),
                "e2e_case_count": len(in_domain_case_ids),
                "prefix_case_count": sum(
                    c["domain"] in IN_DOMAIN for c in cases if c.get("fixed_prefix")
                ),
                "template_groups": in_domain_groups,
                "case_ids": in_domain_case_ids,
                "prefix_case_ids": [
                    c["case_id"] for c in cases if c["domain"] in IN_DOMAIN and c.get("fixed_prefix")
                ],
            },
            "g4_cross_domain_heldout_jobs": {
                "role": "formal_closed_holdout_cross_domain_zero_shot_jobs",
                "domains": ["jobs"],
                "excluded_from_g3_training": True,
                "training_eligible": False,
                "template_group_count": len(cross_domain_groups),
                "e2e_case_count": len(cross_domain_case_ids),
                "prefix_case_count": sum(
                    c["domain"] in CROSS_DOMAIN for c in cases if c.get("fixed_prefix")
                ),
                "template_groups": cross_domain_groups,
                "case_ids": cross_domain_case_ids,
                "prefix_case_ids": [
                    c["case_id"] for c in cases if c["domain"] in CROSS_DOMAIN and c.get("fixed_prefix")
                ],
            },
        },
        "g4_e2e_case_ids": e2e_case_ids,
        "g4_prefix_case_ids": prefix_case_ids,
        "e2e_seed_schedules": e2e_schedules,
        "e2e_seed_schedules_by_id": e2e_schedules_by_id,
        "prefix_seed_schedules": prefix_schedules,
        "prefix_seed_schedules_by_id": prefix_schedules_by_id,
        "template_groups": group_records,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    return cases, manifest, jsonl_bytes, manifest_bytes


def verify_g4_suite_and_manifest(cases, manifest, workspace_root=ROOT):
    workspace_root = Path(workspace_root).resolve()
    historical = _collect_historical_artifacts(workspace_root)

    if len(cases) != 36:
        raise ValueError(f"expected 36 G4 cases, got {len(cases)}")
    if len({c["case_id"] for c in cases}) != 36:
        raise ValueError("duplicate G4 case_id detected")
    if len({c["template_group"] for c in cases}) != 36:
        raise ValueError("expected 36 pairwise-disjoint G4 template_groups")
    if any(not c["template_group"].startswith("g4h9-") for c in cases):
        raise ValueError("all G4 template_groups must start with g4h9-")
    if manifest.get("training_eligible") is not False or manifest.get("tuning_eligible") is not False:
        raise ValueError("G4 manifest must forbid training and tuning")

    # Verify exact domain/category/fault_subtype structure.
    expected_cat_per_domain = {
        "normal": 2,
        "recoverable": 4,
        "retry_required": 2,
        "clarification": 2,
        "unrecoverable": 2,
    }
    for domain in DOMAINS:
        d_cases = [c for c in cases if c["domain"] == domain]
        if len(d_cases) != 12:
            raise ValueError(f"expected 12 cases in {domain}, got {len(d_cases)}")
        for cat, expected_n in expected_cat_per_domain.items():
            cell = [c for c in d_cases if c["category"] == cat]
            if len(cell) != expected_n:
                raise ValueError(f"expected {expected_n} cases in {(domain, cat)}, got {len(cell)}")

        subtypes_by_cat = {
            cat: [c["fault_subtype"] for c in d_cases if c["category"] == cat]
            for cat in CATEGORIES
        }
        if subtypes_by_cat["normal"] != ["direct_ok_h1", "direct_ok_h2"]:
            raise ValueError(f"unexpected normal subtypes in {domain}: {subtypes_by_cat['normal']}")
        if subtypes_by_cat["unrecoverable"] != ["unavailable_valid_id_h1", "unavailable_alt_id_h2"]:
            raise ValueError(f"unexpected unrecoverable subtypes in {domain}: {subtypes_by_cat['unrecoverable']}")
        if domain in IN_DOMAIN:
            if subtypes_by_cat["recoverable"] != [
                "stale_id_locate",
                "stale_id_locate",
                "stale_id_locate_plus_1timeout",
                "stale_id_locate_plus_2timeout",
            ]:
                raise ValueError(f"unexpected in-domain recoverable subtypes: {subtypes_by_cat['recoverable']}")
            if subtypes_by_cat["retry_required"] != [
                "transient_1_retry",
                "transient_2_retries_boundary",
            ]:
                raise ValueError(f"unexpected in-domain retry subtypes: {subtypes_by_cat['retry_required']}")
            if subtypes_by_cat["clarification"] != [
                "missing_id_direct",
                "missing_id_plus_1timeout",
            ]:
                raise ValueError(f"unexpected in-domain clarification subtypes: {subtypes_by_cat['clarification']}")
        else:
            if subtypes_by_cat["recoverable"] != [
                "stale_id_locate",
                "stale_id_locate",
                "stale_id_locate_plus_1poll",
                "stale_id_locate_plus_1poll",
            ]:
                raise ValueError(f"unexpected jobs recoverable subtypes: {subtypes_by_cat['recoverable']}")
            if subtypes_by_cat["retry_required"] != [
                "job_poll_1_step",
                "job_poll_2_steps",
            ]:
                raise ValueError(f"unexpected jobs retry subtypes: {subtypes_by_cat['retry_required']}")
            if subtypes_by_cat["clarification"] != [
                "missing_id_direct",
                "missing_id_plus_1poll",
            ]:
                raise ValueError(f"unexpected jobs clarification subtypes: {subtypes_by_cat['clarification']}")

    # Verify schema, namespace >= 75000, forbidden substrings, POSIX paths, and zero leakage.
    seen_identifiers = set()
    seen_targets = set()
    seen_values = set()

    for c in cases:
        if (
            c["schema_version"] != 1
            or c["evidence_class"] != EVIDENCE_CLASS
            or c["split"] != SPLIT_NAME
        ):
            raise ValueError(f"schema/evidence/split mismatch on {c['case_id']}")

        primary, _ = DOMAIN_TOOLS[c["domain"]]
        valid_id = c["hidden"]["valid_identifier"]
        value = c["hidden"]["value"]
        target = c["request"]["target"]
        req_id = c["request"].get("identifier")

        if c["category"] == "clarification" and "identifier" in c["request"]:
            raise ValueError(f"clarification case must omit identifier: {c['case_id']}")
        if c["category"] != "clarification" and not req_id:
            raise ValueError(f"non-clarification case missing request identifier: {c['case_id']}")
        if (c["category"] == "unrecoverable") != bool(c["hidden"]["unavailable"]):
            raise ValueError(f"unavailable mismatch on {c['case_id']}")

        if c["category"] in ("recoverable", "retry_required"):
            expected_prefix = [tool_call(primary, identifier=req_id)]
            if c.get("fixed_prefix") != expected_prefix:
                raise ValueError(f"fixed_prefix mismatch on {c['case_id']}: {c.get('fixed_prefix')}")
        elif "fixed_prefix" in c:
            raise ValueError(f"unexpected fixed_prefix on {c['case_id']}")

        if c["domain"] == "files":
            for pval in filter(None, (valid_id, req_id)):
                if posixpath.normpath(pval) != pval or not pval.startswith("/"):
                    raise ValueError(f"unnormalized file path on {c['case_id']}: {pval}")

        # Namespace >= 75000 check on identifiers and values.
        for entity_str in filter(None, (valid_id, req_id, value)):
            nums = [int(m) for m in re.findall(r"\d{4,}", entity_str)]
            if not nums or any(n < 75000 for n in nums):
                raise ValueError(f"entity number below 75000 in {c['case_id']}: {entity_str}")

        # Forbidden arm/experiment substring check across all case strings.
        for s in filter(None, (c["case_id"], c["template_group"], valid_id, req_id, target, value)):
            low = s.lower()
            for bad in FORBIDDEN_SUBSTRINGS_CASE_INSENSITIVE:
                if bad in low:
                    raise ValueError(f"forbidden substring {bad!r} found in {s!r}")

        # Internal pairwise uniqueness across all 36 cases.
        if target in seen_targets or value in seen_values:
            raise ValueError(f"duplicate target or value inside G4 suite on {c['case_id']}")
        seen_targets.add(target)
        seen_values.add(value)
        case_ids_set = {valid_id} | ({req_id} if req_id else set())
        if seen_identifiers & case_ids_set:
            raise ValueError(f"duplicate identifier inside G4 suite on {c['case_id']}")
        seen_identifiers.update(case_ids_set)

        # Zero overlap with all historical files + EXP-062 training records.
        if c["case_id"] in historical["forbidden_case_ids"]:
            raise ValueError(f"case_id overlap with prior data: {c['case_id']}")
        if c["template_group"] in historical["forbidden_groups"]:
            raise ValueError(f"template_group overlap with prior data: {c['template_group']}")
        if case_ids_set & historical["forbidden_identifiers"]:
            raise ValueError(f"identifier overlap with prior data on {c['case_id']}")
        if target in historical["forbidden_targets"]:
            raise ValueError(f"target overlap with prior data on {c['case_id']}")
        if value in historical["forbidden_values"]:
            raise ValueError(f"value overlap with prior data on {c['case_id']}")
        for blob in historical["raw_training_blobs"]:
            for probe in (c["case_id"], c["template_group"], valid_id, target, value) + ((req_id,) if req_id else ()):
                if probe in blob:
                    raise ValueError(f"G4 string {probe!r} leaked into EXP-20261120-062 training JSONL")

        # Initial public_view non-leakage check.
        pub_str = json.dumps(Environment(c).public_view(), ensure_ascii=False)
        for secret in (c["case_id"], c["template_group"], c["fault_subtype"], value):
            if secret in pub_str:
                raise ValueError(f"public_view leaked {secret!r} on {c['case_id']}")
        if req_id != valid_id and valid_id in pub_str:
            raise ValueError(f"unrevealed valid_identifier leaked in initial public_view on {c['case_id']}")

    # Verify scripted_reference pass rates and 0% v1 + v2 loops in E2E and fixed_failure_prefix.
    e2e_pairs = [run_case(c, "scripted_reference", "end_to_end", TOOL_BUDGET, TURN_BUDGET) for c in cases]
    e2e_res = [p[0] for p in e2e_pairs]
    pfx_cases = [c for c in cases if c.get("fixed_prefix")]
    if len(pfx_cases) != 18:
        raise ValueError(f"expected 18 fixed_prefix cases, got {len(pfx_cases)}")
    pfx_pairs = [
        run_case(c, "scripted_reference", "fixed_failure_prefix", TOOL_BUDGET, TURN_BUDGET)
        for c in pfx_cases
    ]
    pfx_res = [p[0] for p in pfx_pairs]

    by_mode = {(g["policy"], g["mode"]): g for g in summarize(e2e_res + pfx_res)["groups"]}
    ref_e2e = by_mode[("scripted_reference", "end_to_end")]
    ref_pfx = by_mode[("scripted_reference", "fixed_failure_prefix")]

    if ref_e2e["task_completion"] != {"numerator": 30, "denominator": 30, "value": 1.0}:
        raise ValueError(f"E2E task_completion mismatch: {ref_e2e['task_completion']}")
    if ref_e2e["normal_completion"] != {"numerator": 6, "denominator": 6, "value": 1.0}:
        raise ValueError(f"E2E normal_completion mismatch: {ref_e2e['normal_completion']}")
    if ref_e2e["necessary_retry_success"] != {"numerator": 6, "denominator": 6, "value": 1.0}:
        raise ValueError(f"E2E necessary_retry_success mismatch: {ref_e2e['necessary_retry_success']}")
    if ref_e2e["correct_termination"] != {"numerator": 6, "denominator": 6, "value": 1.0}:
        raise ValueError(f"E2E correct_termination mismatch: {ref_e2e['correct_termination']}")
    if ref_e2e["premature_abandonment"]["numerator"] != 0 or ref_e2e["false_success"]["numerator"] != 0:
        raise ValueError("E2E scripted_reference triggered abandonment or false_success")
    if ref_e2e["invalid_loop_rate"]["numerator"] != 0:
        raise ValueError("E2E scripted_reference triggered v1 invalid loop")

    if ref_pfx["error_recovery"] != {"numerator": 12, "denominator": 12, "value": 1.0}:
        raise ValueError(f"Prefix error_recovery mismatch: {ref_pfx['error_recovery']}")
    if ref_pfx["necessary_retry_success"] != {"numerator": 6, "denominator": 6, "value": 1.0}:
        raise ValueError(f"Prefix necessary_retry_success mismatch: {ref_pfx['necessary_retry_success']}")
    if ref_pfx["task_completion"] != {"numerator": 18, "denominator": 18, "value": 1.0}:
        raise ValueError(f"Prefix task_completion mismatch: {ref_pfx['task_completion']}")
    if ref_pfx["invalid_loop_rate"]["numerator"] != 0:
        raise ValueError("Prefix scripted_reference triggered v1 invalid loop")

    # Verify frozen loop_detector_v2 emits 0 flags across all 36 E2E and 18 prefix reference episodes.
    for res, trj in e2e_pairs:
        hist = [
            ev
            for ev in trj["final_snapshot"]["history"]
            if isinstance(ev.get("action"), dict) and ev["action"].get("type") == "tool"
        ]
        if detect_loops(hist) or detect_loops_v2(hist) or has_invalid_loop_v2(hist, start_index=0):
            raise ValueError(f"detect_loops_v2 flagged reference E2E episode {res['case_id']}")

    for c, (res, trj) in zip(pfx_cases, pfx_pairs):
        hist = [
            ev
            for ev in trj["final_snapshot"]["history"]
            if isinstance(ev.get("action"), dict) and ev["action"].get("type") == "tool"
        ]
        if has_invalid_loop_v2(hist, start_index=res["prefix_tool_attempts"]) or detect_loops_v2(hist):
            raise ValueError(f"detect_loops_v2 flagged reference prefix episode {res['case_id']}")

        # Verify public-only recovery step at prefix snapshot.
        probe_env = Environment(c)
        for act in c["fixed_prefix"]:
            probe_env.execute(act)
        if state_hash(probe_env.snapshot()) != res["prefix_snapshot_sha256"]:
            raise ValueError(f"prefix_snapshot_sha256 mismatch on {c['case_id']}")
        next_step = scripted_policy(probe_env.public_view(), "scripted_reference")
        val = validate_alternative(probe_env, next_step)
        legal_retry_step = (
            val["public_arguments_only"]
            and val["state_progress"]
            and val["response"].get("code") in {"TIMEOUT", "RUNNING"}
        )
        if not (val["locally_validated"] or legal_retry_step):
            raise ValueError(f"prefix snapshot lacks public-only validated step on {c['case_id']}: {val}")

    # Verify seed schedules are pairwise disjoint across all 6 schedules and >= 80000.
    intervals = []
    for sched in manifest["e2e_seed_schedules"] + manifest["prefix_seed_schedules"]:
        sid = sched["schedule_id"]
        for cid, seed in sched["case_seeds"].items():
            if type(seed) is not int or seed < 80000:
                raise ValueError(f"invalid G4 seed {seed} for {cid}@{sid}")
            intervals.append((seed, seed + TURN_BUDGET, f"{cid}@{sid}"))
    if len(intervals) != 3 * 36 + 3 * 18:
        raise ValueError(f"expected 162 scheduled trial intervals, got {len(intervals)}")
    intervals.sort()
    for idx in range(len(intervals) - 1):
        left, right = intervals[idx], intervals[idx + 1]
        if left[1] > right[0]:
            raise ValueError(f"overlapping G4 seed ranges: {left} vs {right}")


def main():
    archive_root = _resolve_archive_root(ROOT)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases-out",
        type=Path,
        default=ROOT / "data/dev/g4_closed_holdout_iter9.jsonl",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=archive_root / "data/processed/g4_holdout_manifest_ITER009.json",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow replacing existing output files with deterministic rebuild.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify existing files match deterministic build byte-for-byte without writing.",
    )
    args = parser.parse_args()

    try:
        rel_cases = str(args.cases_out.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        rel_cases = "data/dev/g4_closed_holdout_iter9.jsonl"

    cases, manifest, jsonl_bytes, manifest_bytes = generate_g4_suite_and_manifest(
        dataset_relpath=rel_cases,
        workspace_root=ROOT,
    )
    verify_g4_suite_and_manifest(cases, manifest, workspace_root=ROOT)

    if args.check:
        if not args.cases_out.is_file() or not args.manifest_out.is_file():
            raise FileNotFoundError("expected G4 suite or manifest file missing")
        if args.cases_out.read_bytes() != jsonl_bytes:
            raise ValueError("G4 cases JSONL differs from deterministic build")
        if args.manifest_out.read_bytes() != manifest_bytes:
            raise ValueError("G4 manifest JSON differs from deterministic build")
        print(
            json.dumps(
                {
                    "checked": True,
                    "e2e_cases": len(cases),
                    "prefix_cases": len(manifest["g4_prefix_case_ids"]),
                    "template_groups": len(manifest["template_groups"]),
                    "dataset_sha256": manifest["dataset_sha256"],
                    "manifest_sha256": sha256_bytes(manifest_bytes),
                }
            )
        )
        return 0

    for target in (args.cases_out, args.manifest_out):
        if target.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite {target} without --overwrite")
        target.parent.mkdir(parents=True, exist_ok=True)

    args.cases_out.write_bytes(jsonl_bytes)
    args.manifest_out.write_bytes(manifest_bytes)
    print(
        json.dumps(
            {
                "e2e_cases_written": len(cases),
                "prefix_cases_written": len(manifest["g4_prefix_case_ids"]),
                "template_groups": len(manifest["template_groups"]),
                "dataset_path": str(args.cases_out),
                "dataset_sha256": manifest["dataset_sha256"],
                "manifest_path": str(args.manifest_out),
                "manifest_sha256": sha256_bytes(manifest_bytes),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
