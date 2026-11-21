# ITER-009 Handoff — `colab_ops9` (`T020`: Colab L4 Detached G3 Training Pipeline & Offline Verifier)

- **Date**: 2026-11-20 (Asia/Shanghai)
- **Owner**: `colab_ops9` (`T020`)
- **Inputs Read**: `AGENTS.md`, `README.md`, `PROGRESS.md`, `agents/TASK_BOARD.md`, `agents/USER_WORKING_AGREEMENTS.md`, `docs/protocols/EXPERIMENT_WORKFLOW.md`, `docs/protocols/EVALUATION_SPEC.md`, `docs/protocols/COLAB_RECEIPT_WORKFLOW.md`, `agents/reviews/iteration6_l4_execution.md`, `operations/COLAB-20261119-004/lifecycle.py`, `operations/COLAB-20261119-004/worker.py`, `experiments/EXP-20261119-035/operation_sources/lifecycle.py`, `experiments/EXP-20261120-062/stdout.log`, `experiments/EXP-20261120-062/public_colab_training_bundle.json`, `toolloop_fix/lora_adapter.py`, `toolloop_fix/lora_eval_runner.py`.
- **Phase A Boundary Compliance**:
  - `~/.local/bin/colab` was **NOT** invoked; zero remote connections, downloads, or cloud sessions were touched.
  - Zero existing files were edited; `scripts/record_run.py` was **NOT** called.

---

## 1. Deliverables & Pinned SHA256 Digests

| File | SHA256 | Role |
|---|---|---|
| `scripts/colab_g3_train_worker.py` | `c605666c5d60b2f50a106c075765624971959a6008e3e8779cfc89c648e26b1e` | Detached one-shot remote Colab L4 multi-arm LoRA training worker + `TLP_CHUNK_V1` / `TLP_G3_RECEIPT_V1` codecs |
| `scripts/run_colab_g3_lifecycle.py` | `449bd317335eb42f2088dc734e24f22e1935019b5fde1eb757434e54339a74de` | Client-side L4 lifecycle controller, 2-way sentinel gate, chunk downloader/reassembler, and mandatory `finally:` teardown |
| `scripts/verify_colab_g3_training.py` | `6082f30ccb19e9c9858ad8976678ba11b867ba75b247089961aa482282d22f13` | Offline `analysis` verifier compatible with `scripts/record_run.py` (`colab_g3_verification.json` + `.md`) |
| `tests/test_colab_g3_pipeline.py` | `9f24e13f3ee2dd3db76ee80995e1abef856e027e05287257b7c8de845478f1ca` | 7 end-to-end offline CPU unit tests covering worker training (`B3`/`B4`/`B5`/`P`), chunking, mocked CLI lifecycle, and verifier |
| `agents/handoffs/iter9_colab_ops.md` | *(this handoff)* | Phase B execution guide, pinned hashes, and verification log |

Cross-checked against `T019` (`lora_trainer9`):
- `toolloop_fix/lora_adapter.py`: `861fb15b746161f505cf79340d95065a0c6c58e23f1ca1d33f5461a8cbdc4dbc`
- `experiments/EXP-20261120-062/public_colab_training_bundle.json`: `08d833addafcf94dcd07ff7ad229c82b3616e92cc34b5636d88d6864f6eda637` (`13,305,451` bytes raw -> `288,581` bytes `bundle.zlib`, SHA256 `9bf9ad48082db05b7420e3acec90ad25ea1207e3a6a38ab41ecd062c1f7104d4`).

---

## 2. Architecture & Safety Details

1. **Detached Worker (`scripts/colab_g3_train_worker.py`)**:
   - Acquires exclusive `started.lock` (`return 3` on duplicate launch) and publishes atomic `stage.txt` transitions (`waiting_for_arm` -> `armed` -> `install_transformers` -> `runtime_check` -> `anonymous_public_download` -> `verify_public_snapshot` -> `verify_training_bundle` -> `load_base_model` -> `tokenize_and_precompute_ref` -> `train_<adapter_id>` -> `pack_chunks` -> `receipt_published_completed` / `receipt_published_failed`).
   - Performs zero package/network/GPU work until **both** `bundle.zlib` exists (`> 0` bytes) **and** `armed.txt` equals `nonce.txt`.
   - Pins the VM's preinstalled CUDA `torch` version in `keep_torch.txt` while installing `transformers==5.23.0` (and `safetensors` if missing).
   - Verifies NVIDIA `L4` + `bfloat16` support, enables TF32 Tensor Cores (`allow_tf32 = True`) for `Qwen3_5` FP32 `chunk_gated_delta_rule` & `LoRALinear` matmuls, downloads public `Qwen/Qwen3.5-0.8B` at revision `2fc06364715b967f1860aea9cf38778875588b17` (`token=False`), and verifies all 9 file sizes & SHA256s.
   - Deduplicates prompt/sequence tokenization across all 6 bundle datasets (`800` unique prompts/completions), verifies remote `B4` `first_divergence_offset` and divergence token IDs (`400/400`), and precomputes exact frozen-base reference `sum_logprob` / `mean_nll` / single-token divergence log-probs with `set_lora_enabled(model, False)`.
   - Uses causally invariant right-padded micro-batching (`micro_batch_sequences=4` during training, `eval_batch_sequences=8` during `inference_mode` reference/post-eval — verified bit/float equivalent to `micro_batch_sequences=1` on `Qwen3_5ForCausalLM` to `< 2e-7` logits and `1.3e-8` gradients) so all 10 jobs (`B3_s101`, `B4_s101`, `B5_s101`, `P_s101`, `P_no_retry_s101`, `P_no_normal_s101`, `P_s202`, `P_s303`, `B3_s202`, `B3_s303`) cut Python/CUDA kernel-launch overhead by 4x–8x while keeping peak VRAM well under L4's 24 GiB.
   - Compresses each `.safetensors` with `zlib.compress(raw, 9)` into `<= 350,000`-byte slices framed as `< 480 KB` `TLP_CHUNK_V1:<nonce>:<adapter_id>:<part_idx>:<total_parts>:<part_sha256>:<base64>\n`, verifies `base_weight_digest(model) == initial_base_digest`, and atomically publishes `g3_training_receipt.json` + `receipt_frame.txt`.

2. **Lifecycle Controller (`scripts/run_colab_g3_lifecycle.py`)**:
   - Verifies baseline `colab sessions` (storing irreversible endpoint SHA256 digests only) and `colab usage`, allocates exclusive `tlfix-<12hex>` `--gpu L4`, verifies isolation (`set(cur) - {own} == set(before)` and CU/hr delta `<= 15`), warms kernel (`TLP_READY`), and verifies 2-way sentinel via **both** `exec` stdout and `colab download` **before** uploading `bundle.zlib` (`288 KB`) and `armed.txt`.
   - Retries transient single-chunk `colab download` hiccups up to 3 times without re-running training, reassembles & SHA256-verifies `<adapter_id>/adapter.safetensors`, and writes `<adapter_id>/adapter_manifest.json` (verified directly against `toolloop_fix.lora_eval_runner.verify_adapter_manifest` and `validate_records.py` `external_inputs`) plus `g3_adapters_manifest.json`.
   - Mandatory `finally:` block (with `SIGTERM -> KeyboardInterrupt` trap and independent cleanup timeouts) stops **only** our owned `tlfix-<12hex>` session and verifies `owned_absent == True` and `original_set_unchanged == True`.

3. **Offline Verifier (`scripts/verify_colab_g3_training.py`)**:
   - Resolves paths cleanly both in live workspace and inside `scripts/record_run.py` `<run_dir>/inputs` frozen snapshots.
   - Audits `lifecycle.jsonl` (teardown, original session preservation, 2-way sentinel, zero URL/token leakage), `g3_training_receipt.json` (`status == 'completed'`, `base_weights_unchanged == True`, L4 GPU, all 9 base model hashes matching local `model_manifest.json`), and every `<adapter_id>/adapter.safetensors` (SHA256, BF16 dtype, finite tensors, non-zero trained `lora_B`, `load_lora_safetensors` structural load, and optional local `AutoTokenizer` probe replay).

---

## 3. Phase B Invocation Reference (for `root` / `T022`)

Recommended lifecycle config fields (`configs/iter9_colab_g3_lifecycle.json`):
```json
{
  "expected_bundle_sha256": "08d833addafcf94dcd07ff7ad229c82b3616e92cc34b5636d88d6864f6eda637",
  "controller_sha256": "449bd317335eb42f2088dc734e24f22e1935019b5fde1eb757434e54339a74de",
  "source_sha256": {
    "lora_adapter.py": "861fb15b746161f505cf79340d95065a0c6c58e23f1ca1d33f5461a8cbdc4dbc",
    "colab_g3_train_worker.py": "c605666c5d60b2f50a106c075765624971959a6008e3e8779cfc89c648e26b1e"
  },
  "wall_limit_seconds": 1500,
  "poll_interval_seconds": 4.0,
  "lora_rank": 4,
  "lora_alpha": 8.0,
  "micro_batch_sequences": 4,
  "eval_batch_sequences": 8
}
```

Commands for `root` in Phase B:
```bash
# 1. Execute single authorized Colab L4 training lifecycle with mandatory teardown:
.venv/bin/python scripts/run_colab_g3_lifecycle.py \
  --config configs/iter9_colab_g3_lifecycle.json \
  --operation-dir operations/COLAB-20261120-005 \
  --adapters-out-dir models/adapters/iter9_g3

# 2. Archive & audit remote training provenance offline via record_run.py:
.venv/bin/python scripts/record_run.py \
  --id EXP-20261120-065 \
  --kind analysis \
  --config configs/iter9_verify_colab_g3.json \
  --hypothesis '离线审计核验Colab L4会话隔离释放、基座权重零变更与全部10个G3 LoRA适配器SHA256及张量完整性' \
  --acceptance 'verified=true；owned_absent=true；base_weights_unchanged=true；10个safetensors加载与tokenizer探针重放一致' \
  --owner root \
  -- .venv/bin/python scripts/verify_colab_g3_training.py --config '{config}' --output '{run_dir}' --verify-local-load
```
*(In `configs/iter9_verify_colab_g3.json`, set `"operation_dir": "operations/COLAB-20261120-005"`, `"adapters_dir": "models/adapters/iter9_g3"`, `"expected_bundle_sha256": "08d833addafcf94dcd07ff7ad229c82b3616e92cc34b5636d88d6864f6eda637"`, `"verify_local_load": true`, `"verify_tokenizer_probes": true`.)*

---

## 4. Actual Verification Executed in Phase A

1. `.venv/bin/python -m unittest tests/test_colab_g3_pipeline.py -v` -> **Ran 7 tests in 1.850s — OK**
   - `test_worker_started_lock_and_arm_gate`: exclusive lock (`rc=3`) + unarmed timeout fail-closed check.
   - `test_worker_trains_b3_b4_b5_p_and_packs_chunks`: end-to-end CPU training of `B3`, `B4`, `B5`, `P`; loss decrease on all 4 arms; `base_weights_unchanged == True`; multi-part `<480 KB` chunk framing & bit-identical `.safetensors` reassembly; `load_lora_safetensors` round-trip.
   - `test_chunk_reassembly_rejects_tamper_missing_and_wrong_nonce`: rejects wrong nonce, wrong `adapter_id`, missing part, duplicate index, tampered base64 payload, and wrong safetensors SHA256.
   - `test_worker_rejects_b4_divergence_mismatch`: fail-closed B4 remote tokenizer divergence verification.
   - `test_lifecycle_end_to_end_and_verifier_acceptance_and_rejections`: full mocked `colab` CLI lifecycle preserving baseline session, verifying 2-way sentinel pre-arm, recovering from injected `.part00.txt` download glitch, passing `lora_eval_runner.verify_adapter_manifest` and `verify_colab_g3_training.py`, and rejecting unreleased sessions, mutated base digests, and tampered `.safetensors`.
   - `test_lifecycle_mandatory_teardown_on_keyboard_interrupt`: proves `finally:` stops owned L4 session and preserves baseline sessions on `KeyboardInterrupt`.
   - `test_real_qwen35_micro_model_batched_vs_unbatched_equivalence`: proves `micro_batch_sequences=4` vs `1` numerical parity on a real 2-layer `Qwen3_5ForCausalLM` (`linear_attention` + `full_attention`).
2. Preflight bundle compression check on `EXP-20261120-062/public_colab_training_bundle.json` -> `288,581` bytes `bundle.zlib` (`SHA256 9bf9ad48082db05b7420e3acec90ad25ea1207e3a6a38ab41ecd062c1f7104d4`).
3. `.venv/bin/python scripts/validate_records.py` -> `{"runs_checked": 74, "errors": []}`.

---

## 5. Known Limitations & Boundaries
- Offline CPU unit tests verify worker logic, loss optimization, chunk framing/reassembly, CLI teardown state machine, and verifier gates; they do **not** constitute real L4 GPU training evidence until Phase B executes `run_colab_g3_lifecycle.py` and `verify_colab_g3_training.py` on actual Colab L4 outputs.
- Exact per-session Colab CU billing remains subject to platform settlement granularity; `lifecycle.jsonl` records server-reported before/allocated/final `balance_cu` and `rate_cu_hr` snapshots rather than inventing exact session charges.
