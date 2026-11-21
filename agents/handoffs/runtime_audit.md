# Runtime compatibility audit handoff

- Date: 2026-11-16, Asia/Shanghai; owner: `/root/source_audit`.
- Output: `docs/research/LOCAL_RUNTIME_PLAN.md` and this handoff only.
- Scope: read-only primary-source verification. No package install, weight download, model execution, or shared config/code edits by this agent.

## Immediate route sent to root

Use an isolated Python 3.12 environment with pinned `transformers==5.23.0` and `torch==2.15.0`; official release metadata requires Python >=3.11. Apple arm64 wheel verified. Start official unquantized checkpoint through built-in `Qwen3_5ForCausalLM`, `trust_remote_code=False`, no CUDA serving stack or remote kernels. CPU FP32/eager tiny probe before MPS; actual installation/load/speed results remain root's work.

## Material findings delivered to simulator

- Native Qwen3-Coder function/parameter tool syntax; assistant tool_calls arguments must be mapping.
- tokenizer EOS 248046 differs from text_config EOS 248044; use actual local-tokenizer/config verification and record explicit stop set. Earlier mistaken im_end label corrected immediately.
- Transformers native generation does not supply a same-name presence-penalty processor in inspected version. Additive penalty must consider only current generated suffix, not whole prompt/history, to match documented service semantics.
- Fixed tree has no generation_config.json; inspect derived local generation configuration rather than inventing a downloaded one.

## Download identity

Revision `2fc06364715b967f1860aea9cf38778875588b17`.
Weight file bytes `1746942600`; SHA256 `04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696` verified using HF raw pointer, not weight transfer.
Entire selected download byte count still needs actual manifest; UI ~1.77 GB is rounded, index tensor bytes are not full file bytes.

## Limitations

Documentation and source compatibility are not a local smoke result. MPS may need CPU fallback, and it must be logged. MLX official-source unquantized conversion is a fallback, not already benchmarked or installed. Avoid using current main docs as exact installed-version proof; record actual packages and loading diagnostics. Full sources and conservative gate sequence are in LOCAL_RUNTIME_PLAN.

## Follow-up: pre-run adapter source review

Read-only inspection at 2026-11-16T06:22:42Z, before final adapter freeze. No test or model execution by this agent; implementation was still being changed by its owner. Relevant source hashes at this checkpoint:

| File | SHA256 |
|---|---|
| toolloop_fix/model_backend.py | 7790f563f7c5a903ada9a3f3629d873fb6b8fe7b4ff4d20e645b69981f408286 |
| toolloop_fix/model_protocol.py | cee51e42a7434918fda990c68cd1dd9e902c63fd430e5f3cf208663e6c917b34 |
| toolloop_fix/model_run.py | b970aa27891de93bc54a03dda4f3d7b04d590aca9766f1c279093e2435167f0d |
| installed Transformers generation/utils.py | 01f7d1d68c9fd501ae12d516df408f3c098215ef53d8d8cbbe8104cab79e94b7 |

Findings sent to root and simulator:

1. Installed Transformers native text class and language-prefix conversion match the researched route. Loading diagnostics must still fail closed on missing/mismatched language weights before B0; owner was implementing this gate, not yet verified here.
2. Current presence penalty uses unique tokens only from current generated suffix. Installed generation/utils.py merges custom processors before temperature/top-k warpers (lines 1594 and 1613+), so no sampling-order issue was found by inspection.
3. EOS runtime union is appropriate, but actual tokenizer_config was not yet downloaded at this inspection. Verify loaded token IDs rather than treating this review as a loading receipt.
4. Official template forbids suffix after a tool call. Current parser accepts it and joins prefix/suffix as assistant content, which the next template renders before the call. Thus raw artifacts are preserved but replay ordering may change. Recommend strict suffix rejection or explicitly document normalization before broader B0. This is an adapter fidelity concern, not a claim of weight corruption.
5. Recommend explicit use_safetensors=True and logging PYTORCH_ENABLE_MPS_FALLBACK plus MPS built/available state. First-parameter device alone cannot establish that every operation ran on GPU.
6. No hidden-case/category/snapshot input to the model was found in the inspected public_view -> messages_for_view -> backend path. This is source inspection, not a complete information-flow proof.

Commands were targeted sed/rg reads and shasum of the named files, with no installs, network execution, weight access, or experiment creation. Subsequent adapter revisions need their own frozen tests/load evidence.
