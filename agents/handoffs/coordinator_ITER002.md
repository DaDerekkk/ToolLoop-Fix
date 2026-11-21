# 协调者最新交接 — ITER-002 / 2026-11-16

## 授权与当前阶段
用户追加明确要求继续多 agent、安装、下载权重、开始运行。本地隔离安装、官方下载和真实推理已完成；不再等待旧的环境选择。无付费 API/云 GPU/上传发布/训练。旧 ITER-001 总交接保存在 coordinator_ITER001.md。

## 已落地
- 项目内 Python 3.12.13、torch 2.15.0、transformers 5.23.0；系统 Python 未改。
- 官方 Qwen/Qwen3.5-0.8B revision 2fc06364715b967f1860aea9cf38778875588b17，9文件1,769,916,415 bytes，哈希/大小验证通过；无量化，text-only原生CausalLM。
- M5/16 GiB主机MPS/BF16可实际运行；CPU/FP32也能运行但较慢。MPS fallback=0，没有脚本接管。
- schema3记录契约、实际模型provenance/raw generations/工具轨迹、94项联合测试通过。

## 权威实验
原9runs保留；新增：007=92测试；008=加载日志set序列化故障（无生成）；009=失败回归；010=94测试通过；011=CPU真实单例但终止identifier错误，任务失败；012=MPS单例成功；013=15开发pilot；REVIEW-003=独立全量核验。
当前共17runs，最终完整性检查0errors。每run有冻结输入/配置/日志/哈希；安装下载另见operations两条记录。

## Pilot事实与严格边界
EXP-013：15案例、103轮、111,370输入/5,801输出tokens，generate491.107秒。严格可完成任务2/12、正常1/3、必要重试0/3；原型规则循环4/15、parser错误29/103、预算耗尽5/15。
字段false_success4/15表示finish_task未满足目标，其中部分文本其实描述失败；正确终止0/3要求reason精确错误码，不能说3个都没有停止。CPU/MPS单次不同结果不是设备质量实验。
jobs-retry_required有较清楚的PRECONDITION后重复get_result，公开工具/反馈完整且先于后续格式错误。files-recoverable的重复ask_user也可复查，但必要性受模拟契约限制。

## 接手入口
- PROGRESS.md、iterations/ITER-002_local_model_start.md。
- docs/protocols/LOCAL_MODEL_RUN.md：离线真实运行命令、外部manifest、设备/采样/种子边界。
- agents/reviews/model_first_run_review.md：独立加载及失败归因审查。
- agents/reviews/model_pilot_acceptance.md及REVIEW-003：逐行raw→parse→event、公开上下文、状态/指标复核。
- data/raw/model_runs_20261116.json与data/processed/failure_review_queue_20261116.json：原始索引、13项待审失败，不是训练数据。

## 决策与下一任务
接受可审计本地运行/pilot交付；P1研究门禁未通过，保持hold training。先在新iteration做终止/格式语义和通用B1/B2对照，冻结jobs-retry失败前缀并验证公开信息下替代动作；保留当前pilot不重评分。改善正常能力后扩约200开发案例、多种子和人工检测审查，再考虑训练。当前真实模型runner仅E2E，固定前缀支持待实现。

## 安全与记录
不覆盖任何旧run；模型/代码/配置改动另建run。付费服务/新云资源仍需明确授权。权重放忽略的models/，安装放runtime/.venv；外部清单Hash不是签名或恶意进程沙箱。当前seed按case_index/turn派生，正式配对要固定显式case种子，不能随意重排。
