# 实验 / 迭代记录模板

复制此模板到新的 `iterations/<ITERATION_ID>.md`；运行原始证据由 `experiments/<RUN_ID>/` 保存。不要以此模板替代 manifest 或逐例数据。

## 运行前登记
- Iteration ID / 日期（Asia/Shanghai）：
- Owner / reviewer / 编辑范围：
- 上一 iteration / parent run：
- 证据类别：synthetic_harness / model_inference / model_training / analysis
- 阶段门禁：G0 / G1 / G2 / G3 / G4
- 假设（可否证的一句话）：
- 本次主要改动（其他变量如何保持不变）：
- 数据版本 / 分组 / 范围 / 来源 run：
- 模型 revision / tokenizer / template / 后端（不适用填 N/A）：
- 配置路径 / 预算 / 随机种子：
- 比较对象 / 指标与分母：
- 预设验收与停损条件：
- 资源与权限是否满足 / 已知限制：

## 实际执行
| RUN_ID | 命令 / frozen config | 退出状态 | 证据路径 |
|---|---|---|---|
| 待填 | 待填 | planned / completed / failed / interrupted | 待填 |

- 实际执行的测试命令与结果（未跑写未跑及原因）：
- 与预登记的偏离 / 原因 / 是否 exploratory：
- 失败、中断或缺失数据（全部保留）：

## 结果核对
| 指标 / 分层 | 分子 | 分母 | 值 / 区间 | 原始数据路径 |
|---|---:|---:|---|---|
| 待填 | — | — | 未运行 | 待填 |

- 样本数、哈希、协议、快照隔离、预算、可见性检查：
- 正常行为和合法重试反例：
- 抽查轨迹的 case/step 与 reviewer：
- 无法测量/实现的指标（不是 0）：

## 结论与交接
- 决策：accept / revise / reject / inconclusive
- 证据支持的结论：
- 证据不支持的结论：
- 副作用 / 混杂 / 可信度限制：
- 阶段是否通过（引用门禁证据，不能只写测试全过）：
- 下一步最小行动 / owner / 输入 / 输出 / 验收标准：
- 更新记录：PROGRESS、任务板、handoff 路径。
