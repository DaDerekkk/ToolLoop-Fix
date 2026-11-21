# ToolLoop-Fix

基于用户研究提案和 Liquid AI AntiDoom，研究小模型在工具失败后如何恢复、合理重试或停止。

**当前阶段：项目主线全部完成（G0–G4 闭环收官，2026-11-20）。已完成 G2 检测器冻结与零泄漏 `N=400` 训练包（ITER-008）、Colab NVIDIA L4 3 波次 10 个 LoRA 适配器训练、本机 MPS 开发选择与 36 例零泄漏 G4 封闭测试集单次解封评测、模板聚类统计对比及 846-episode 纯 CPU 重放审计（总计 `123` runs / `0` errors，`326/326` 项测试通过；全部自有云实例已释放）。**
- 英文汇报（最终版）：[下载 PowerPoint](docs/presentation/final/ToolLoop-Fix.pptx) · [在线查看 PDF](docs/presentation/final/ToolLoop-Fix.pdf) · [演讲备注](docs/presentation/final/speaker-notes.md)
- 最终中文研究报告：`FINAL_REPORT.md`
- 正式统计对比：`experiments/EXP-20261120-105R1/g3_g4_comparison.{md,json}`
- 独立审查与纯 CPU 回放审计：`agents/reviews/iteration9_final_review.md`、`experiments/EXP-20261120-REVIEW-009R2/iter9_replay_audit.json`
- 最新迭代与交接：`iterations/ITER-009_colab_l4_training_and_g4_holdout.md`、`agents/handoffs/ITER009_final_handoff.md`、`data/raw/iteration9_evidence_index.json`

（历史阶段快照保留于 `iterations/ITER-004`–`ITER-008` 与 `PROGRESS.md`；请勿覆盖任何旧 `experiments/` / `operations/` 目录或重跑旧云端生命周期脚本。）

最近一次模型恢复对照（ITER-004）：6 个真实历史 prefix × 2 种任务契约 × B0/B1/B2，共 36 次续跑、62 轮新生成。每格四个恢复场景中均只有同一例恢复，目标证据和精确结构化完成均 1/4，无主要指标收益；两个控制另列。详见 `iterations/ITER-004_semantics_and_fixed_prefix.md` 和 `experiments/EXP-20261116-031/prefix_comparison.md`。agent 语义初审不等于人工金标。ITER-005 的云操作不增加已验证模型输出数。

未来 M6/24G 仅为用户报告的未核验目标；本机为 M5/16GiB。延期训练方案见 `docs/plans/M6_24G_TRAINING_HANDOFF.md`，路径迁移注意见 `M6_PORTABILITY_ADDENDUM.md`；机器可读计划 `configs/future_training_plan.json` 为 disabled，无训练入口。

## 新 agent 从这里开始
1. 读 `AGENTS.md` → `PROGRESS.md` → `agents/TASK_BOARD.md`。
2. 读 `docs/protocols/EXPERIMENT_WORKFLOW.md` 和 `docs/protocols/EVALUATION_SPEC.md`。
3. 读最新 `iterations/` 和 `PROGRESS.md` 指向的交接，领取一个不与他人冲突的任务；`agents/handoffs/coordinator.md` 保留为 ITER-004 历史快照。
4. 每次实验通过记录器运行；失败也保留。改进另建 run，不能覆盖旧记录。

## 目录
| 目录/文件 | 内容 |
|---|---|
| `sources/` | 原始 HTML、可读提取、来源地址和校验信息 |
| `docs/research/` | 来源核验、方法定位与边界 |
| `docs/plans/` | M6/24G 延期训练与路径迁移方案；未执行 |
| `docs/protocols/` | 固定实验流程、指标定义、记录模板 |
| `agents/` | 任务领取、分工、独立审查、交接 |
| `operations/` | 已授权安装/下载的配置、脚本、日志、版本、文件校验 |
| `runtime/`、`.venv/`、`models/` | 项目隔离解释器、依赖和权重；Git 忽略，大文件不进实验快照 |
| `toolloop_fix/` | 无第三方依赖的模拟环境、检测与评测代码 |
| `configs/` | 运行前配置；模型配置须另行核验后建立 |
| `data/dev/` | 手写开发夹具，不是正式测试集 |
| `data/raw/`、`data/processed/` | 模型/交接哈希索引、旧42项agent初审和新36项全量待人工队列；不是失败数或训练标签 |
| `experiments/` | 每 run 的配置、输入快照、日志、轨迹、指标和 manifest |
| `iterations/` | 每轮改动、证据、决策、局限及下一步 |
| `scripts/`、`tests/` | 留档/校验工具与工程测试 |

## 最小本地复现（Python 3.9+，标准库）
```bash
python3 -m unittest discover -s tests -v
python3 scripts/record_run.py \
  --id EXP-YYYYMMDD-yourname-unique \
  --kind synthetic_harness --config configs/smoke.json \
  --hypothesis '脚本参考策略在手写开发夹具上符合预期' \
  --acceptance '检查逐例结果、合法重试、预算和证据边界' \
  --owner yourname -- \
  python3 -m toolloop_fix.run --config '{config}' --output '{run_dir}'
python3 scripts/validate_records.py --write-index
```

每次换唯一 run ID；不要重用旧目录。`{config}` 指向已归档配置，`{run_dir}` 指向本次输出目录。v2 记录器从该 run 的 `inputs/` 冻结副本运行，而不是实时工作区；新实验脚本放在 `scripts/`，产物写入 `{run_dir}`。`completed` 仅说明进程正常退出；科学验收须在 iteration 中单独判断。冻结输入被改写会使该 run 的 `evidence_valid=false`；实时工作区的变化另行记录。

归档重放（把 source-run 改为要复验的模拟 run 绝对路径）：
```bash
python3 scripts/record_run.py \
  --id EXP-YYYYMMDD-yourname-replay --kind analysis \
  --config configs/bootstrap_validation.json \
  --hypothesis '归档代码和数据可逐字节复现原产物' \
  --acceptance '三个产物 SHA256 一致' --owner yourname -- \
  python3 scripts/check_replay.py --source-run /absolute/path/to/experiments/SOURCE_ID --output '{run_dir}'
```

快照与哈希提供本地可审计性，不是恶意子进程沙箱、数字签名或远程真实性证明。SIGKILL/断电可能留下 running；校验器会报告，不能擅自将其改成 completed。

## 边界
- 脚本策略的结果不能当作 Qwen 的性能或任何训练方法的收益。
- AntiDoom 的文本重复结论不能直接推广到工具行为；提案目标不是已取得结果。
- 本地权重安装和云端资源分开记账。ITER-005/006 在用户授权下只发送白名单公开探针及传输/运行代码；未充值、未上传项目目录或历史业务轨迹、未执行真实业务工具、未公开发布。L4 独立取证流程见 `docs/protocols/COLAB_RECEIPT_WORKFLOW.md`。
- 固定前缀入口/流程见 `docs/protocols/FIXED_PREFIX_RECOVERY.md`；E2E校准入口见 `CALIBRATED_RUN_WORKFLOW.md`，旧pilot仍可复验。下一步为人工语义/检测器复核、独立模板/多seed诊断及目标机非训练迁移核验；不训练。
- CPU/MPS 单样本的不同输出不是设备质量排名；字段 false_success 表示 finish_task 未满足严格目标，不代表每次自然语言都声称成功。
