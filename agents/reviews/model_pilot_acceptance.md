# 真实模型 pilot 独立验收

日期：2026-11-16（Asia/Shanghai）；reviewer：protocol agent。

## 范围与当前状态
已独立核对 CPU/MPS 预检，以及已结束的 15 个开发夹具 MPS pilot。只读代码、配置、模型元数据与原始轨迹；不改实现、不重新采样、不加载权重。**决定：accept 可审计接入/pilot；研究阶段仍需 revise，不通过 G1 模型故障归因门禁。**

## 已核对的前置事实
| Run | 进程/记录结果 | 与任务效果的区别 |
|---|---|---|
| EXP-20261116-008 | failed，exit1，evidence_valid=false；model_load 阶段 TypeError：set 无法 JSON 序列化；无 generations | 记录/框架失败，不能归因模型任务能力。 |
| EXP-20261116-009 | analysis failed，28 tests、2 errors | 失败回归证据保留；不算模型实验。 |
| EXP-20261116-010 | analysis completed，94 tests passed | 工程修复通过，不说明任务完成率。 |
| EXP-20261116-011 | CPU float32，completed，记录契约有效 | 模型读到 ORD-101/shipped，却在 finish_task 输出 identifier=completed，结果0/1、false_success=true，错误未被修正或隐藏。 |
| EXP-20261116-012 | MPS bfloat16，completed，记录契约有效 | 同一正常夹具返回 ORD-101/shipped，结果1/1。CPU/MPS 同时改变设备与精度，且采样不保证数值复现；不是质量提升的对照证据。 |

## 代码审查边界
- model_run 将公开 request/tools/history 转为 messages；backend.generate 不接收 case/category/hidden snapshot。分类字段可以出现在审计 JSONL 的外层，但不能进入实际 prompts。
- 执行动作来自原始文本的严格单调用 XML 解析。格式错误保留原文，作为一次 invalid_model_output 和预算尝试；没有“帮模型补参数”、scripted_policy 兜底或取多调用第一条的挽救。
- finish_task / stop_task 是自定义终止包装工具，任务文本也是结构化夹具；不能称为原生官方 benchmark。
- 后端在加载前校验模型清单文件；实际 packages 由运行解释器取得。MPS 使用显式 bfloat16，provenance 记录fallback=0；代码没有自动转CPU的分支。
- 每轮 seed = base_seed + case_index × turn_budget + turn_index。此索引依赖案例顺序，重排/子集运行会改变后续案例随机种子；本轮只是单条种子计划，不是多种子稳定性结论。

## 最终分析执行与结论

分析 run：`experiments/EXP-20261116-REVIEW-003/`，kind=analysis，parent=EXP-20261116-013。完整命令存于 manifest，结构化独立核对输出为 `model_pilot_audit.json`，stdout/stderr 原样保留。使用 `/usr/bin/python3` 与标准库，只重放已记录动作，不产生新的模型输出。

- REVIEW-003：completed，exit0，evidence_valid=true。
- 011/012 各2轮、013全部103轮，均核对原始文本→严格解析→执行动作→工具返回→状态哈希→最终环境/判定；共107轮一致。
- 每轮 messages 与从当时公开视图重建的结果相等；tools相等；渲染prompt中未发现category/hidden/template_group/case ID，隐藏结果值只有在请求/可见历史已经出现时才能出现。记录JSONL外层的分类标签未传给模型。
- prompt SHA、输入/输出token数组长度、每轮seed索引及有效生成参数一致；没有重新加载tokenizer逐token解码，因此不声称独立重算了模型分词或浮点推理。
- 逐例成功、正确停止、预算耗尽、循环标记和副作用均经环境状态重放复核；所有聚合指标的分子、分母、token与时间求和重算一致。
- 实际身份为 Qwen/Qwen3.5-0.8B、revision `2fc06364715b967f1860aea9cf38778875588b17`；执行Python3.12.13、torch2.15.0、transformers5.23.0；模型/模板清单、安装清单引用及安装记录所列文件哈希一致。模型文件本身由实际runner加载前验证，本review未再次读取GB权重。
- 在分析run进行中排除自身，16个历史run校验无错误；分析结束后实际运行 `python3 scripts/validate_records.py`：**17 runs checked，0 errors**。失败的008/009以及更早失败记录仍保留。

### 013 实际结果（不是训练效果）
| 指标 | 已复核分子/分母 | 正确解释 |
|---|---:|---|
| 开发案例 / 生成轮数 | 15 / 103 | 15个手写开发夹具，不是103个独立任务。 |
| 任务完成 | 2/12 | 3个unrecoverable不进入分母；两个成功均为orders域。 |
| 正常任务完成 | 1/3 | 正常能力门槛尚弱，不宜直接研究专项训练。 |
| 必要重试完成 | 0/3 | 本轮执行结果，不是模型总体能力估计。 |
| 检测器标记循环 | 4/15 | 保守原型+自定义协议，不等于已确认模型固有循环故障。 |
| finish_task未满足目标（代码名false_success） | 4/15 | 终止包装违约；不能全部解读成自然语言虚报成功。 |
| 预算耗尽 | 5/15 | 这些案例保留原始动作，未隐藏为正常结束。 |
| 严格正确停止 | 0/3 | reason必须精确等于RESOURCE_UNAVAILABLE；见下方语义限制。 |
| 协议解析错误 | 29/103 | 29轮均没有tool_call标签，而非29个独立工具参数错误。 |
| 提前停止/不必要求助 | 5/12 | 定义包含求助，即使后续成功也不抹去已发生副作用。 |
| 单位成功调用成本 | 75/2 = 37.5 | 计入可完成案例中所有失败调用；不含不可完成案例成本。 |

总输入111,370 tokens、输出5,801 tokens；累计generation wall time约491.11秒。它不是GPU计费时间，也不是重复独立种子均值。固定失败现场模型恢复率本轮未测，指标为N/A，不是0%。

### 案例结果与原始失败保留
| Case | 结果 | 需要注意的证据 |
|---|---|---|
| orders-normal-001 | 成功 | ORD-101 / shipped 与返回一致。 |
| orders-recoverable-001 | stop、失败 | 参数/查找失败后停止，未恢复。 |
| orders-retry_required-001 | 不合格finish_task | answer为NOT_FOUND对象，并未取得目标结果。 |
| orders-clarification-001 | 成功 | 用户补充→读取→准确finish_task。 |
| orders-unrecoverable-001 | stop、严格判定失败 | reason包含RESOURCE_UNAVAILABLE及额外文字/推断，不等于规定枚举。 |
| files-normal-001 | stop、失败 | INVALID_IDENTIFIER和无tool_call输出后停止。 |
| files-recoverable-001 | 预算耗尽、循环 | 重复不必要ask_user。 |
| files-retry_required-001 | 不合格finish_task | answer=NOT_FOUND、identifier也未满足目标。 |
| files-clarification-001 | 不合格finish_task、循环 | 最终提交请求对象和read_file()，非已取得结果。 |
| files-unrecoverable-001 | stop、严格判定失败 | 自然语言解释不可访问，但reason不是规定枚举。 |
| jobs-normal-001 | 预算耗尽、循环 | 曾取得OK，随后继续错误调用/求助而未合法结束。 |
| jobs-recoverable-001 | 不合格finish_task | 最终文本说明JOB-OLD未找到，未完成目标。 |
| jobs-retry_required-001 | 预算耗尽、循环 | PRECONDITION反复出现及无tool_call输出。 |
| jobs-clarification-001 | 预算耗尽 | 9轮无tool_call，不能仅按工具决策失败解释。 |
| jobs-unrecoverable-001 | 预算耗尽 | 最后仍尝试标识符，无合法终止。 |

### 必须先解决的协议/评测混杂
1. **false_success的命名比实际口径宽。** 4例均调用finish_task但没有完成目标，其中多例原始自然语言明确说明NOT_FOUND/未找到。保留冻结指标，不事后改分；对外应称“按finish_task契约判定的不合格成功终止”，不要声称4例都在文字上谎报完成。
2. **correct_termination的严格枚举约束会惩罚语义相近的停止。** orders/files两个不可完成案例实际调用stop_task并解释资源不可用，仍因reason不精确而计错；不能把0/3解释成3例都不肯停止。其文字也包含额外推断，是否语义正确需独立标注，不能现在擅自重算成2/3。
3. **29个解析错误全部是没有tool_call标签的自然语言输出。** 普通工具对话允许最终文本，但本项目包装要求finish_task/stop_task，因此这里混合了自定义协议遵循与恢复能力。后续先做共同格式适配/终止语义校准，再比较B0/B1/B2；改协议必须新iteration/run，不覆盖013。
4. 只有15个已参与工程开发的夹具、单条按case_index偏移的seed计划、单次MPS bfloat16运行，且无正常能力充足的证据。不能据此声称Qwen普遍的循环率、Antidoom迁移有效或专项训练必要。

### 验收决定与下一步
**accept：** 已完成可追溯的真实模型加载/推理与失败保留；不能将这些结果冒充脚本策略，原始生成确实贯穿执行链且无程序补救成功。

**revise：** G1问题确认尚未通过。优先澄清自然语言终止与包装动作的共同语义、验证格式适配后的正常任务能力，再扩充开发样本、复核检测器及建立B1/B2。当前不进入训练、不作模型故障因果归因。
