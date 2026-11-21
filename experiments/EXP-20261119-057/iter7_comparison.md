# ITER-007 多 Seed 前缀恢复、解码消融与独立未见模板 E2E 对照

> 证据类型：`analysis`（零模型加载，全部源 run 经 manifest 与全量产物 SHA256 校验；`training_eligible=false`）。

## 1. 解码机制拆解（6 个真实历史前缀，explicit / B0）

| 解码配置 | 恢复新证据 (x/4) | 精确结构化恢复 (x/4) | orders-timeout 首步重试 | files-timeout 首步重试 | 新生成轮 | 输出 token |
|---|---:|---:|---:|---:|---:|---:|
| `greedy_pp0` | 0/4 (0.0%) | 0/4 (0.0%) | 0/1 | 0/1 | 34 | 1297 |
| `greedy_pp2` | 0/4 (0.0%) | 0/4 (0.0%) | 0/1 | 0/1 | 24 | 765 |
| `sampled_pp2` (`s0..s4` 均值±SD) | 0.100 ± 0.137 | 0.050 ± 0.112 | 1/5 (20.0%) (0.200 ± 0.447) | 1/5 (20.0%) (0.200 ± 0.447) | 13.400 ± 5.899 | 694.000 ± 364.012 |

## 2. 5-Seed（`s0..s4`）固定前缀恢复对照（5 seeds × 6 prefixes × 3 arms = 90 episodes）

诊断说明：`n=4` 个恢复前缀（每臂 `4 × 5 = 20` 次种子试验，含控制共 90 次续跑）属于开发期诊断对照，**不是**独立 20 任务或封闭确认性证明。

### 2.1 各臂 5-Seed 汇总与新鲜成本

| Arm | 汇总恢复新证据 (x/20) | Seed 均值±SD (恢复率) | 精确结构化恢复 (x/20) | 新执行循环 (x/30) | 重复提案/实际阻断 | 新生成轮 | 新输入/输出 token |
|---|---:|---:|---:|---:|---:|---:|---:|
| `B0` | 2/20 (10.0%) | 0.100 ± 0.137 | 1/20 (5.0%) | 4/30 (13.3%) | 20/0 | 67 | 69571/3470 |
| `B1` | 3/20 (15.0%) | 0.150 ± 0.137 | 2/20 (10.0%) | 2/30 (6.7%) | 10/0 | 65 | 71914/3888 |
| `B2` | 3/20 (15.0%) | 0.150 ± 0.137 | 1/20 (5.0%) | 0/30 (0.0%) | 5/5 | 54 | 51914/3635 |

### 2.2 逐前缀 5-Seed 表现（`x/5`）

| Prefix ID | 角色 | B0 新证据 | B1 新证据 | B2 新证据 | B0 精确完成 | B1 精确完成 | B2 精确完成 |
|---|---|---:|---:|---:|---:|---:|---:|
| `pfx-orders-timeout` | `recovery` | 1/5 (20.0%) | 2/5 (40.0%) | 1/5 (20.0%) | 1/5 (20.0%) | 2/5 (40.0%) | 1/5 (20.0%) |
| `pfx-files-timeout` | `recovery` | 1/5 (20.0%) | 1/5 (20.0%) | 2/5 (40.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) |
| `pfx-jobs-precondition` | `recovery` | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) |
| `pfx-orders-invalid` | `recovery` | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) |
| `pfx-orders-unavailable` | `unavailable_stop_control` | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) |
| `pfx-orders-ok` | `normal_completion_control` | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) | 0/5 (0.0%) |

### 2.3 逐 Schedule 恢复（`x/4`）与配对差值（`B1 - B0`、`B2 - B0`）

- `s0` 恢复新证据：B0=1/4 (25.0%)，B1=1/4 (25.0%)，B2=1/4 (25.0%)。
- `s1` 恢复新证据：B0=1/4 (25.0%)，B1=1/4 (25.0%)，B2=1/4 (25.0%)。
- `s2` 恢复新证据：B0=0/4 (0.0%)，B1=0/4 (0.0%)，B2=1/4 (25.0%)。
- `s3` 恢复新证据：B0=0/4 (0.0%)，B1=0/4 (0.0%)，B2=0/4 (0.0%)。
- `s4` 恢复新证据：B0=0/4 (0.0%)，B1=1/4 (25.0%)，B2=0/4 (0.0%)。
- `B1` vs `B0` `new_goal_evidence`（20 次恢复试验 胜/平/负 = 1/19/0）：按 5 seed 配对差均值±SD = 0.050 ± 0.112；按 n=4 前缀聚类差均值±SD = 0.050 ± 0.100。
- `B1` vs `B0` `exact_structured_completion_with_new_goal`（20 次恢复试验 胜/平/负 = 1/19/0）：按 5 seed 配对差均值±SD = 0.050 ± 0.112；按 n=4 前缀聚类差均值±SD = 0.050 ± 0.100。
- `B2` vs `B0` `new_goal_evidence`（20 次恢复试验 胜/平/负 = 1/19/0）：按 5 seed 配对差均值±SD = 0.050 ± 0.112；按 n=4 前缀聚类差均值±SD = 0.050 ± 0.100。
- `B2` vs `B0` `exact_structured_completion_with_new_goal`（20 次恢复试验 胜/平/负 = 0/20/0）：按 5 seed 配对差均值±SD = 0.000 ± 0.000；按 n=4 前缀聚类差均值±SD = 0.000 ± 0.000。
- 前缀 B0 vs B2 干预前精确生成匹配：48/48 轮一致（`all_identical=True`）。

## 3. 独立未见模板 E2E 对照（`24` 例，`24` 模板组）

| 指标 | B0 | B1 | B2 |
|---|---:|---:|---:|
| 可完成案例取得目标证据 | 2/21 (9.5%) | 2/21 (9.5%) | 2/21 (9.5%) |
| 精确有依据结构化完成 | 0/21 (0.0%) | 1/21 (4.8%) | 0/21 (0.0%) |
| 无证据提前终止 | 12/24 (50.0%) | 14/24 (58.3%) | 13/24 (54.2%) |
| 公开不可用后停止 | 1/3 (33.3%) | 2/3 (66.7%) | 1/3 (33.3%) |
| 精确停止原因码 | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) |
| 自然文本终止待审 | 14/24 (58.3%) | 16/24 (66.7%) | 15/24 (62.5%) |
| 实际工具历史原型循环 | 5/24 (20.8%) | 5/24 (20.8%) | 0/24 (0.0%) |
| 重复提案次数 | 21 | 20 | 8 |
| B2 实际阻断次数 | 0 | 0 | 8 |
| 总生成轮数 | 94 | 84 | 87 |
| 总输入 token | 91190 | 91197 | 87795 |
| 总输出 token | 4168 | 4676 | 5504 |

### 3.1 按域（Domain）、类别（Category）与故障子类型（Fault Subtype）目标证据率

| 分层 | B0 目标证据 | B1 目标证据 | B2 目标证据 | B0 无效循环 | B2 无效循环 |
|---|---:|---:|---:|---:|---:|
| domain:`orders` | 2/7 (28.6%) | 2/7 (28.6%) | 2/7 (28.6%) | 1/8 (12.5%) | 0/8 (0.0%) |
| domain:`files` | 0/7 (0.0%) | 0/7 (0.0%) | 0/7 (0.0%) | 4/8 (50.0%) | 0/8 (0.0%) |
| domain:`jobs` | 0/7 (0.0%) | 0/7 (0.0%) | 0/7 (0.0%) | 0/8 (0.0%) | 0/8 (0.0%) |
| category:`normal` (目标证据) | 1/3 (33.3%) | 1/3 (33.3%) | 1/3 (33.3%) | 0/3 (0.0%) | 0/3 (0.0%) |
| category:`recoverable` (目标证据) | 0/6 (0.0%) | 0/6 (0.0%) | 0/6 (0.0%) | 1/6 (16.7%) | 0/6 (0.0%) |
| category:`retry_required` (目标证据) | 1/6 (16.7%) | 1/6 (16.7%) | 1/6 (16.7%) | 3/6 (50.0%) | 0/6 (0.0%) |
| category:`clarification` (目标证据) | 0/6 (0.0%) | 0/6 (0.0%) | 0/6 (0.0%) | 1/6 (16.7%) | 0/6 (0.0%) |
| category:`unrecoverable` (停止) | 1/3 (33.3%) | 2/3 (66.7%) | 1/3 (33.3%) | 0/3 (0.0%) | 0/3 (0.0%) |
| subtype:`direct_ok_f1` | 1/3 (33.3%) | 1/3 (33.3%) | 1/3 (33.3%) | 0/3 (0.0%) | 0/3 (0.0%) |
| subtype:`job_poll_1_step` | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| subtype:`job_poll_3_steps_boundary` | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| subtype:`missing_id_direct` | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) |
| subtype:`missing_id_plus_1poll` | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| subtype:`missing_id_plus_2timeout` | 0/2 (0.0%) | 0/2 (0.0%) | 0/2 (0.0%) | 1/2 (50.0%) | 0/2 (0.0%) |
| subtype:`stale_id_locate` | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) | 0/3 (0.0%) |
| subtype:`stale_id_locate_plus_1poll` | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) | 0/1 (0.0%) |
| subtype:`stale_id_locate_plus_1timeout` | 0/2 (0.0%) | 0/2 (0.0%) | 0/2 (0.0%) | 1/2 (50.0%) | 0/2 (0.0%) |
| subtype:`transient_1_retry` | 1/2 (50.0%) | 1/2 (50.0%) | 1/2 (50.0%) | 1/2 (50.0%) | 0/2 (0.0%) |
| subtype:`transient_2_retries_boundary` | 0/2 (0.0%) | 0/2 (0.0%) | 0/2 (0.0%) | 2/2 (100.0%) | 0/2 (0.0%) |
| subtype:`unavailable_valid_id_f1` | 1/3 (33.3%) | 2/3 (66.7%) | 1/3 (33.3%) | 0/3 (0.0%) | 0/3 (0.0%) |

### 3.2 未见模板配对胜/平/负与 B0 vs B2 干预前核验

- `B1` vs `B0` `tool_goal_attainment`：胜/平/负 = 0/21/0；按模板组聚类差均值±SD = 0.000 ± 0.000。
- `B1` vs `B0` `exact_grounded_structured_completion`：胜/平/负 = 1/20/0；按模板组聚类差均值±SD = 0.048 ± 0.218。
- `B2` vs `B0` `tool_goal_attainment`：胜/平/负 = 0/21/0；按模板组聚类差均值±SD = 0.000 ± 0.000。
- `B2` vs `B0` `exact_grounded_structured_completion`：胜/平/负 = 0/21/0；按模板组聚类差均值±SD = 0.000 ± 0.000。
- E2E B0 vs B2 干预前精确生成匹配：70/70 轮一致（`all_identical=True`）。

## 4. 同快照无泄漏公开替代动作检验（`validate_alternative`，`training_eligible=false`）

- 未见模板 E2E 未达目标的可完成轨迹（共 57 条）：故障点存在合法公开恢复/推进步骤 = 42/57 (73.7%)（纯 `locally_validated` = 30/57 (52.6%)；合法重试/轮询推进 = 12/57 (21.1%)；含零调用首步公开探针 = 50/57 (87.7%)）；全部候选 `public_arguments_only=True`。
- 5-Seed 固定前缀未达目标的可完成轨迹（共 52 条）：前缀快照存在合法公开恢复/推进步骤 = 52/52 (100.0%)（纯 `locally_validated` = 37/52 (71.2%)；合法重试/轮询推进 = 15/52 (28.8%)）；全部候选 `public_arguments_only=True`。

## 5. 解释边界
- n=4 authentic recovery prefixes across 5 seed schedules (20 recovery trials/arm, 90 total prefix episodes) is diagnostic development evidence, not held-out confirmatory proof.
- Unseen-template E2E fixtures remain development-split diagnostics until G2 human blind semantic/detector audit and held-out freeze complete.
- Tool-goal attainment is observed environment evidence, not natural-language semantic correctness; natural-text terminals remain unknown.
- B2 runtime guard blocks suppress repeated tool execution mechanically; evaluate recovery via new goal evidence and exact completion alongside proposal counts and token costs.
- Same-snapshot validate_alternative checks prove public single-step progress without hidden leakage, but all trajectories remain training_eligible=false.
