# 终止协议与证据评分校准 v1

日期：2026-11-16（Asia/Shanghai）。协议版本 `qwen_xml_or_native_terminal_v1`；评分版本 `evidence_and_terminal_form_v1`。

## 目标与不变项
013 的29次协议错误均为无 tool_call 的自然语言输出；部分 finish_task 文本明确说明失败，部分 stop_task 已停止但 reason 不满足旧精确枚举。因此先区分工具执行证据、终止格式与未判定的语言语义，再进行共同协议下的 B0/B1/B2。不训练，不修改已有17个历史run或指标。

保留旧 `model_protocol.py` / `harness.py` 的实现与历史结果。新代码只位于 `toolloop_fix/calibrated_protocol.py`，旧严格XML解析作为底层依赖。

## 1. 解析规则
| 模型输出 | 新协议处理 | 绝不自动推断 |
|---|---|---|
| 一个完整合法XML工具调用 | 原样保留工具名/参数，按环境执行 | 不修参数、不换工具、不补正确ID。 |
| 合法finish_task / stop_task | 保留原始结构化终止动作 | 不以调用finish_task本身当成功。 |
| 没有协议控制标记的非空自然响应 | 正常终止，语言语义待审 | 不猜成功、拒绝、求助或失败是否真实。 |
| 无XML的JSON/代码等终止载荷 | 当不透明终止文本，待人工语义审查 | 不把JSON中的name/arguments猜成工具调用。 |
| 部分/多调用XML、异常会话标记 | 仍为无效协议尝试 | 不挽救第一条或把残缺调用当自然结束。 |
| token截断、空输出、仅EOS | 仍无效；按共同预算处理 | 不用截断文本伪造完成。 |

自然文本的透明内部表示为：
```json
{"type":"stop","reason":"NATURAL_LANGUAGE_TERMINAL","raw_text":"未经改写的模型原文"}
```
这只是让现有环境结束交互的生命周期标记，**不声称模型调用了stop_task或其文字有“停止/失败”的语义**。parse另记 `terminal_form=natural_text`、`call=null`、原始文本、旧严格解析诊断。自然终止消耗当前生成轮次，但不是额外业务工具调用；之后不再发FORMAT_ERROR反馈或生成下一轮。

## 2. 公共API与消息边界
- `parse_calibrated_output(raw_text, token_limit_reached=False)`：包含原兼容字段 valid/error/action/call/assistant_content，新增 protocol_version、raw_text、terminal_form、is_terminal、legacy_strict_parse。
- `messages_for_calibrated_view(view, decisions, extra_prompt='')`：用于没有guard阻断的环境历史。共用 `CALIBRATED_SYSTEM_PROMPT`；仅B1通过extra_prompt追加一般建议；不改用户请求、工具结果或秘密标签。
- `tool_schemas`：复用既有公开工具schema；B0/B1/B2使用同一工具集合。
- 已有terminal的decisions不能再调用消息helper；它会明确报错，防止自然结束又进入错误反馈循环。
- B2被阻断的proposal不是环境执行。root runner须独立记录时间顺序，在真实assistant proposal后添加明确的**user guard反馈**；不能往envhistory伪造tool response，不能调用env.execute来模拟一次被阻断执行。
- `score_calibrated_episode(case, final_snapshot, decisions, budget_exhausted)`只供评测器。case/隐藏预期不可传给模型或guard。中间可有blocked decisions；目标证据只来自实际环境history，而非decision/guard文本。末尾terminal decision必须与实际snapshot terminal一致。

## 3. 分开报告的评分字段
| 字段 | 定义与口径 |
|---|---|
| tool_goal_attainment | 实际历史有匹配目标的OK返回，且最终证据对象与之相符。已取得目标后再预算耗尽，不抹去这个已观察事实；它**不是最终回答正确率/任务成功率**。 |
| exact_grounded_structured_completion | 合法finish_task的answer与identifier精确匹配目标，已有实际工具证据，且预算未耗尽。只评结构化格式，不能当所有自然回答的通用成功率。 |
| terminal_form / terminal_form_valid | natural_text、structured_finish、structured_stop或none；只说明格式/终止状态，不说明内容真实。 |
| unsupported_finish_wrapper | finish_task未获得相符的目标证据或字段不匹配；不等同自然语言谎报。 |
| natural_termination_semantics_unknown | 自然终止载荷尚未作语义成功/失败判断，即使它看起来像正确答案。 |
| stopped_after_public_unavailable | 自然或结构化停止前，实际工具曾返回非retryable RESOURCE_UNAVAILABLE；只是可观察的停止证据关联，不保证解释原因正确。 |
| exact_stop_reason_code | 结构化stop的reason精确等于已观察到的RESOURCE_UNAVAILABLE。 |
| stop_reason_semantics_unknown | 自然/结构化stop未满足上述精确证据形式；解释真假需要另审。即使包含正确代码和额外推断，也不自动判真实。 |
| premature_stop_without_goal_evidence | 可完成夹具以自然/结构化stop结束且未取得目标证据。这是“未达目标即终止”的操作性标签，不推断文本是否有合理求助意图。 |
| budget_exhausted | 保留独立预算终止事实。 |
| legacy_strict_parse | 最后输出在旧严格解析器下的valid/error诊断；**不是旧策略的反事实完整任务得分**。 |

目标证据和精确结构化完成均以预先定义的可完成案例为分母（当前15夹具中12个）；不可完成的3例另报告停止行为。语义未知必须单列，不能当0个正确自然回答，也不能当正确自然回答。

## 4. 历史、校准与新对照的证据级别
1. 旧013及之前所有指标冻结不变。
2. 离线重解析013只回答“原始输出将被哪种语法/终止类别接收”。自然终止会切断后续对话，所以不能拿旧后续生成推算新协议的完整任务表现。
3. 新协议下先作新的B0小规模校准，再开展B0/B1/B2配对；共同模型revision、采样、工具、终止评分、预算、案例顺序/显式case-seed映射。
4. B1仅增加通用提示；B2仅使用公开信息阻断无益重复，不替模型生成改正动作。proposal、executed、blocked三种计数必须分开；阻断消耗相同的proposal/轮次预算。
5. 自然文本的人工复核、停止原因语义评定及未来宽松回答判定都需单独版本和记录；不得事后为某一arm修改判分规则。

## 5. 定向验证与剩余边界
已执行：
```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -p test_calibrated_protocol.py -v
```
**18项通过**：原XML不变；自然结束不重试；空/残缺/多调用/截断仍拒绝；公共消息只加显式B1提示；目标证据与精确完成分离；错误ID不修复；stop证据不做语言真伪判定；blocked decision不能制造OK证据；terminal/snapshot一致性。

本模块未运行模型、未新增依赖、未提供自然语言裁判。它仅适用于当前可重置的读取类夹具；写操作、真实服务、副作用恢复、token语义和统计泛化需后续独立设计。训练门禁未因此通过。
