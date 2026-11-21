# ITER-007 交接：180 例分组开发套件与无泄漏划分清单（T007a / T008a）

- **ID / Owner**：`T007a/T008a` / `dataset7`
- **输入证据**：`toolloop_fix/harness.py`、`toolloop_fix/run.py`、`data/dev/pilot_cases.jsonl`、`data/dev/timeout_crossed_iter6.jsonl`、`scripts/audit_calibrated_runs.py`、`docs/protocols/EVALUATION_SPEC.md`、`docs/protocols/EXPERIMENT_WORKFLOW.md`（均只读）
- **本任务新增文件（严格限于以下 5 个文件，未改动任何既有文件）**：
  1. `scripts/build_grouped_dev_suite.py`
  2. `data/dev/grouped_dev_suite_iter7.jsonl`（SHA256: `d34b717b6872d9e5645a1bc65fc749215b3c61878083209f1db63eb0ef7f2c58`）
  3. `data/processed/grouped_split_manifest_ITER007.json`（SHA256: `d5eae1a8cbdf1d15941e9bb83412f5d49bfd9971cf546180701c95e593102648`）
  4. `tests/test_grouped_suite_iter7.py`
  5. `agents/handoffs/iter7_grouped_suite.md`

---

## 1. 数据集结构与兼容契约（`synthetic_harness`）

- **规模与平衡**：确定性生成 **180 例** 开发夹具（`schema_version: 1`、`evidence_class: "synthetic_harness"`、`split: "dev"`），与 `pilot_cases.jsonl`（15 例）合计 **195 例** G1 开发夹具（对齐 `EXPERIMENT_WORKFLOW.md` G1 约 200 例开发规模）。
- **三维平衡网格**：`3` 业务域（`orders`、`files`、`jobs`）× `5` 任务类别（`normal`、`recoverable`、`retry_required`、`clarification`、`unrecoverable`）× `3` 模板家族（`f1`、`f2`、`f3`）= **45 个独立 `template_group`**；每组 `4` 个实体/取值变体（`01..04`）= 每 `(domain, category)` 单元 **12 例**、总计 **180 例**。
- **字面目标与路径规范化**：
  - 所有 `request["target"]` 均采用无歧义字面查找键（如 `"lookup_key:ord_ledger_ca1_v1"`），不含诸如 `"latest order"`、`"daily report"` 的自然语言指令歧义。
  - `files` 域全部 `valid_identifier` 与请求 `identifier` 均为已规范化 POSIX 绝对路径（如 `"/vault_ca1/rec_4311.txt"`，`posixpath.normpath(p) == p`，无首尾空格、末尾斜杠或 `..`）。
  - 请求标识符与目录采用中性业务编码（`ca..ce` 批次号），不在公开 `request` 中暴露 `normal`/`recoverable`/`unrecoverable`/`stale` 等先验语义提示。
- **故障子类型（`fault_subtype` 顶层元数据，不进入 `Environment.public_view()`）**：
  - `normal`（无 `fixed_prefix`）：`direct_ok_f1`、`direct_ok_f2`、`direct_ok_f3`。
  - `recoverable`（均带单步 `fixed_prefix`）：
    - `f1`：`stale_id_locate`（`INVALID_IDENTIFIER -> locate -> OK`）
    - `f2`：`stale_id_locate_plus_1timeout`（`INVALID_IDENTIFIER -> locate -> TIMEOUT -> retry -> OK`）
    - `f3`：`orders`/`files` 为 `stale_id_locate_plus_2timeout`（`transient_remaining=2` 复合恢复边界）；`jobs` 为 `stale_id_locate_plus_1poll`（`INVALID_IDENTIFIER -> find_job -> PRECONDITION -> poll_job(READY) -> get_result(OK)`）。
  - `retry_required`（均带单步 `fixed_prefix`）：
    - `orders`/`files`：`f1` `transient_1_retry`（1 次超时）、`f2` `transient_2_retries_boundary`（2 次超时契约上限边界）、`f3` `transient_1_retry_alt_schema`（独立命名空间）。
    - `jobs`：`f1` `job_poll_1_step`（1 次轮询）、`f2` `job_poll_2_steps`（2 次轮询）、`f3` `job_poll_3_steps_boundary`（3 次轮询契约上限边界）。
  - `clarification`（`request` 省略 `identifier`，无 `fixed_prefix`）：
    - `f1` `missing_id_direct`、`f2` `missing_id_plus_1timeout`、`f3` `missing_id_plus_2timeout`（`orders`/`files`）或 `missing_id_plus_1poll`（`jobs`）。
  - `unrecoverable`（`unavailable=True`，无 `fixed_prefix`）：
    - `f1` `unavailable_valid_id_f1`（`request.identifier == valid_identifier`）
    - `f2` `unavailable_alt_id_f2`（`request.identifier != valid_identifier`；依照 `harness.py` 第 135–138 行精确行为，主工具先检查 `state["unavailable"]` 并直接返回 `RESOURCE_UNAVAILABLE`，不经过 `INVALID_IDENTIFIER`，且不向公开请求泄漏 `valid_identifier`）
    - `f3` `unavailable_archive_id_f3`（归档命名空间，`request.identifier == valid_identifier`）。
- **无泄漏与历史样本 100% 隔离**：
  - 与 `pilot_cases.jsonl` 及 `timeout_crossed_iter6.jsonl` 的全部 `case_id`、`template_group`、`identifier`、`target`、`value` 做到集合不相交且无子串包含。
  - 全量 180 例通过 `audit_calibrated_runs.py` 同口径提示词非泄漏校验：`case_id`、`hidden.value`、未揭露的 `hidden.valid_identifier`（共 84 例：36 `recoverable` + 36 `clarification` + 12 `unavailable_alt_id_f2`）及评测标签键均不出现在初始 `public_view()` 或 `B0`/`B1`/`explicit` 渲染消息中。

---

## 2. 分组划分清单与 `mps_unseen_eval24` 切片（经协调者确认采用 Option A）

为满足 `EVALUATION_SPEC.md` §5 模板聚类配对原则，使 `mps_unseen_eval24_case_ids` 的 24 例严格来自 **24 个互不相同的独立 `template_group`** 且与候选池/保留池 **0 重叠**：

| 分区名 | 业务域 | 模板组数 | 案例数 | 用途与隔离边界 |
|---|---|---:|---:|---|
| `dev_InDomain_candidate_pool` | `orders`, `files` | 14 | 56 | 域内未来候选池（当前 `training_eligible: false`） |
| `dev_InDomain_selection` | `orders`, `files` | 16 | 64 | 域内独立未见模板选择/开发诊断池（含 `eval24` 域内 16 组） |
| `dev_CrossDomain_diagnostic` | `jobs` | 8 | 32 | 跨域留出开发诊断池（含 `eval24` 跨域 8 组，禁止进域内训练池） |
| `dev_CrossDomain_reserved` | `jobs` | 7 | 28 | 跨域留出保留开发池（禁止进域内训练池） |

- **零交叉核验**：四个分区两两之间在 `template_group`、`case_id`、`identifier`（含 `request.identifier` 与 `hidden.valid_identifier`）、`target`、`value` 上交集全部为空集。
- **`mps_unseen_eval24_case_ids` 与 `mps_unseen_eval24_case_seeds`**：
  - 严格取自 `dev_InDomain_selection`（`orders` 8 例 + `files` 8 例）与 `dev_CrossDomain_diagnostic`（`jobs` 8 例），共 **24 例 / 24 个独立 `template_group`**。
  - 每域覆盖 8 个故障槽位：`normal`、`recoverable_stale_id_locate`、`recoverable_compound`、`retry_required_1_step`、`retry_required_boundary`、`clarification_direct`、`clarification_compound`、`unrecoverable`（`8 × 3 = 24`）。
  - 确定性非重叠种子：`6000, 6032, ..., 6736`（步长 `32`），在 `turn_budget=16` 下各案例回合种子窗口互不重叠，且与历史 `0..5999` 种子区间零重叠，直接通过 `toolloop_fix.calibrated_run.validate_config` 校验。
- **阶段门禁显式锁定**：`training_eligible: false`、`formal_closed_holdout_unsealed: false`。

---

## 3. 实际执行的命令与验证结果

1. 生成与确定性字节一致性自检：
   ```bash
   python3 scripts/build_grouped_dev_suite.py && python3 scripts/build_grouped_dev_suite.py --check
   ```
   - 退出码：`0`；二次 `--check` 逐字节比对 `grouped_dev_suite_iter7.jsonl` 与 `grouped_split_manifest_ITER007.json` 完全一致。

2. 单元测试套件（10 项测试）：
   ```bash
   python3 -m unittest tests/test_grouped_suite_iter7.py -v
   ```
   - 退出码：`0`（`Ran 10 tests in 1.726s — OK`）。
   - `toolloop_fix.harness.summarize` 实测结果：
     - `scripted_reference`（`end_to_end` 180 例 / `fixed_failure_prefix` 72 例）：`task_completion = 144/144 (100%)`、`normal_completion = 36/36 (100%)`、`error_recovery = 36/36 (100%)`、`necessary_retry_success = 36/36 (100%)`、`correct_termination = 36/36 (100%)`、`invalid_loop_rate = 0/180 (0%)` & `0/72 (0%)`；且全轨迹无任何一步触发 B2 `guard_action` 误拦截。
     - `scripted_naive`：`end_to_end` 完成 `60/144`（36 `normal` + 24 `orders`/`files` 瞬态超时重试），其余 120 例非可重试故障全部触发预期循环并耗尽预算（`invalid_loop_rate = 120/180`）；`fixed_failure_prefix` 完成 `24/72`（`error_recovery = 0/36`、`necessary_retry_success = 24/36`、`invalid_loop_rate = 48/72`）。
     - `python3 -m toolloop_fix.run` 临时目录完整运行产出 `504` 行评测结果与轨迹，退出码 `0`。

---

## 4. 边界与局限说明

- 本套件及清单均为开发期产物（`synthetic_harness` / `analysis`），**不是正式封闭测试集（G4 未创建/未解封），亦不代表 Qwen3.5-0.8B 在这些独立模板上的真实表现或任何训练有效性**。
- 本任务未调用 `scripts/record_run.py`、未加载模型权重、未联网或接触云实例。后续 Phase B（`T015b`）可直接从 `grouped_split_manifest_ITER007.json` 读取 `mps_unseen_eval24_case_ids` 与 `mps_unseen_eval24_case_seeds` 构建冻结运行配置并通过 `record_run.py` 归档。
