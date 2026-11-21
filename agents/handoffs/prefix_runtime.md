# Fixed-prefix runtime handoff — ITER-004

## New files only
- `toolloop_fix/prefix_run.py`
- `tests/test_prefix_run.py`
- `docs/protocols/FIXED_PREFIX_RECOVERY.md`
- this handoff

No existing inference/core/scoring modules, prior results or historical artifacts were edited. No model/GPU call, installation, download or training was performed by this agent.

## API / runtime contract
`PREFIX_SCHEMA_VERSION=authentic_tool_prefix_v1`, `PREFIX_RUNTIME_VERSION=verified_prefix_continuation_v1`, `TASK_WORDING_VERSION=read_report_literal_target_v1`.

- `prepare_prefix(row, config)` verifies pinned source records, actual historical model-inference kind, original fixture, unchanged environment code, raw parsed actions/responses, state and full prefix snapshot. It rebuilds public dialogue, never trusts snapshot alone or reuses source system prompts.
- `run_prefix_case(row, backend, config, callbacks..., prepared=None)` runs only fresh continuation; cached prepared contexts bind row/arm/wording/total budgets. Every call clones a fresh Environment from the verified snapshot so branches cannot contaminate each other.
- `prefix_metrics(results)` reuses calibrated terminal scoring but adds explicit recovery denominator, already-at-goal controls, continuation/historical costs and inherited/new-loop separation.
- CLI: `python -m toolloop_fix.prefix_run --config FILE --output DIR`. Root owns actual configs/data/model runs. Standard model artifacts plus `prefix_contexts.jsonl`; streaming partial/failure retention and no overwrite.

The exact explicit public objective is frozen in the SOP. Only initial user text changes; Environment request/hidden/state/tool docs/prefix calls/results stay unchanged. B0/B2 prompts remain identical; B1 uses existing additive guidance. Source prefix replay bypasses intervention because it is historical, while current guard observations on that prefix are reported separately. New B2 proposals follow the existing budgeted no-substitution guard.

## Verification actually run so far
- Initial narrow diagnostic: 28 tests passed.
- Added source/current harness identity and prepared-budget consistency regressions first; 30-test diagnostic produced three failing assertions (two budget subtests and the environment-code test).
- After the minimal gates, `python3 -m unittest discover -s tests -p test_prefix_run.py -v`: **30 tests passed**.

Tests use explicitly synthetic temporary source archives and a FakeBackend; they are not new model results. Coverage includes exact source/raw replay, hash/record/role/token boundaries, explicit-vs-legacy request isolation, B0/B2 identity, independent deep-copy branches, equal/over budgets, seed offset, legal retries/polls, guard blocked-cost/state isolation, malformed/truncated/failed generation, inherited/new loops, normal/unavailable controls, and no source-hidden prompt leakage.

## Integration / remaining boundaries
Root's authentic six-prefix source readback and archived combined suite remain separate verification steps. Fixed-prefix real runs have not been executed by this agent. Selected historical prefixes are conditional development cases, not natural failure rate or independent E2E trials. Native answer meaning remains unknown. Report recovery on recovery-eligible prefixes only; pre-obtained normal-control evidence must not inflate it.

`turns`/attempt fields are total prefix+continuation; fresh generation count is `continuation_turns`. `input_tokens`/`output_tokens`/generation_seconds are fresh only. Historical costs are separate observations, never charged again. An exactly exhausted prefix yields zero fresh generations and an exhausted case; an all-zero-continuation model run cannot satisfy a nonempty real-generation artifact contract and must not be called successful inference.

## Actual pinned-source readback and final freeze
Using root's `data/dev/fixed_prefix_cases_iter4.jsonl` and all six `configs/prefix_{legacy,explicit}_{b0,b1,b2}.json`, this agent executed **36 prepare checks** against the real pinned source archives, without loading/calling a model. All passed: 6 prefixes, 8 historical tool turns; 4 recovery, 1 normal-completion control, 1 unavailable-stop control; exactly 1 prefix already has goal evidence.

Independent assertions confirmed one shared prefix snapshot/public-history hash per prefix across six conditions; B0/B2 dialogues are identical within each wording; B1 changes only the additive system guidance; legacy/explicit change only the initial user message, not subsequent dialogue or tools. Model calls in this check: **0**.

The runtime, tests, SOP and handoff are **frozen** for root's archived joint tests, official tokenizer context gate and six real continuation runs. No further edits to inference inputs without coordinator instruction. The parent reports independent source review checks separately; root-owned archived evidence remains authoritative for actual GPU execution.
