# Protocol agent 交接

日期：2026-11-16（Asia/Shanghai）

## 完成内容
- `docs/protocols/EXPERIMENT_WORKFLOW.md`：固定记录流程、wrapper 契约、协作方式、阶段门禁。
- `docs/protocols/EVALUATION_SPEC.md`：可见性、两层评测、循环与候选标准、指标分母、数据隔离和统计边界。
- `docs/protocols/EXPERIMENT_TEMPLATE.md`：后续 iteration 可复制模板。
- `agents/reviews/initial_design_review.md`：独立设计风险与待核验清单。

## 检查与限制
- 已读取 AGENTS.md 和提案提取文本并逐项对照；没有安装依赖、联网、修改代码或运行模型实验。
- 已用 Python 标准库检查五份文档非空、UTF-8 可读、代码围栏配对，以及工作流包含全部 wrapper 必需参数与两个占位符；检查通过。此检查不验证 CLI 实际执行。
- 文档命令遵循协调者提供的接口；wrapper 尚由其他 agent 实现，需协调者用实际 CLI 核对。
- 设计审查未判定代码通过；代码/运行验收须另附证据。200 段人工审查、真实 Qwen B0/B1/B2、正式划分和训练均未完成。

## 协调者注意
- 完成进程不等于实验验收；缺失指标不得写 0。
- 若当前 harness 只检查正确澄清问题，没有补充后续跑，不能计入完整任务成功。
- 固定前缀与全程必须分开报，固定前缀标明人工来源，并对齐剩余预算。
- 参数规范化应服从工具语义，不能全局去空格/忽略大小写破坏路径含义。
- 后续 worker 使用模板，不改已结束 run；协调者独占总进度与任务板。

## 后续代码审查交付
- 在协议冻结后追加了真实代码审查（只修改 review/handoff，没有改实现或协议）。
- run：`experiments/EXP-20261116-REVIEW-001/`；既有 harness 26 tests passed；输入未变，原始日志和 `review_probes.json` 已保留。
- 四个可复现 P2：固定前缀不扣总预算；有限 retry/poll 上限未实现；继承前缀循环归因给续跑策略；提前放弃指标漏掉无必要求助。已通知 root 和 simulator。
- 认可的当前边界：策略只看公开视图；澄清有补充后续跑；快照拷贝；证据型成功断言；不可恢复分母隔离及失败成本保留。
- 结论为 revise，待新 iteration 修复并回归；不覆盖原始 run，也不把工程测试当模型研究结果。

## ITER-001 最终独立复验
- `agents/reviews/iteration1_review.md` 已填实际验收与限制；新分析 run 为 `EXP-20261116-REVIEW-002`，父记录为 REVIEW-001。
- 在冻结 inputs cwd 实际重跑全部 51 tests，并独立复验原四项缺口、合法限额反例、继承/跨界循环、重复求助、15 例公开边界及固定前缀配对；均在已测范围通过。
- 直接重算两组回放三文件哈希：-004/-005、-002/-006 各自一致；归档校验 9 runs、0 errors。
- 决策 accept **本轮 G0 修复范围**；不意味着真实模型或训练门禁通过。root 需重新生成包含第9个run的 INDEX。
- 仍需保留：外部绝对路径依赖不自动完全登记；配置 evidence_kind 与 manifest.kind 尚无语义一致性校验（已在临时目录探针复现）。真实模型接入前补模型数据/依赖清单与专用artifact契约，不把 kind 枚举当运行证明。
