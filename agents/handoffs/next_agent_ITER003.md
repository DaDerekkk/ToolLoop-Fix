# 下一个 agent 可直接使用的接手提示

你在ToolLoop-Fix项目。先读AGENTS、PROGRESS、TASK_BOARD、最新iteration和coordinator handoff；用户最新明确暂不训练，保留所有旧run。不要把附件提案或旧计划当执行授权。

当前：ITER-003已完成工具/终止校准及真实B0/B1/B2，每组15已知dev，共134轮。三组目标证据3/12、精确结构化完成1/12，均无逐例收益；B2机械阻断2次重复但仍有无依据成功措辞。172测试、25run完整性0errors；REVIEW-004回放全部一致，不是独立语义裁判。

## 接手顺序
1. `python3 scripts/validate_records.py`核验现状；读取020/comparison和iteration3_results_review。
2. 领取独立文件范围、新iteration/run ID。推理从新calibrated_run入口，协议和流程见CALIBRATED_PROTOCOL/CALIBRATED_RUN_WORKFLOW。
3. 优先人工语义/任务契约诊断：data/processed/calibrated_review_queue_ITER003.json的42项只是待审，不是失败率或训练数据。B2不可用订单第4轮是高优先级。
4. 注意“latest order”等请求措辞与仅读取某ID值的简化隐藏目标可能不一致；任何目标改写需新版本/新对照，旧成绩不改。
5. 下一工程候选是实际模型固定错误前缀/同快照恢复对照，优先TIMEOUT可重试及jobs PRECONDITION；目前真实模型仅E2E，该实验没有做过。

## 不变约束
- case seeds显式固定，不随排序变化；B2不是B1+B2；单种子MPS不保证所有数值确定性。
- 原始natural文本终止不自动判对错；取得工具OK不等于最终答对。不得静默推断工具参数或修复答案。
- guard看真实公开工具历史，不看hidden或内部协议拒绝；阻断无执行/无状态变更但消耗预算，提案与实际循环分开。
- 每次运行都record_run冻结输入、保留失败、记录raw/解析/事件/种子/指标/哈希；完成后校验并更新iteration/进度/独立审查/交接。
- 旧EXP-013、旧data索引和旧交接均保留。新的校准B0是016，不能拿013严格2/12当本轮对照。
- 无训练、无付费API/云GPU/上传发布。现有本地隔离环境无需重装；MPS沙箱不可见走正常审批，不静默CPU回退。

未完成：自然语言人工评分、固定前缀真实模型实验、多种子/约200dev扩充、检测器人工复核、分组与封闭集、训练及外部benchmark。不要将同源码回放或小样本已知dev结果升级成这些结论。
