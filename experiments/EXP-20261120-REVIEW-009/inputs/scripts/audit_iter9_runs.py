#!/usr/bin/env python3
"""Independent pure-Python CPU replay and provenance auditor for ITER-009 (zero ML imports).

Replays every recorded episode across all 32 completed ITER-009 `model_inference` runs
(`6` eval24 + `13` G4 E2E + `13` G4 fixed_failure_prefix = `846` real episodes) purely from
`generations.jsonl` and frozen fixture JSONL files through `toolloop_fix.harness.Environment`,
`calibrated_protocol`, `baselines.guard_action`, `detect_loops` (v1), and frozen G2
`detect_loops_v2` (v2). Also audits all 3 Colab L4 training wave verification reports,
zero leakage, `toolloop_fix/harness.py` immutability, and Proposal §06 guard invariance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from toolloop_fix.baselines import guard_action
from toolloop_fix.calibrated_protocol import parse_calibrated_output, score_calibrated_episode
from toolloop_fix.harness import Environment, detect_loops, state_hash
from toolloop_fix.loop_detector_v2 import DETECTOR_V2_VERSION, detect_loops_v2, has_invalid_loop_v2

FORBIDDEN_ML = ("torch", "transformers", "peft", "trl", "safetensors", "accelerate")
EXPECTED_HARNESS_SHA256 = "02aa7a4a4b08e7a1ceac68b25183d957006ef5e3a8db4fb0609647e84b10f444"


def _resolve_archive_root(root):
    root = Path(root).resolve()
    if (root / "experiments").is_dir() and (root / "data/processed").is_dir():
        return root
    parent_manifest = root.parent / "manifest.json"
    if parent_manifest.is_file():
        src = json.loads(parent_manifest.read_text(encoding="utf-8")).get("source_workspace")
        if src and (Path(src) / "experiments").is_dir():
            return Path(src).resolve()
    return root


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def replay_and_audit_run(run_dir):
    run_dir = Path(run_dir).resolve()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    prov = json.loads((run_dir / "model_provenance.json").read_text(encoding="utf-8"))
    results = _read_jsonl(run_dir / "case_results.jsonl")
    trajectories = _read_jsonl(run_dir / "trajectories.jsonl")
    generations = _read_jsonl(run_dir / "generations.jsonl")

    assert manifest["status"] == "completed" and manifest["evidence_valid"] is True, f"{run_dir.name} not valid"
    assert manifest["inputs"]["toolloop_fix/harness.py"] == EXPECTED_HARNESS_SHA256, f"{run_dir.name} harness mismatch"

    cases_rows = _read_jsonl(run_dir / "inputs" / cfg["cases_path"])
    cases_by_id = {row["case_id"]: row for row in cases_rows}

    comp_arm = cfg["comparison_arm"]
    has_lora = cfg.get("lora_adapter") is not None
    if has_lora:
        assert comp_arm == "B0", f"{run_dir.name} LoRA run must use comparison_arm=B0"
        ap = prov.get("adapter_provenance") or {}
        assert ap.get("base_weights_unchanged_on_load") is True, f"{run_dir.name} base_weights_unchanged_on_load != True"
        assert ap.get("safetensors_sha256") == cfg["lora_adapter"]["safetensors_sha256"], f"{run_dir.name} adapter sha mismatch"

    gens_by_case = {}
    for g in generations:
        gens_by_case.setdefault(g["case_id"], []).append(g)

    mode = cfg["mode"]
    tool_budget = int(cfg["tool_budget"])
    turn_budget = int(cfg["turn_budget"])

    mismatches = []
    episodes_replayed = 0
    generations_checked = 0
    total_guard_blocks = 0

    for res, traj in zip(results, trajectories):
        cid = res["case_id"]
        assert traj["case_id"] == cid
        base_cid = res.get("base_case_id", cid)
        case = cases_by_id[base_cid]
        env = Environment(case)

        history = []
        attempts = 0
        actual_tools = 0
        blocks = 0

        if mode == "fixed_failure_prefix":
            fixed_prefix = case["fixed_prefix"]
            for step_action in fixed_prefix:
                before = state_hash(env.public_view())
                tool_res = env.execute_tool(step_action["tool"], step_action["arguments"])
                after = state_hash(env.public_view())
                history.append({"action": step_action, "result": tool_res, "before": before, "after": after})
                attempts += 1
                actual_tools += 1

        case_gens = gens_by_case.get(cid, [])
        generations_checked += len(case_gens)
        terminal = None
        turns = len(case["fixed_prefix"]) if mode == "fixed_failure_prefix" else 0
        parser_errors = 0

        for gen_row in case_gens:
            turns += 1
            raw_text = gen_row["raw_text"]
            parsed, err = parse_calibrated_output(raw_text, env.public_view()["tools"])
            if err is not None:
                parser_errors += 1
                attempts += 1
                continue
            if parsed["type"] in ("finish", "stop", "natural"):
                terminal = parsed
                break
            attempts += 1
            if comp_arm == "B2":
                blocked_reason = guard_action(parsed, history)
                if blocked_reason is not None:
                    blocks += 1
                    continue
            before = state_hash(env.public_view())
            tool_res = env.execute_tool(parsed["tool"], parsed["arguments"])
            after = state_hash(env.public_view())
            history.append({"action": parsed, "result": tool_res, "before": before, "after": after})
            actual_tools += 1

        outcome = score_calibrated_episode(case, env, terminal, attempts > tool_budget or (terminal is None and (attempts >= tool_budget or turns >= turn_budget)))
        flags_v1 = detect_loops(history)
        flags_v2 = detect_loops_v2(history)
        inv_v2 = has_invalid_loop_v2(flags_v2)

        total_guard_blocks += blocks
        episodes_replayed += 1

        checks = [
            ("exact_grounded_structured_completion", outcome["exact_grounded_structured_completion"], res["exact_grounded_structured_completion"]),
            ("tool_goal_attainment", outcome["tool_goal_attainment"], res["tool_goal_attainment"]),
            ("has_invalid_loop", bool(flags_v1), bool(res["has_invalid_loop"])),
            ("has_invalid_loop_v2", bool(inv_v2), bool(res["has_invalid_loop_v2"])),
            ("guard_blocked_proposals", blocks, int(res["guard_blocked_proposals"])),
            ("actual_tool_attempts", actual_tools, int(res["actual_tool_attempts"])),
            ("parser_error_turns", parser_errors, int(res["parser_error_turns"])),
            ("exact_stop_reason_code", outcome["exact_stop_reason_code"], res["exact_stop_reason_code"]),
            ("premature_stop_without_goal_evidence", outcome["premature_stop_without_goal_evidence"], res["premature_stop_without_goal_evidence"]),
            ("unsupported_finish_wrapper", outcome["unsupported_finish_wrapper"], res["unsupported_finish_wrapper"]),
        ]
        for field, expected_v, recorded_v in checks:
            if expected_v != recorded_v:
                mismatches.append({"case_id": cid, "field": field, "replayed": expected_v, "recorded": recorded_v})

    if comp_arm != "B2":
        assert total_guard_blocks == 0, f"{run_dir.name} non-B2 arm had guard_blocked_proposals={total_guard_blocks}"

    return {
        "run_id": run_dir.name,
        "mode": mode,
        "comparison_arm": comp_arm,
        "adapter_id": cfg.get("lora_adapter", {}).get("adapter_id") if has_lora else None,
        "episodes_replayed": episodes_replayed,
        "generations_checked": generations_checked,
        "guard_blocked_proposals": total_guard_blocks,
        "exact_replay_mismatches": len(mismatches),
        "mismatches": mismatches[:10],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    before_ml = {m for m in FORBIDDEN_ML if m in sys.modules}
    cfg = json.loads(args.config.read_text(encoding="utf-8"))
    arch = _resolve_archive_root(ROOT)

    harness_sha = _sha256_file(arch / "toolloop_fix/harness.py")
    assert harness_sha == EXPECTED_HARNESS_SHA256, "live harness.py SHA256 changed!"

    # Audit Colab verification runs
    colab_audits = []
    total_adapters_verified = 0
    for rid in cfg["colab_verification_runs"]:
        vpath = arch / "experiments" / rid / "colab_g3_verification.json"
        vobj = json.loads(vpath.read_text(encoding="utf-8"))
        assert vobj["verified"] is True, f"{rid} not verified"
        assert vobj["lifecycle_audit"]["owned_absent"] is True, f"{rid} owned_absent != True"
        assert vobj["receipt_and_adapter_audit"]["base_weights_unchanged"] is True, f"{rid} base_weights_unchanged != True"
        n_ad = int(vobj["receipt_and_adapter_audit"]["adapter_count"])
        total_adapters_verified += n_ad
        colab_audits.append({
            "run_id": rid,
            "verified": True,
            "owned_absent": True,
            "base_weights_unchanged": True,
            "adapter_count": n_ad,
            "fla_triton_enabled": vobj["receipt_and_adapter_audit"]["runtime"].get("fla_triton_enabled"),
        })

    # Audit all 32 ITER-009 model_inference runs
    run_audits = []
    total_episodes = 0
    total_generations = 0
    total_mismatches = 0
    for rid in cfg["model_inference_runs"]:
        res = replay_and_audit_run(arch / "experiments" / rid)
        run_audits.append(res)
        total_episodes += res["episodes_replayed"]
        total_generations += res["generations_checked"]
        total_mismatches += res["exact_replay_mismatches"]

    # Cross-check G4 manifest zero-leakage flags
    g4_manifest = json.loads((arch / "data/processed/g4_holdout_manifest_ITER009.json").read_text(encoding="utf-8"))
    disjoint = g4_manifest["disjointness_checks"]
    assert all(
        disjoint[k] is True
        for k in (
            "zero_case_id_overlap_with_all_prior",
            "zero_identifier_overlap_with_all_prior",
            "zero_raw_substring_overlap_with_exp062_dpo_records",
            "zero_target_overlap_with_all_prior",
            "zero_template_group_overlap_with_all_prior",
            "zero_value_overlap_with_all_prior",
        )
    ), "G4 disjointness check failed"

    after_ml = {m for m in FORBIDDEN_ML if m in sys.modules}
    assert after_ml == before_ml, f"forbidden ML modules imported: {sorted(after_ml - before_ml)}"

    report = {
        "schema_version": "iter9_independent_cpu_replay_audit_v1",
        "evidence_class": "analysis",
        "zero_ml_imports_verified": True,
        "harness_py_sha256": harness_sha,
        "harness_py_immutable": True,
        "detector_v2_version": DETECTOR_V2_VERSION,
        "colab_waves_audited": colab_audits,
        "total_colab_adapters_verified": total_adapters_verified,
        "model_inference_runs_audited": len(run_audits),
        "total_episodes_replayed": total_episodes,
        "total_generations_checked": total_generations,
        "total_exact_replay_mismatches": total_mismatches,
        "g4_zero_leakage_verified": True,
        "audit_passed": total_mismatches == 0 and total_adapters_verified == 10,
        "per_run_audits": run_audits,
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "iter9_replay_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "audit_passed": report["audit_passed"],
        "model_inference_runs_audited": report["model_inference_runs_audited"],
        "total_episodes_replayed": report["total_episodes_replayed"],
        "total_generations_checked": report["total_generations_checked"],
        "total_exact_replay_mismatches": report["total_exact_replay_mismatches"],
        "total_colab_adapters_verified": report["total_colab_adapters_verified"],
    }))
    return 0 if report["audit_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
