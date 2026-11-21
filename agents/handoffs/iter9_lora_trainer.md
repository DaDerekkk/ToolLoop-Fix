# ITER-009 Handoff — `lora_trainer9` (`T019`)

## 1. Scope & Strict Boundaries Respected
- **Owner / Task**: `lora_trainer9` (`T019`)
- **Created files (exact 4-file allowlist)**:
  1. `toolloop_fix/lora_adapter.py`
  2. `toolloop_fix/lora_eval_runner.py`
  3. `tests/test_lora_and_losses.py`
  4. `agents/handoffs/iter9_lora_trainer.md`
- **Untouched**: `toolloop_fix/harness.py`, `toolloop_fix/model_backend.py`, `toolloop_fix/iter7_runners.py`, `toolloop_fix/loop_detector_v2.py`, `scripts/record_run.py`, `scripts/validate_records.py`, and all existing experiments/configs/packages. Zero network or Colab calls executed.

## 2. Key Technical Details

### A. `toolloop_fix/lora_adapter.py` (`LORA_ADAPTER_VERSION = "pure_pytorch_qwen35_text_lora_v1"`)
- **Standalone Colab & local parity**: Imports only Python stdlib (`ctypes`, `hashlib`, `json`, `math`, `pathlib`) plus lazy `torch` / `torch.nn.functional` and `safetensors`, with zero imports of `toolloop_fix.*` modules (verified both by AST audit and by subprocess execution from an isolated temporary directory without `toolloop_fix` on `sys.path`).
- **Target & Exclusion Coverage**:
  - `TARGET_LEAF_MODULES`: `("q_proj", "k_proj", "v_proj", "o_proj", "in_proj_qkv", "in_proj_z", "in_proj_a", "in_proj_b", "out_proj", "gate_proj", "up_proj", "down_proj")` — covers both Qwen3.5 full-attention layers and gated linear-attention layers + SwiGLU MLPs (**186 Linear modules / 2,705,664 trainable scalars at `rank=4`, ~5.16 MiB in `bfloat16`** on Qwen3.5-0.8B).
  - `EXCLUDED_PREFIXES`: `("visual.", "model.visual.", "vision_tower.", "model.vision_tower.", "mtp.", "model.mtp.", "lm_head")`.
  - **Important Qwen3.5 `CausalLM` vs `ConditionalGeneration` alignment**: HuggingFace `Qwen3_5ForCausalLM` (used by `TransformersBackend`) strips `.language_model` on checkpoint load so text layers live at `model.layers.*`, whereas `Qwen3_5ForConditionalGeneration` places them at `model.language_model.layers.*` in the exact same sorted order. `inject_lora` and `load_lora_safetensors` transparently resolve and cross-align `model.language_model.layers.*` <-> `model.layers.*` while still failing closed (`ValueError`) on any missing, unexpected, or shape-mismatched tensor keys.
- **Step-0 Exact Identity & Base Immutability**:
  - `LoRALinear` freezes `base.weight` (and optional `base.bias`), initializes `lora_A` on an isolated CPU `torch.Generator().manual_seed(seed + idx * 1009)` (`normal_(0.0, 0.02)`) in sorted module-name order, and zeros `lora_B` (`max_abs_diff == 0.0` at step 0 and whenever `set_lora_enabled(model, False)` is set).
  - `base_weight_digest(model)` canonicalizes `<mod>.base.weight` -> `<mod>.weight` and hashes `(name, shape, dtype, raw_bytes_cpu)` via zero-copy `uint8` `ctypes` `memoryview` (avoiding PyTorch `bfloat16` `.numpy()` limitations), yielding identical SHA256 digests before `inject_lora`, after `inject_lora`, and after optimizer steps.
- **Causal Token-Aligned Losses**:
  - `sequence_completion_stats(logits, full_ids, prompt_len)`: slices `logits[0, prompt_len - 1 : L - 1].float()` against `full_ids[prompt_len : L]`, masking 100% of prompt-internal predictions (`0.0` gradient outside `[prompt_len - 1, L - 1)`).
  - `single_token_divergence_logprobs(logits, prompt_len, div_offset, div_chosen_id, div_rejected_id)`: evaluates `pos = prompt_len + div_offset - 1`, leaving `logits.grad[0, :pos]` and `logits.grad[0, pos+1:]` identically `0.0` so shared `<tool_call>` prefixes before `div_offset` are never suppressed in `B4`.
  - `dpo_step_loss(...)`: computes reference-anchored `beta * ((pol_c - ref_c) - (pol_r - ref_r))` with `-F.logsigmoid` plus `sft_anchor * chosen_nll`.

### B. `toolloop_fix/lora_eval_runner.py` (`LORA_EVAL_RUNNER_VERSION = "iter9_lora_and_g4_eval_runner_v1"`)
- Supports CLI subcommands:
  1. `explicit-e2e`: calibrated explicit (`read_report_literal_target_v1`) `end_to_end` runner for both base arms (`B0`, `B1`, `B2` with `lora_adapter: null`) and LoRA-adapted arms (`B3`, `B4`, `B5`, `P`, `P_no_retry`, `P_no_normal`).
  2. `curated-prefix`: deterministic replay of `case["fixed_prefix"]` in `Environment` under `EXPLICIT_TASK_PREFIX` using canonical `<tool_call>` XML history (producing identical post-prefix snapshots, dialogue histories, and remaining `tool_budget - len(fixed_prefix)` / `turn_budget - len(fixed_prefix)` budgets across `B0..P`), supporting both single `case_seeds` and multi-schedule `seed_schedules`. Reports `recoverable` and `retry_required` recovery separately per `EVALUATION_SPEC.md`.
  3. `explicit-e2e-multiseed`: evaluates `seed_schedules` (`s1..s3`) in a **single model + adapter load**, tagging `case_id: f"{cid}@{sid}"`, `base_case_id: cid`, `schedule_id: sid`.
- **Proposal §06 Fairness & Provenance Enforcement**:
  - Trained LoRA configs must declare `comparison_arm == "B0"` (rejecting `B1` prompt additions and `B2` runtime guards) and include a SHA256-verified `adapter_manifest` in `config["external_inputs"]` matching `lora_adapter.safetensors_sha256` (supporting both single-adapter and multi-adapter `adapters` manifests).
  - Every `case_results.jsonl` and `trajectories.jsonl` row records both v1 (`has_invalid_loop`, `loop_flags`) and G2-frozen v2 (`has_invalid_loop_v2`, `loop_flags_v2`, `detector_v2_version = "nonretryable_repeat_without_progress_v2"`), and `model_provenance.json` records `adapter_provenance` (including `base_weights_unchanged_on_load: true` and `train_seed`).

## 3. Actual Verification Commands & Results
1. `.venv` PyTorch + `safetensors` + tiny HF `Qwen3_5ForCausalLM` unit suite:
   - Command: `.venv/bin/python -m unittest tests/test_lora_and_losses.py -v`
   - Result: **10 tests ran in 1.41s — `OK` (exit code `0`)**, including `validate_records.config_errors == []` and `validate_records.model_artifact_errors == []` across all 3 subcommands (`explicit-e2e`, `curated-prefix` single & multiseed, `explicit-e2e-multiseed`).
2. System pure-stdlib Python fallback check (no `torch`/`safetensors` installed):
   - Command: `python3 -m unittest tests/test_lora_and_losses.py -v`
   - Result: **10 tests ran in 0.088s — `OK (skipped=5)` (exit code `0`)**, confirming stdlib importability and zero regression for pure-stdlib test discovery.
3. Archive integrity check (read-only):
   - Command: `python3 scripts/validate_records.py`
   - Result: `{"runs_checked": 74, "errors": []}` (exit code `0`).

## 4. Next Steps for Coordinator (`T022`)
- Freeze Phase A inputs after `T020` and `T021` complete their unit tests, execute the authorized Colab L4 training lifecycle (`COLAB-20261120-005`), verify reassembled `.safetensors` adapters, and run `python -m toolloop_fix.lora_eval_runner` via `scripts/record_run.py` on `dev_InDomain_selection` (`eval24`) followed by the sealed G4 holdout (`explicit-e2e-multiseed` and `curated-prefix`).
