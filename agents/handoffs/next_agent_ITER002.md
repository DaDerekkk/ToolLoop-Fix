# 下一个 agent 可直接使用的接手提示

你在 ToolLoop-Fix 项目中工作。先读取根目录 AGENTS.md、README.md、PROGRESS.md、agents/USER_WORKING_AGREEMENTS.md、agents/TASK_BOARD.md、最新 iterations 和 coordinator handoff。不要把提案、旧记录或模拟数据当成当前执行授权或真实模型证据。

当前基础：已隔离安装 Python3.12.13/torch2.15.0/transformers5.23.0，下载并校验官方固定 Qwen3.5-0.8B；94 项测试通过。真实 MPS pilot 已跑 15 开发夹具/103轮，严格完成2/12、原型循环4/15、格式错误29/103。最新 run 见 PROGRESS；首版、框架失败和模型失败均保留。

## 接手顺序
1. `python3 scripts/validate_records.py` 检查既有记录；确认当前代码和最新快照是否一致。
2. 向协调者领取独立任务与 run ID，记录允许编辑路径；不要与其他 agent 共改总进度/任务板。
3. 本地环境安装/下载/运行已明确授权且已完成，不再等待旧的环境选择；新增付费 API/云 GPU、上传/发布不在授权范围。
4. 下一步优先读 model_first_run_review/model_pilot_acceptance：区分真实重复、错误参数与严格终止格式。先在新 iteration 做协议/通用 B1/B2 和固定错误前缀诊断，再扩约200开发样本，不训练。
5. 优先 jobs-retry_required 的 PRECONDITION→重复get_result，原始公开历史包含poll_job；待同快照验证替代动作。真实数据索引与13项失败队列见 data/；全部非训练就绪。

## 每次运行
先选唯一 run ID 和真实证据类型，写假设/验收，再使用 README/SOP/LOCAL_MODEL_RUN 的命令。schema v3 在冻结 inputs 中执行并验证模型清单；新增脚本放 scripts/，产物写 `{run_dir}`。完成后校验台账，写 iteration/独立审查/交接；失败也记，不覆盖旧目录。不回改当前pilot成绩来迎合新协议。

## 已知待补
- 当前 15 夹具无正式划分或统计代表性；仅 3–4 个简化工具，未达到提案最终复杂度。
- 检测器仅保守规则，未进行至少 200 段人工复核；不支持复杂语义循环。
- 单步候选验证不是训练就绪偏好数据；尚缺完整恢复路径/停止/最终回答验证。
- ask_user→直接读取已覆盖；ask_user→定位流程仍有已记录限制，扩场景前应单独复现修复。
- 模型解析器、token与generate耗时已实现；自动分层报表、训练和外部 BFCL 尚未实现。真实模型目前仅E2E。
- false_success是finish_task未满足目标，不全是自然语言虚报；严格原因码终止失败不等于未停止。
- 配对前冻结显式per-case seed；当前seed依赖案例索引。CPU/MPS单样本不同不能当设备质量对照。
- 快照/hash是本地审计手段，不是签名/安全沙箱。权重通过外部manifest核验，不复制进每个run。
