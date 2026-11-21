# ITER-001 独立复验清单

日期：2026-11-16（Asia/Shanghai）　Owner：protocol agent

## 状态与范围
**状态：已完成独立复验；accept（仅本轮 G0 修复范围，保留 R3/R7 等限制）。** 本文件先写验收预期，随后附实际结果；没有修改协议、实现或历史 run。用户选择先完成本地工程验证，模型环境与真实推理另议。

父证据：`EXP-20261116-REVIEW-001/review_probes.json`，其 26 个既有 harness 测试通过，但新增边界探针发现四项缺口。记录系统问题另参见 `agents/reviews/recording_review.md`。后继 run 必须引用父证据，不能覆盖初始记录。

## A. Harness 修复验收
| ID | 必须复验的输入/操作 | 预期结果 | 状态 / 证据 |
|---|---|---|---|
| H1 | 1 步固定前缀 + naive 续跑，总 tool_budget=12 | prefix+continuation ≤12；记录两段和 total；budget_exhausted=true 且 success=false | 通过；REVIEW-002 total_budget：1+11=12 |
| H2 | 前缀恰好耗尽/超过工具或交互预算；包含 malformed 尝试 | 不执行预算外前缀或续跑；不可隐藏免费工具尝试；失败/无效前缀有明确记录 | 通过；exact_budget_prefix / over_budget_prefix + 工具/轮数测试 |
| H3 | 同前缀、两策略、相同总预算 | 快照一致，剩余调用/轮数一致；原始快照不被策略或候选分支污染 | 通过；all_fixed_prefix_pairing + 深拷贝/候选测试 |
| H4 | timeout 正好在允许的 2 次 retry 内恢复 | 不误报无效循环，正常取得证据并完成；max retries=2 的初次调用与重试计数语义清楚 | 通过；timeout_2：TIMEOUT×2→OK→FINAL |
| H5 | 父探针 transient_remaining=5；任意策略尝试越限 | 不再越限成功；公开限额由环境/判断层执行而非仅脚本自律；越限动作或违规指标可追踪 | 通过；timeout_5：第4次返回限额错误，success=false、loop=true |
| H6 | poll 正好到第 3 次 READY；以及 polls_remaining=5 | 3 次以内合法反例不误报；5 次案例不能像旧版越限成功；计数/上限进入快照并可恢复 | 通过；polls_3 成功无循环；polls_5 不成功且循环被检出；runtime 属于快照 |
| H7 | 3 次旧错误组成 prefix，续跑 LOCATED→OK→FINAL | inherited flags 保留，但 continuation has_invalid_loop=false；不能把继承故障归因新策略 | 通过；inherited_loop：prefix indices 1/2，continuation=[] |
| H8 | prefix 最后一次失败后，续跑立即再次相同失败 | 新循环仍被检出，索引属于 continuation；不能为了 H7 丢掉跨边界检测 | 通过；cross_boundary_loop：index=1 |
| H9 | 已有 identifier 却 ask_user，再完成任务 | 记录 unnecessary_clarification；与 premature_abandonment/stop 的关系符合所声明口径，不因最终成功抹去副作用 | 通过；unnecessary_help：success=true，求助及副作用均=true |
| H10 | 合法缺信息→ask_user→答复→读取→最终回答；之后再重复 ask_user | 必要首次澄清不惩罚；已获得信息后的重复求助按公开契约处理，不能无限免费获取同一信息 | 通过；原夹具+required_help 测试、repeat_help_snapshot |
| H11 | 修改后重新运行原 15 夹具与全部既有测试 | 保留正常完成、有限重试、正确停止、证据断言、无隐藏标签边界、malformed 预算、零分母语义 | 通过；REVIEW-002 重跑全部51测试；all_15_public_boundaries |
| H12 | 重算成本、模式和副作用指标 | total/continuation 成本口径显式；不可恢复案例排除完成分母；失败调用仍计成本；无效分母=null | 通过；成本/分母单测，读取 -004 metrics schema2 |

H5/H6 的异常扩展夹具仅用于限额测试，不得混入正式“可完成任务”指标后宣称模型失败。若达到公开限额后业务仍不可完成，终止语义与标签需另行定义，不可沿用旧 hidden category 自动给研究结论。

## B. 记录系统修复验收
| ID | 必须复验的输入/操作 | 预期结果 | 状态 / 证据 |
|---|---|---|---|
| R1 | 普通 wrapper run | 默认实际 cwd 为冻结 inputs；manifest 明确运行位置、配置与输入来源，不再只保存副本却运行可变源码 | 通过；REVIEW-002 frozen_cwd 与 manifest 一致 |
| R2 | 冻结后只改变工作区输入，不碰快照 | 实际子进程仍运行已冻结版本；工作区漂移与证据是否无效的声明一致，不能默默换代码 | 通过；live_change 与 live_edit_restore 两项回归重跑通过 |
| R3 | 配置自身、数据路径、命令引用 workspace 绝对路径/快照外文件 | 禁止绕过冻结边界，或显式 external-input 清单与哈希并标限制；不能声称所有输入已冻结 | 部分/后续门禁：配置路径约束已测；任意绝对路径及外部数据未自动清单化 |
| R4 | 对归档 config、input、output 分别进行受控篡改测试（仅临时测试副本） | 校验器报错、非零退出；原始历史 run 不被测试修改 | 通过已测范围；config 漏列篡改、input 变更、stdout 篡改测试均报错 |
| R5 | 漏列/多列文件、manifest 无效类型/枚举/ID/path | inventory 与 schema 验证能发现；路径不能逃逸 run；不能仅验证列出的哈希就接受未列入的 artifacts | 部分验证；schema/枚举/非对象/漏列配置测试通过；路径与inventory集合检查已读代码，非穷举畸形输入 |
| R6 | completed+非零 exit、缺 end time/log/config、running 半成品 | 不应判为完整有效证据；失败/中断原样保留且可被索引区分 | 部分验证；缺结束字段/未知状态测试通过；退出码及文件存在性检查已读代码，未实测信号/掉电 |
| R7 | 配置与 manifest/结果证据类别冲突 | 校验报错或明确拒绝；synthetic、analysis 与模型证据不能混称 | 未实现，保留限制；REVIEW-002 known_unimplemented_semantic_kind_crosscheck |
| R8 | 重复 ID；运行命令非零退出；缺失命令 | 不覆盖；故障仍有可读 manifest/stdout/stderr/exit；validator 不把失败当科学验收通过 | 通过已测范围；重复ID拒绝、exit7保留；无命令运行前拒绝（代码审查） |
| R9 | 验证本轮之前的 v1 历史记录 | 不覆盖旧 manifest；schema 兼容或清楚标 legacy 的限制；review/unit-test run 与完整 harness run 的所需 artifacts 契约区分 | 通过；9个run审计0错误，含v1失败/诊断；-002与-006回放3哈希相同 |
| R10 | 重新生成 INDEX | 只生成派生索引；不会改变 run 证据；展示完整性与进程状态，而非把 completed 写成科学通过 | 已读代码，独立审查不写INDEX；本次新增第9个run后由root重新生成 |

记录器是可追溯执行工具，不是安全沙箱或签名系统；任意 Python 子进程可尝试读外部文件，哈希也不防能同时改 manifest 的攻击者。验收只检查声明的项目内输入与真实运行路径一致，不做不成立的绝对隔离承诺。

## C. 复验执行方式
1. 等 root 宣布代码冻结后读取当前实现与新增测试，核对每项实际修复位置。
2. 使用唯一后继 run 保存完整命令、快照、stdout/stderr 和结构化 probe 结果。单位测试/审查探针的 kind 与 artifact contract 按新版 recorder 明确设置，不伪装成模型推理。
3. 重新运行父探针并增加 H2/H4/H6/H8/H10 对照，既验证 bug 消失，也验证合法行为未被误伤。
4. 实际通过项填入 run 与 case/检查名；未运行写未运行与原因。不要只引用实现者声称通过。
5. 检查完整归档 validator 与索引；最后写 accept/revise/inconclusive，并区分工程边界与研究阶段。

## 尚不属于本轮可验收结论
真实模型循环率、训练收益、检测器 200 段人工复核、模板外/跨域泛化、正式统计置信区间、GPU 可行性和训练数据质量，均仍 pending。即使本清单全过也只是 G0 工程记录与模拟框架验收，不是 G1/G2 研究门禁通过。

## 独立复验结果与决定

- 新 run：`experiments/EXP-20261116-REVIEW-002/`，kind=analysis，parent=REVIEW-001；manifest 保存完整命令。run 退出码 0、evidence_valid=true、冻结 inputs 未变化。
- 实际重跑 `python3 -m unittest discover -s tests -v`：**51 tests passed**；非引用实现者结果。
- 额外独立断言及逐项结果见该 run 的 `review_probes.json`；原四项 findings 均按预期消除，合法限额内重试/轮询、公开视图与快照隔离没有在已测范围回归。
- 直接读取已归档文件重新计算 SHA256，而非只相信 comparison.json：`-004 ↔ -005/replay`、`-002 ↔ -006/replay` 各自的 trajectories/case_results/metrics 三个文件均相同。只说明冻结版本的确定性回放，不说明新旧实现结果应该相同。
- run 结束后实际执行 `python3 scripts/validate_records.py`：**9 runs checked，0 errors**。历史失败 run 仍保留为失败，不转为通过。

**决定：accept 本轮 ITER-001 的 G0 修复范围。** 原四项 harness 缺口及初始记录审查中的冻结执行、配置漏检、schema 主要缺口已有执行证据。此决定不声称上述扩展清单所有项均完成。

### 交接时必须保留的限制
1. R3：record_run 默认冻结已知项目路径，但不拦截任意 Python/命令读取绝对外部路径；这些依赖不会自动全部归档。真实模型/data/raw/data/processed 接入前必须显式登记版本、路径、哈希、运行依赖；复现脚本需声明读取旧 run 的绝对路径。
2. R7：临时目录中设置 config.evidence_kind=analysis、manifest.kind=model_inference，现校验器仍返回无错误。说明 kind 是登记标签而非执行语义证明；本轮实际 run 标签正确，不因此推断其被污染，但进入模型阶段前应补一致性检查与模型专用 artifact contract。
3. 脚本 reference 在公开限额耗尽后仍可能继续触发错误直至总预算；本轮修复的是“不越限推进/虚假成功”和检测，而非提供所有扩展故障的最优策略。
4. 15 个夹具/42 行评测不是独立模型样本；手工前缀、保守循环原型、单步候选、无正式统计与真实工具语义的限制继续成立。
5. 源码、协议、模型/数据清单、已结束 run 均未被本 reviewer 改写；只新增分析 run 和更新本 review/handoff。
