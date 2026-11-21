# 协调者最新交接 — ITER-003 / 2026-11-16

## 用户范围与状态
最新用户要求：多agent、先校准工具调用/终止，再B1/B2；旧结果保留，暂不训练。本轮已完成，**没有训练**。旧总交接保存在coordinator_ITER002.md（更早ITER001也保留）。

## 先读与核验
1. AGENTS → PROGRESS → iterations/ITER-003_protocol_calibration_controls.md。
2. docs/protocols/CALIBRATED_PROTOCOL.md及CALIBRATED_RUN_WORKFLOW.md。
3. agents/reviews/iteration3_design_review.md、iteration3_results_review.md；实验REVIEW-004/calibrated_audit.json。
4. `python3 scripts/validate_records.py`；当前25run、0errors。不要给已结束目录补文件。

## 当前证据
- 014=149测试；015=103旧生成语法诊断及官方tokenizer渲染；不反事实重评分。
- 016/017/018=新B0/B1/B2，各15已知dev、同显式seedmap(0,16,…224)、同官方权重/采样/预算、MPSBF16；总134轮真实生成。
- 019=172联合测试；020=实际溯源/数据/代码配对比较；REVIEW-004=全部45案例/134轮CPU数据回放一致，无新生成。
- 三arm目标证据均3/12，精确有依据结构化完成均1/12；两指标逐例0胜/12平/0负。必要重试目标证据均0/3。
- B0/B1/B2分别41/47/46轮，26/31/27真实调用，重复提案均2次；B2阻断2次，实际原型循环0/15（B0/B1各1/15）。不能称模型学会恢复或提高成功率。
- 自然终止unknown11/10/12例。B1两次输出token截断；B2有无依据成功措辞重点案例，自动unknown未改。

## 代码/接口关键点
- 旧harness/model_protocol/model_run不改。新calibrated_protocol、baselines、calibrated_run独立入口。
- B1只追加通用指导；B2系统prompt与B0完全相同，不含B1。
- 自然文本为内部生命周期stop，raw保留；不伪称模型调用stop_task，不计实际工具执行。严格XML截断/多调用仍拒绝。
- guard只读真实工具公开历史；不看内部FORMAT_ERROR/lifecycle作为新进展，不看hidden。动作+响应定义新观察，合法retry及成功信息保守放行。
- blocked response=null/executed=false、全snapshot不变；原始提案保留，显式user runtime notice，不伪造tool result。阻断/invalid均消耗预算。
- 原始EOS保留；仅构造对话时去掉尾EOS让tokenizer补一次。loop_flags索引是actual_tool_history，不是全部事件。
- score目标证据不是任务完成率；精确结构化完成不评自然回答真伪。source_audit已发现拿到OK后仍声称不能完成的实例。

## 分类材料
- data/raw/calibrated_model_runs_ITER003.json：3个真实run原始文件/hash索引。
- data/processed/calibrated_review_queue_ITER003.json：42待审项（33自然终止），不是42个失败或训练标签；含评测标签，禁止整对象喂模型/guard。
- 高优先级：018/generations.jsonl第13行，orders-unrecoverable第4轮。第12行重复ask_user被guard阻断后，模型声称成功但没有OK证据；只作为agent定性发现，待人工复核。
- 实验/迭代/agent交接均保留。开发时的前置失败诊断见baselines handoff；正式验收以014/019冻结测试为准。

## 后续建议与禁止事项
- 不训练、不生成声称验证完毕的偏好数据。P1/P2未过。
- 先任务措辞/自然终止语义复核；明确读取任务目标，再新版本控制实验，不回改本轮。
- 冻结TIMEOUT、jobs PRECONDITION的真实错误前缀做同快照恢复对照，当前尚未实现真实模型固定前缀模式。
- 然后扩dev模板、多种子、人工检测器复核和分组/封闭划分。
- 不静默换后端/改种子/改prompt后继续同run，不把本轮耗时当设备或效率基准。
- 本地环境已安装，可离线复用；新云资源/付费API/上传发布仍不在授权内。
