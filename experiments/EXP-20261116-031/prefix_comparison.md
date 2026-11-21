# ITER-004 固定前缀续跑对照

每格4个恢复前缀；另列1个已取得证据的正常完成控制、1个不可用停止控制。单一种子安排，不训练。

| 条件 | 新目标证据/恢复 | 精确结构化完成/恢复 | 正常控制精确完成 | 不可用控制停止 | 自然终止待审 | 新执行循环 | 重复提案/阻断 |
|---|---:|---:|---:|---:|---:|---:|---:|
| legacy/B0 | 1/4 | 1/4 | 0/1 | 0/1 | 2/6 | 0/6 | 0/0 |
| legacy/B1 | 1/4 | 1/4 | 0/1 | 0/1 | 3/6 | 0/6 | 0/0 |
| legacy/B2 | 1/4 | 1/4 | 0/1 | 0/1 | 2/6 | 0/6 | 0/0 |
| explicit/B0 | 1/4 | 1/4 | 0/1 | 1/1 | 4/6 | 1/6 | 1/0 |
| explicit/B1 | 1/4 | 1/4 | 0/1 | 1/1 | 3/6 | 1/6 | 1/0 |
| explicit/B2 | 1/4 | 1/4 | 0/1 | 1/1 | 4/6 | 0/6 | 2/2 |

## 成本与预算（历史不计作本次新生成）

| 条件 | 历史轮/新生成轮 | 总提案/总执行 | 新输入/输出token | 历史输入/输出token（未重跑） | 解析错/预算耗尽 |
|---|---:|---:|---:|---:|---:|
| legacy/B0 | 8/9 | 11/11 | 7517/416 | 5918/288 | 0/0/6 |
| legacy/B1 | 8/11 | 13/13 | 10846/670 | 5918/288 | 0/0/6 |
| legacy/B2 | 8/9 | 11/11 | 7517/416 | 5918/288 | 0/0/6 |
| explicit/B0 | 8/9 | 11/11 | 8245/454 | 5918/288 | 0/0/6 |
| explicit/B1 | 8/14 | 17/15 | 15107/905 | 5918/288 | 2/1/6 |
| explicit/B2 | 8/10 | 12/10 | 9411/572 | 5918/288 | 0/0/6 |

## 逐前缀配对：胜/平/负（仅4个恢复前缀）
- legacy B1 vs B0 new_goal_evidence：0/4/0。
- legacy B1 vs B0 exact_structured_completion_with_new_goal：0/4/0。
- legacy B2 vs B0 new_goal_evidence：0/4/0。
- legacy B2 vs B0 exact_structured_completion_with_new_goal：0/4/0。
- explicit B1 vs B0 new_goal_evidence：0/4/0。
- explicit B1 vs B0 exact_structured_completion_with_new_goal：0/4/0。
- explicit B2 vs B0 new_goal_evidence：0/4/0。
- explicit B2 vs B0 exact_structured_completion_with_new_goal：0/4/0。
- B0 explicit vs legacy new_goal_evidence：0/4/0。
- B0 explicit vs legacy exact_structured_completion_with_new_goal：0/4/0。
- B1 explicit vs legacy new_goal_evidence：0/4/0。
- B1 explicit vs legacy exact_structured_completion_with_new_goal：0/4/0。
- B2 explicit vs legacy new_goal_evidence：0/4/0。
- B2 explicit vs legacy exact_structured_completion_with_new_goal：0/4/0。

## 解释边界
- 取得目标证据不等于回答正确；自然终止语义仍 unknown。正常控制已在前缀取得的证据不是恢复收益。
- 预算包含历史前缀；新token/耗时只来自续跑（含每轮重新输入历史），历史生成成本单列且未重跑。
- B2机械阻断可减少执行循环，不等于模型学会恢复；不能只看循环下降。
- 同4个恢复前缀跨条件复用，不视为独立36样本；不做显著性、总体泛化或自然故障率声明。
- 所有旧run及成绩保留；本轮不训练。
