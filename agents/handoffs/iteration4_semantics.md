# ITER-004 semantic review handoff

Owner：protocol；2026-11-16；状态：**内容完成，可归档；无训练**。

## 新增文件与契约

仅新增：
1. `docs/research/ITER004_SEMANTIC_REVIEW.md`：结论、例证、冻结 objective、评分边界。
2. `data/processed/semantic_review_ITER004.json`：42 项 agent 初审 + 3 项队列外 exact controls。
3. `agents/reviews/iteration4_semantics.md`：有界 acceptance 与待实现 gate。
4. 本 handoff。

JSON 顶层 `items` 为42条；每条含协调者需要的 `source_run`、`source_generations_path`（绝对）、`source_generations_sha256`、`generation_line_1based`、`case_id`、`turn`、`agent_review_label/reason/evidence`、`review_status`、`training_eligible`。另外保存原文、trajectory source 行号/hash、公开工具/notice、歧义与flags。`supplementary_exact_structured_controls` 仅3条正对照，不混入42分母。

`review_version=public_evidence_agent_semantics_v1`；全部 `review_status=agent_review_pending_human`、human_reviewer=null、training_eligible=false。不是金标签，不把整对象喂给模型/guard。

## 归档前后动作

- 协调者计划将本次资料作为新 analysis run 归档；复制并 SHA-pin 新文档/JSON，核对每条 raw 引用，不改历史 run。
- 公开目标使用已批准精确短模板 `read_report_literal_target_v1`；模板文本 SHA256（不含末尾换行）为 `23387e549853199654f9d55e355ece50e7d811cf30027de74c9adb0cbeea8064`。全文在研究文档和 JSON `proposed_public_objective.text`。
- 原系统提示/parser/scorer不变，只替换初始user文本；legacy作为另一措辞水平。原前缀历史不可改写成符合新目标的历史。
- 本审核未执行模型/训练/工程测试；42项 source/raw/parse/history 一致性断言已实际执行。prefix运行仍须独立 source/replay/预算/种子/泄漏审计。

## 关键风险供后续 agent

1. 018 generations 行12阻断后行13无依据成功；公开返回无OK，不能把guard拦截视为业务成功。旧unknown不改。
2. 若 prefix已包含OK（正常完成控制），目标证据是继承的，不能作为续跑恢复率分子；最终交付和新证据分开。
3. 每wording×arm为4恢复+1正常+1不可用控制；不要统算6个恢复任务，也不把36续跑当36独立新模板。
4. `latest order`有任务范围歧义；新定义只能前瞻使用，不能倒推旧回答错误。
5. unsupported表示公开证据不蕴涵，不等于隐藏世界已证明为假。失败回显finish不等于谎报成功；自然提问不等于真实澄清调用。
6. 42项队列非随机，单agent非盲审，没有人工裁决；无总体语义率、训练资格或P1/P2通过声明。

完成后等待协调者指定独立 prefix source/tests gate；不要自行启动GPU或训练。
