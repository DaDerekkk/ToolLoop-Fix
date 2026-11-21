# ITER-004 — 语义复核、固定错误前缀恢复对照与延期训练交接

## 运行前登记（2026-11-16，Asia/Shanghai）
用户要求：多agent继续；先复核B2无依据成功、任务措辞及终止语义，再做固定错误前缀恢复对照。**暂不训练**。用户报告未来有M6/24G；这不是本机硬件验证或训练启动授权。

### 假设与设计
- B2可机械拦截重复但不能保证后续语言真实性；先复核旧42项待审输出，保留原自动unknown，agent复核不是人工金标。
- 原请求target如latest order/daily report可能被理解为额外操作；只对initial user task增加共同明确释义，独立于B1/B2，旧成绩不改。
- 固定6条真实历史工具前缀：订单TIMEOUT(016,t1)、文件TIMEOUT(017,t1)、job PRECONDITION(013,t1–2)、订单INVALID_IDENTIFIER(016,t1)、订单RESOURCE_UNAVAILABLE+多余澄清(018,t1–2)、订单OK(016,t1)。前四为恢复，后两为停止/完成控制；不能当自然故障发生率。
- 2种任务措辞(legacy/explicit) × 3arm(B0/B1/B2) × 6前缀 = 36次新续跑。历史prefix转植于新共同上下文，不声称新措辞当初自然生成了该prefix。
- explicit版本read_report_literal_target_v1；仅initial user释义不同，prefix assistant/tool原始内容不改。B0/B2系统提示相同，B1只用已冻结通用提醒。
- 固定官方Qwen3.5-0.8B revision、MPS BF16、原采样/256输出上限。总预算12工具提案/16轮，前缀计入；同prefix独立深拷贝与相同剩余预算。种子1000,1032,…1160，每续跑seed=prefix_base+已用prefix轮+continuation_index，各条件共享；不重采样选优。
- 模型/guard只看公开请求、工具、prefix及续跑公开历史；hidden只供环境/评测。历史source系统提示不沿用，原始source完整记录只供审计。
- 指标：四恢复prefix新取得目标证据/4、精确结构化完成/4；正常OK控制的证据本来已存在，不能计新恢复；不可用停止控制另报。所有自然终止语义unknown。原有循环与续跑新增循环、重复提案/实际阻断分开。
- 成本：prefix已花历史tokens与本次新生成分列，绝不把移植prefix当本次生成；本次prompttokens包含反复输入的prefix。业务工具/轮数总预算含prefix。

### 顺序与验收
1. 旧25run哈希/完整性基线；语义复核与taskcontract冻结，归档analysis。
2. 构造带sourcehash/原始行号的prefixdataset，独立重放每一prefix状态/响应，无补动作/参数。
3. 单元测试+独立源码/信息边界门禁通过，冻结6份推理配置后顺序GPU运行，不并发占内存。
4. 配对结果、逐轮CPU重放/预算检查，旧记录完整性核验。失败留档不覆盖，不因结果差而改规则。
5. 更新分类索引、iteration、进度/任务板及交接；M6目标机器训练只交付disabled计划/检查清单。

### 停损与限制
模型加载/GPU/记录/快照/公开边界异常则保存失败并停，先修后新run；无CPU脚本接管、训练、云资源、上传。仅6条选择过的已知dev前缀与单种子，不是统计显著性/泛化/端到端效果。M6实际芯片、内存、OS/框架/模型兼容及训练内存均待目标机实测。

## 分工
- root：来源构造、配置/归档、实际运行、分析审计与总交接。
- protocol：42项agent语义复核、措辞契约、独立prefix审核。
- simulator：独立prefix runtime及测试/固定流程。
- source_audit：官方资料核对、M6/24G迁移与延期训练计划，禁止训练/安装/远程连接。

## 状态（预登记时）
当前旧25run校验0errors。语义审核及prefix实现中；本迭代尚未运行新模型或训练。后续结果依时间追加。

## 本机与目标机区分
本机只读sysctl首次在沙箱被拒绝，按正常审批后读取成功：Apple M5、17,179,869,184bytes RAM（16GiB）、macOS26.6.2。此次不连接M6；用户报告的M6/24G仍为未来待核验目标。没有安装或训练。源码前置诊断测试的原始输出不等于正式实验，联合门禁将另行record_run归档。

## 语义/资源记录与prefix构造（真实续跑之前）
- 42项队列+3结构化正对照已完成agent逐项复核，引用SHA/行号/原文核验；不是人工金标，不改旧自动分数。只保留一例明确无依据成功措辞为重点风险，不把所选队列标签计成总体错误率。
- EXP-021归档因review/handoff同basename触发排他写入保护失败；不是模型/语义失败，未加载模型。保留该失败，先加唯一名称预检查及2项回归（修前1失败，修后2通过），新EXP-021R1完整归档7份材料、45原文引用和只读权重头数字。
- M6计划已disabled归档，权重文本存储752,393,024标量为文件头算术，不是训练内存实测；未来明确授权和G1/G2通过前不得反向传播。
- EXP-022从不可变来源抽出6个prefix、8轮既有真实工具调用；完整动作/响应/业务状态逐一重放。dataset SHA256=503af73359679e57f7522b37334fba8b342f2a95e916bc55b5a095f4782a3441。
- 配置6份已在模型运行前建立；semantic/archive失败没有触发任何模型重采样。

## 正式续跑门禁
- EXP-023：210项冻结联合测试通过，包含原172项与prefix/构造/归档回归。
- EXP-024：官方本地tokenizer渲染36个起点，最多1092输入tokens；共享状态/公开历史/工具/剩余预算、只改initial user措辞、B0/B2系统提示一致通过。未加载模型或训练。
- 独立源码审查关闭历史harness版本漂移、prepared预算错配与分支环境复用问题。旧E2E核心模块未改。
- 025–030按legacy B0/B1/B2、explicit B0/B1/B2固定顺序运行，单GPU顺序执行，进程或记录失败即停；本段写入时真实续跑已开始，尚未得到六组最终结果。

## 最终结果与结论
- 025–030六格全部completed/evidence_valid；36次案例续跑、62次fresh generation，输入/输出58,643/3,433tokens，实际续跑工具23次。独特历史prefix只有8工具轮，被六格引用为48次exposure，绝不是48次新生成。
- 每格四个恢复prefix均为同一订单TIMEOUT成功：新证据1/4、精确结构化完成1/4；各arm/措辞配对0胜/4平/0负。files TIMEOUT、jobs PRECONDITION及orders INVALID未恢复。
- 正常完成控制prefix已获OK，不能算新恢复；明确契约下自然值/ID交付更清楚的单例观察不升级为总体语义成功率。不可用后停止仍不保证解释真实。
- explicit B2阻断2次重复；legacy B2无触发且与B0对应9轮raw/tokens/messages/seed相同。explicit两组首次干预前/含首次block的8个配对生成相同；其后无依据语言问题仍存在。仅支持机械执行抑制，不支持学会恢复。
- 明确契约下文件虚构value=0、jobs称做过实际未执行的poll/读取等风险见independent results review。自然语义unknown不改，agent判断不作为人工训练标签。
- REVIEW-005由protocol正式执行：36案例/62已有生成精确CPU回放，36首提示/token/seed与024一致；只是记录/实现一致性。
- EXP-031正式六格比较；EXP-032最终240项联合测试通过；EXP-033归档最终独立设计/结果review、prefix/audit/comparison交接、M6路径迁移补充。run编号不强制时间顺序，实际时序以manifest UTC为准。
- 新6run索引、36项全量待人工队列及交接hash索引写data/raw、data/processed；36不是失败数，全部training_eligible=false。旧42项review仍为agent初审，旧自动成绩与25个旧manifest不变。

## M6/24G 留给后续的训练部分
021R1归档训练方案与disabled机器配置；033归档路径迁移补充。目标机未连接/核验，本轮实际主机M5/16GiB。先核硬件/环境/文件hash/绝对路径映射、独立推理复现；再人工数据/检测/分组门禁及训练专属记录契约；获新明确授权后才做LoRA梯度/内存/checkpoint探针及B3/B4/B5/P公平比较。任何资源数字均非目标机benchmark，未运行训练、反向传播、安装、下载、迁移、上传。

## 最终验收
工程和审计链 bounded accept；本轮没有观察到主要恢复收益，语义可信性仍不足。保持P1部分完成、P2未过、训练HOLD。下一agent从PROGRESS/coordinator/FIXED_PREFIX_RECOVERY与未来机器两份计划接手；旧latest入口另存ITER003版本。报告成本表的“解析错/预算耗尽”单元格式是“次数/耗尽数/案例数”，结构化JSON分列字段为权威口径，不是三项相除。
