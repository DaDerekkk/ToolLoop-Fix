#!/usr/bin/env python3
"""Deterministic builder for ITER-007 180-case grouped synthetic_harness dev suite and split manifest."""
import argparse
import hashlib
import json
import posixpath
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from toolloop_fix.harness import DOMAIN_TOOLS, EVIDENCE_CLASS, Environment, run_case, summarize, tool_call

DOMAINS = ("orders", "files", "jobs")
IN_DOMAIN = ("orders", "files")
CROSS_DOMAIN = ("jobs",)
CATEGORIES = ("normal", "recoverable", "retry_required", "clarification", "unrecoverable")
CATEGORY_COHORTS = {
    "normal": "ca",
    "recoverable": "cb",
    "retry_required": "cc",
    "clarification": "cd",
    "unrecoverable": "ce",
}
FAMILIES = (1, 2, 3)
VARIANTS = (1, 2, 3, 4)

EVAL24_SEED_START = 6000
EVAL24_SEED_STEP = 32


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def sha256_file(path):
    return sha256_bytes(Path(path).read_bytes())


def _entity_strings(domain, category, family, variant, d_idx, c_idx):
    cohort = CATEGORY_COHORTS[category]
    cohort_up = cohort.upper()
    offset = d_idx * 300 + c_idx * 50 + family * 10 + variant
    num = 4000 + offset
    snum = 6000 + offset
    vnum = 8000 + offset

    if domain == "orders":
        if family == 1:
            valid_id = f"ORD-{cohort_up}1-{num}"
            alt_id = f"ORD-{cohort_up}1-{snum}"
            target = f"lookup_key:ord_ledger_{cohort}1_v{variant}"
            value = f"ord_status_cleared_{cohort}1_{vnum}"
        elif family == 2:
            valid_id = f"INV-{cohort_up}2-{num}"
            alt_id = f"INV-{cohort_up}2-{snum}"
            target = f"lookup_key:ord_waybill_{cohort}2_v{variant}"
            value = f"ord_status_settled_{cohort}2_{vnum}"
        else:
            valid_id = f"PO-{cohort_up}3-{num}"
            alt_id = f"PO-{cohort_up}3-{snum}"
            target = f"lookup_key:ord_manifest_{cohort}3_v{variant}"
            value = f"ord_status_verified_{cohort}3_{vnum}"
    elif domain == "files":
        if family == 1:
            valid_id = f"/vault_{cohort}1/rec_{num}.txt"
            alt_id = f"/vault_{cohort}1/rec_{snum}.txt"
            target = f"lookup_key:fil_index_{cohort}1_v{variant}"
            value = f"fil_digest_sha_{cohort}1_{vnum}"
        elif family == 2:
            valid_id = f"/ledger_{cohort}2/entry_{num}.json"
            alt_id = f"/ledger_{cohort}2/entry_{snum}.json"
            target = f"lookup_key:fil_catalog_{cohort}2_v{variant}"
            value = f"fil_payload_crc_{cohort}2_{vnum}"
        else:
            valid_id = f"/coldstore_{cohort}3/snap_{num}.log"
            alt_id = f"/coldstore_{cohort}3/snap_{snum}.log"
            target = f"lookup_key:fil_archive_{cohort}3_v{variant}"
            value = f"fil_extract_tag_{cohort}3_{vnum}"
    elif domain == "jobs":
        if family == 1:
            valid_id = f"JOB-{cohort_up}1-{num}"
            alt_id = f"JOB-{cohort_up}1-{snum}"
            target = f"lookup_key:job_handle_{cohort}1_v{variant}"
            value = f"job_output_rows_{cohort}1_{vnum}"
        elif family == 2:
            valid_id = f"TSK-{cohort_up}2-{num}"
            alt_id = f"TSK-{cohort_up}2-{snum}"
            target = f"lookup_key:job_ticket_{cohort}2_v{variant}"
            value = f"job_output_checksum_{cohort}2_{vnum}"
        else:
            valid_id = f"WRK-{cohort_up}3-{num}"
            alt_id = f"WRK-{cohort_up}3-{snum}"
            target = f"lookup_key:job_dispatch_{cohort}3_v{variant}"
            value = f"job_output_digest_{cohort}3_{vnum}"
    else:
        raise ValueError(f"unsupported domain: {domain}")
    return valid_id, alt_id, target, value


def _family_spec(domain, category, family):
    """Return (fault_subtype, transient_remaining, polls_remaining, unavailable, id_mode, has_prefix, partition, eval24_slot)."""
    is_jobs = domain == "jobs"
    in_domain = domain in IN_DOMAIN

    if category == "normal":
        subtype = f"direct_ok_f{family}"
        selected = family == 1
        eval24_slot = "normal" if selected else None
        transient, polls, unavail, id_mode, has_prefix = 0, 0, False, "valid", False
    elif category == "recoverable":
        has_prefix = True
        id_mode = "alt"
        unavail = False
        if family == 1:
            subtype = "stale_id_locate"
            transient, polls = 0, 0
            selected = True
            eval24_slot = "recoverable_stale_id_locate"
        elif family == 2:
            subtype = "stale_id_locate_plus_1timeout"
            transient, polls = 1, 0
            selected = not is_jobs
            eval24_slot = "recoverable_compound" if selected else None
        else:
            if is_jobs:
                subtype = "stale_id_locate_plus_1poll"
                transient, polls = 0, 1
                selected = True
                eval24_slot = "recoverable_compound"
            else:
                subtype = "stale_id_locate_plus_2timeout"
                transient, polls = 2, 0
                selected = False
                eval24_slot = None
    elif category == "retry_required":
        has_prefix = True
        id_mode = "valid"
        unavail = False
        if is_jobs:
            if family == 1:
                subtype = "job_poll_1_step"
                transient, polls = 0, 1
                selected = True
                eval24_slot = "retry_required_1_step"
            elif family == 2:
                subtype = "job_poll_2_steps"
                transient, polls = 0, 2
                selected = False
                eval24_slot = None
            else:
                subtype = "job_poll_3_steps_boundary"
                transient, polls = 0, 3
                selected = True
                eval24_slot = "retry_required_boundary"
        else:
            if family == 1:
                subtype = "transient_1_retry"
                transient, polls = 1, 0
                selected = True
                eval24_slot = "retry_required_1_step"
            elif family == 2:
                subtype = "transient_2_retries_boundary"
                transient, polls = 2, 0
                selected = True
                eval24_slot = "retry_required_boundary"
            else:
                subtype = "transient_1_retry_alt_schema"
                transient, polls = 1, 0
                selected = False
                eval24_slot = None
    elif category == "clarification":
        has_prefix = False
        id_mode = "omitted"
        unavail = False
        if family == 1:
            subtype = "missing_id_direct"
            transient, polls = 0, 0
            selected = True
            eval24_slot = "clarification_direct"
        elif family == 2:
            subtype = "missing_id_plus_1timeout"
            transient, polls = 1, 0
            selected = False
            eval24_slot = None
        else:
            if is_jobs:
                subtype = "missing_id_plus_1poll"
                transient, polls = 0, 1
            else:
                subtype = "missing_id_plus_2timeout"
                transient, polls = 2, 0
            selected = True
            eval24_slot = "clarification_compound"
    elif category == "unrecoverable":
        has_prefix = False
        transient, polls = 0, 0
        unavail = True
        # Note on harness.py lines 135-138: Environment checks state["unavailable"]
        # BEFORE identifier != state["valid_identifier"] on primary tool calls, so
        # primary(alt_id) when unavailable=True immediately returns RESOURCE_UNAVAILABLE.
        if family == 1:
            subtype = "unavailable_valid_id_f1"
            id_mode = "valid"
            selected = True
            eval24_slot = "unrecoverable"
        elif family == 2:
            subtype = "unavailable_alt_id_f2"
            id_mode = "alt"
            selected = False
            eval24_slot = None
        else:
            subtype = "unavailable_archive_id_f3"
            id_mode = "valid"
            selected = False
            eval24_slot = None
    else:
        raise ValueError(f"unsupported category: {category}")

    if in_domain:
        partition = "dev_InDomain_selection" if selected else "dev_InDomain_candidate_pool"
    else:
        partition = "dev_CrossDomain_diagnostic" if selected else "dev_CrossDomain_reserved"

    return {
        "fault_subtype": subtype,
        "transient_remaining": transient,
        "polls_remaining": polls,
        "unavailable": unavail,
        "id_mode": id_mode,
        "has_prefix": has_prefix,
        "partition": partition,
        "eval24_slot": eval24_slot,
    }


def generate_suite_and_manifest(dataset_relpath="data/dev/grouped_dev_suite_iter7.jsonl"):
    cases = []
    group_records = []
    partition_groups = {
        "dev_InDomain_candidate_pool": [],
        "dev_InDomain_selection": [],
        "dev_CrossDomain_diagnostic": [],
        "dev_CrossDomain_reserved": [],
    }
    partition_cases = {key: [] for key in partition_groups}
    eval24_candidates = []

    for d_idx, domain in enumerate(DOMAINS):
        primary, _ = DOMAIN_TOOLS[domain]
        for c_idx, category in enumerate(CATEGORIES):
            for family in FAMILIES:
                spec = _family_spec(domain, category, family)
                template_group = f"iter7-{domain}-{category}-f{family}"
                partition = spec["partition"]
                partition_groups[partition].append(template_group)
                group_case_ids = []

                for variant in VARIANTS:
                    case_id = f"gdev7-{domain}-{category}-f{family}-{variant:02d}"
                    valid_id, alt_id, target, value = _entity_strings(
                        domain, category, family, variant, d_idx, c_idx
                    )
                    if domain == "files":
                        for path_val in (valid_id, alt_id):
                            if posixpath.normpath(path_val) != path_val or not path_val.startswith("/"):
                                raise ValueError(f"unnormalized file path: {path_val}")

                    request = {"domain": domain, "target": target}
                    if spec["id_mode"] == "valid":
                        request["identifier"] = valid_id
                    elif spec["id_mode"] == "alt":
                        request["identifier"] = alt_id
                    elif spec["id_mode"] != "omitted":
                        raise ValueError(f"unknown id_mode: {spec['id_mode']}")

                    case = {
                        "case_id": case_id,
                        "category": category,
                        "domain": domain,
                        "evidence_class": EVIDENCE_CLASS,
                        "fault_subtype": spec["fault_subtype"],
                        "hidden": {
                            "polls_remaining": spec["polls_remaining"],
                            "transient_remaining": spec["transient_remaining"],
                            "unavailable": spec["unavailable"],
                            "valid_identifier": valid_id,
                            "value": value,
                        },
                        "request": request,
                        "schema_version": 1,
                        "split": "dev",
                        "template_group": template_group,
                    }
                    if spec["has_prefix"]:
                        case["fixed_prefix"] = [tool_call(primary, identifier=request["identifier"])]

                    cases.append(case)
                    group_case_ids.append(case_id)
                    partition_cases[partition].append(case_id)

                    if spec["eval24_slot"] is not None and variant == 1:
                        eval24_candidates.append({
                            "case_id": case_id,
                            "domain": domain,
                            "category": category,
                            "family": family,
                            "fault_subtype": spec["fault_subtype"],
                            "eval24_slot": spec["eval24_slot"],
                            "template_group": template_group,
                            "partition": partition,
                        })

                group_records.append({
                    "template_group": template_group,
                    "domain": domain,
                    "category": category,
                    "family": family,
                    "fault_subtype": spec["fault_subtype"],
                    "partition": partition,
                    "eval24_slot": spec["eval24_slot"],
                    "case_ids": group_case_ids,
                })

    jsonl_bytes = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in cases
    ).encode("utf-8")
    dataset_sha256 = sha256_bytes(jsonl_bytes)

    eval24_case_ids = []
    eval24_case_seeds = {}
    eval24_details = []
    for idx, entry in enumerate(eval24_candidates):
        seed = EVAL24_SEED_START + idx * EVAL24_SEED_STEP
        cid = entry["case_id"]
        eval24_case_ids.append(cid)
        eval24_case_seeds[cid] = seed
        eval24_details.append({
            **entry,
            "case_seed": seed,
            "seed_range_turn16": [seed, seed + 15],
        })

    pilot_path = ROOT / "data/dev/pilot_cases.jsonl"
    iter6_path = ROOT / "data/dev/timeout_crossed_iter6.jsonl"

    slot_order = (
        "normal",
        "recoverable_stale_id_locate",
        "recoverable_compound",
        "retry_required_1_step",
        "retry_required_boundary",
        "clarification_direct",
        "clarification_compound",
        "unrecoverable",
    )

    manifest = {
        "schema_version": 1,
        "manifest_id": "grouped_split_manifest_ITER007",
        "iteration": "ITER-007",
        "evidence_class": "analysis",
        "dataset_evidence_class": EVIDENCE_CLASS,
        "dataset_path": dataset_relpath,
        "dataset_sha256": dataset_sha256,
        "generator_script": "scripts/build_grouped_dev_suite.py",
        "training_eligible": False,
        "formal_closed_holdout_unsealed": False,
        "gate_status": {
            "g1_dev_fixture_total_with_pilot15": len(cases) + 15,
            "g2_human_detector_and_semantic_audit_complete": False,
            "g3_training_authorized": False,
            "g4_closed_holdout_created_or_unsealed": False,
            "note": (
                "Development-only grouped split manifest under EVALUATION_SPEC.md §5. "
                "Preserves 1 case per distinct template_group across all 24 cases in "
                "mps_unseen_eval24_case_ids (Option A: 16 selection + 8 diagnostic groups). "
                "Not a formal closed holdout and not eligible for training."
            ),
        },
        "summary": {
            "total_cases": len(cases),
            "total_template_groups": len(group_records),
            "variants_per_template_group": len(VARIANTS),
            "domains": list(DOMAINS),
            "in_domain_domains": list(IN_DOMAIN),
            "cross_domain_held_out_domain": "jobs",
            "categories": list(CATEGORIES),
            "cases_per_domain_category_cell": len(FAMILIES) * len(VARIANTS),
            "families_per_domain_category_cell": len(FAMILIES),
            "partition_group_counts": {k: len(v) for k, v in partition_groups.items()},
            "partition_case_counts": {k: len(v) for k, v in partition_cases.items()},
        },
        "disjointness_checks": {
            "zero_template_group_overlap_across_partitions": True,
            "zero_identifier_overlap_across_partitions": True,
            "zero_target_overlap_across_partitions": True,
            "zero_value_overlap_across_partitions": True,
            "disjoint_from_pilot_cases_jsonl": True,
            "disjoint_from_timeout_crossed_iter6_jsonl": True,
            "pilot_cases_sha256": sha256_file(pilot_path),
            "timeout_crossed_iter6_sha256": sha256_file(iter6_path),
        },
        "partitions": {
            "dev_InDomain_candidate_pool": {
                "role": "in_domain_future_candidate_pool_dev_only",
                "domains": ["orders", "files"],
                "training_eligible": False,
                "template_group_count": len(partition_groups["dev_InDomain_candidate_pool"]),
                "case_count": len(partition_cases["dev_InDomain_candidate_pool"]),
                "template_groups": partition_groups["dev_InDomain_candidate_pool"],
                "case_ids": partition_cases["dev_InDomain_candidate_pool"],
            },
            "dev_InDomain_selection": {
                "role": "in_domain_unseen_template_selection_and_dev_eval",
                "domains": ["orders", "files"],
                "training_eligible": False,
                "template_group_count": len(partition_groups["dev_InDomain_selection"]),
                "case_count": len(partition_cases["dev_InDomain_selection"]),
                "template_groups": partition_groups["dev_InDomain_selection"],
                "case_ids": partition_cases["dev_InDomain_selection"],
            },
            "dev_CrossDomain_diagnostic": {
                "role": "cross_domain_held_out_diagnostic_dev_eval",
                "domains": ["jobs"],
                "training_eligible": False,
                "excluded_from_in_domain_training_pool": True,
                "template_group_count": len(partition_groups["dev_CrossDomain_diagnostic"]),
                "case_count": len(partition_cases["dev_CrossDomain_diagnostic"]),
                "template_groups": partition_groups["dev_CrossDomain_diagnostic"],
                "case_ids": partition_cases["dev_CrossDomain_diagnostic"],
            },
            "dev_CrossDomain_reserved": {
                "role": "cross_domain_held_out_reserved_dev_pool",
                "domains": ["jobs"],
                "training_eligible": False,
                "excluded_from_in_domain_training_pool": True,
                "template_group_count": len(partition_groups["dev_CrossDomain_reserved"]),
                "case_count": len(partition_cases["dev_CrossDomain_reserved"]),
                "template_groups": partition_groups["dev_CrossDomain_reserved"],
                "case_ids": partition_cases["dev_CrossDomain_reserved"],
            },
        },
        "mps_unseen_eval24_case_ids": eval24_case_ids,
        "mps_unseen_eval24_case_seeds": eval24_case_seeds,
        "mps_unseen_eval24_slice": {
            "case_count": len(eval24_case_ids),
            "distinct_template_group_count": len({row["template_group"] for row in eval24_details}),
            "seed_start": EVAL24_SEED_START,
            "seed_step": EVAL24_SEED_STEP,
            "seed_end": EVAL24_SEED_START + (len(eval24_case_ids) - 1) * EVAL24_SEED_STEP,
            "non_overlapping_with_0_to_5999": True,
            "source_partitions": ["dev_InDomain_selection", "dev_CrossDomain_diagnostic"],
            "domain_counts": {
                d: sum(row["domain"] == d for row in eval24_details) for d in DOMAINS
            },
            "category_counts": {
                c: sum(row["category"] == c for row in eval24_details) for c in CATEGORIES
            },
            "slot_counts": {
                s: sum(row["eval24_slot"] == s for row in eval24_details) for s in slot_order
            },
            "cases": eval24_details,
        },
        "template_groups": group_records,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return cases, manifest, jsonl_bytes, manifest_bytes


def verify_suite_and_manifest(cases, manifest):
    if len(cases) != 180:
        raise ValueError(f"expected 180 cases, got {len(cases)}")
    if len({c["case_id"] for c in cases}) != 180:
        raise ValueError("duplicate case_id detected")
    if len({c["template_group"] for c in cases}) != 45:
        raise ValueError("expected 45 distinct template_groups")
    if manifest["training_eligible"] is not False or manifest["formal_closed_holdout_unsealed"] is not False:
        raise ValueError("training_eligible and formal_closed_holdout_unsealed must both be False")

    # Verify scripted_reference and scripted_naive across both evaluation modes.
    e2e_ref = [run_case(c, "scripted_reference", "end_to_end")[0] for c in cases]
    pfx_cases = [c for c in cases if c.get("fixed_prefix")]
    if len(pfx_cases) != 72:
        raise ValueError(f"expected 72 fixed_prefix cases, got {len(pfx_cases)}")
    pfx_ref = [run_case(c, "scripted_reference", "fixed_failure_prefix")[0] for c in pfx_cases]
    e2e_naive = [run_case(c, "scripted_naive", "end_to_end")[0] for c in cases]
    pfx_naive = [run_case(c, "scripted_naive", "fixed_failure_prefix")[0] for c in pfx_cases]

    summary = {
        (g["policy"], g["mode"]): g
        for g in summarize(e2e_ref + pfx_ref + e2e_naive + pfx_naive)["groups"]
    }
    ref_e2e = summary[("scripted_reference", "end_to_end")]
    ref_pfx = summary[("scripted_reference", "fixed_failure_prefix")]
    if ref_e2e["task_completion"] != {"numerator": 144, "denominator": 144, "value": 1.0}:
        raise ValueError(f"unexpected e2e task_completion: {ref_e2e['task_completion']}")
    if ref_pfx["error_recovery"] != {"numerator": 36, "denominator": 36, "value": 1.0}:
        raise ValueError(f"unexpected pfx error_recovery: {ref_pfx['error_recovery']}")
    if ref_e2e["necessary_retry_success"] != {"numerator": 36, "denominator": 36, "value": 1.0}:
        raise ValueError(f"unexpected e2e necessary_retry_success: {ref_e2e['necessary_retry_success']}")
    if ref_e2e["correct_termination"] != {"numerator": 36, "denominator": 36, "value": 1.0}:
        raise ValueError(f"unexpected e2e correct_termination: {ref_e2e['correct_termination']}")
    if ref_e2e["invalid_loop_rate"]["numerator"] != 0 or ref_pfx["invalid_loop_rate"]["numerator"] != 0:
        raise ValueError("scripted_reference triggered invalid loop")

    # Verify zero partition leakage.
    by_id = {c["case_id"]: c for c in cases}
    part_names = list(manifest["partitions"])
    for i, left_name in enumerate(part_names):
        left_cases = [by_id[cid] for cid in manifest["partitions"][left_name]["case_ids"]]
        for right_name in part_names[i + 1:]:
            right_cases = [by_id[cid] for cid in manifest["partitions"][right_name]["case_ids"]]
            for extractor in (
                lambda c: {c["template_group"]},
                lambda c: {c["hidden"]["valid_identifier"]} | ({c["request"]["identifier"]} if "identifier" in c["request"] else set()),
                lambda c: {c["request"]["target"]},
                lambda c: {c["hidden"]["value"]},
            ):
                left_vals = set().union(*(extractor(c) for c in left_cases))
                right_vals = set().union(*(extractor(c) for c in right_cases))
                if left_vals & right_vals:
                    raise ValueError(f"partition overlap between {left_name} and {right_name}")

    # Quick public_view non-leakage check on all 180 cases.
    for case in cases:
        view_str = json.dumps(Environment(case).public_view(), ensure_ascii=False)
        if case["case_id"] in view_str or case["hidden"]["value"] in view_str:
            raise ValueError(f"public_view leak on {case['case_id']}")
        if case["request"].get("identifier") != case["hidden"]["valid_identifier"]:
            if case["hidden"]["valid_identifier"] in view_str:
                raise ValueError(f"unrevealed valid_identifier leaked on {case['case_id']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases-out",
        type=Path,
        default=ROOT / "data/dev/grouped_dev_suite_iter7.jsonl",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=ROOT / "data/processed/grouped_split_manifest_ITER007.json",
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
        rel_cases = "data/dev/grouped_dev_suite_iter7.jsonl"

    cases, manifest, jsonl_bytes, manifest_bytes = generate_suite_and_manifest(rel_cases)
    verify_suite_and_manifest(cases, manifest)

    if args.check:
        if not args.cases_out.is_file() or not args.manifest_out.is_file():
            raise FileNotFoundError("expected suite or manifest file missing")
        if args.cases_out.read_bytes() != jsonl_bytes:
            raise ValueError("cases JSONL differs from deterministic build")
        if args.manifest_out.read_bytes() != manifest_bytes:
            raise ValueError("manifest JSON differs from deterministic build")
        print(json.dumps({
            "checked": True,
            "cases": len(cases),
            "template_groups": len(manifest["template_groups"]),
            "dataset_sha256": manifest["dataset_sha256"],
            "manifest_sha256": sha256_bytes(manifest_bytes),
        }))
        return 0

    for target in (args.cases_out, args.manifest_out):
        if target.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite {target} without --overwrite")
        target.parent.mkdir(parents=True, exist_ok=True)

    args.cases_out.write_bytes(jsonl_bytes)
    args.manifest_out.write_bytes(manifest_bytes)
    print(json.dumps({
        "cases_written": len(cases),
        "template_groups": len(manifest["template_groups"]),
        "eval24_cases": len(manifest["mps_unseen_eval24_case_ids"]),
        "dataset_path": str(args.cases_out),
        "dataset_sha256": manifest["dataset_sha256"],
        "manifest_path": str(args.manifest_out),
        "manifest_sha256": sha256_bytes(manifest_bytes),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
