# ITER-004 fixed-prefix comparison handoff

## Ownership and status
Owner: simulator. New files only: `scripts/compare_prefix_runs.py`, `tests/test_prefix_comparison.py`, this handoff. **Frozen** for the coordinator's archived comparison. No frozen inference code, existing configurations, datasets, source runs or scores were changed; no model call, installation or training was performed.

## Interface and checks
`python3 scripts/compare_prefix_runs.py --config configs/iteration4_prefix_comparison.json --output DIR`

Consumes six `run_dirs` entries containing absolute path and pinned manifest SHA256. Other analysis-config fields, including the optional preflight-context reference, are allowed but are not claimed as independently audited by this comparer. Writes `prefix_comparison.json` and `prefix_comparison.md`; refuses either existing output filename. Source archives are read-only.

Checks include completed valid model-inference status; complete input/output inventories and byte hashes; six unique wording-by-arm cells with exactly six paired prefix IDs; common configuration apart from policy/arm/wording; shared inference-code/data hashes and actual runtime/model/tokenizer/generation provenance; identical prefix identity, original metadata, snapshots, public histories, budget starts and seeds; context budget/source binding; fresh generation seed offsets/counts/token costs; historical prefix costs kept separate; total budget accounting including prefix; B0/B2 system-prompt identity; within-wording task-text equality; unchanged system prompt across wording profiles; saved primary aggregates against case rows.

This is a descriptive comparison, not another execution replay/scorer implementation. Independent runtime/event/source replay remains the separate protocol audit's responsibility. Hash consistency establishes preservation, not proof that an original artifact is truthful. Actual-weight attestation and natural-language answer semantics are not newly inferred here.

## Outputs and interpretation
- Primary recovery denominator is **4**, never 6 or 36. New goal evidence and exact structured completion with new evidence are separate.
- One normal completion control already has goal evidence at the prefix; one unavailable-stop control is separate. Neither inflates recovery.
- Proposed repeats, actual new executed loops, inherited loops, blocks, parser errors, budget exhaustion, unknown natural terminals and unsupported finish wrappers remain separate.
- Fresh continuation generations/input/output tokens/time are separate from previously observed historical prefix costs. Fresh input includes actual repeated prefix context. Cost per new goal includes unsuccessful recovery attempts.
- Paired wins/ties/losses include case IDs: B1/B2 versus B0 within each wording, and explicit versus legacy within each arm, only over the same four recovery prefixes.
- No significance, independence, generalization, natural failure-rate, training or learned-recovery claim.

## Verification actually performed
`python3 -m unittest discover -s tests -p test_prefix_comparison.py -v`: **16 tests passed**. Tests use temporary fake source archives and `FakeBackend`; they are analysis fixtures, not model evidence. Coverage includes paired W/T/L, four-recovery/two-control boundaries, fresh/historical accounting, mechanical blocks, complete inventories, read-only preservation, provenance/config/seed/snapshot/history/context tampering, pre-existing-goal exclusion, saved metrics, Markdown shape and CLI no-overwrite.

An earlier narrow discovery also rediscovered 30 imported prefix-runtime tests (45 total passed). The new test module now imports the helper module rather than its TestCase class, so it contains only its intended 16 tests.

`py_compile` initially could not write the system Python cache outside the sandbox. It passed with `PYTHONPYCACHEPREFIX=/tmp/toolloop-prefix-comparison-pycache python3 -m py_compile scripts/compare_prefix_runs.py tests/test_prefix_comparison.py`; no permissions or dependencies were changed.

Read-only development check using `compare(json.loads(Path('configs/iteration4_prefix_comparison.json').read_text())['run_dirs'])` passed all eight gates on the six actual completed archives. It read **36 continuation episodes and 62 fresh generation rows**. Each cell has **1/4 new goal evidence and 1/4 exact structured completion**. This diagnostic wrote no comparison artifact and made zero model calls; coordinator-owned run 031 is the authoritative archived comparison. Initial development exposed and fixed a comparison tuple-order typo before this successful readback; original source artifacts were unchanged.

## Coordinator next step
Archive the narrow tests and comparison via `scripts/record_run.py`, retain the separate independent replay audit and official tokenizer context gate, then summarize paired results with the above boundaries. Do not claim that suppressing execution through B2 blocking alone proves recovery improvement.
