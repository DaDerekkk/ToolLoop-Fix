# ITER-009 交接：36 例 G4 正式封闭测试集、零泄漏清单与 G3/G4 统计比较器（`T021`）

- **ID / Owner**：`T021` / `g4_eval_suite9`
- **输入证据（均只读，零修改）**：
  - `AGENTS.md`、`docs/protocols/EVALUATION_SPEC.md`、`sources/proposal_extracted.txt`（§06–§08）
  - `toolloop_fix/harness.py`、`toolloop_fix/loop_detector_v2.py`
  - `data/processed/grouped_split_manifest_ITER007.json`、`scripts/build_g3_preference_dataset.py`
  - `data/dev/{pilot_cases,fixed_prefix_cases_iter4,timeout_crossed_iter6,grouped_dev_suite_iter7}.jsonl`
  - `experiments/EXP-20261120-062/{p_toolloop_fix_dpo,b5_general_expert_dpo}.jsonl` 与 `experiments/EXP-20261119-054..056`
- **本任务新增文件（严格限于以下 6 个新文件，未修改任何既有文件，未调用 `scripts/record_run.py`）**：
  1. `scripts/build_g4_closed_holdout.py`（SHA256: `997865a81ac35f55638da75a44fc313a56e98b009e6724357aac0b09f761d5ca`）
  2. `data/dev/g4_closed_holdout_iter9.jsonl`（SHA256: `b968e360999a6836e68013ffaffcb6cc889431f44f766cab6bdd175bfd878460`）
  3. `data/processed/g4_holdout_manifest_ITER009.json`（SHA256: `1b11e4f5f7a408cc8306c0cc427dfc23cafdab7e780b3e9f49e5a2359c3c4e68`）
  4. `scripts/compare_g3_g4_results.py`（SHA256: `30ee03e7b2b5380fef06e94acda722864b74412eb06f49af1dfaeed0b07234fe`）
  5. `tests/test_g4_holdout_and_comparator.py`（SHA256: `7a8c3a9093da83aeeabfd3cd381e95f6f7ee5e1784111c0bf07b06aef0968dc2`）
  6. `agents/handoffs/iter9_g4_suite.md`

---

## 1. G4 正式封闭测试集与确定性种子计划（`synthetic_harness`）

- **结构与分层（36 例 / 36 个全新两两不交 `template_group`: `g4h9-...`，`split: "closed_holdout_g4"`）**：
  - **域内未见模板（`orders` + `files`：24 例 / 24 个独立 `template_group`）**：每域 12 例：
    - `normal`（2）：`direct_ok_h1`、`direct_ok_h2`
    - `recoverable`（4，均含单步 `fixed_prefix` `[tool_call(primary, identifier=stale_id)]`）：`stale_id_locate` × 2、`stale_id_locate_plus_1timeout` × 1、`stale_id_locate_plus_2timeout` × 1
    - `retry_required`（2，均含单步 `fixed_prefix` `[tool_call(primary, identifier=valid_id)]`）：`transient_1_retry` × 1、`transient_2_retries_boundary` × 1
    - `clarification`（2，`request` 省略 `identifier`）：`missing_id_direct` × 1、`missing_id_plus_1timeout` × 1
    - `unrecoverable`（2，`unavailable=True`）：`unavailable_valid_id_h1`、`unavailable_alt_id_h2`
  - **跨域零样本留出域（`jobs`：12 例 / 12 个独立 `template_group`，100% 排除在 G3 训练之外）**：
    - `normal`（2）：`direct_ok_h1`、`direct_ok_h2`
    - `recoverable`（4，均含单步 `fixed_prefix`）：`stale_id_locate` × 2、`stale_id_locate_plus_1poll` × 2
    - `retry_required`（2，均含单步 `fixed_prefix`）：`job_poll_1_step` × 1、`job_poll_2_steps` × 1
    - `clarification`（2）：`missing_id_direct` × 1、`missing_id_plus_1poll` × 1
    - `unrecoverable`（2）：`unavailable_valid_id_h1`、`unavailable_alt_id_h2`
  - **合计**：`36` 例端到端（`30` 可完成 + `6` 不可恢复）与 `18` 例 `fixed_failure_prefix`（`12` `recoverable` + `6` `retry_required`）。
- **硬零泄漏与兼容性核验**：
  - 全部实体编号严格位于 `75101..77312`（`>=75000`），批次代号采用 `GH1..GH9`，规避 `B0..B5`、`CB1/CB2`、`EXP-` 等任何大小写敏感/不敏感禁用子串；`files` 路径全部为规范化 POSIX 绝对路径。
  - 与 `pilot_cases.jsonl`、`fixed_prefix_cases_iter4.jsonl`、`timeout_crossed_iter6.jsonl`、`grouped_dev_suite_iter7.jsonl`（含候选池扩展变体 `v5..v20`）以及 `EXP-20261120-062` 中全部 `400 + 400` 条 `p_toolloop_fix_dpo.jsonl` / `b5_general_expert_dpo.jsonl` 训练样本实现 **0% 集合重叠与 0% 原文子串泄漏**。
  - 初始 `Environment(case).public_view()` 100% 不泄漏 `case_id`、`template_group`、`fault_subtype`、`hidden.value` 及未揭露的 `valid_identifier`。
  - `scripted_reference` 在 `end_to_end` 与 `fixed_failure_prefix` 两层达成 `task_completion = 30/30 (100%)`、`error_recovery = 12/12 (100%)`、`necessary_retry_success = 6/6 (100%)`、`correct_termination = 6/6 (100%)`，且 `detect_loops`（v1）与冻结 `detect_loops_v2` 均为 `0%` 误报循环。
- **确定性非重叠多采样种子表（`g4_holdout_manifest_ITER009.json`）**：
  - E2E 三种子表：`g4_e2e_s1`（`80000` 起，步长 `32`，36 例）、`g4_e2e_s2`（`82000` 起）、`g4_e2e_s3`（`84000` 起）
  - Prefix 三种子表：`g4_pfx_s1`（`90000` 起，步长 `32`，18 例）、`g4_pfx_s2`（`92000` 起）、`g4_pfx_s3`（`94000` 起）
  - 共 `162` 个采样 episode 的 `[seed, seed+16)` 区间两两严格不交，且与历史 `0..6751` 区间完全隔离。

---

## 2. G3/G4 统计比较器与诚实假设裁决（`scripts/compare_g3_g4_results.py`）

- **纯标准库与零模型加载**：仅依赖 Python 标准库与纯 Python `toolloop_fix.{harness,loop_detector_v2}`，执行前后强制断言未导入 `torch`/`transformers`/`peft`/`trl`，并支持 `_resolve_archive_root` 快照目录回溯。
- **完整性与公平门禁**：
  - 逐一校验 `dev_selection_runs`、`g4_e2e_runs`、`g4_prefix_runs` 及可选 `multi_train_seed_runs` / `multi_train_seed_prefix_runs` 的 `manifest.json` SHA256、`inputs`/`outputs` 全清单哈希、`external_inputs` 哈希、核心环境代码哈希、配对 `case_seeds` 与解码参数一致性。
  - 同时兼容单种子表 `case_results.jsonl` 与多种子表 `base_case_id`（或 `base_prefix_id`）+ `schedule_id` 格式。
  - 强制核验非 `B2` 各臂（含 `P`、`B3`、`B4`、`B5` 及消融臂）`guard_blocked_proposals == 0`（落实 Proposal §6 主方法不挂外部防循环规则）。
  - 在真实归档 `EXP-20261119-054..056`（`eval24`）实测核验通过：冻结 `loop_detector_v2` 在 `B0` 检出 `7/24`（v1 为 `5/24`）、`B2` 检出 `3/24` 非相邻循环（v1 为 `0/24`），并按确定性规则选出 `B1`（唯一 `1/21` 精确结构化完成）作为开发集最强不训练基线。
- **完整统计口径与诚实裁决输出（`g3_g4_comparison.json` + `g3_g4_comparison.md`）**：
  - 并排报告全部 **8 项 Proposal §7.2 主指标**（`exact_grounded_structured_completion` 与 `tool_goal_attainment` 并列绝不混算、`fixed_failure_prefix` 下 `recoverable` 与 `retry_required` 分离报告、`invalid_loop_rate_v2` 主口径及 4 类循环细分、零成功分母输出 `null`/`N/A`）。
  - 输出 **域内（`orders`+`files`, 24 例）vs 零样本跨域（`jobs`, 12 例）** 分层、多采样种子均值与样本标准差（严禁 best-of-K）、**3 训练种子（`s101, s202, s303`）均值/标准差/95% $t$-CI**，以及全部 7 组对照（`P vs B0`、`P vs Strongest Untrained`、`P vs B3`、`P vs B4`、`P vs B5`、`P vs P_no_retry`、`P vs P_no_normal`）的**模板聚类配对差值与 95% $t$-置信区间**（`df = G_eligible - 1`）。
  - 内置 Proposal §8.1 验收检验与 §8.2 **`P > B3` vs `P ≈ B3` vs `P < B3`** 诚实归因裁决：当 `P` 与纠错 SFT（`B3`）聚类 95% CI 含 0 或点估计接近时，明确输出 `P ≈ B3` 并承认收益主要来自执行验证的高质量失败现场纠错数据而非偏好损失本身优越。

---

## 3. 实际运行命令与核验结果

```bash
.venv/bin/python scripts/build_g4_closed_holdout.py && .venv/bin/python scripts/build_g4_closed_holdout.py --check
.venv/bin/python -m unittest tests/test_g4_holdout_and_comparator.py -v
.venv/bin/python scripts/validate_records.py
```

- `build_g4_closed_holdout.py` 与 `--check`：退出码 `0`，逐字节重放一致。
- `tests/test_g4_holdout_and_comparator.py`：**9 项测试全部通过（`Ran 9 tests in 1.637s — OK`）**。
- `validate_records.py`：**74 个历史 run 完整性校验 `0 errors`**。

---

## 4. 限制与下一步（供 `T022` 协调者接入）

- 当前 G4 套件本身属 `synthetic_harness` 测试夹具，尚未在 G4 上运行任何模型推理；`T022` 须先在 `eval24` 完成开发集锁定，再对 `g4_closed_holdout_iter9.jsonl` 执行**单次解封评测**（严禁看 G4 调参），最后通过 `scripts/record_run.py` 调用 `scripts/compare_g3_g4_results.py --config <cfg> --output <dir>` 生成正式归档报告。
