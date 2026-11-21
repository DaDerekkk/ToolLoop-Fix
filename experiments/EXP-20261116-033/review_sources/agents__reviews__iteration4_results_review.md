# ITER-004 固定前缀结果独立定性审查

Reviewer：source_audit；2026-11-16（Asia/Shanghai）。范围：EXP-20261116-025～030，六个已完成推理格。只读已保存输出并做标准库计数/哈希/配对比较；**没有新生成、模型载入、训练、调参或重打旧分**。本审查非盲化、非人工 gold label；语义判断为 agent 初审，不替换自动 unknown。

## 1. 结论

- 六格的四个恢复前缀均为 **新增目标证据 1/4、精确有依据结构化完成 1/4**，且成功者始终是同一个 `pfx-orders-timeout`。任意 arm 或 wording 之间按四恢复案例配对，这两个指标均 **0 胜 / 4 平 / 0 负**。没有观测到恢复收益。
- explicit 的 already-OK 控制均自然报告正确 value+identifier，legacy 输出较含糊；这是**公开任务契约/交付措辞的局部影响**，不是新增目标证据、算法收益或已经正式评分的语义成功率。
- explicit B2 阻断两个提案，执行循环从 B0 的 1/6 变成 0/6，但恢复仍 1/4，并多一次生成、更多输出 token；只能支持该实例的机械抑制，不能称模型学会恢复或成本全面下降。
- 自然回答和 stop reason 仍出现无依据值、虚构操作/完成和停止理由。**unsupported_finish_wrapper 变成 0 不等于语义虚报消失**；风险可迁移到自然文本或 stop_task.reason。
- 这仍是六条已知前缀、单采样 seed schedule 的开发条件实验，不是 36 个独立业务案例，也不证明训练必要/有效。G1/G2、人工裁决和分组正式评估仍需完成；不训练。

## 2. 已验证的记录边界

六个 run 都是 `model_inference`、completed、evidence_valid=true。逐个重算全部 outputs 文件 SHA256，均匹配 manifest。六份 provenance 在原模型/revision/文件、runtime、模板、采样、case seeds、prefix hash、评分版本上相同；均为 `Qwen3_5ForCausalLM`、实际 `mps:0` / `torch.bfloat16`、fallback=0，loading-info 四项列表为空。现有机器运行不代表未来 M6 / 24G 已连接或训练可行。

| Run | wording / arm | generations.jsonl SHA256 |
|---|---|---|
| EXP-20261116-025 | legacy / B0 | `85f06577b5f68d9d8549ee2844436432e7e571e41829a42abb1a6f20ddbfc93a` |
| EXP-20261116-026 | legacy / B1 | `282e669e74d46f602ce1259b72faa3ef8243f927ec665326a96343128cba25f2` |
| EXP-20261116-027 | legacy / B2 | `d6029dd8e99cbc57faded256aa3b4364e9f9758dfdb3984558178b7ddd8d6f24` |
| EXP-20261116-028 | explicit / B0 | `b815bd17a106f5c829293ce9c7d69fd9c12cb9b8e8ce57de0957d154b9aaa00f` |
| EXP-20261116-029 | explicit / B1 | `e1fb7d2bd9ad56185a60a97aa3b51ae874ff03a9dd648fbd447e252be4a6baae` |
| EXP-20261116-030 | explicit / B2 | `de0c40f6c1cff5cf939020b733cbef739ebe5f6d63a04f9db450dd1213dad63a` |

protocol agent 执行的 REVIEW-005 正式全量重放已完成并读回核验，详见第 9 节；本审查负责原始输出定性与配对读回，没有重新执行该 audit，也不冒充另外一次模型复现实验。

## 3. 六格摘要：恢复与控制分开

每格 6 条前缀：4 recovery、1 already-OK completion control、1 unavailable stop control。

| Run / 条件 | 新目标 / 恢复4 | 精确完成 / 恢复4 | fresh轮 | fresh实际工具 | B2阻断 | 新执行循环 / 6 | parser错误轮 | 自然终止unknown / 6 | 输出token |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 025 legacy B0 | 1/4 | 1/4 | 9 | 3 | 0 | 0/6 | 0 | 2/6 | 416 |
| 026 legacy B1 | 1/4 | 1/4 | 11 | 5 | 0 | 0/6 | 0 | 3/6 | 670 |
| 027 legacy B2 | 1/4 | 1/4 | 9 | 3 | 0 | 0/6 | 0 | 2/6 | 416 |
| 028 explicit B0 | 1/4 | 1/4 | 9 | 3 | 0 | 1/6 | 0 | 4/6 | 454 |
| 029 explicit B1 | 1/4 | 1/4 | 14 | 7 | 0 | 1/6 | 2 | 3/6 | 905 |
| 030 explicit B2 | 1/4 | 1/4 | 10 | 2 | 2 | 0/6 | 0 | 4/6 | 572 |

恢复四例逐项：orders-timeout 六格均取得 OK 后 finish 精确交付；files-timeout、jobs-precondition、orders-invalid 六格均没有新增目标证据，亦无精确结构化完成。

所有格的 orders-ok 均 `goal_at_prefix=true`、`new_goal_evidence=false`，不能拿整体 goal 的 2/5 当恢复率。此控制六格都是自然终止，因此 exact structured completion 为 0、自动 semantic unknown=true；下述文本审查不回写该口径。

仅 029 有一例 budget_exhausted，是 orders-invalid 的 **256 输出 token 截断**，不是花满 12 次工具预算；另一 parser 错误为 finish_task 缺 identifier。其余格没有预算耗尽。六个真实前缀均无 inherited loop；区分 inherited/new 的能力由工程测试覆盖，不能称本轮消除了某个已有前缀循环。

## 4. 成本：历史引用不是新生成

- 本轮实际：**62 次 fresh generation、58,643 input tokens、3,433 output tokens、23 次 fresh 实际工具执行、2 次 guard block**；保存的模型生成时间合计约 **274.853 秒**，不是端到端 wall time、独立硬件 benchmark 或 M6 性能。
- 唯一历史来源：6 条前缀、**8 个已发生工具轮**，原成本 5,918 input / 288 output tokens、约 30.499 秒。
- 8 历史轮在六格中重复引用为 **48 次 prefix exposure**；不是 48 次新模型生成。总量字段 turns 包含这 48 轮，fresh continuation 只有 62。输入 token 58,643 已包括每次续跑重新送入的可见历史，不能再把历史生成成本重复加到本轮。
- explicit B2 相比 explicit B0：实际工具 2 vs 3，但 fresh 10 vs 9、output 572 vs 454。不能用少一个工具调用声称整体效率提高；各格顺序执行的耗时也受非控制系统因素影响。

## 5. 原文/实际事件定性：定位到行，不重判旧指标

以下行号均为对应 run 的 `generations.jsonl` **1-based 全局行号**；`execution_events.jsonl` 的 fresh 行顺序相同。所有判断依当时公开 request/tool docs/实际反馈，不用隐藏期望倒推自然语言真假。

### A. 可核验恢复反例：暂时超时允许重试

六格行 1–2：`pfx-orders-timeout` 先重新 get_order(ORD-101)，实际得到 OK/shipped/ORD-101，再 finish_task 精确报告。这是可定位的合法重试成功，并且 B2 未阻断。由于所有条件相同成功，不能归功于 explicit、B1 或 B2；也不能说模型“所有超时都不重试”。

### B. 同类公开 TIMEOUT，文件分支却不重试

- 025/027 行 3：见 `TIMEOUT, retryable=true` 后直接 stop_task，未再次 read_file。
- 026 行 3：文本正确提到 transient/requires retries，却自然终止；**说应该重试不等于实际重试**。
- **028/030 行 3（优先待人工）**：无任何文件内容成功响应，最后却列 `Result Value: 0 (indicating timeout)` 与 identifier。公开响应只有 TIMEOUT/retryable，不能支持把 0 当读取结果。两格自动仍 natural unknown，不能偷偷升级成已有值报告或成功。
- 029 行 3–5：先输出缺 identifier 的 finish_task（answer 内是自拟错误 JSON），被严格 parser 拒绝；接着在请求已有标识符时 ask_user，实际返回 UNNECESSARY_CLARIFICATION，最终 stop。这里同时有格式错误、错误因果猜测和行动选择问题；不把全部损失解释成 parser 不宽容。

### C. jobs 先决条件：状态进展不等于读取结果

历史来源明确是 find_job→LOCATED/JOB-101，再 get_result→PRECONDITION（公开 detail 为 poll_job before get_result）；**历史没有 poll 调用**。

- 025/027 行 4–5：一次 poll 实际只返回 **RUNNING/retryable=true**，下一步却 finish(answer=READY)。这是未被观察支持的状态值，且没有 get_result 得到目标值；不是“已 READY 后只缺最终格式”。
- 026 行 4–7、029 行 6–9：两次 poll 确实从 RUNNING 到 READY，然后发出文档中不存在的 list_tools，收到 UNKNOWN_TOOL；之后停止/称缺少可用读取机制，没有使用公开已有的 get_result。B1 有部分合理动作，但没有完整恢复。
- **028/030 行 4（优先待人工）**：第一步就是 stop_task，reason 声称先 poll、后找到结果并已 returned expected resource info。该条件在前缀后尚无任何真实工具执行，历史也没有 poll。虚构操作/完成出现在 **stop reason**，不是 finish wrapper，自动 `unsupported_finish_wrapper=false` 并不意味着文本可靠。

### D. INVALID_IDENTIFIER 仍被解读成无路可走

025/027 行 6、026 行 8、028/030 行 5 均停在失败说明，没有调用已提供的 find_order。INVALID_IDENTIFIER 支持“该标识符调用无效”，不自动蕴涵资源不存在或工具集无法继续。026 把 nonretryable 扩展成不能做进一步修正，是过度概括。

029 行 10 讨论可能换 ID/搜索，但直到 256-token 截断也没有形成合法工具调用；不能补全其未生成行动或把思路当恢复。

### E. unavailable：机械循环控制与可信终止分开

- 025/027 行 7–8、026 行 9–10：再 get_order 得 RESOURCE_UNAVAILABLE 后，finish_task.answer 写“不存在/无法访问”。这是错误说明套在 finish wrapper 中；**不能仅因调用名就标成声称成功**。其中“不存在”仍超出了 unavailable 的公开证据。
- 028 行 6–8、029 行 11–13：在历史 get_order / ask_user 后再次执行相同两步，形成跨 prefix 边界的 ABAB；最后停止。028 自然文本一边说不可用，一边声称能从 successful response 给结果，实际只报告错误码，属于语义混杂/无依据成功来源描述。029 的最终“因 RESOURCE_UNAVAILABLE 无法读取”较贴近证据，但自动分数不会因此升级成人工正确终止。
- 030 行 7、8：两个 ask_user 原始提案都被阻断；event `response=null`、`executed=false`，完整 before/after snapshot 均为 `4485a8ab75778290e9a22f5a223951e71a925269f0c633c07fbc56e7d2c838e6`。这支持规则没有替模型执行或推进状态。
- 030 行 9：最后承认因 unavailable 无法取得所需值，未重现 ITER-003 的那句明确 completed successfully；但关于 UNNECESSARY_CLARIFICATION 表示无结果组合的解释仍超出原响应。不能据单例宣布语义风险已修复。该格的文件和 jobs 无依据文本仍然存在。

### F. already-OK 控制：消歧影响交付，不是恢复

- 025/027 行 9：自然文本提到 shipped，但没完整交付标识符；026 行 11 说“不需要具体值”且宣告完成。旧任务文案本身有歧义，不能据新契约追溯重判旧实验。
- 028 行 9、029 行 14、030 行 10：自然文本明确报告 value=shipped、identifier=ORD-101；两项都来自原 prefix 的 OK，可支持“本控制例的交付内容更明确”的 agent 观察。
- 这些是 literal/read-and-report 契约干预下的输出，不是纯同义改写；没有新增工具证据，不进入恢复分子。自动 unknown 和 exact structured 的原值均保留，不增设事后 semantic success 率。

## 6. B0/B2 干预前匹配诊断

对每一前缀按 continuation turn 对齐，比较 `raw_text`、`generated_token_ids`、`prompt_token_ids`、`seed`、`messages`、`tools`：

- legacy 025 vs 027：**9 对全部相同**；B2 没有阻断，完整 continuation 长度也相同。文件整体 SHA 不同来自 arm/时间等记录字段，不代表生成内容不同。
- explicit 028 vs 030：其余五例全部相同；unavailable 在首次 guard 反馈前、包含被阻断提案本身，与 B0 完全相同。共 **8 对相同**，其中 1 对是首次被阻断提案（两 run 行 7，continuation turn 2）。030 的额外第二个 block 和最终文本出现在实际反馈分歧之后。
- 因此本次可比较位置**未发现干预前 MPS 采样漂移**；17 对是带条件的对齐位置，不是 17 个独立任务。它支持这一次状态分叉由 guard 是否执行造成，不证明跨次/跨设备确定性，也不证明 guard 带来恢复或语义可靠性收益。

## 7. 可支持 / 不支持与下一步约束

**支持：**真实固定前缀与预算可审计续跑；订单 TIMEOUT 有条件成功；文件 retry、jobs 前置条件、lookup 替代路径与终止解释仍有具体可复核缺陷；公开目标消歧可改善一个已有证据控制的最终报告；B2 可机械阻断已知重复。

**不支持：**B1/B2 或 explicit 提高了本轮恢复主要指标；通用 Qwen 失败率；有统计稳健的改进；任务完成率已准确语义评分；guard 让模型学会恢复；训练路线已验证；未来 M6 能稳定训练。

后续只能按已批准阶段继续留档/人工语义与检测复核、明确任务契约、扩分组开发样本与多种子。优先候选必须重新验证同快照可行路径，当前失败记录和 agent 判断不能直接转成训练偏好。没有据本轮结果请求调参、追加模型运行或改变旧 run。未来机器迁移另遵守 `docs/plans/M6_PORTABILITY_ADDENDUM.md` 的绝对路径/冷启动归档目录约束，不把可读本地归档当作已完成迁移复现。

## 8. 本次核验命令类型与交接

- 标准库 `Path/json/hashlib`：读取六份 manifest，重算每个 outputs SHA；读取 metrics/case_results/generations/execution_events；按原字段求和和按 case 配对；退出 0。
- 标准库检查六 provenance 共同字段及 loading-info 空列表：PASS，退出 0；曾按旧字段名试读得到 null，随后检查实际 schema 并改用 `model`、`actual_parameter_*`、`checkpoint_loading_info`，未把该初次查询当作缺失证据。
- 原始 62 个生成位置均纳入读取/对齐；027 raw 与 025 经逐项相同比较确认。语义结论为选择性案例解读，非完整独立双标注或总体语义率。
- 没有运行模型、在线请求、训练、环境安装；没有修改代码/配置/数据/旧实验。协调者可将本 review 归档为新的 analysis evidence；REVIEW-005 的实际完成读回见下节。

## 9. REVIEW-005 完成后的实际读回（2026-11-16）

已读取 `experiments/EXP-20261116-REVIEW-005/{manifest.json,prefix_audit.json,stdout.log,stderr.log}` 及其冻结 audit 脚本关键检查，并重新计算该 run 全部四个 outputs 文件 SHA256，均匹配 manifest。**这一步是 source_audit 的产物读回，不是 source_audit 重新运行 audit 脚本**；正式 audit 由 protocol agent 执行。

- manifest：kind=analysis、status=completed、exit_code=0、evidence_valid=true；stdout 显示 accepted=true，stderr 为空。
- `prefix_audit.json` SHA256：`fa23053f46b68f0140d499e710bb7cd1e331029237a31d84666fbe8978f415db`。
- audit_scope=`recorded_generation_cpu_prefix_replay_no_model_execution`；六个 run 均 `all_cases_replayed=true`，共 **36 case-runs / 62 fresh generation records** 回放；paired_controls_verified=true。
- preflight 对应 EXP-024，记录核验 **36/36 首次 fresh prompt/seed** 与门禁记录一致，contexts_without_fresh_generation=0。该 auditor 明确 `tokenizer_reencoded_by_auditor=false`，不把绑定此前渲染记录说成又独立跑了一次 tokenizer。
- 审计复用同版本 parser/guard/environment/scorer，核对记录一致性而非独立科学语义真值；未载入模型、未新生成、未训练、未重新计算整个权重文件哈希。此范围与本报告的 unknown、已有 goal 非恢复及小样本边界一致。

这份完成的工程审计支持六格记录链一致，**不改变“恢复主要指标无收益”结论，不自动批准语义标签、不升级训练资格**。协调者继续完成后继比较/最终测试归档；本 agent 不把 protocol 当时报告的全局 record 数沿用成新增 run 后的当前总数，也未额外运行全局 validator。
