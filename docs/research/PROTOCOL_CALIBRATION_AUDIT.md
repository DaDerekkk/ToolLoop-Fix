# ITER-003 协议校准与 B0/B1/B2 设计审查

- 日期：2026-11-16，Asia/Shanghai；owner：`/root/source_audit`。
- 用户本轮顺序：**先校准工具/终止协议，再做B1/B2对照；保留旧结果；不训练。**
- 本文是实施前建议，不是已执行结果。协调者批准并冻结的实际协议/配置优先；差异须写入审查。
- 只读证据：当前 model_protocol/model_run/harness、EXP-013 原始生成/事件、ITER-002及现行SOP。无需新增网络研究、依赖或模型调用。

## 1. 为什么必须先分清协议与行为

EXP-013 的29/103轮格式错误来自当前“每轮恰好一个XML调用”契约；其中自然语言返回并不自动等于模型工具协议失效。错误被作为新的user-role反馈插入历史，可能延长原本会结束的对话。另一方面，jobs-retry_required在该格式反馈出现前已重复get_result，不能把全部重复归咎适配器。

另外，`finish_task(answer=NOT_FOUND, ...)`不当然意味着自然语言声称成功；`stop_task(reason=解释文本)`确实停止了，但旧评估要求reason精确等于RESOURCE_UNAVAILABLE。旧指标可作为原协议结果保留，不能继续用同名指标混淆新语义。

## 2. 建议的最小确定性校准

| 输出 | 控制处理 | 可以判断 / 不可以判断 |
|---|---|---|
| 完整单个XML业务调用 | 原样解析与执行；不补参数、不纠正ID | 语法/执行结果可判；不凭隐藏答案修复 |
| 完整finish_task | 记录结构化final，再做独立证据校验 | 可判字段与成功工具证据是否吻合；不能只因用了此函数就断言语义上声称成功 |
| 完整stop_task | 保留原始reason并终止 | 可判确已停止、先前是否见到不可用响应、reason是否精确代码；任意解释的语义正确性可保持unknown |
| 非空自然语言，正常EOS，无工具/角色控制标记 | 记录terminal_text_unknown并返回控制；不追加FORMAT_ERROR重试 | 只知道本轮没有业务调用并返回文本；不自动算成功、澄清成功、正确停止或虚报成功 |
| 空输出、部分XML、多个调用、非法控制标记 | 保留严格语法失败；不抢救第一个调用 | 明确规则及错误类型；不能混入普通自然语言分母 |
| token截断或上下文耗尽 | 保留不完整/预算状态；不提升为合法文本terminal | 不算成功，即使截断前碰巧出现像答案的内容 |

先检测截断及控制/XML残片，再接受普通文本，防止缺少闭合标签的工具请求被误作自然语言终止。工具参数与成功答案不得从历史自动填写；不引入LLM judge或teacher。自然语言中的问题不自动触发ask_user，否则会发明用户交互；需要自动执行澄清时仍须显式调用工具。

**推荐不做的“修复”：** 任意字符串包含正确答案就算完成；看到RESOURCE_UNAVAILABLE子串就判理由正确；从旧隐藏标签选择合法下一步；为模型补正确ID；把自然语言解析成推测的工具调用。

## 3. 终止、语义与完成应分层

至少保留以下概念，具体字段名可由实现者冻结：

1. `termination_form`：structured_finish / structured_stop / plain_text / budget / runtime_failure。
2. `grounded_completion`：确有成功工具证据，结构化答案/ID满足目标且未耗尽预算。评测器可用隐藏目标作断言，但结果不返回策略。
3. `final_grounding_status`：matches_observed_success / mismatch / no_success_evidence / not_applicable。
4. `unsupported_structured_finish`：结构化finish但未通过grounding。**不称confirmed_false_success**；若旧字段因兼容保留，必须标明legacy含义，禁止当新主指标。
5. `termination_after_observed_unavailability`：已有公开RESOURCE_UNAVAILABLE之后返回控制，是可观测行为，不等于解释语义正确；与exact_reason_code_match分列。
6. `semantic_terminal_status`：unknown为合法结果。纯文本及未能用确定性规则识别的解释，不默认为正确或错误语义。若未实现显式成功主张识别，confirmed_false_success应为未实现/null，而不是零。

不得因为循环减少就判成功：plain_text_unknown提前结束会自然减少后续循环。需同时报告unknown terminal、grounded completion、正常任务、合法重试、预算和成本。以本次15例探索数据不设事后“通过率阈值”。

## 4. B0/B1/B2：保持单一干预及同一底座

### B0-calibrated
使用本轮冻结的校准协议、相同工具schema、普通系统提示和完整公开历史。旧EXP-013保留名称legacy strict pilot，不能只改标签成为新B0。

### B1-generic-prompt
仅在校准B0上加一段固定、跨域的提醒：依据公开工具契约和最新反馈行动，不猜测标识符，重复失败前检查是否已有新信息或允许重试，取得结果后按证据终止。不能加入case/category/hidden值、正确下一步或特定失败案例专用提示。

建议本轮只做通用提示，不同时重写/删除历史，以免无法区分改动来源。历史压缩或失败动作转述应单独命名后续arm；如实际选择该路线，必须保存原历史与变换映射。

### B2-public-repeat-guard
建议从最窄规则开始：当前提议与最近一次**实际执行的**业务行动规范化后相同，该行动返回明确不可重试错误，此后没有新增实际工具信息，则可拒绝提议。retryable=true的TIMEOUT/RUNNING等必须放行，成功读取和普通重复工具名称不是阻断依据。规范化只能沿用环境真正认可的等价规则。

guard只读公开请求、工具文档、公开已执行历史、当前提议；不得读case对象、category、hidden、expected answer、私有snapshot或未来环境结果。它不调用teacher、不查询真实环境“试一下”、不替换成正确动作。

阻断时必须保留：原始提议、guard规则/理由、引用的公开事件、executed=false、业务状态未变、独立guard反馈。不得把GUARD_BLOCK伪装成原工具实际响应，不在环境history里伪造一次失败调用。若下一轮模型继续提同一行动，继续作为提议记录，而不是免费重采样。

B2为B0+guard，不偷偷叠加B1提醒；若叠加就标B1+B2并保留单独B2。本窄guard不声称覆盖ABAB、语义改写或所有无进展行为。

## 5. 预算、种子、事件与指标

- 固定同一模型revision、MPS/BF16、采样、模板、协议、数据与成功断言；B1只变提示、B2只变guard。
- 每次模型生成均消耗一轮；格式失败及被guard阻断的业务提议照样占用预注册的proposal预算，不能给B2免费重试。终止输出不冒充业务工具调用。
- 至少分开：generation_turns、tool_proposals、actual_tool_dispatches、blocked_proposals、malformed_outputs、terminal_outputs。若沿用tool_attempts旧字段，写清其是预算计数而非真实执行次数。
- B2同时报告提议重复/guard命中、实际执行循环及grounded completion。执行循环减少可能只是规则强制效果，不是模型学会恢复。
- 显式冻结`case_id -> seed_base`及每轮派生规则；例如将EXP-013的0,16,...224基值按15个case_id固化，再用seed_base+turn_index。同一case在任意子集/重排中必须得到相同seed；所有arm一致。不同提示/路径下同seed不保证同token随机结果，不能称完全共随机数实验。
- 主比较按case配对报告原始计数/输赢平；15例单seed只做开发探索。不把103轮当103个独立任务，不以挑选最好seed代替完整结果。

## 6. 建议执行顺序与停损

1. 记录校准设计/测试契约；新增确定性单元测试：文本终止、残缺XML、EOS/token截断、结构化失败描述、解释性stop、合法retry、guard阻断/不变状态、无隐藏信息、seed重排稳定。
2. 可新建analysis run对EXP-013原始输出重解析，展示分类差异；原run不修改。该分析**不是反事实动态成绩**，新协议早停后原轨迹后续输出不会发生。
3. 小规模真实校准检查正常调用/终止、普通文本、不可用停止、合法重试；冻结协议与失败处理后再执行三组15dev配对B0/B1/B2。不能边看B1结果边改B0底座。
4. 校准出现新协议错误、隐藏信息输入、guard事件伪造、预算不对齐或配对seed漂移：停止研究解释，保留run、修正后新run。
5. 之后可单独登记jobs-retry_required t3之前的自然失败前缀续跑；前缀来源、snapshot/public history hash、已有消耗与剩余预算各arm完全相同。未实现该模式前不得把E2E称条件恢复实验。

本轮不训练、不构建声称就绪的DPO标签。校准通过只是测量基础改善，不证明算法新颖性或训练必要性。
