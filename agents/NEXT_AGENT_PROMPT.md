# 下一个 agent 接手提示 — ITER-004 后

先读AGENTS、README、PROGRESS、最新iteration、TASK_BOARD和coordinator handoff。最新用户明确：暂不训练，先语义和恢复诊断；未来M6/24G保留训练方案。目标机未连接，当前实测M5/16GiB。

本轮已逐项agent初审42旧输出+3正对照，但不是人工金标；旧分数不改。6真实prefix×2任务契约×B0/B1/B2=36续跑/62fresh生成；所有格新证据/精确结构化恢复均1/4，是相同订单TIMEOUT场景，0胜/4平/0负。explicit B2阻断2次但虚构结果/执行等语义问题仍存在。正常OK控制的继承证据不算恢复；所有自然文本自动unknown。

## 开始动作
1. `python3 scripts/validate_records.py`，查最新最终run数和错误，不把旧handoff快照当当前运行状态。
2. 读ITER-004、FIXED_PREFIX_RECOVERY、iteration4_results_review、REVIEW-005与最新比较结果；检查45旧review引用/新36待审队列的证据边界。
3. 领取独立任务/文件范围、新iteration/runID。原始所有run只读，021归档失败也保留。
4. 非训练下一步：人工语义/任务契约复核；独立模板、多seed开发诊断，分别验证TIMEOUT重试、PRECONDITION轮询、参数定位、正确停止与正常结果交付。不事后改自动评分或静默更换措辞。

## M6 后续
读M6_24G_TRAINING_HANDOFF.md、M6_PORTABILITY_ADDENDUM.md与disabled future_training_plan.json。当前路径/manifest/cwd含M5绝对路径，目标机先新增迁移映射、来源hash检查与派生配置，禁止改旧manifest冒充复现。目标硬件、环境、梯度、LoRA模块、训练内存、checkpoint恢复均待验证；不自动下载、安装或训练。

训练方案留有B3/B4/B5/P、LoRA优先探针、loss/reference/normal-retry保持、分组/holdout/多seed、checkpoint与回滚。只有新明确授权且G1/G2与训练记录契约通过后才能开始。当前所有reviewqueue training_eligible=false；agent自审不等于人工标签。无付费API/云GPU/上传发布授权。
