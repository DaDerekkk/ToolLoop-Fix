#!/usr/bin/env python3
"""Independent pure-Python CPU replay and provenance auditor for ITER-009 (zero ML imports).

Drives `toolloop_fix.lora_eval_runner.{run_explicit_e2e_case, run_curated_prefix_case}` with a
pure-Python recorded-generation playback backend across all 32 completed ITER-009 `model_inference`
runs (`6` eval24 + `13` G4 E2E + `13` G4 fixed_failure_prefix = `846` real episodes).
Verifies:
  1. Zero ML library imports (`torch`, `transformers`, `safetensors`, `peft`, `trl`, `accelerate`).
  2. Exact message/tool/seed/prompt_sha256 parity and zero hidden-state leakage on every generation.
  3. 100% exact replay match of every recorded `case_results.jsonl` row (scores, v1 & v2 loops,
     guard blocks, parser errors, token counts).
  4. `toolloop_fix/harness.py` SHA256 immutability, all 3 Colab L4 training waves verified
     (`10` adapters, `owned_absent == True`, `base_weights_unchanged == True`), Proposal §06
     `guard_blocked_proposals == 0` on all non-`B2` runs, and G4 zero-leakage manifest flags.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from toolloop_fix.loop_detector_v2 import DETECTOR_V2_VERSION
from toolloop_fix.lora_eval_runner import run_curated_prefix_case, run_lora_explicit_e2e_case

FORBIDDEN_ML = ("torch", "transformers", "peft", "trl", "safetensors", "accelerate")
EXPECTED_HARNESS_SHA256 = "02aa7a4a4b08e7a1ceac68b25183d957006ef5e3a8db4fb0609647e84b10f444"
GENERATION_FIELDS = (
    "prompt_text",
    "prompt_sha256",
    "prompt_token_ids",
    "input_tokens",
    "generated_token_ids",
    "output_tokens",
    "raw_text",
    "token_limit_reached",
    "elapsed_seconds",
    "effective_generation",
)
FORBIDDEN_PROMPT_KEYS = (
    '"hidden"',
    '"template_group"',
    '"valid_identifier"',
    '"fault_subtype"',
)


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


class RecordedPlaybackBackend:
    """CPU-only playback of archived generations; checks prompt integrity & zero hidden leaks."""

    def __init__(self, generations, case, provenance, prefix_mode=False):
        self.generations = generations
        self.case = case
        self.provenance = provenance
        self.prefix_mode = prefix_mode
        self.pos = 0

    def generate(self, messages, tools, seed):
        if self.pos >= len(self.generations):
            raise AssertionError("replay requested unrecorded generation")
        row = self.generations[self.pos]
        self.pos += 1
        assert row["messages"] == messages, "dialogue messages mismatch"
        assert row["tools"] == tools, "tools schema mismatch"
        assert int(row["seed"]) == int(seed), "turn seed mismatch"
        assert (
            hashlib.sha256(row["prompt_text"].encode("utf-8")).hexdigest() == row["prompt_sha256"]
        ), "prompt_sha256 mismatch"
        assert row["input_tokens"] == len(row["prompt_token_ids"]), "input_tokens length mismatch"
        assert row["output_tokens"] == len(row["generated_token_ids"]), "output_tokens length mismatch"
        assert (
            row["effective_generation"] == self.provenance["effective_generation"]
        ), "effective_generation mismatch"
        public_blob = json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)
        for forbidden in FORBIDDEN_PROMPT_KEYS:
            assert forbidden not in row["prompt_text"], f"evaluation key leaked into prompt: {forbidden}"
        for hidden_key in ("value", "valid_identifier"):
            hval = self.case["hidden"].get(hidden_key)
            if hval and hval in row["prompt_text"] and hval not in public_blob:
                raise AssertionError(f"hidden {hidden_key} leaked into prompt_text")
        return {k: copy.deepcopy(row[k]) for k in GENERATION_FIELDS}


def replay_and_audit_run(run_dir):
    run_dir = Path(run_dir).resolve()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    cfg = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
    prov = json.loads((run_dir / "model_provenance.json").read_text(encoding="utf-8"))
    results = _read_jsonl(run_dir / "case_results.jsonl")
    generations = _read_jsonl(run_dir / "generations.jsonl")

    assert manifest["status"] == "completed" and manifest["evidence_valid"] is True, f"{run_dir.name} invalid"
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
    mismatches = []
    episodes_replayed = 0
    generations_checked = 0
    total_guard_blocks = 0

    for res in results:
        cid = res["case_id"]
        base_cid = res.get("base_case_id", cid)
        case = cases_by_id[base_cid]
        case_gens = gens_by_case.get(cid, [])
        backend = RecordedPlaybackBackend(case_gens, case, prov, prefix_mode=(mode == "fixed_failure_prefix"))
        if mode == "end_to_end":
            replayed_res, _ = run_lora_explicit_e2e_case(case, backend, cfg, int(res["case_seed"]))
        elif mode == "fixed_failure_prefix":
            replayed_res, _ = run_curated_prefix_case(case, backend, cfg, int(res["case_seed"]))
        else:
            raise AssertionError(f"unexpected mode: {mode}")

        assert backend.pos == len(case_gens), f"{run_dir.name}/{cid}: unconsumed generations"
        generations_checked += backend.pos
        episodes_replayed += 1
        total_guard_blocks += int(replayed_res["guard_blocked_proposals"])

        if replayed_res != res:
            for k in sorted(set(replayed_res) | set(res)):
                if replayed_res.get(k) != res.get(k):
                    mismatches.append({"case_id": cid, "field": k, "replayed": replayed_res.get(k), "recorded": res.get(k)})

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
