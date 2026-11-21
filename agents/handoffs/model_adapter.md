# Real-model adapter handoff — 2026-11-16

## Delivered and frozen scope
- `toolloop_fix/model_protocol.py`: official Qwen coder-style XML parser, public function schemas plus explicit `finish_task` / `stop_task`, full visible structured history.
- `toolloop_fix/model_backend.py`: lazy-imported offline Transformers / `Qwen3_5ForCausalLM`, raw safetensors only, no remote code, no quantization, explicit CPU float32 or MPS bfloat16; model identity/file verification and actual runtime provenance.
- `toolloop_fix/model_run.py`: actual-model E2E runner using existing in-memory Environment, accounting/evaluator/detector; no scripted fallback.
- `configs/model_preflight.json`: **unconfigured template**. Root must create runtime configs with absolute model path and verified manifest hash; template paths deliberately fail rather than finding arbitrary local models.
- `tests/test_model_adapter.py`: 26 standard-library tests; fake backend is explicitly a unit-test double, not model evidence.

## Actual verification performed by adapter agent
`python3 -m unittest discover -s tests -p 'test_model*.py' -v`: **26 tests passed** (earlier pass: 25, then added suffix-order regression).
No model inference, model download, package installation or weight loading was run by this agent. Real runtime execution belongs to root and must be recorded separately.

Tests cover XML parsing, no inferred JSON/plain-answer conversion, malformed/duplicate/multiple calls, explicit final/stop arguments, suffix rejection, exact raw preservation, matched assistant tool_calls / tool results, no hidden category/answer in inference input, complete failure history, output/call budgets, no false success from ungrounded final claims, retained failures, model-manifest hashes/path boundaries, incomplete checkpoint rejection, and additive generated-token-only presence penalty.

## Required runtime contract
CLI: `python -m toolloop_fix.model_run --config FILE --output DIR`.
- Configuration uses `model={model_id,revision,backend,model_path,device,dtype}` and `sampling={enable_thinking,do_sample,temperature,top_p,top_k,min_p,presence_penalty,repetition_penalty,max_new_tokens,seed}`.
- `external_inputs[name=model_manifest]` identifies a small root-created manifest `{model_id,revision,model_path,files:[{path,size_bytes,sha256}]}`. Runner verifies manifest hash, identity, all listed actual files and no unmanifested model/config/tokenizer files before loading.
- Pinned revision: `2fc06364715b967f1860aea9cf38778875588b17`. Default selected case: `orders-normal-001`; root can select more cases in a separately frozen config.
- `output_loading_info=True`: any missing/mismatched/error text weights or unexplained unexpected text keys fail closed. Only known unused vision-prefixed keys are allowed, and full loading diagnostics are stored.
- EOS list is the union of **actual** tokenizer.eos_token_id and loaded model.generation_config.eos_token_id, not guessed IDs. Official pinned files differ (`<|im_end|>` vs `<|endoftext|>`); both effective values are logged.
- Official text nonthinking sampling is configured at temp1/top_p1/top_k20/min_p0/presence2/repetition1. Additive presence penalty subtracts once per unique token in **the current generated suffix**, not the prompt/history. It is a custom LogitsProcessor because installed Transformers lacks a native equivalent; effective implementation/scope are recorded. This is not a repetition-penalty approximation.
- The official tokenizer `apply_chat_template(...tools=...,enable_thinking=False,add_generation_prompt=True)` is used, and its actual template SHA256 is recorded. Arguments in assistant tool_calls are mappings, as required by this template.
- Runtime provenance records executable/Python/packages, actual model class/parameter dtype+device, model generation config, MPS availability/built state and named fallback environment flag. No full environment variables or secrets are collected.

## Artifacts and failure handling
`model_provenance.json`, `generations.jsonl`, `trajectories.jsonl`, `case_results.jsonl`, `metrics.json`; additionally `execution_events.jsonl`, `partial_case.json`, and `model_failure.json` if needed.
Each generated turn retains raw text, token IDs/counts, rendered prompt and messages, parser decision, effective sampler and elapsed generation time. Outputs stream/flush per turn/case. Existing artifact names are never overwritten by a new run. Failure is retained; no missing result is replaced with scripted success.

## Adapter interpretation / limitations
- This is a **custom protocol preflight on development fixtures**, not native BFCL, frozen held-out evaluation or a claim of Qwen tool-loop weakness.
- Single call per turn is required. A multi-call model output executes no tools and counts as one invalid protocol-turn attempt, while its requested call count is retained. This accounting choice is explicit and must remain fixed across methods.
- Plain final prose is invalid in this adapter; terminal decisions must use finish/stop pseudo-tools. Prefix commentary is preserved; suffix prose after a tool call is rejected instead of being moved earlier in replay. Malformed output is retained as assistant content plus an explicit user protocol-error message; no fake tool response is invented.
- Output-token exhaustion ends the case unsuccessfully even if the partial text resembles a complete call. Context limit causes a retained runtime failure rather than silently truncating history.
- Seed is base + case index × turn budget + turn index; deterministic backend numerics across machines/devices are not promised. CPU and MPS are distinct recorded configurations, not interchangeable performance evidence.
- End-to-end only at this stage. Source environment/detector limitations remain; parser failures must be separated from model-decision failures. No training or model-vs-model result has been established.

## Real preflight metadata repair
- Root's `EXP-20261116-008` loaded checkpoint weights but failed **before generation** while JSON-encoding Transformers 5.23 loading diagnostics; `set` fields were not JSON-compatible. This is a framework adapter defect, not a model behavioral failure. Original run retained.
- Installed source confirms `LoadStateDictInfo.to_dict()` returns set-valued `missing_keys`, `unexpected_keys`, and `mismatched_keys` without conversion. Two regression tests were added first; root archived `EXP-20261116-009` with 28 tests / 2 expected errors before the fix.
- Minimal fix: preserve all loading-info fields, convert only top-level sets to deterministic sorted lists, and apply the same safe representation to fail-closed error diagnostics. The actual missing/mismatched weight gate still executes before successful normalization; nonempty missing-key sets continue to raise a checkpoint-load error rather than a JSON error.
- Actual post-fix narrow diagnostic: `python3 -m unittest discover -s tests -p 'test_model*.py' -v` — **28 tests passed**. No model re-run was performed by this agent; root owns the next archived combined test / CPU run.
