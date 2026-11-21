#!/usr/bin/env python3
"""Read-only ITER-009 G3/G4 statistical comparator and honest hypothesis verdict evaluator.

Stdlib + pure-Python `toolloop_fix.harness` / `toolloop_fix.loop_detector_v2` only (zero model loading).
Verifies run manifests and artifact SHA256s across:
  - `dev_selection_runs`: paired `eval24` E2E selection runs (`B0..B5`, `P`, `P_no_retry`, `P_no_normal`)
  - `g4_e2e_runs`: G4 36-case closed holdout E2E runs (+ optional `multi_train_seed_runs`)
  - `g4_prefix_runs`: G4 18-case closed holdout `fixed_failure_prefix` runs (+ optional `multi_train_seed_prefix_runs`)
"""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from toolloop_fix.harness import detect_loops, ratio
from toolloop_fix.loop_detector_v2 import DETECTOR_V2_VERSION, detect_loops_v2

COMPARATOR_SCHEMA_VERSION = "iter9_g3_g4_comparison_v1"
FORBIDDEN_ML_MODULES = ("torch", "transformers", "peft", "trl", "accelerate")

UNTRAINED_ARMS = ("B0", "B1", "B2")
TRAINED_ARMS = ("B3", "B4", "B5", "P", "P_no_retry", "P_no_normal")
CANONICAL_ARM_ORDER = UNTRAINED_ARMS + TRAINED_ARMS
ALLOWED_ARMS = frozenset(CANONICAL_ARM_ORDER)

DOMAINS = ("orders", "files", "jobs")
IN_DOMAIN_SET = frozenset({"orders", "files"})
CROSS_DOMAIN_SET = frozenset({"jobs"})
CATEGORIES = ("normal", "recoverable", "retry_required", "clarification", "unrecoverable")

LOOP_V2_TYPES = (
    "identical_or_equivalent_repeat",
    "short_cycle_abab",
    "nonadjacent_or_multistep_repeat_without_progress",
    "retry_limit_violation",
)

SHARED_CORE_FILES = tuple(
    "toolloop_fix/" + name
    for name in (
        "harness.py",
        "model_protocol.py",
        "model_backend.py",
        "model_run.py",
        "calibrated_protocol.py",
        "baselines.py",
        "calibrated_run.py",
    )
)

REQUIRED_RUN_OUTPUTS = frozenset(
    {
        "config.json",
        "model_provenance.json",
        "case_results.jsonl",
        "metrics.json",
        "generations.jsonl",
        "trajectories.jsonl",
    }
)

SAMPLING_INVARIANT_KEYS = (
    "enable_thinking",
    "do_sample",
    "temperature",
    "top_p",
    "top_k",
    "min_p",
    "presence_penalty",
    "repetition_penalty",
    "max_new_tokens",
)

# Exact two-sided 95% Student-t critical values (t_{0.975, df}) for common df,
# backed by a pure-stdlib Simpson integration solver for arbitrary df >= 1.
T_CRIT_95_TABLE = {
    1: 12.706204736174936,
    2: 4.302652729749557,
    3: 3.182446305283708,
    4: 2.7764451051977934,
    5: 2.5705818356362897,
    6: 2.4469118511449692,
    7: 2.3646242515927844,
    8: 2.306004135204166,
    9: 2.262157162798205,
    10: 2.228138851986273,
    11: 2.200985160091597,
    12: 2.178812829667229,
    13: 2.1603686564627924,
    14: 2.144786687917803,
    15: 2.1314495455597764,
    16: 2.1199052992212545,
    17: 2.1098155778333476,
    18: 2.1009220402410355,
    19: 2.0930240544083096,
    20: 2.0859634472658684,
    23: 2.06865761041903,
    29: 2.045229642132706,
    35: 2.030107928250273,
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


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


def _resolve_path(path_value, workspace_root=ROOT):
    p = Path(path_value)
    if p.is_absolute():
        return p.resolve()
    ws = Path(workspace_root).resolve()
    candidate_ws = ws / p
    if candidate_ws.exists():
        return candidate_ws.resolve()
    arch = _resolve_archive_root(ws)
    return (arch / p).resolve()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_jsonl(path):
    return [
        json.loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def t_crit_95(df):
    """Return exact two-sided 95% Student-t critical value for df >= 1 using stdlib math."""
    require(type(df) is int and df >= 1, f"df must be positive integer, got {df!r}")
    if df in T_CRIT_95_TABLE:
        return T_CRIT_95_TABLE[df]
    if df > 500:
        z = 1.959963984540054
        return z + (z ** 3 + z) / (4.0 * df) + (5 * z ** 5 + 16 * z ** 3 + 3 * z) / (96.0 * df * df)

    coeff = math.exp(math.lgamma((df + 1) / 2.0) - math.lgamma(df / 2.0)) / math.sqrt(df * math.pi)

    def pdf(x):
        return coeff * ((1.0 + (x * x) / df) ** (-0.5 * (df + 1)))

    def cdf(x, steps=2000):
        h = x / steps
        acc = pdf(0.0) + pdf(x)
        for idx in range(1, steps):
            acc += (4.0 if idx % 2 == 1 else 2.0) * pdf(idx * h)
        return 0.5 + acc * h / 3.0

    lo, hi = 1.95, 13.0
    for _ in range(70):
        mid = 0.5 * (lo + hi)
        if cdf(mid) < 0.975:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def sample_stats(values):
    """Compute mean, sample SD, SE, and two-sided 95% t-CI over a sequence of numeric values."""
    vals = [float(v) for v in values]
    n = len(vals)
    if n == 0:
        return {
            "n": 0,
            "mean": None,
            "sample_sd": None,
            "se": None,
            "df": None,
            "t_crit_95": None,
            "ci95": None,
            "min": None,
            "max": None,
            "values": [],
        }
    avg = sum(vals) / n
    if n >= 2:
        var = sum((x - avg) ** 2 for x in vals) / (n - 1)
        sd = math.sqrt(var)
        se = sd / math.sqrt(n)
        df = n - 1
        t_val = t_crit_95(df)
        half = t_val * se
        ci95 = [avg - half, avg + half]
    else:
        sd = None
        se = None
        df = None
        t_val = None
        ci95 = None
    return {
        "n": n,
        "mean": avg,
        "sample_sd": sd,
        "se": se,
        "df": df,
        "t_crit_95": t_val,
        "ci95": ci95,
        "min": min(vals),
        "max": max(vals),
        "values": vals,
    }


def _extract_actual_tool_history(trajectory):
    if "actual_tool_history" in trajectory and isinstance(trajectory["actual_tool_history"], list):
        return trajectory["actual_tool_history"]
    final_snap = trajectory.get("final_snapshot") or {}
    hist = final_snap.get("history")
    require(isinstance(hist, list), "trajectory lacks actual_tool_history and final_snapshot.history")
    return [
        ev
        for ev in hist
        if isinstance(ev, dict)
        and isinstance(ev.get("action"), dict)
        and ev["action"].get("type") == "tool"
    ]


def _extract_train_seed(ref, config, provenance, run_root, default_index=None):
    for source in (ref, config, provenance):
        if isinstance(source, dict) and source.get("train_seed") is not None:
            return source["train_seed"]
        for nested_key in ("adapter", "lora", "training"):
            nested = source.get(nested_key) if isinstance(source, dict) else None
            if isinstance(nested, dict):
                for k in ("train_seed", "seed"):
                    if nested.get(k) is not None:
                        return nested[k]
    for candidate_text in (
        str(config.get("policy_name", "")),
        str(run_root.name),
    ):
        match = re.search(r"(?:train_seed|seed|_s)(\d{2,6})\b", candidate_text)
        if match:
            return int(match.group(1))
    return f"run_{default_index}" if default_index is not None else "single"


def _verify_run_manifest_and_inventory(ref, workspace_root=ROOT):
    require(
        isinstance(ref, dict)
        and isinstance(ref.get("path"), str)
        and isinstance(ref.get("manifest_sha256"), str),
        f"invalid run reference: {ref!r}",
    )
    run_root = _resolve_path(ref["path"], workspace_root)
    manifest_path = run_root / "manifest.json"
    require(manifest_path.is_file(), f"missing manifest.json in {run_root}")
    actual_manifest_sha = sha256_file(manifest_path)
    require(
        actual_manifest_sha == ref["manifest_sha256"],
        f"manifest SHA256 mismatch for {run_root.name}: expected {ref['manifest_sha256']}, got {actual_manifest_sha}",
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest.get("kind") == "model_inference"
        and manifest.get("status") == "completed"
        and manifest.get("exit_code") == 0
        and manifest.get("evidence_valid") is True
        and not manifest.get("contract_errors"),
        f"run {run_root.name} is not a completed valid model_inference record",
    )

    for section, base_dir in (("inputs", run_root / "inputs"), ("outputs", run_root)):
        entries = manifest.get(section)
        require(isinstance(entries, dict) and entries, f"empty {section} in {run_root.name}")
        for rel_name, expected_sha in entries.items():
            require(not Path(rel_name).is_absolute(), f"absolute path in {run_root.name} {section}: {rel_name}")
            candidate = (base_dir / rel_name).resolve()
            candidate.relative_to(base_dir.resolve())
            require(
                candidate.is_file() and sha256_file(candidate) == expected_sha,
                f"artifact SHA256 mismatch in {run_root.name} ({section}): {rel_name}",
            )
        actual_files = {
            str(p.relative_to(base_dir))
            for p in base_dir.rglob("*")
            if p.is_file()
            and (
                section == "inputs"
                or (p.relative_to(base_dir).parts[0] != "inputs" and p.name != "manifest.json")
            )
        }
        require(
            actual_files == set(entries),
            f"file inventory mismatch in {run_root.name} ({section})",
        )

    if "artifact_sha256" in ref:
        require(isinstance(ref["artifact_sha256"], dict), "artifact_sha256 must be a dict")
        for out_name, pinned_sha in ref["artifact_sha256"].items():
            require(
                manifest["outputs"].get(out_name) == pinned_sha,
                f"pinned artifact_sha256 mismatch in {run_root.name} for {out_name}",
            )

    require(
        REQUIRED_RUN_OUTPUTS.issubset(manifest["outputs"]),
        f"missing required model_inference outputs in {run_root.name}",
    )
    require(
        sha256_file(run_root / "config.json") == manifest["config_sha256"],
        f"config_sha256 mismatch in {run_root.name}",
    )

    ws_harness = Path(workspace_root).resolve() / "toolloop_fix/harness.py"
    require(
        manifest["inputs"].get("toolloop_fix/harness.py") == sha256_file(ws_harness),
        f"harness.py SHA256 mismatch in {run_root.name}",
    )
    ws_det_v2 = Path(workspace_root).resolve() / "toolloop_fix/loop_detector_v2.py"
    if "toolloop_fix/loop_detector_v2.py" in manifest["inputs"] and ws_det_v2.is_file():
        require(
            manifest["inputs"]["toolloop_fix/loop_detector_v2.py"] == sha256_file(ws_det_v2),
            f"loop_detector_v2.py SHA256 mismatch in {run_root.name}",
        )

    config = json.loads((run_root / "config.json").read_text(encoding="utf-8"))
    provenance = json.loads((run_root / "model_provenance.json").read_text(encoding="utf-8"))
    require(
        provenance.get("evidence_class") == "model_inference"
        and provenance.get("model") == config.get("model")
        and provenance.get("sampling") == config.get("sampling")
        and provenance.get("model_files_verified") is True,
        f"model_provenance contract mismatch in {run_root.name}",
    )
    for ext in config.get("external_inputs", []):
        ext_file = run_root / "external_inputs" / (ext["name"] + ".json")
        require(
            ext_file.is_file() and sha256_file(ext_file) == ext["sha256"],
            f"external_input SHA256 mismatch in {run_root.name}: {ext['name']}",
        )
        if ext["name"] == "model_manifest":
            require(
                provenance.get("model_manifest_sha256") == ext["sha256"],
                f"model_manifest_sha256 mismatch in {run_root.name}",
            )

    return run_root, manifest, config, provenance


def _parse_base_and_schedule(row, default_schedule_id="s0"):
    raw_cid = row["case_id"]
    sid = row.get("schedule_id")
    base_cid = row.get("base_case_id") or row.get("base_prefix_id")
    if base_cid is None:
        if "@" in raw_cid:
            base_cid, inferred_sid = raw_cid.split("@", 1)
            sid = sid or inferred_sid
        else:
            base_cid = raw_cid
    if sid is None:
        if "@" in raw_cid:
            _, sid = raw_cid.split("@", 1)
        else:
            sid = default_schedule_id
    trial_key = row.get("trial_id") or (f"{base_cid}@{sid}" if "@" in raw_cid or "schedule_id" in row else raw_cid)
    return str(base_cid), str(sid), str(trial_key)


def _normalize_episode(arm_name, expected_mode, case_obj, res_row, trj_row, schedule_id):
    require(
        res_row.get("evidence_class") == "model_inference"
        and trj_row.get("evidence_class") == "model_inference",
        f"non-model_inference episode in arm {arm_name}",
    )
    require(
        res_row.get("mode") == expected_mode and trj_row.get("mode") == expected_mode,
        f"mode mismatch in arm {arm_name}: expected {expected_mode}, got {res_row.get('mode')}",
    )

    domain = case_obj["domain"]
    category = case_obj["category"]
    tgroup = case_obj["template_group"]
    completable = category != "unrecoverable"
    require(
        res_row["domain"] == domain
        and res_row["category"] == category
        and res_row["template_group"] == tgroup
        and bool(res_row["completable"]) == completable,
        f"case metadata mismatch on {case_obj['case_id']} in arm {arm_name}",
    )

    guard_blocks = int(res_row.get("guard_blocked_proposals", 0))
    if arm_name != "B2":
        require(
            guard_blocks == 0,
            f"Proposal §6 violation: arm {arm_name} had guard_blocked_proposals={guard_blocks} on {case_obj['case_id']}",
        )

    if expected_mode == "fixed_failure_prefix":
        default_pfx_len = len(case_obj.get("fixed_prefix", []))
        start_index = int(
            res_row.get(
                "prefix_actual_tool_attempts",
                res_row.get("prefix_tool_attempts", res_row.get("prefix_turns", default_pfx_len)),
            )
        )
        require(start_index >= 1, f"fixed_failure_prefix requires start_index >= 1 on {case_obj['case_id']}")
        goal_at_prefix = bool(res_row.get("goal_at_prefix", False))
    else:
        start_index = 0
        goal_at_prefix = False

    hist = _extract_actual_tool_history(trj_row)
    flags_v1 = [f for f in detect_loops(hist) if f["index"] >= start_index]
    flags_v2 = [f for f in detect_loops_v2(hist) if f["index"] >= start_index]

    if "has_invalid_loop_v2" in res_row:
        require(
            bool(res_row["has_invalid_loop_v2"]) == bool(flags_v2),
            f"recorded has_invalid_loop_v2 mismatch on {case_obj['case_id']} in arm {arm_name}",
        )

    unnecessary_clar_count = sum(
        isinstance(ev.get("response"), dict) and ev["response"].get("code") == "UNNECESSARY_CLARIFICATION"
        for ev in hist[start_index:]
    )

    exact_raw = bool(res_row["exact_grounded_structured_completion"])
    goal_raw = bool(res_row["tool_goal_attainment"])
    new_goal = (
        bool(res_row["new_goal_evidence"])
        if "new_goal_evidence" in res_row
        else bool(goal_raw and not goal_at_prefix)
    )
    exact_comp = bool(exact_raw and not goal_at_prefix)

    tool_attempts = int(res_row["tool_attempts"])
    prefix_tool_attempts = int(res_row.get("prefix_tool_attempts", start_index if expected_mode == "fixed_failure_prefix" else 0))
    cont_tool_attempts = int(
        res_row.get(
            "continuation_proposal_attempts",
            res_row.get("continuation_tool_attempts", tool_attempts - prefix_tool_attempts),
        )
    )
    in_tok = int(res_row.get("input_tokens", 0) or 0)
    out_tok = int(res_row.get("output_tokens", 0) or 0)

    type_set_v2 = sorted({f["type"] for f in flags_v2})
    type_counts_v2 = collections.Counter(f["type"] for f in flags_v2)

    return {
        "base_case_id": case_obj["case_id"],
        "schedule_id": schedule_id,
        "case_seed": int(res_row["case_seed"]),
        "domain": domain,
        "is_in_domain": domain in IN_DOMAIN_SET,
        "category": category,
        "fault_subtype": case_obj.get("fault_subtype", res_row.get("fault_subtype", category)),
        "template_group": tgroup,
        "completable": completable,
        "goal_at_prefix": goal_at_prefix,
        "exact_completion": exact_comp,
        "goal_attainment": new_goal if expected_mode == "fixed_failure_prefix" else goal_raw,
        "has_invalid_loop_v2": bool(flags_v2),
        "loop_types_v2": type_set_v2,
        "loop_flag_counts_v2": {t: int(type_counts_v2.get(t, 0)) for t in LOOP_V2_TYPES},
        "has_invalid_loop_v1": bool(flags_v1),
        "loop_types_v1": sorted({f["type"] for f in flags_v1}),
        "premature_abandonment": bool(res_row["premature_stop_without_goal_evidence"]),
        "unnecessary_clarification": bool(unnecessary_clar_count > 0),
        "premature_or_unnecessary_clarification": bool(
            completable and (res_row["premature_stop_without_goal_evidence"] or unnecessary_clar_count > 0)
        ),
        "false_success": bool(res_row["unsupported_finish_wrapper"]),
        "structured_finish_claimed": res_row.get("terminal_form") == "structured_finish",
        "natural_termination_unknown": bool(res_row["natural_termination_semantics_unknown"]),
        "stopped_after_unavailable": bool(res_row["stopped_after_public_unavailable"]),
        "exact_stop_reason_code": bool(res_row["exact_stop_reason_code"]),
        "budget_exhausted": bool(res_row["budget_exhausted"]),
        "tool_attempts": tool_attempts,
        "continuation_tool_attempts": cont_tool_attempts,
        "actual_tool_attempts": int(res_row.get("actual_tool_attempts", 0)),
        "guard_blocked_proposals": guard_blocks,
        "parser_error_turns": int(res_row.get("parser_error_turns", 0)),
        "turns": int(res_row["turns"]),
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "generation_seconds": float(res_row.get("generation_seconds", 0.0) or 0.0),
    }


def _load_arm_bundle(arm_name, ref_or_refs, expected_mode, workspace_root=ROOT, train_seed_idx=None):
    """Load an arm's run(s), supporting single-schedule, multiseed, or list-of-schedule refs."""
    refs = list(ref_or_refs) if isinstance(ref_or_refs, list) else [ref_or_refs]
    require(refs, f"empty run reference list for arm {arm_name}")

    schedules = collections.OrderedDict()
    seeds_by_schedule = {}
    run_ids = []
    run_paths = []
    manifest_shas = []
    dataset_by_id = None
    dataset_sha = None
    core_hashes = None
    base_config = None
    base_provenance = None
    resolved_train_seed = None

    for idx, ref in enumerate(refs):
        default_sid = ref.get("schedule_id", f"s{idx}" if len(refs) > 1 else "s0")
        run_root, manifest, config, provenance = _verify_run_manifest_and_inventory(ref, workspace_root)
        require(config.get("mode") == expected_mode, f"expected mode={expected_mode} in {run_root.name}")

        cases_rel = config["cases_path"]
        this_data_sha = manifest["inputs"][cases_rel]
        rows_data = read_jsonl(run_root / "inputs" / cases_rel)
        # Support both direct case fixtures (`g4_closed_holdout_iter9.jsonl`) and wrapped `original_case` fixtures.
        this_dataset = {}
        for d_row in rows_data:
            cid = d_row["case_id"]
            this_dataset[cid] = d_row["original_case"] if "original_case" in d_row else d_row

        if dataset_sha is None:
            dataset_sha = this_data_sha
            dataset_by_id = this_dataset
            core_hashes = {k: manifest["inputs"][k] for k in SHARED_CORE_FILES if k in manifest["inputs"]}
            base_config = config
            base_provenance = provenance
            resolved_train_seed = _extract_train_seed(ref, config, provenance, run_root, train_seed_idx)
        else:
            require(this_data_sha == dataset_sha, f"dataset SHA256 mismatch across schedule refs for {arm_name}")

        res_rows = read_jsonl(run_root / "case_results.jsonl")
        trj_rows = read_jsonl(run_root / "trajectories.jsonl")
        require(
            len(res_rows) == len(trj_rows) and len(res_rows) > 0,
            f"case_results/trajectories count mismatch in {run_root.name}",
        )

        trj_by_key = {}
        for t_row in trj_rows:
            b_cid, sid, t_key = _parse_base_and_schedule(t_row, default_sid)
            require((sid, b_cid) not in trj_by_key, f"duplicate trajectory {(sid, b_cid)} in {run_root.name}")
            trj_by_key[(sid, b_cid)] = (t_key, t_row)

        local_sids = []
        for r_row in res_rows:
            b_cid, sid, r_key = _parse_base_and_schedule(r_row, default_sid)
            require(b_cid in dataset_by_id, f"case {b_cid} missing from dataset in {run_root.name}")
            require((sid, b_cid) in trj_by_key, f"missing trajectory for {(sid, b_cid)} in {run_root.name}")
            t_key, t_row = trj_by_key[(sid, b_cid)]
            require(r_key == t_key, f"trial key mismatch {r_key} vs {t_key} in {run_root.name}")

            if sid not in local_sids:
                require(sid not in schedules, f"duplicate schedule_id {sid} across refs for arm {arm_name}")
                local_sids.append(sid)
                schedules[sid] = collections.OrderedDict()
                seeds_by_schedule[sid] = {}
            require(b_cid not in schedules[sid], f"duplicate base_case_id {b_cid} in schedule {sid} for {arm_name}")
            ep = _normalize_episode(arm_name, expected_mode, dataset_by_id[b_cid], r_row, t_row, sid)
            schedules[sid][b_cid] = ep
            seeds_by_schedule[sid][b_cid] = ep["case_seed"]

        run_ids.append(manifest["run_id"])
        run_paths.append(str(run_root))
        manifest_shas.append(sha256_file(run_root / "manifest.json"))

    schedule_ids = list(schedules.keys())
    cfg_cids = base_config.get("case_ids") if isinstance(base_config, dict) else None
    if isinstance(cfg_cids, list) and set(cfg_cids) == set(schedules[schedule_ids[0]].keys()):
        for sid in schedule_ids:
            schedules[sid] = collections.OrderedDict((cid, schedules[sid][cid]) for cid in cfg_cids)

    base_case_ids = list(schedules[schedule_ids[0]].keys())
    for sid in schedule_ids[1:]:
        require(
            list(schedules[sid].keys()) == base_case_ids,
            f"case ordering/set mismatch across schedules in arm {arm_name}",
        )

    all_episodes = [ep for sid in schedule_ids for ep in schedules[sid].values()]
    return {
        "arm": arm_name,
        "mode": expected_mode,
        "train_seed": resolved_train_seed,
        "run_ids": run_ids,
        "run_paths": run_paths,
        "manifest_sha256s": manifest_shas,
        "dataset_sha256": dataset_sha,
        "core_hashes": core_hashes,
        "config": base_config,
        "provenance": base_provenance,
        "schedule_ids": schedule_ids,
        "base_case_ids": base_case_ids,
        "schedules": schedules,
        "seeds_by_schedule": seeds_by_schedule,
        "all_episodes": all_episodes,
        "cases_by_id": {cid: dataset_by_id[cid] for cid in base_case_ids},
    }


def _verify_suite_pairing(
    bundles_by_arm,
    suite_label,
    expected_case_ids=None,
    expected_dataset_sha=None,
    strict_same_device=False,
):
    arms = list(bundles_by_arm.keys())
    require(arms, f"no arms loaded for {suite_label}")
    ref_bundle = bundles_by_arm[arms[0]]

    if expected_dataset_sha is not None:
        require(
            ref_bundle["dataset_sha256"] == expected_dataset_sha,
            f"{suite_label} dataset SHA256 mismatch against manifest",
        )
    if expected_case_ids is not None:
        require(
            set(ref_bundle["base_case_ids"]) == set(expected_case_ids)
            and len(ref_bundle["base_case_ids"]) == len(expected_case_ids),
            f"{suite_label} case_ids mismatch against manifest",
        )

    ref_cfg = ref_bundle["config"]
    ref_prov = ref_bundle["provenance"]
    devices_by_arm = {}
    for arm, bundle in bundles_by_arm.items():
        require(bundle["dataset_sha256"] == ref_bundle["dataset_sha256"], f"{suite_label}: dataset SHA differs in {arm}")
        require(bundle["base_case_ids"] == ref_bundle["base_case_ids"], f"{suite_label}: base_case_ids differ in {arm}")
        require(
            len(bundle["schedule_ids"]) == len(ref_bundle["schedule_ids"]),
            f"{suite_label}: sampling schedule count differs in {arm}",
        )
        # Match paired seeds schedule-by-schedule in canonical schedule order (even if single-schedule label differs).
        for sid_ref, sid_cur in zip(ref_bundle["schedule_ids"], bundle["schedule_ids"]):
            require(
                bundle["seeds_by_schedule"][sid_cur] == ref_bundle["seeds_by_schedule"][sid_ref],
                f"{suite_label}: case_seeds mismatch between {arms[0]} ({sid_ref}) and {arm} ({sid_cur})",
            )
        for shared_file, expected_hash in ref_bundle["core_hashes"].items():
            if shared_file in bundle["core_hashes"]:
                require(
                    bundle["core_hashes"][shared_file] == expected_hash,
                    f"{suite_label}: core file {shared_file} hash differs in {arm}",
                )
        cfg = bundle["config"]
        prov = bundle["provenance"]
        for mkey in ("model_id", "revision", "dtype"):
            require(
                cfg["model"].get(mkey) == ref_cfg["model"].get(mkey),
                f"{suite_label}: model.{mkey} differs in {arm}",
            )
        devices_by_arm[arm] = cfg["model"].get("device")
        if strict_same_device:
            require(
                cfg["model"].get("device") == ref_cfg["model"].get("device"),
                f"{suite_label}: model.device differs in {arm}",
            )
        require(
            prov.get("chat_template_sha256") == ref_prov.get("chat_template_sha256")
            and prov.get("model_manifest_sha256") == ref_prov.get("model_manifest_sha256"),
            f"{suite_label}: model_manifest or chat_template SHA differs in {arm}",
        )
        require(
            cfg.get("tool_budget") == ref_cfg.get("tool_budget")
            and cfg.get("turn_budget") == ref_cfg.get("turn_budget"),
            f"{suite_label}: tool_budget or turn_budget differs in {arm}",
        )
        for skey in SAMPLING_INVARIANT_KEYS:
            if skey in ref_cfg["sampling"] or skey in cfg["sampling"]:
                require(
                    cfg["sampling"].get(skey) == ref_cfg["sampling"].get(skey),
                    f"{suite_label}: sampling.{skey} differs in {arm}",
                )
    unique_devices = sorted({str(v) for v in devices_by_arm.values()})
    return {
        "devices_by_arm": devices_by_arm,
        "unique_devices": unique_devices,
        "cross_device_limitation": (
            f"Mixed execution devices across arms in {suite_label}: {devices_by_arm}"
            if len(unique_devices) > 1
            else None
        ),
    }


def compute_slice_metrics(episodes, mode):
    """Compute all 8 Proposal §7.2 primary metrics + v2 loop type breakdown on an episode list."""
    total = len(episodes)
    completable = [ep for ep in episodes if ep["completable"]]
    normal = [ep for ep in episodes if ep["category"] == "normal"]
    recoverable = [ep for ep in episodes if ep["category"] == "recoverable" and not ep["goal_at_prefix"]]
    retry_req = [ep for ep in episodes if ep["category"] == "retry_required" and not ep["goal_at_prefix"]]
    clarification = [ep for ep in episodes if ep["category"] == "clarification"]
    unrecoverable = [ep for ep in episodes if not ep["completable"]]
    structured_claims = [ep for ep in episodes if ep["structured_finish_claimed"]]

    exact_succ = sum(ep["exact_completion"] for ep in completable)
    goal_succ = sum(ep["goal_attainment"] for ep in completable)
    comp_tool_attempts = sum(ep["tool_attempts"] for ep in completable)
    comp_cont_attempts = sum(ep["continuation_tool_attempts"] for ep in completable)
    comp_tokens = sum(ep["total_tokens"] for ep in completable)

    loop_v2_breakdown = {}
    for ltype in LOOP_V2_TYPES:
        ep_with_type = sum(ltype in ep["loop_types_v2"] for ep in episodes)
        flag_total = sum(ep["loop_flag_counts_v2"][ltype] for ep in episodes)
        loop_v2_breakdown[ltype] = {
            "episode_rate": ratio(ep_with_type, total),
            "flag_occurrences": flag_total,
        }

    return {
        "mode": mode,
        "episode_count": total,
        "completable_episode_count": len(completable),
        "unrecoverable_episode_count": len(unrecoverable),
        # 1. Task completion (exact grounded structured completion AND tool goal attainment side-by-side)
        "task_completion_exact": ratio(exact_succ, len(completable)),
        "task_completion_goal": ratio(goal_succ, len(completable)),
        "normal_completion_exact": ratio(sum(ep["exact_completion"] for ep in normal), len(normal)),
        "normal_completion_goal": ratio(sum(ep["goal_attainment"] for ep in normal), len(normal)),
        "recoverable_completion_exact": ratio(sum(ep["exact_completion"] for ep in recoverable), len(recoverable)),
        "recoverable_completion_goal": ratio(sum(ep["goal_attainment"] for ep in recoverable), len(recoverable)),
        "clarification_completion_exact": ratio(
            sum(ep["exact_completion"] for ep in clarification), len(clarification)
        ),
        "clarification_completion_goal": ratio(
            sum(ep["goal_attainment"] for ep in clarification), len(clarification)
        ),
        # 2. Conditional error recovery (authoritative in fixed_failure_prefix on recoverable; separate from retry_required)
        "error_recovery_exact": (
            ratio(sum(ep["exact_completion"] for ep in recoverable), len(recoverable))
            if mode == "fixed_failure_prefix"
            else ratio(0, 0)
        ),
        "error_recovery_goal": (
            ratio(sum(ep["goal_attainment"] for ep in recoverable), len(recoverable))
            if mode == "fixed_failure_prefix"
            else ratio(0, 0)
        ),
        # 3. Invalid loop rate (frozen v2 authoritative alongside legacy v1 + v2 type breakdown)
        "invalid_loop_rate_v2": ratio(sum(ep["has_invalid_loop_v2"] for ep in episodes), total),
        "invalid_loop_rate_v1": ratio(sum(ep["has_invalid_loop_v1"] for ep in episodes), total),
        "loop_v2_breakdown_by_type": loop_v2_breakdown,
        # 4. Necessary retry success (on retry_required tasks)
        "necessary_retry_success_exact": ratio(sum(ep["exact_completion"] for ep in retry_req), len(retry_req)),
        "necessary_retry_success_goal": ratio(sum(ep["goal_attainment"] for ep in retry_req), len(retry_req)),
        # 5. Premature abandonment (on completable tasks)
        "premature_abandonment": ratio(sum(ep["premature_abandonment"] for ep in completable), len(completable)),
        "unnecessary_clarification_rate": ratio(sum(ep["unnecessary_clarification"] for ep in episodes), total),
        "premature_or_unnecessary_clarification": ratio(
            sum(ep["premature_or_unnecessary_clarification"] for ep in completable), len(completable)
        ),
        # 6. False success (unsupported_finish_wrapper across all episodes + conditional rate)
        "false_success": ratio(sum(ep["false_success"] for ep in episodes), total),
        "false_success_given_structured_finish": ratio(
            sum(ep["false_success"] for ep in structured_claims), len(structured_claims)
        ),
        "natural_termination_semantics_unknown": ratio(
            sum(ep["natural_termination_unknown"] for ep in episodes), total
        ),
        # 7. Correct termination (on unrecoverable tasks: exact stop_task reason AND stopped_after_public_unavailable)
        "correct_termination_exact": ratio(
            sum(ep["exact_stop_reason_code"] for ep in unrecoverable), len(unrecoverable)
        ),
        "correct_termination_stopped": ratio(
            sum(ep["stopped_after_unavailable"] for ep in unrecoverable), len(unrecoverable)
        ),
        # 8. Cost per success (completable attempts / exact completions, and completable tokens / exact completions; null if 0)
        "cost_per_success": {
            "tool_attempts_per_exact_completion": ratio(comp_tool_attempts, exact_succ),
            "continuation_tool_attempts_per_exact_completion": ratio(comp_cont_attempts, exact_succ),
            "tokens_per_exact_completion": ratio(comp_tokens, exact_succ),
            "tool_attempts_per_goal_attainment": ratio(comp_tool_attempts, goal_succ),
            "tokens_per_goal_attainment": ratio(comp_tokens, goal_succ),
            "total_completable_tool_attempts": comp_tool_attempts,
            "total_completable_continuation_tool_attempts": comp_cont_attempts,
            "total_completable_tokens": comp_tokens,
            "total_all_episodes_tokens": sum(ep["total_tokens"] for ep in episodes),
            "total_input_tokens": sum(ep["input_tokens"] for ep in episodes),
            "total_output_tokens": sum(ep["output_tokens"] for ep in episodes),
            "total_generation_seconds": sum(ep["generation_seconds"] for ep in episodes),
        },
        "diagnostics": {
            "budget_exhaustion": ratio(sum(ep["budget_exhausted"] for ep in episodes), total),
            "parser_error_turns": sum(ep["parser_error_turns"] for ep in episodes),
            "guard_blocked_proposals": sum(ep["guard_blocked_proposals"] for ep in episodes),
            "total_turns": sum(ep["turns"] for ep in episodes),
        },
    }


RATE_METRIC_KEYS = (
    "task_completion_exact",
    "task_completion_goal",
    "normal_completion_exact",
    "normal_completion_goal",
    "error_recovery_exact",
    "error_recovery_goal",
    "invalid_loop_rate_v2",
    "invalid_loop_rate_v1",
    "necessary_retry_success_exact",
    "necessary_retry_success_goal",
    "premature_abandonment",
    "false_success",
    "correct_termination_exact",
    "correct_termination_stopped",
)


def summarize_arm_bundle(bundle):
    """Summarize pooled metrics, domain/category stratifications, and multi-sampling-seed means & SDs."""
    mode = bundle["mode"]
    all_eps = bundle["all_episodes"]
    overall = compute_slice_metrics(all_eps, mode)
    in_domain = compute_slice_metrics([ep for ep in all_eps if ep["is_in_domain"]], mode)
    cross_domain = compute_slice_metrics([ep for ep in all_eps if not ep["is_in_domain"]], mode)
    by_domain = {d: compute_slice_metrics([ep for ep in all_eps if ep["domain"] == d], mode) for d in DOMAINS}
    by_category = {
        cat: compute_slice_metrics([ep for ep in all_eps if ep["category"] == cat], mode)
        for cat in CATEGORIES
    }

    by_schedule = collections.OrderedDict()
    for sid in bundle["schedule_ids"]:
        sched_eps = list(bundle["schedules"][sid].values())
        by_schedule[sid] = compute_slice_metrics(sched_eps, mode)

    sampling_seed_stats = {}
    for mkey in RATE_METRIC_KEYS:
        vals = [
            by_schedule[sid][mkey]["value"]
            for sid in bundle["schedule_ids"]
            if by_schedule[sid][mkey]["value"] is not None
        ]
        sampling_seed_stats[mkey] = sample_stats(vals)

    return {
        "arm": bundle["arm"],
        "mode": mode,
        "train_seed": bundle["train_seed"],
        "run_ids": bundle["run_ids"],
        "manifest_sha256s": bundle["manifest_sha256s"],
        "sampling_schedule_ids": bundle["schedule_ids"],
        "sampling_seed_count": len(bundle["schedule_ids"]),
        "unique_case_count": len(bundle["base_case_ids"]),
        "unique_template_group_count": len({ep["template_group"] for ep in all_eps}),
        "total_episodes": len(all_eps),
        "aggregation_rule": "mean_over_sampling_seeds_never_best_of_k",
        "overall": overall,
        "in_domain_orders_files": in_domain,
        "cross_domain_heldout_jobs": cross_domain,
        "by_domain": by_domain,
        "by_category": by_category,
        "by_sampling_schedule": by_schedule,
        "sampling_seed_stats": sampling_seed_stats,
    }


def summarize_multi_train_seeds(arm_name, bundles):
    """Aggregate R independent training seeds (e.g. s101, s202, s303) -> training-seed means, SDs, and 95% t-CIs."""
    require(len(bundles) >= 2, f"multi_train_seed summary for {arm_name} requires >= 2 training seeds")
    seen_paths = set()
    seen_seeds = []
    per_seed = []
    for b in bundles:
        for p in b["run_paths"]:
            require(p not in seen_paths, f"duplicate run path in multi_train_seed for {arm_name}: {p}")
            seen_paths.add(p)
        require(b["train_seed"] not in seen_seeds, f"duplicate train_seed {b['train_seed']} for {arm_name}")
        seen_seeds.append(b["train_seed"])
        per_seed.append(summarize_arm_bundle(b))

    def _agg_section(section_name):
        out = {}
        for mkey in RATE_METRIC_KEYS:
            vals = [
                s[section_name][mkey]["value"]
                for s in per_seed
                if s[section_name][mkey]["value"] is not None
            ]
            out[mkey] = sample_stats(vals)
        for ckey in ("tool_attempts_per_exact_completion", "tokens_per_exact_completion"):
            vals = [
                s[section_name]["cost_per_success"][ckey]["value"]
                for s in per_seed
                if s[section_name]["cost_per_success"][ckey]["value"] is not None
            ]
            out[ckey] = {
                **sample_stats(vals),
                "zero_success_seed_count": sum(
                    s[section_name]["cost_per_success"][ckey]["value"] is None for s in per_seed
                ),
            }
        return out

    return {
        "arm": arm_name,
        "train_seed_count": len(per_seed),
        "train_seeds": seen_seeds,
        "overall": _agg_section("overall"),
        "in_domain_orders_files": _agg_section("in_domain_orders_files"),
        "cross_domain_heldout_jobs": _agg_section("cross_domain_heldout_jobs"),
        "by_train_seed": {str(s["train_seed"]): s for s in per_seed},
    }


CLUSTER_METRICS_SPEC = {
    "task_completion_exact": (
        lambda ep: ep["completable"],
        lambda ep: float(ep["exact_completion"]),
    ),
    "task_completion_goal": (
        lambda ep: ep["completable"],
        lambda ep: float(ep["goal_attainment"]),
    ),
    "normal_completion_exact": (
        lambda ep: ep["category"] == "normal",
        lambda ep: float(ep["exact_completion"]),
    ),
    "normal_completion_goal": (
        lambda ep: ep["category"] == "normal",
        lambda ep: float(ep["goal_attainment"]),
    ),
    "error_recovery_exact": (
        lambda ep: ep["category"] == "recoverable" and not ep["goal_at_prefix"],
        lambda ep: float(ep["exact_completion"]),
    ),
    "error_recovery_goal": (
        lambda ep: ep["category"] == "recoverable" and not ep["goal_at_prefix"],
        lambda ep: float(ep["goal_attainment"]),
    ),
    "invalid_loop_rate_v2": (
        lambda ep: True,
        lambda ep: float(ep["has_invalid_loop_v2"]),
    ),
    "invalid_loop_rate_v1": (
        lambda ep: True,
        lambda ep: float(ep["has_invalid_loop_v1"]),
    ),
    "necessary_retry_success_exact": (
        lambda ep: ep["category"] == "retry_required" and not ep["goal_at_prefix"],
        lambda ep: float(ep["exact_completion"]),
    ),
    "necessary_retry_success_goal": (
        lambda ep: ep["category"] == "retry_required" and not ep["goal_at_prefix"],
        lambda ep: float(ep["goal_attainment"]),
    ),
    "premature_abandonment": (
        lambda ep: ep["completable"],
        lambda ep: float(ep["premature_abandonment"]),
    ),
    "false_success": (
        lambda ep: True,
        lambda ep: float(ep["false_success"]),
    ),
    "correct_termination_exact": (
        lambda ep: not ep["completable"],
        lambda ep: float(ep["exact_stop_reason_code"]),
    ),
    "correct_termination_stopped": (
        lambda ep: not ep["completable"],
        lambda ep: float(ep["stopped_after_unavailable"]),
    ),
}


def _cluster_means_from_bundles(bundles, pred_fn, val_fn, domain_filter=None):
    """Compute per-template_group mean outcome across all sampling/training seeds in `bundles`."""
    buckets = collections.OrderedDict()
    for b in bundles:
        for ep in b["all_episodes"]:
            if domain_filter is not None and not domain_filter(ep):
                continue
            if not pred_fn(ep):
                continue
            tg = ep["template_group"]
            buckets.setdefault(tg, []).append(val_fn(ep))
    return collections.OrderedDict((tg, sum(vs) / len(vs)) for tg, vs in buckets.items())


def compute_clustered_paired_comparison(lhs_bundles, rhs_bundles, lhs_label, rhs_label, mode):
    """Compute template-clustered paired differences (lhs - rhs) & 95% t-CIs (`df = G - 1`)."""
    lhs_list = lhs_bundles if isinstance(lhs_bundles, list) else [lhs_bundles]
    rhs_list = rhs_bundles if isinstance(rhs_bundles, list) else [rhs_bundles]
    total_groups_all = len({ep["template_group"] for ep in lhs_list[0]["all_episodes"]})

    def _compare_scope(domain_filter, scope_total_groups):
        metrics_out = {}
        for mkey, (pred_fn, val_fn) in CLUSTER_METRICS_SPEC.items():
            if mkey.startswith("error_recovery_") and mode != "fixed_failure_prefix":
                metrics_out[mkey] = {
                    "applicable": False,
                    "total_suite_template_groups": scope_total_groups,
                    "eligible_template_groups": 0,
                    "lhs_mean": None,
                    "rhs_mean": None,
                    "mean_diff": None,
                    "ci95": None,
                    "df": None,
                }
                continue
            lhs_g = _cluster_means_from_bundles(lhs_list, pred_fn, val_fn, domain_filter)
            rhs_g = _cluster_means_from_bundles(rhs_list, pred_fn, val_fn, domain_filter)
            require(
                list(lhs_g.keys()) == list(rhs_g.keys()),
                f"template_group mismatch between {lhs_label} and {rhs_label} on {mkey}",
            )
            groups = list(lhs_g.keys())
            if not groups:
                metrics_out[mkey] = {
                    "applicable": False,
                    "total_suite_template_groups": scope_total_groups,
                    "eligible_template_groups": 0,
                    "lhs_mean": None,
                    "rhs_mean": None,
                    "mean_diff": None,
                    "ci95": None,
                    "df": None,
                }
                continue
            diffs = [lhs_g[g] - rhs_g[g] for g in groups]
            st = sample_stats(diffs)
            lhs_mean = sum(lhs_g.values()) / len(groups)
            rhs_mean = sum(rhs_g.values()) / len(groups)
            wins = [g for g, d in zip(groups, diffs) if d > 1e-12]
            losses = [g for g, d in zip(groups, diffs) if d < -1e-12]
            ties = [g for g, d in zip(groups, diffs) if abs(d) <= 1e-12]
            metrics_out[mkey] = {
                "applicable": True,
                "total_suite_template_groups": scope_total_groups,
                "eligible_template_groups": len(groups),
                "lhs_mean": lhs_mean,
                "rhs_mean": rhs_mean,
                "mean_diff": st["mean"],
                "sample_sd": st["sample_sd"],
                "se": st["se"],
                "df": st["df"],
                "t_crit_95": st["t_crit_95"],
                "ci95": st["ci95"],
                "ci95_excludes_zero": bool(st["ci95"] and (st["ci95"][0] > 0.0 or st["ci95"][1] < 0.0)),
                "cluster_wins": len(wins),
                "cluster_ties": len(ties),
                "cluster_losses": len(losses),
            }
        return metrics_out

    in_dom_groups = len({ep["template_group"] for ep in lhs_list[0]["all_episodes"] if ep["is_in_domain"]})
    cross_groups = len({ep["template_group"] for ep in lhs_list[0]["all_episodes"] if not ep["is_in_domain"]})

    return {
        "contrast": f"{lhs_label} vs {rhs_label}",
        "lhs": lhs_label,
        "rhs": rhs_label,
        "mode": mode,
        "overall": _compare_scope(None, total_groups_all),
        "in_domain_orders_files": _compare_scope(lambda ep: ep["is_in_domain"], in_dom_groups),
        "cross_domain_heldout_jobs": _compare_scope(lambda ep: not ep["is_in_domain"], cross_groups),
    }


def select_strongest_untrained_on_dev(dev_summaries):
    """Deterministically select strongest untrained arm (B0/B1/B2) on dev_selection_runs without touching G4."""
    present = [arm for arm in UNTRAINED_ARMS if arm in dev_summaries]
    require(present, "at least one untrained arm (B0/B1/B2) required in dev_selection_runs")

    def sort_key(arm):
        ov = dev_summaries[arm]["overall"]
        return (
            ov["task_completion_exact"]["value"] or 0.0,
            ov["task_completion_goal"]["value"] or 0.0,
            -(ov["invalid_loop_rate_v2"]["value"] or 0.0),
            -(ov["premature_abandonment"]["value"] or 0.0),
            -(ov["false_success"]["value"] or 0.0),
            -UNTRAINED_ARMS.index(arm),
        )

    ranked = sorted(present, key=sort_key, reverse=True)
    return {
        "selected_arm": ranked[0],
        "ranking": ranked,
        "selection_rule": (
            "dev_selection_runs deterministic lexicographic order: max task_completion_exact, "
            "max task_completion_goal, min invalid_loop_rate_v2, min premature_abandonment, "
            "min false_success (evaluated strictly on dev eval24 prior to G4 unsealing)"
        ),
        "dev_scores": {
            arm: {
                "task_completion_exact": dev_summaries[arm]["overall"]["task_completion_exact"]["value"],
                "task_completion_goal": dev_summaries[arm]["overall"]["task_completion_goal"]["value"],
                "invalid_loop_rate_v2": dev_summaries[arm]["overall"]["invalid_loop_rate_v2"]["value"],
                "premature_abandonment": dev_summaries[arm]["overall"]["premature_abandonment"]["value"],
                "false_success": dev_summaries[arm]["overall"]["false_success"]["value"],
            }
            for arm in present
        },
    }


def evaluate_honest_hypothesis_verdicts(
    strongest_untrained_info,
    g4_e2e_summaries,
    g4_pfx_summaries,
    e2e_contrasts,
    pfx_contrasts,
    multi_seed_e2e=None,
    multi_seed_pfx=None,
):
    """Evaluate Proposal §8.1 engineering targets and §8.2 honest research attribution (`P > B3` vs `P ≈ B3`)."""
    p_e2e = g4_e2e_summaries["P"]["overall"]
    b0_e2e = g4_e2e_summaries["B0"]["overall"]
    p_pfx = g4_pfx_summaries["P"]["overall"]

    u_arm = strongest_untrained_info["selected_arm"]
    if u_arm not in g4_pfx_summaries:
        u_arm = "B0"
    u_pfx = g4_pfx_summaries[u_arm]["overall"]

    # 1. Invalid loop relative reduction >= 30% vs B0 (EVALUATION_SPEC §6: N/A if baseline == 0)
    b0_loop = b0_e2e["invalid_loop_rate_v2"]["value"]
    p_loop = p_e2e["invalid_loop_rate_v2"]["value"]
    loop_diff_ci = e2e_contrasts["P_vs_B0"]["overall"]["invalid_loop_rate_v2"]
    comp_diff = e2e_contrasts["P_vs_B0"]["overall"]["task_completion_exact"]["mean_diff"]
    task_degraded = comp_diff is not None and comp_diff < -0.02 - 1e-12

    if b0_loop is None or b0_loop == 0.0:
        rel_loop_red = None
        loop_target_met = None
        loop_status = "not_applicable_zero_baseline"
    else:
        rel_loop_red = (b0_loop - p_loop) / b0_loop
        if task_degraded:
            loop_target_met = False
            loop_status = "failed_loop_reduced_via_task_degradation"
        else:
            loop_target_met = bool(rel_loop_red >= 0.30 - 1e-12)
            loop_status = "met" if loop_target_met else "not_met"

    # 2. Error recovery >= +5pp vs dev-selected strongest untrained in fixed_failure_prefix
    rec_contrast = pfx_contrasts["P_vs_Strongest_Untrained"]["overall"]["error_recovery_exact"]
    rec_goal_contrast = pfx_contrasts["P_vs_Strongest_Untrained"]["overall"]["error_recovery_goal"]
    rec_gain_exact = rec_contrast["mean_diff"]
    rec_gain_goal = rec_goal_contrast["mean_diff"]
    rec_target_met = bool(rec_gain_exact is not None and rec_gain_exact >= 0.05 - 1e-12)

    # Also check conservative oracle max untrained on G4 prefix
    present_untrained_pfx = [a for a in UNTRAINED_ARMS if a in g4_pfx_summaries]
    oracle_best_u = max(
        present_untrained_pfx,
        key=lambda a: (
            g4_pfx_summaries[a]["overall"]["error_recovery_exact"]["value"] or 0.0,
            g4_pfx_summaries[a]["overall"]["error_recovery_goal"]["value"] or 0.0,
        ),
    )
    oracle_rec_gain = (p_pfx["error_recovery_exact"]["value"] or 0.0) - (
        g4_pfx_summaries[oracle_best_u]["overall"]["error_recovery_exact"]["value"] or 0.0
    )

    # 3. Normal & necessary retry retention within -2pp vs B0
    norm_exact_c = e2e_contrasts["P_vs_B0"]["overall"]["normal_completion_exact"]
    norm_goal_c = e2e_contrasts["P_vs_B0"]["overall"]["normal_completion_goal"]
    retry_e2e_exact_c = e2e_contrasts["P_vs_B0"]["overall"]["necessary_retry_success_exact"]
    retry_e2e_goal_c = e2e_contrasts["P_vs_B0"]["overall"]["necessary_retry_success_goal"]
    retry_pfx_exact_c = (
        pfx_contrasts["P_vs_B0"]["overall"]["necessary_retry_success_exact"]
        if "P_vs_B0" in pfx_contrasts
        else None
    )

    norm_retained = bool(
        norm_exact_c["mean_diff"] is not None
        and norm_exact_c["mean_diff"] >= -0.02 - 1e-12
        and (norm_goal_c["mean_diff"] is None or norm_goal_c["mean_diff"] >= -0.02 - 1e-12)
    )
    retry_retained = bool(
        retry_e2e_exact_c["mean_diff"] is not None
        and retry_e2e_exact_c["mean_diff"] >= -0.02 - 1e-12
        and (retry_e2e_goal_c["mean_diff"] is None or retry_e2e_goal_c["mean_diff"] >= -0.02 - 1e-12)
    )

    # 4. Side-effects guard (premature abandonment & false success do not increase as loop substitute)
    abandon_c = e2e_contrasts["P_vs_B0"]["overall"]["premature_abandonment"]
    false_succ_c = e2e_contrasts["P_vs_B0"]["overall"]["false_success"]
    stop_exact_c = e2e_contrasts["P_vs_B0"]["overall"]["correct_termination_exact"]
    no_side_effect_compensation = bool(
        (abandon_c["mean_diff"] is not None and abandon_c["mean_diff"] <= 0.02 + 1e-12)
        and (false_succ_c["mean_diff"] is not None and false_succ_c["mean_diff"] <= 0.02 + 1e-12)
        and not task_degraded
    )

    # 5. Cost per success efficiency
    p_cost_att = p_e2e["cost_per_success"]["tool_attempts_per_exact_completion"]["value"]
    b0_cost_att = b0_e2e["cost_per_success"]["tool_attempts_per_exact_completion"]["value"]
    p_cost_tok = p_e2e["cost_per_success"]["tokens_per_exact_completion"]["value"]
    b0_cost_tok = b0_e2e["cost_per_success"]["tokens_per_exact_completion"]["value"]
    if p_cost_att is None:
        cost_status = "failed_zero_exact_completions"
        cost_target_met = False
    elif b0_cost_att is None:
        cost_status = "enabled_nonzero_exact_completion_from_zero_baseline"
        cost_target_met = True
    elif p_cost_att <= b0_cost_att + 1e-12 or (comp_diff is not None and comp_diff > 0.0):
        cost_status = (
            "lower_or_equal_unit_cost"
            if p_cost_att <= b0_cost_att + 1e-12
            else "higher_completion_with_bounded_unit_cost"
        )
        cost_target_met = True
    else:
        cost_status = "unit_cost_increased_without_completion_gain"
        cost_target_met = False

    # 6. Proposal §8.2 Honest Attribution: `P > B3` vs `P ≈ B3` vs `P < B3`
    p_vs_b3_attribution = {"available": False}
    if "P_vs_B3" in e2e_contrasts and "P_vs_B3" in pfx_contrasts:
        c_e2e = e2e_contrasts["P_vs_B3"]["overall"]
        c_pfx = pfx_contrasts["P_vs_B3"]["overall"]
        d_rec = c_pfx["error_recovery_exact"]["mean_diff"] or 0.0
        ci_rec = c_pfx["error_recovery_exact"]["ci95"]
        d_comp = c_e2e["task_completion_exact"]["mean_diff"] or 0.0
        ci_comp = c_e2e["task_completion_exact"]["ci95"]
        d_loop = c_e2e["invalid_loop_rate_v2"]["mean_diff"] or 0.0
        ci_loop = c_e2e["invalid_loop_rate_v2"]["ci95"]

        sig_p_rec = bool(ci_rec and ci_rec[0] > 0.0)
        sig_p_comp = bool(ci_comp and ci_comp[0] > 0.0)
        sig_p_loop = bool(ci_loop and ci_loop[1] < 0.0 and d_comp >= -0.02 - 1e-12 and d_rec >= -1e-12)

        sig_b3_rec = bool(ci_rec and ci_rec[1] < 0.0)
        sig_b3_comp = bool(ci_comp and ci_comp[1] < 0.0)

        train_seed_consistent_p = True
        train_seed_delta_e2e = None
        train_seed_delta_pfx = None
        if multi_seed_e2e and "P" in multi_seed_e2e and "B3" in multi_seed_e2e:
            train_seed_delta_e2e = (
                multi_seed_e2e["P"]["overall"]["task_completion_exact"]["mean"]
                - multi_seed_e2e["B3"]["overall"]["task_completion_exact"]["mean"]
            )
            if train_seed_delta_e2e <= 0.0 and d_comp > 0.0:
                train_seed_consistent_p = False
        if multi_seed_pfx and "P" in multi_seed_pfx and "B3" in multi_seed_pfx:
            train_seed_delta_pfx = (
                multi_seed_pfx["P"]["overall"]["error_recovery_exact"]["mean"]
                - multi_seed_pfx["B3"]["overall"]["error_recovery_exact"]["mean"]
            )
            if train_seed_delta_pfx <= 0.0 and d_rec > 0.0:
                train_seed_consistent_p = False

        if (
            (d_rec >= 0.05 - 1e-12 or d_comp >= 0.05 - 1e-12 or d_loop <= -0.05 + 1e-12)
            and (sig_p_rec or sig_p_comp or sig_p_loop)
            and d_rec >= -0.02 - 1e-12
            and d_comp >= -0.02 - 1e-12
            and train_seed_consistent_p
        ):
            relation = "P > B3"
            substatus = "statistically_supported_preference_gain_over_error_sft"
            interpretation = (
                "专项失败现场行动级 DPO (`P`) 在匹配相同 400 个决策点与预算下，相比纠错 SFT (`B3`) "
                "取得了模板聚类 95% 置信区间排零的可复现额外收益（`P > B3`）。"
            )
        elif (d_rec <= -0.05 + 1e-12 or d_comp <= -0.05 + 1e-12) and (sig_b3_rec or sig_b3_comp):
            relation = "P < B3"
            substatus = "error_sft_outperformed_preference_optimization"
            interpretation = (
                "纠错 SFT (`B3`) 在封闭测试集上显著优于动作级 DPO (`P`)（`P < B3`）；"
                "直接最大似然监督比离线偏好损失更稳健，不应宣称 DPO 训练目标优越（Proposal §8.2）。"
            )
        else:
            relation = "P ≈ B3"
            if d_rec > 1e-12 or d_comp > 1e-12:
                substatus = "point_positive_or_small_diff_ci95_includes_zero"
            else:
                substatus = "equivalent_or_non_superior_to_error_sft"
            interpretation = (
                "普通纠错 SFT (`B3`) 在共享经验证失败现场正例后同样有效（`P ≈ B3`，配对模板聚类 95% CI 含 0 "
                "或点估计差值未达显著分离）：按 Proposal §8.2 与 EVALUATION_SPEC §6，应诚实承认主要收益来自"
                "执行验证的高质量失败现场纠错数据与动作契约监督，而非偏好损失函数本身优越。"
            )

        p_vs_b3_attribution = {
            "available": True,
            "relation": relation,
            "substatus": substatus,
            "prefix_error_recovery_exact_diff": d_rec,
            "prefix_error_recovery_exact_ci95": ci_rec,
            "e2e_task_completion_exact_diff": d_comp,
            "e2e_task_completion_exact_ci95": ci_comp,
            "e2e_invalid_loop_v2_diff": d_loop,
            "e2e_invalid_loop_v2_ci95": ci_loop,
            "three_train_seed_e2e_completion_diff": train_seed_delta_e2e,
            "three_train_seed_prefix_recovery_diff": train_seed_delta_pfx,
            "honest_interpretation": interpretation,
        }

    # Auxiliary mechanism & ablation verdicts (`B4`, `B5`, `P_no_retry`, `P_no_normal`)
    mechanism_checks = {}
    for key, arm_rhs, primary_metric_e2e, primary_metric_pfx in (
        ("P_vs_B4", "B4", "task_completion_exact", "error_recovery_exact"),
        ("P_vs_B5", "B5", "task_completion_exact", "error_recovery_exact"),
        ("P_vs_P_no_retry", "P_no_retry", "necessary_retry_success_exact", "necessary_retry_success_exact"),
        ("P_vs_P_no_normal", "P_no_normal", "normal_completion_exact", "error_recovery_exact"),
    ):
        if key in e2e_contrasts and key in pfx_contrasts:
            ce = e2e_contrasts[key]["overall"][primary_metric_e2e]
            cp = pfx_contrasts[key]["overall"][primary_metric_pfx]
            mechanism_checks[key] = {
                "rhs_arm": arm_rhs,
                "e2e_metric": primary_metric_e2e,
                "e2e_mean_diff": ce["mean_diff"],
                "e2e_ci95": ce["ci95"],
                "prefix_metric": primary_metric_pfx,
                "prefix_mean_diff": cp["mean_diff"],
                "prefix_ci95": cp["ci95"],
            }

    return {
        "engineering_targets_section_8_1": {
            "invalid_loop_reduction_ge_30pct_vs_B0": {
                "b0_rate": b0_loop,
                "p_rate": p_loop,
                "absolute_diff": loop_diff_ci["mean_diff"],
                "clustered_ci95": loop_diff_ci["ci95"],
                "df": loop_diff_ci["df"],
                "relative_reduction": rel_loop_red,
                "target_threshold_relative": 0.30,
                "target_met": loop_target_met,
                "status": loop_status,
            },
            "error_recovery_gain_ge_5pp_vs_strongest_untrained": {
                "dev_selected_strongest_untrained_arm": u_arm,
                "strongest_untrained_prefix_recovery_exact": u_pfx["error_recovery_exact"]["value"],
                "p_prefix_recovery_exact": p_pfx["error_recovery_exact"]["value"],
                "absolute_diff_exact": rec_gain_exact,
                "clustered_ci95_exact": rec_contrast["ci95"],
                "df": rec_contrast["df"],
                "absolute_diff_goal": rec_gain_goal,
                "clustered_ci95_goal": rec_goal_contrast["ci95"],
                "g4_oracle_best_untrained_arm": oracle_best_u,
                "absolute_diff_vs_g4_oracle_untrained_exact": oracle_rec_gain,
                "target_threshold_absolute_pp": 0.05,
                "target_met": rec_target_met,
                "ci95_excludes_zero": rec_contrast.get("ci95_excludes_zero", False),
            },
            "normal_and_retry_retention_within_minus_2pp_vs_B0": {
                "normal_completion_exact_diff": norm_exact_c["mean_diff"],
                "normal_completion_exact_ci95": norm_exact_c["ci95"],
                "normal_completion_goal_diff": norm_goal_c["mean_diff"],
                "necessary_retry_e2e_exact_diff": retry_e2e_exact_c["mean_diff"],
                "necessary_retry_e2e_exact_ci95": retry_e2e_exact_c["ci95"],
                "necessary_retry_e2e_goal_diff": retry_e2e_goal_c["mean_diff"],
                "necessary_retry_prefix_exact_diff": (
                    retry_pfx_exact_c["mean_diff"] if retry_pfx_exact_c else None
                ),
                "tolerance_floor": -0.02,
                "normal_retained": norm_retained,
                "necessary_retry_retained": retry_retained,
                "target_met": bool(norm_retained and retry_retained),
            },
            "behavioral_side_effects_guard": {
                "premature_abandonment_diff_vs_B0": abandon_c["mean_diff"],
                "premature_abandonment_ci95": abandon_c["ci95"],
                "false_success_diff_vs_B0": false_succ_c["mean_diff"],
                "false_success_ci95": false_succ_c["ci95"],
                "correct_termination_exact_diff_vs_B0": stop_exact_c["mean_diff"],
                "correct_termination_exact_ci95": stop_exact_c["ci95"],
                "target_met": no_side_effect_compensation,
            },
            "cost_per_success_efficiency": {
                "b0_tool_attempts_per_exact_completion": b0_cost_att,
                "p_tool_attempts_per_exact_completion": p_cost_att,
                "b0_tokens_per_exact_completion": b0_cost_tok,
                "p_tokens_per_exact_completion": p_cost_tok,
                "status": cost_status,
                "target_met": cost_target_met,
            },
        },
        "research_attribution_section_8_2": {
            "p_vs_b3_error_sft": p_vs_b3_attribution,
            "mechanism_and_ablation_contrasts": mechanism_checks,
        },
    }


def _fmt_ratio(r_obj):
    if not r_obj or r_obj.get("value") is None:
        return "N/A"
    return f"{r_obj['numerator']}/{r_obj['denominator']} ({r_obj['value'] * 100.0:.1f}%)"


def _fmt_pct(val):
    return "N/A" if val is None else f"{val * 100.0:.1f}%"


def _fmt_pp(val):
    return "N/A" if val is None else f"{val * 100.0:+.1f}pp"


def _fmt_ci_pp(ci):
    if not ci or ci[0] is None or ci[1] is None:
        return "N/A"
    return f"[{ci[0] * 100.0:+.1f}pp, {ci[1] * 100.0:+.1f}pp]"


def _fmt_cost(r_obj, digits=1):
    if not r_obj or r_obj.get("value") is None:
        return "N/A"
    return f"{r_obj['value']:.{digits}f}"


def render_markdown_report(report):
    lines = [
        "# ITER-009 G3 Training & G4 Formal Closed Holdout Statistical Comparison",
        "",
        f"- **Schema**: `{report['schema_version']}` (`evidence_class={report['evidence_class']}`, zero model execution)",
        f"- **Authoritative Loop Detector**: `{report['detector_v2_version']}` (reported side-by-side with v1)",
        f"- **Dev-Selected Strongest Untrained Baseline (`eval24`)**: **`{report['dev_strongest_untrained']['selected_arm']}`**",
        f"- **Sampling-Seed Aggregation Rule**: `mean_over_sampling_seeds_never_best_of_k`",
        "",
        "## 1. Dev Selection (`eval24` E2E, 24 Unseen Template Groups)",
        "",
        "| Arm | Exact Completion | Goal Attainment | Invalid Loop v2 | Invalid Loop v1 | Retry Exact | Abandonment | False Success | Stop Exact | Attempts/Exact | Tokens/Exact |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm in report["dev_selection"]["arm_order"]:
        ov = report["dev_selection"]["arms"][arm]["overall"]
        cs = ov["cost_per_success"]
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                arm,
                _fmt_ratio(ov["task_completion_exact"]),
                _fmt_ratio(ov["task_completion_goal"]),
                _fmt_ratio(ov["invalid_loop_rate_v2"]),
                _fmt_ratio(ov["invalid_loop_rate_v1"]),
                _fmt_ratio(ov["necessary_retry_success_exact"]),
                _fmt_ratio(ov["premature_abandonment"]),
                _fmt_ratio(ov["false_success"]),
                _fmt_ratio(ov["correct_termination_exact"]),
                _fmt_cost(cs["tool_attempts_per_exact_completion"], 2),
                _fmt_cost(cs["tokens_per_exact_completion"], 0),
            )
        )

    lines.extend(
        [
            "",
            "## 2. G4 Closed Holdout — End-to-End Primary Metrics (`G=36` Unseen Template Groups)",
            "",
            "| Arm | Seeds | Exact Completion | Goal Attainment | Normal Exact | Retry Exact | Loop v2 (Auth.) | Loop v1 | Abandonment | False Success | Stop Exact / Stopped | Attempts/Exact | Tokens/Exact |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in report["g4_e2e"]["arm_order"]:
        s = report["g4_e2e"]["arms"][arm]
        ov = s["overall"]
        cs = ov["cost_per_success"]
        sd_comp = s["sampling_seed_stats"]["task_completion_exact"]["sample_sd"]
        comp_str = _fmt_ratio(ov["task_completion_exact"])
        if sd_comp is not None:
            comp_str += f" (±{sd_comp * 100.0:.1f}%)"
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} / {} | {} | {} |".format(
                arm,
                s["sampling_seed_count"],
                comp_str,
                _fmt_ratio(ov["task_completion_goal"]),
                _fmt_ratio(ov["normal_completion_exact"]),
                _fmt_ratio(ov["necessary_retry_success_exact"]),
                _fmt_ratio(ov["invalid_loop_rate_v2"]),
                _fmt_ratio(ov["invalid_loop_rate_v1"]),
                _fmt_ratio(ov["premature_abandonment"]),
                _fmt_ratio(ov["false_success"]),
                _fmt_pct(ov["correct_termination_exact"]["value"]),
                _fmt_pct(ov["correct_termination_stopped"]["value"]),
                _fmt_cost(cs["tool_attempts_per_exact_completion"], 2),
                _fmt_cost(cs["tokens_per_exact_completion"], 0),
            )
        )

    lines.extend(
        [
            "",
            "### 2.1 G4 E2E Loop v2 Breakdown by Loop Type",
            "",
            "| Arm | Total Loop v2 | Adjacent Repeat (`AA`) | Short Cycle (`ABAB`) | Non-Adjacent / 3-Cycle | Retry-Limit Violation |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in report["g4_e2e"]["arm_order"]:
        ov = report["g4_e2e"]["arms"][arm]["overall"]
        bd = ov["loop_v2_breakdown_by_type"]
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} |".format(
                arm,
                _fmt_ratio(ov["invalid_loop_rate_v2"]),
                _fmt_ratio(bd["identical_or_equivalent_repeat"]["episode_rate"]),
                _fmt_ratio(bd["short_cycle_abab"]["episode_rate"]),
                _fmt_ratio(bd["nonadjacent_or_multistep_repeat_without_progress"]["episode_rate"]),
                _fmt_ratio(bd["retry_limit_violation"]["episode_rate"]),
            )
        )

    lines.extend(
        [
            "",
            "### 2.2 G4 E2E In-Domain (`orders`+`files`, 24 Groups) vs Zero-Shot Cross-Domain (`jobs`, 12 Groups)",
            "",
            "| Arm | In-Domain Exact | In-Domain Goal | In-Domain Loop v2 | Cross-Domain (`jobs`) Exact | Cross-Domain (`jobs`) Goal | Cross-Domain (`jobs`) Loop v2 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in report["g4_e2e"]["arm_order"]:
        s = report["g4_e2e"]["arms"][arm]
        ind = s["in_domain_orders_files"]
        crd = s["cross_domain_heldout_jobs"]
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} |".format(
                arm,
                _fmt_ratio(ind["task_completion_exact"]),
                _fmt_ratio(ind["task_completion_goal"]),
                _fmt_ratio(ind["invalid_loop_rate_v2"]),
                _fmt_ratio(crd["task_completion_exact"]),
                _fmt_ratio(crd["task_completion_goal"]),
                _fmt_ratio(crd["invalid_loop_rate_v2"]),
            )
        )

    lines.extend(
        [
            "",
            "## 3. G4 Closed Holdout — `fixed_failure_prefix` Conditional Recovery (`G=18` Prefix Groups)",
            "",
            "| Arm | Seeds | Recoverable Exact (`G=12`) | Recoverable Goal (`G=12`) | Retry-Required Exact (`G=6`) | Retry-Required Goal (`G=6`) | In-Domain Rec. Exact | Cross-Domain (`jobs`) Rec. Exact | Loop v2 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for arm in report["g4_prefix"]["arm_order"]:
        s = report["g4_prefix"]["arms"][arm]
        ov = s["overall"]
        ind = s["in_domain_orders_files"]
        crd = s["cross_domain_heldout_jobs"]
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                arm,
                s["sampling_seed_count"],
                _fmt_ratio(ov["error_recovery_exact"]),
                _fmt_ratio(ov["error_recovery_goal"]),
                _fmt_ratio(ov["necessary_retry_success_exact"]),
                _fmt_ratio(ov["necessary_retry_success_goal"]),
                _fmt_ratio(ind["error_recovery_exact"]),
                _fmt_ratio(crd["error_recovery_exact"]),
                _fmt_ratio(ov["invalid_loop_rate_v2"]),
            )
        )

    if report["multi_train_seed"]["e2e"] or report["multi_train_seed"]["prefix"]:
        lines.extend(
            [
                "",
                "## 4. Multi-Training-Seed Stability (`s101, s202, s303`)",
                "",
                "| Layer | Arm | Train Seeds | Exact Completion / Recovery (Mean ± SD) | Goal Attainment (Mean ± SD) | Invalid Loop v2 (Mean ± SD) | Cross-Domain `jobs` Exact (Mean ± SD) |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for arm, ms in report["multi_train_seed"]["e2e"].items():
            ov = ms["overall"]
            crd = ms["cross_domain_heldout_jobs"]
            c_m, c_sd = ov["task_completion_exact"]["mean"], ov["task_completion_exact"]["sample_sd"]
            g_m, g_sd = ov["task_completion_goal"]["mean"], ov["task_completion_goal"]["sample_sd"]
            l_m, l_sd = ov["invalid_loop_rate_v2"]["mean"], ov["invalid_loop_rate_v2"]["sample_sd"]
            j_m, j_sd = crd["task_completion_exact"]["mean"], crd["task_completion_exact"]["sample_sd"]
            lines.append(
                "| G4 E2E | `{}` | `{}` | {} ± {} | {} ± {} | {} ± {} | {} ± {} |".format(
                    arm,
                    ",".join(str(x) for x in ms["train_seeds"]),
                    _fmt_pct(c_m),
                    _fmt_pct(c_sd),
                    _fmt_pct(g_m),
                    _fmt_pct(g_sd),
                    _fmt_pct(l_m),
                    _fmt_pct(l_sd),
                    _fmt_pct(j_m),
                    _fmt_pct(j_sd),
                )
            )
        for arm, ms in report["multi_train_seed"]["prefix"].items():
            ov = ms["overall"]
            crd = ms["cross_domain_heldout_jobs"]
            c_m, c_sd = ov["error_recovery_exact"]["mean"], ov["error_recovery_exact"]["sample_sd"]
            g_m, g_sd = ov["error_recovery_goal"]["mean"], ov["error_recovery_goal"]["sample_sd"]
            l_m, l_sd = ov["invalid_loop_rate_v2"]["mean"], ov["invalid_loop_rate_v2"]["sample_sd"]
            j_m, j_sd = crd["error_recovery_exact"]["mean"], crd["error_recovery_exact"]["sample_sd"]
            lines.append(
                "| G4 Prefix | `{}` | `{}` | {} ± {} | {} ± {} | {} ± {} | {} ± {} |".format(
                    arm,
                    ",".join(str(x) for x in ms["train_seeds"]),
                    _fmt_pct(c_m),
                    _fmt_pct(c_sd),
                    _fmt_pct(g_m),
                    _fmt_pct(g_sd),
                    _fmt_pct(l_m),
                    _fmt_pct(l_sd),
                    _fmt_pct(j_m),
                    _fmt_pct(j_sd),
                )
            )

    lines.extend(
        [
            "",
            "## 5. Template-Clustered Paired Differences & 95% $t$-Confidence Intervals (`df = G_eligible - 1`)",
            "",
            "| Contrast | E2E Exact Completion $\\Delta$ (95% CI, `df=29`) | E2E Loop v2 $\\Delta$ (95% CI, `df=35`) | Prefix Recovery Exact $\\Delta$ (95% CI, `df=11`) | E2E Retry Exact $\\Delta$ (`df=5`) | E2E Normal Exact $\\Delta$ (`df=5`) |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for cname, e2e_c in report["paired_clustered_contrasts"]["g4_e2e"].items():
        pfx_c = report["paired_clustered_contrasts"]["g4_prefix"].get(cname)
        ce_comp = e2e_c["overall"]["task_completion_exact"]
        ce_loop = e2e_c["overall"]["invalid_loop_rate_v2"]
        ce_ret = e2e_c["overall"]["necessary_retry_success_exact"]
        ce_norm = e2e_c["overall"]["normal_completion_exact"]
        cp_rec = pfx_c["overall"]["error_recovery_exact"] if pfx_c else None
        lines.append(
            "| `{}` | {} {} | {} {} | {} {} | {} | {} |".format(
                e2e_c["contrast"],
                _fmt_pp(ce_comp["mean_diff"]),
                _fmt_ci_pp(ce_comp["ci95"]),
                _fmt_pp(ce_loop["mean_diff"]),
                _fmt_ci_pp(ce_loop["ci95"]),
                _fmt_pp(cp_rec["mean_diff"]) if cp_rec else "N/A",
                _fmt_ci_pp(cp_rec["ci95"]) if cp_rec else "",
                _fmt_pp(ce_ret["mean_diff"]),
                _fmt_pp(ce_norm["mean_diff"]),
            )
        )

    hv = report["honest_hypothesis_verdicts"]
    eng = hv["engineering_targets_section_8_1"]
    att = hv["research_attribution_section_8_2"]["p_vs_b3_error_sft"]

    rel_red = eng["invalid_loop_reduction_ge_30pct_vs_B0"]["relative_reduction"]
    rel_red_str = "N/A (baseline=0)" if rel_red is None else f"{rel_red * 100.0:.1f}%"

    lines.extend(
        [
            "",
            "## 6. Honest Hypothesis Verdict Table (Proposal §8.1 & §8.2)",
            "",
            "| Dimension | Preregistered Criterion | Observed Estimate & 95% Clustered CI | Verdict |",
            "|---|---|---|---|",
            "| Invalid Loop Reduction (vs `B0`) | Relative reduction $\\ge 30\\%$ + report absolute $\\Delta$ | Rel: `{}`, Abs: `{}` `{}` | `{}` |".format(
                rel_red_str,
                _fmt_pp(eng["invalid_loop_reduction_ge_30pct_vs_B0"]["absolute_diff"]),
                _fmt_ci_pp(eng["invalid_loop_reduction_ge_30pct_vs_B0"]["clustered_ci95"]),
                eng["invalid_loop_reduction_ge_30pct_vs_B0"]["status"],
            ),
            "| Conditional Error Recovery (vs `{}`) | $\\ge +5.0\\text{{pp}}$ in `fixed_failure_prefix` | Exact: `{}` `{}`, Goal: `{}` | `{}` |".format(
                eng["error_recovery_gain_ge_5pp_vs_strongest_untrained"]["dev_selected_strongest_untrained_arm"],
                _fmt_pp(eng["error_recovery_gain_ge_5pp_vs_strongest_untrained"]["absolute_diff_exact"]),
                _fmt_ci_pp(eng["error_recovery_gain_ge_5pp_vs_strongest_untrained"]["clustered_ci95_exact"]),
                _fmt_pp(eng["error_recovery_gain_ge_5pp_vs_strongest_untrained"]["absolute_diff_goal"]),
                "met" if eng["error_recovery_gain_ge_5pp_vs_strongest_untrained"]["target_met"] else "not_met",
            ),
            "| Normal & Retry Retention (vs `B0`) | Drop no worse than $-2.0\\text{{pp}}$ | Normal $\\Delta$: `{}`, Retry E2E $\\Delta$: `{}` | `{}` |".format(
                _fmt_pp(eng["normal_and_retry_retention_within_minus_2pp_vs_B0"]["normal_completion_exact_diff"]),
                _fmt_pp(eng["normal_and_retry_retention_within_minus_2pp_vs_B0"]["necessary_retry_e2e_exact_diff"]),
                "met" if eng["normal_and_retry_retention_within_minus_2pp_vs_B0"]["target_met"] else "not_met",
            ),
            "| Behavioral Side-Effects Guard | Abandonment & False Success not compensating | Abandon $\\Delta$: `{}`, FalseSucc $\\Delta$: `{}` | `{}` |".format(
                _fmt_pp(eng["behavioral_side_effects_guard"]["premature_abandonment_diff_vs_B0"]),
                _fmt_pp(eng["behavioral_side_effects_guard"]["false_success_diff_vs_B0"]),
                "met" if eng["behavioral_side_effects_guard"]["target_met"] else "not_met",
            ),
            "| Unit Cost per Exact Success | Lower cost or higher completion w/ finite cost | `B0`: `{}` att, `P`: `{}` att (`{}` tok) | `{}` |".format(
                "N/A"
                if eng["cost_per_success_efficiency"]["b0_tool_attempts_per_exact_completion"] is None
                else f"{eng['cost_per_success_efficiency']['b0_tool_attempts_per_exact_completion']:.2f}",
                "N/A"
                if eng["cost_per_success_efficiency"]["p_tool_attempts_per_exact_completion"] is None
                else f"{eng['cost_per_success_efficiency']['p_tool_attempts_per_exact_completion']:.2f}",
                "N/A"
                if eng["cost_per_success_efficiency"]["p_tokens_per_exact_completion"] is None
                else f"{eng['cost_per_success_efficiency']['p_tokens_per_exact_completion']:.0f}",
                eng["cost_per_success_efficiency"]["status"],
            ),
        ]
    )

    if att.get("available"):
        lines.extend(
            [
                "| **Core Attribution (`P` vs `B3` Error SFT)** | Reproducible gain over matched failure-point SFT | Prefix Rec $\\Delta$: `{}` `{}`, E2E Comp $\\Delta$: `{}` `{}` | **`{}`** |".format(
                    _fmt_pp(att["prefix_error_recovery_exact_diff"]),
                    _fmt_ci_pp(att["prefix_error_recovery_exact_ci95"]),
                    _fmt_pp(att["e2e_task_completion_exact_diff"]),
                    _fmt_ci_pp(att["e2e_task_completion_exact_ci95"]),
                    att["relation"],
                ),
                "",
                "### Proposal §8.2 Scientific Interpretation",
                "",
                f"> {att['honest_interpretation']}",
            ]
        )

    lines.append("")
    return "\n".join(lines)


def compare_g3_g4_results(config, workspace_root=ROOT):
    before_ml = {m for m in FORBIDDEN_ML_MODULES if m in sys.modules}
    require(isinstance(config, dict), "comparison config must be a JSON object")
    for req_section in ("dev_selection_runs", "g4_e2e_runs", "g4_prefix_runs"):
        require(
            isinstance(config.get(req_section), dict) and config[req_section],
            f"missing or empty required dict section: {req_section}",
        )

    workspace_root = Path(workspace_root).resolve()
    g4_manifest_path = _resolve_path(
        config.get("g4_manifest_path", "data/processed/g4_holdout_manifest_ITER009.json"),
        workspace_root,
    )
    dev_manifest_path = _resolve_path(
        config.get("dev_manifest_path", "data/processed/grouped_split_manifest_ITER007.json"),
        workspace_root,
    )

    g4_manifest = json.loads(g4_manifest_path.read_text(encoding="utf-8")) if g4_manifest_path.is_file() else None
    dev_manifest = json.loads(dev_manifest_path.read_text(encoding="utf-8")) if dev_manifest_path.is_file() else None

    def _ordered_arms(arm_dict):
        unknown = set(arm_dict.keys()) - ALLOWED_ARMS
        require(not unknown, f"unknown arm names: {sorted(unknown)}")
        require({"B0", "P"}.issubset(arm_dict.keys()), "both B0 and P are required")
        return [a for a in CANONICAL_ARM_ORDER if a in arm_dict]

    dev_order = _ordered_arms(config["dev_selection_runs"])
    e2e_order = _ordered_arms(config["g4_e2e_runs"])
    pfx_order = _ordered_arms(config["g4_prefix_runs"])

    dev_bundles = collections.OrderedDict(
        (arm, _load_arm_bundle(arm, config["dev_selection_runs"][arm], "end_to_end", workspace_root))
        for arm in dev_order
    )
    e2e_bundles = collections.OrderedDict(
        (arm, _load_arm_bundle(arm, config["g4_e2e_runs"][arm], "end_to_end", workspace_root))
        for arm in e2e_order
    )
    pfx_bundles = collections.OrderedDict(
        (arm, _load_arm_bundle(arm, config["g4_prefix_runs"][arm], "fixed_failure_prefix", workspace_root))
        for arm in pfx_order
    )

    strict_dev = bool(config.get("strict_same_device", False))
    dev_device_info = _verify_suite_pairing(
        dev_bundles,
        "dev_selection_runs",
        expected_case_ids=dev_manifest["mps_unseen_eval24_case_ids"] if dev_manifest and config.get("enforce_manifest_ids", True) else None,
        expected_dataset_sha=dev_manifest["dataset_sha256"] if dev_manifest and config.get("enforce_manifest_ids", True) else None,
        strict_same_device=strict_dev,
    )
    e2e_device_info = _verify_suite_pairing(
        e2e_bundles,
        "g4_e2e_runs",
        expected_case_ids=g4_manifest["g4_e2e_case_ids"] if g4_manifest and config.get("enforce_manifest_ids", True) else None,
        expected_dataset_sha=g4_manifest["dataset_sha256"] if g4_manifest and config.get("enforce_manifest_ids", True) else None,
        strict_same_device=strict_dev,
    )
    pfx_device_info = _verify_suite_pairing(
        pfx_bundles,
        "g4_prefix_runs",
        expected_case_ids=g4_manifest["g4_prefix_case_ids"] if g4_manifest and config.get("enforce_manifest_ids", True) else None,
        expected_dataset_sha=g4_manifest["dataset_sha256"] if g4_manifest and config.get("enforce_manifest_ids", True) else None,
        strict_same_device=strict_dev,
    )

    dev_summaries = collections.OrderedDict((a, summarize_arm_bundle(b)) for a, b in dev_bundles.items())
    e2e_summaries = collections.OrderedDict((a, summarize_arm_bundle(b)) for a, b in e2e_bundles.items())
    pfx_summaries = collections.OrderedDict((a, summarize_arm_bundle(b)) for a, b in pfx_bundles.items())

    strongest_untrained = select_strongest_untrained_on_dev(dev_summaries)
    su_arm = strongest_untrained["selected_arm"]

    # Optional multi-training-seed runs (`s101, s202, s303` for P and B3)
    multi_e2e_bundles = collections.OrderedDict()
    multi_e2e_summaries = collections.OrderedDict()
    for arm, ref_list in (config.get("multi_train_seed_runs") or {}).items():
        require(arm in ALLOWED_ARMS and isinstance(ref_list, list) and len(ref_list) >= 2, f"invalid multi_train_seed_runs for {arm}")
        loaded = [
            _load_arm_bundle(arm, item.get("runs", item) if isinstance(item, dict) and "runs" in item else item, "end_to_end", workspace_root, idx + 1)
            for idx, item in enumerate(ref_list)
        ]
        temp_map = collections.OrderedDict([("anchor_P", e2e_bundles["P"])] + [(f"{arm}_{i}", b) for i, b in enumerate(loaded)])
        _verify_suite_pairing(temp_map, f"multi_train_seed_runs[{arm}]")
        multi_e2e_bundles[arm] = loaded
        multi_e2e_summaries[arm] = summarize_multi_train_seeds(arm, loaded)

    multi_pfx_bundles = collections.OrderedDict()
    multi_pfx_summaries = collections.OrderedDict()
    for arm, ref_list in (config.get("multi_train_seed_prefix_runs") or {}).items():
        require(arm in ALLOWED_ARMS and isinstance(ref_list, list) and len(ref_list) >= 2, f"invalid multi_train_seed_prefix_runs for {arm}")
        loaded = [
            _load_arm_bundle(arm, item.get("runs", item) if isinstance(item, dict) and "runs" in item else item, "fixed_failure_prefix", workspace_root, idx + 1)
            for idx, item in enumerate(ref_list)
        ]
        temp_map = collections.OrderedDict([("anchor_P", pfx_bundles["P"])] + [(f"{arm}_{i}", b) for i, b in enumerate(loaded)])
        _verify_suite_pairing(temp_map, f"multi_train_seed_prefix_runs[{arm}]")
        multi_pfx_bundles[arm] = loaded
        multi_pfx_summaries[arm] = summarize_multi_train_seeds(arm, loaded)

    # Build all required template-clustered paired contrasts on G4 E2E and G4 Prefix
    contrast_specs = [
        ("P_vs_B0", "B0", "B0"),
        ("P_vs_Strongest_Untrained", su_arm if su_arm in e2e_bundles else "B0", f"Strongest_Untrained({su_arm if su_arm in e2e_bundles else 'B0'})"),
        ("P_vs_B3", "B3", "B3 (Error SFT)"),
        ("P_vs_B4", "B4", "B4 (Single-Token FTPO)"),
        ("P_vs_B5", "B5", "B5 (General Expert DPO)"),
        ("P_vs_P_no_retry", "P_no_retry", "P_no_retry"),
        ("P_vs_P_no_normal", "P_no_normal", "P_no_normal"),
        ("P_vs_B1", "B1", "B1"),
        ("P_vs_B2", "B2", "B2"),
    ]

    e2e_contrasts = collections.OrderedDict()
    for ckey, rhs_arm, rhs_title in contrast_specs:
        if rhs_arm in e2e_bundles:
            e2e_contrasts[ckey] = compute_clustered_paired_comparison(
                e2e_bundles["P"], e2e_bundles[rhs_arm], "P", rhs_title, "end_to_end"
            )
    if "P" in multi_e2e_bundles and "B3" in multi_e2e_bundles:
        e2e_contrasts["P_vs_B3_3train_seed_cluster_avg"] = compute_clustered_paired_comparison(
            multi_e2e_bundles["P"],
            multi_e2e_bundles["B3"],
            "P (3-train-seed avg)",
            "B3 (3-train-seed avg)",
            "end_to_end",
        )

    pfx_contrasts = collections.OrderedDict()
    for ckey, rhs_arm, rhs_title in contrast_specs:
        target_arm = (su_arm if su_arm in pfx_bundles else "B0") if ckey == "P_vs_Strongest_Untrained" else rhs_arm
        target_title = f"Strongest_Untrained({target_arm})" if ckey == "P_vs_Strongest_Untrained" else rhs_title
        if target_arm in pfx_bundles:
            pfx_contrasts[ckey] = compute_clustered_paired_comparison(
                pfx_bundles["P"], pfx_bundles[target_arm], "P", target_title, "fixed_failure_prefix"
            )
    if "P" in multi_pfx_bundles and "B3" in multi_pfx_bundles:
        pfx_contrasts["P_vs_B3_3train_seed_cluster_avg"] = compute_clustered_paired_comparison(
            multi_pfx_bundles["P"],
            multi_pfx_bundles["B3"],
            "P (3-train-seed avg)",
            "B3 (3-train-seed avg)",
            "fixed_failure_prefix",
        )

    verdicts = evaluate_honest_hypothesis_verdicts(
        strongest_untrained_info=strongest_untrained,
        g4_e2e_summaries=e2e_summaries,
        g4_pfx_summaries=pfx_summaries,
        e2e_contrasts=e2e_contrasts,
        pfx_contrasts=pfx_contrasts,
        multi_seed_e2e=multi_e2e_summaries,
        multi_seed_pfx=multi_pfx_summaries,
    )

    after_ml = {m for m in FORBIDDEN_ML_MODULES if m in sys.modules}
    require(after_ml == before_ml, f"comparator imported forbidden ML modules: {sorted(after_ml - before_ml)}")

    return {
        "schema_version": COMPARATOR_SCHEMA_VERSION,
        "evidence_class": "analysis",
        "zero_model_execution": True,
        "detector_v2_version": DETECTOR_V2_VERSION,
        "dev_strongest_untrained": strongest_untrained,
        "dev_selection": {
            "arm_order": dev_order,
            "device_pairing": dev_device_info,
            "arms": dev_summaries,
        },
        "g4_e2e": {
            "arm_order": e2e_order,
            "device_pairing": e2e_device_info,
            "arms": e2e_summaries,
        },
        "g4_prefix": {
            "arm_order": pfx_order,
            "device_pairing": pfx_device_info,
            "arms": pfx_summaries,
        },
        "multi_train_seed": {
            "e2e": multi_e2e_summaries,
            "prefix": multi_pfx_summaries,
        },
        "paired_clustered_contrasts": {
            "g4_e2e": e2e_contrasts,
            "g4_prefix": pfx_contrasts,
        },
        "honest_hypothesis_verdicts": verdicts,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True, help="Comparison JSON config path.")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for JSON and Markdown reports.")
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting existing report files.")
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    report = compare_g3_g4_results(config, workspace_root=ROOT)
    md_text = render_markdown_report(report)

    out_dir = args.output
    out_dir.mkdir(parents=True, exist_ok=True)
    json_out = out_dir / "g3_g4_comparison.json"
    md_out = out_dir / "g3_g4_comparison.md"
    for target in (json_out, md_out):
        if target.exists() and not args.overwrite:
            raise FileExistsError(f"refusing to overwrite {target} without --overwrite")

    json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_out.write_text(md_text, encoding="utf-8")
    print(
        json.dumps(
            {
                "json_report": str(json_out),
                "markdown_report": str(md_out),
                "dev_selected_strongest_untrained": report["dev_strongest_untrained"]["selected_arm"],
                "p_vs_b3_verdict": report["honest_hypothesis_verdicts"]["research_attribution_section_8_2"][
                    "p_vs_b3_error_sft"
                ].get("relation"),
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
