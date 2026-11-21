# T015a 交接 — ITER-007 运行器、配对比较与独立 CPU 回放审计（`runner7`）

日期：2026-11-19（Asia/Shanghai）  
Owner：`runner7`

## 1. 本次新建文件（严格在授权 6 文件范围内）
1. `toolloop_fix/iter7_runners.py`：提供 `prefix-decoding`、`prefix-multiseed`、`explicit-e2e` 三个子命令，不加载 `TransformersBackend` 时纯标准库可导入，兼容 `scripts/validate_records.py` 全部 schema v3 模型契约。
2. `scripts/compare_iter7_runs.py`：只读离线比较脚本（零模型加载），校验全部源 run `manifest.json` 与 `inputs`/`outputs` SHA256，生成 `iter7_comparison.json` 与 `iter7_comparison.md`。
3. `scripts/audit_iter7_runs.py`：独立纯 CPU 已记录生成回放审计器（`Iter7RecordedBackend`，零 PyTorch/Transformers 导入），生成 `iter7_audit.json`。
4. `tests/test_iter7_runners.py`：16 项标准库单元与集成测试（含篡改/泄漏拒绝测试、真实历史 `EXP-028..030` 回放兼容、真实 6 前缀贪心 `prepare` 校验及 `grouped_dev_suite_iter7.jsonl` 兼容测试）。
5. `docs/protocols/ITER007_MULTISEED_AND_TEMPLATES.md`：ITER-007 多 seed、解码拆解与未见模板 E2E 协议文档。
6. `agents/handoffs/iter7_runners.md`：本交接文件。

**边界确认**：未修改 `toolloop_fix/harness.py`、`model_run.py`、`calibrated_run.py`、`prefix_run.py`、`baselines.py`、`calibrated_protocol.py`、`model_backend.py`、`model_protocol.py`、`PROGRESS.md`、`TASK_BOARD.md` 或任何既有文件；未调用 `scripts/record_run.py`；未加载 1.7GB 模型权重；未触碰网络/Colab 或安装依赖。

## 2. 供 Phase B（`root` + `auditor7`）直接使用的命令与配置要点
- **解码消融（`greedy_pp0` / `greedy_pp2`）**：
  ```bash
  .venv/bin/python -m toolloop_fix.iter7_runners prefix-decoding --config '{config}' --output '{run_dir}'
  ```
  在 `configs/prefix_explicit_b0.json` 基础上设 `"sampling": {..., "do_sample": false, "top_k": 0, "presence_penalty": 0.0}`（或 `2.0`）及可选 `"decoding_regime": "greedy_pp0"`（或 `"greedy_pp2"`）。
- **多 Seed 固定前缀（`s1..s4` 单次模型加载）**：
  ```bash
  .venv/bin/python -m toolloop_fix.iter7_runners prefix-multiseed --config '{config}' --output '{run_dir}'
  ```
  将单表 `"case_seeds"` 替换为 `"seed_schedules": [{"schedule_id": "s1", "case_seeds": {...}}, ...]`，注意 `s1..s4` 种子区间不仅彼此不相交，还需与 `EXP-028..030`（`s0`：`1000..1176`）不相交（例如从 `2000` 起步、步长 `>=16`）。
- **独立未见模板 E2E（`B0` / `B1` / `B2`）**：
  ```bash
  .venv/bin/python -m toolloop_fix.iter7_runners explicit-e2e --config '{config}' --output '{run_dir}'
  ```
  设 `"mode": "end_to_end"`、`"wording_profile": "explicit"`、`"task_wording_version": "read_report_literal_target_v1"`、`"cases_path": "data/dev/grouped_dev_suite_iter7.jsonl"`。
- **离线比较与独立回放审计**：
  ```bash
  python3 scripts/compare_iter7_runs.py --config '{config}' --output '{run_dir}'
  python3 scripts/audit_iter7_runs.py --config '{config}' --output '{run_dir}'
  ```
  两者均可接收包含 `prefix_s0_runs`、`prefix_multiseed_runs`、`prefix_decoding_runs`、`e2e_runs`（`audit_iter7_runs.py` 亦支持扁平 `run_dirs`）的配置 JSON。

## 3. 实际执行的验证命令与结果
1. `python3 -m unittest tests/test_iter7_runners.py -v`：**16 项测试全部通过（OK）**。
   - 覆盖三个子命令运行与 `validate_records`（`config_errors` + `model_artifact_errors`）零错误校验；
   - 覆盖真实 `data/dev/fixed_prefix_cases_iter4.jsonl` 在 `do_sample=False` 下的 12 次只读 `prepare_iter7_prefix` 源校验（0 模型调用）；
   - 覆盖真实 `data/dev/grouped_dev_suite_iter7.jsonl` 接入与回放校验；
   - 覆盖真实已归档 `EXP-20261116-028/029/030` 的纯 CPU 回放审计；
   - 覆盖 11-run 完整套件比较（解码拆解、90 episode 5-seed 前缀聚类/配对统计、未见模板分层、多阶段复合故障 `validate_alternative` 无泄漏检验、B0 vs B2 干预前精确一致性）以及标签泄漏/隐藏值泄漏/哈希重封篡改拒绝测试。

## 4. 已知限制与后续注意
- `n=4` 个恢复前缀跨 5 个 seed schedule（每臂 20 次恢复试验）用于分离解码与采样方差，不构成封闭测试集的确认性证明。
- 自然语言终止回答的语义真值在自动评分中仍保持 `unknown`，须结合 `T013b/T007b` 盲审包由人工完成 G2 复核。
- 所有 `validate_alternative` 诊断结果均锁定 `training_eligible: false`，在 G2 门禁与明确训练授权前不得用于模型训练。
