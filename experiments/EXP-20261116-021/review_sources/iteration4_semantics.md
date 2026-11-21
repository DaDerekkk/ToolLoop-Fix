# ITER-004 语义审查验收

Reviewer：protocol agent；日期：2026-11-16。范围仅新增语义审查资料；没有代码、历史指标或模型运行变更。

## 判断：有界 accept，人工审查仍 pending

- 已读 42 项 terminal/last raw、生成前公开反馈及工具文档；包括 33 natural、3 stop、4 finish、2 truncated。另核对 3 个队列外精确结构化完成正对照。
- 原始 generation 路径/SHA/行号/case/turn/raw/parse 与队列逐项匹配；trajectory SHA 与 actual_tool_history 匹配；每条公开 tool 消息与真实工具反馈相等。
- 018 行 12→13 的关键链可复核：重复 ask_user 被阻断（response=null、全 snapshot 不变）后，无 OK 的情况下明确声称成功。标记为 agent 发现的 unsupported success claim，而不是更新自动 unknown 或伪造人工标签。
- 017 orders-normal 的 latest 解释有真实任务歧义；正常 jobs 已有 OK 后未交付结果也有明确证据。二者不可合成一个笼统“模型拒绝成功”的机制。
- 所有 finish_task 与自然成功声明分开；失败说明包在 finish_task 中只证明结构化结果不合约，不自动证明语言虚报。

## 预运行设计门禁

| 检查项 | 结论 |
|---|---|
| 公开目标不包含答案/类别/修复步骤 | 通过文本审查；version `read_report_literal_target_v1`。 |
| 目标文字只改变 initial user，legacy 保留 | 已由协调者批准；尚需实现审计。 |
| 共同 parser/scorer 与未知语义不动 | 设计要求，不在本任务修改代码。 |
| 真实历史、同快照/预算/显式 seed | 后续 prefix 工程 gate；本文件不声称已验证。 |
| 已有 OK 不算新恢复，4恢复+2控制分开 | 已提醒协调者，需结果字段/分母落实。 |
| agent 初审与 human/training 标注隔离 | JSON 全部 `agent_review_pending_human`、human_reviewer=null、training_eligible=false。 |
| 旧记录保护 | 只新增授权的 4 个文件；旧 source 内容 SHA 逐项核对。 |

## 实际执行与未执行

执行了本地只读摘录/JSON解析/文件 SHA256 与逐条一致性断言，随后将逐条写定的 agent 理由写入新 JSON；这不是新模型实验或自动语义裁判。源数据断言覆盖 42/42；补充正对照 3 条。完整机器可核验归档由协调者另建 analysis run。

未执行模型推理、GPU、训练、联网、安装或代码测试；本任务没有代码改动，不能把内容审查称为测试套件通过。没有穷尽 134 轮所有中间语言，没有人工裁决、盲审或标注一致性统计。

详表：`data/processed/semantic_review_ITER004.json`。冻结文本及判断边界：`docs/research/ITER004_SEMANTIC_REVIEW.md`。
