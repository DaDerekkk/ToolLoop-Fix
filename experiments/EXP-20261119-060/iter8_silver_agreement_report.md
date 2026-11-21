# ITER-008 G2 双盲银标一致性报告（解盲前）

- 治理层级 (`dataset_governance_tier`): `dual_blind_silver_plus_human_disagreement_adjudication_v1`
- 声称 100% 纯手工金标 (`pure_manual_human_gold_claimed`): `False`
- 防冒充校验 (`zero_human_impersonation_in_silver`): `True`

## 1. 终止语义盲包一致性 (`166` 项，7 类)

- Pack ID: `G2-SEM-150c85da5c48ff88`
- Exact Agreement: `0.9578 (159/166)` (`p_o=0.9578`, `p_e=0.2163`)
- Unweighted Cohen's κ: `0.9462`
- 需人类裁决条数: `7`

## 2. 循环检测盲审包一致性 (`358` 段，2×2)

- Pack ID: `G2-DET-32057bb391f00e90`
- Exact Agreement: `1.0000 (358/358)` (`p_o=1.0000`, `p_e=0.5035`)
- Unweighted Cohen's κ: `1.0000`
- 2×2 表: `(A=True,B=True)=164`, `(A=True,B=False)=0`, `(A=False,B=True)=0`, `(A=False,B=False)=194`
- `detect_loops` v1 检出段数: `144` | `detect_loops_v2` 检出段数: `162` | `v1 != v2` 差异段数: `18`
- 需人类裁决总段数（`A != B` ∪ `v1 != v2`）: `18`
