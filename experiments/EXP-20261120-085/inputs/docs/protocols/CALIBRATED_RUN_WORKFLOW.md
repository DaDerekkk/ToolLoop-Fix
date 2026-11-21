# 校准后 B0/B1/B2 固定流程

适用 ITER-003 起的开发 E2E；用户明确暂不训练。旧 LOCAL_MODEL_RUN 是旧严格 XML pilot，复现旧结果继续用旧入口；新对照用本文件，不混用成绩。

## 1. 接手与预登记
1. 读 AGENTS、最新 PROGRESS、iteration、coordinator handoff 和 CALIBRATED_PROTOCOL。
2. 先 `python3 scripts/validate_records.py`，不得修改或补写已结束 run。
3. 在新 iteration 写假设、范围、停损、数据/种子/预算/判分版本；领取独立任务与唯一 run ID。
4. B0/B1/B2 配置除 comparison_arm/policy_name 外完全一致；模型 revision、device/dtype、采样、工具、案例与 per-case seeds 固定。B2 不含 B1 提示。
5. 冻结推理代码后先归档 tests，再通过必要的离线校准。新规则必须有负例，不能先看组间成绩再调参。

## 2. 真实推理（只使用现有隔离环境）
示例从项目根目录运行，替换唯一 ID 与假设/验收；不可复用已有目录：
```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
TOKENIZERS_PARALLELISM=false PYTORCH_ENABLE_MPS_FALLBACK=0 \
.venv/bin/python scripts/record_run.py \
  --id EXP-YYYYMMDD-NEW --kind model_inference \
  --config configs/calibrated_b0.json \
  --hypothesis '运行前写明' --acceptance '运行前写明' --owner YOUR_AGENT \
  -- '/Users/derek/Documents/ChatGPT/LLM 作业/.venv/bin/python' \
  -m toolloop_fix.calibrated_run --config '{config}' --output '{run_dir}'
```
- 三组顺序使用 GPU，不并发占用16GiB主机；MPS在沙箱不可见时使用正常工具审批，不静默切CPU。
- 不联网/不安装新包/不训练。若推理失败，保留失败run并解释原因；修正必须新ID，不能无日志重采样。
- `generations.jsonl`保存raw/prompt/tokenids/seed/parse/guard；`execution_events.jsonl`明确业务执行、内部终止、内部解析拒绝、guard阻断。
- B2阻断的response=null，executed=false；guard notice不是tool result。阻断仍消耗预算。
- 原始EOS不改，构造对话时仅移除将由模板添加的尾部EOS。
- loop_flags索引仅指trajectory.actual_tool_history，不是所有generation/event索引。

## 3. 运行后分析与审查
1. 生成新analysis配置，`run_dirs`为3个绝对路径及各自manifest SHA256，evidence_kind=analysis。
2. 通过record_run归档 `scripts/compare_calibrated_runs.py --config '{config}' --output '{run_dir}'`。
3. 另一agent通过record_run归档 `scripts/audit_calibrated_runs.py --config '{config}' --output '{run_dir}'`；此脚本只回放已有生成，不加载模型。另作源码/定性复核，不把重算同实现冒充独立语义裁判。
4. `python3 scripts/validate_records.py --write-index`；记录失败也必须通过完整性检查。
5. 原始索引写data/raw，待人工语义/检测器复核队列写data/processed；不是训练标签。
6. 追加iteration结果与限制，更新PROGRESS/taskboard/README/coordinator/NEXT_AGENT_PROMPT。覆盖“最新入口”前保存旧交接副本。

## 必须同时报告
- 目标证据取得/12，与精确有依据结构化完成/12；二者都不是已验证的自然语言任务成功率。
- 正常、必要重试子组；不可用后停止/原因码/语义unknown。
- 自然终止unknown、unsupported finish、预算耗尽。
- 重复提案、实际阻断、真实执行循环、所有轮次/token成本；不要把B2机制阻断叫模型学会恢复。
- 逐例胜平负、失败案例、未知语义、单种子/已知开发集限制。禁止改旧EXP-013评分当新基线。
