# 协调者最新交接 — ITER-004 / 2026-11-16

## 范围
用户要求先复核B2无依据成功/任务措辞/终止语义，再固定错误前缀对照；暂不训练，训练相关方案留给未来M6/24G。当前已完成复核、六格真实续跑、比较/回归和延期训练交接。无训练、无新安装下载、无目标机连接/迁移/上传。旧总交接coordinator_ITER003.md保留。

## 权威记录
- 021=归档同名冲突失败；021R1=新名称/前置校验修复后成功，归档42项agent审核+3正对照与disabled训练计划；旧指标未改。
- 022=6真实prefix/8历史工具轮抽取（来源013/016/017/018）；dataset SHA503af73359679e57f7522b37334fba8b342f2a95e916bc55b5a095f4782a3441。
- 023=210测试；024=36官方tokenizer起点核对通过。
- 025/026/027=legacy B0/B1/B2，028/029/030=explicit B0/B1/B2，各6prefix共36案例/62fresh生成。
- REVIEW-005=36案例/62轮记录精确回放及024首轮提示/种子核对通过，无新推理。031=六格比较；032=最终240项测试；033=最终review/交接/路径迁移补充归档。40run完整性0errors，25个旧manifest保持不变。

## 结果和限制
所有格仅同一订单TIMEOUT恢复：新目标证据1/4、精确结构化完成1/4，B1/B2或措辞对比均0胜/4平/0负。只有explicit B2触发2次guard；文件虚构value=0、jobs声称已poll/完成但无执行证据等仍需语义复核。正常OK控制的证据先前已在prefix中，不能算新恢复。自然文本保持unknown，不把agent初审当人工金标。

真实新生成62轮、输入/输出58,643/3,433token、23次实际续跑工具。8历史轮被6格引用=48prefix exposure，不是新推理。单一seed/选择过的已知devprefix，不做显著性、总体故障率、端到端或训练收益声明。

## 运行接口
- `toolloop_fix/prefix_run.py`新入口，旧E2E核心未改。任务措辞version=read_report_literal_target_v1，prefix schema=authentic_tool_prefix_v1。
- legacy initialuser保持原请求；explicit只替换initialuser释义，target定义为字面lookup label，属于任务契约干预而非纯同义重写。历史prefix assistant/tool原始内容保持；不沿用source systemprompt。
- prepare_prefix验证源manifest/rawgen/traj/cases和当前/历史harness一致；按raw重放每个action/response/状态，不能只信snapshot。
- 每个分支fresh Environment+deepcopy已验证snapshot；prepared绑定row/arm/wording/两预算。模型/guard不见hidden/eval标签。
- 总12工具提案/16轮含prefix；首轮fresh seeds1001/1033/1066/1097/1130/1161，各条件相同。prefix无本次generation；历史tokens和freshtokens分列。
- inherited loop按实际工具history index<prefixcount，新增loop含跨边界触发。此次6真实prefix均无inherited loop，继承循环仅由合成回归验证。
- 审计首轮prompt SHA/token/seed与024绑定，trace/result/prefix_context/metrics独立计数与同代码重放。不是独立语义真值。

## 后续机器/训练留下的材料
- `docs/plans/M6_24G_TRAINING_HANDOFF.md`（已归档021R1）；`configs/future_training_plan.json`为plan_only/disabled，没有训练入口。
- `docs/plans/M6_PORTABILITY_ADDENDUM.md`处理绝对路径、旧cwd和模型清单迁移；旧manifest不得重写，未来需新增路径映射及派生prefix/config。
- 当前主机M5/16GiB，M6/24G只是用户报告的未核验目标。规划内存不代表实测；现有推理通过不证明backward或24G全量DPO可行。
- 未来先目标机硬件/离线完整性/推理复现；再人工数据/分组门禁、训练记录契约、明确授权；然后LoRA小步内存/梯度/checkpoint恢复探针与公平比较。训练部分**未执行**。

## 分类与接手
`PROGRESS.md`、ITER-004、FIXED_PREFIX_RECOVERY.md → 021R1语义资料、025–030raw、独立结果review及REVIEW-005。data/raw有新6run索引；data/processed有旧42项agent初审及新36项待人工队列；都不是训练标签，不把整对象喂模型。模型权重/旧结果只读，失败新ID，禁止无日志重采样。
