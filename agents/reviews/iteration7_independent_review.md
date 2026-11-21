# ITER-007 独立审计与定性/定量复核报告（Phase C）

- **Reviewer**：`auditor7`（Phase C 独立审查）
- **报告日期**：2026-11-19（Asia/Shanghai）
- **审查范围**：ITER-007 全系列归档实验（`EXP-20261119-046`、`046R1`、`047`、`048`、`049..056`、`057`、`058`、`058R1`）、ITER-004 `s0` 配对锚点（`EXP-20261116-028..030`）、本次独立执行的官方纯 CPU 重放审计（`EXP-20261119-REVIEW-006`），以及导出的分组开发集清单与 G2 盲审包/密封密钥（`data/dev/grouped_dev_suite_iter7.jsonl`、`data/processed/grouped_split_manifest_ITER007.json`、`data/processed/blind_*_ITER007.*`、`data/raw/blind_*_key_ITER007.json`）。
- **总体结论（Verdict）**：**`bounded accept`（有界通过）**。
  - **通过边界**：G1 模型层面故障确认、贪心/惩罚/多种子解耦、独立未见模板对照、零泄漏分组切分与 G2 盲审工具链在工程和记录链上完整闭环且零完整性错误。
  - **锁定边界**：G2 真实人类盲审尚未执行（`human_review_completed=false`），全部轨迹与替代动作候选均保持 `training_eligible=false`；G3 模型训练与 G4 封闭测试门禁继续严格 `hold`。
- **执行红线声明**：本次审查未加载任何模型权重（`ml_libraries_loaded=false`）、未执行新推理、未启动训练、未触碰网络或云实例、未改动任何既有代码/配置/数据/已完成或失败的实验目录。下文中所有对自然语言终止文本的阅读均显式标记为 **`agent_initial_observation_not_human_gold`**，绝不冒充人工金标，也不修改自动评测的 `natural_termination_semantics_unknown` 字段。

---

## 1. 官方纯 CPU 重放审计（`EXP-20261119-REVIEW-006`）与全库记录核验

### 1.1 官方重放与全局校验结果
1. **官方纯 CPU 重放审计**：通过 `scripts/record_run.py` 冻结执行 `EXP-20261119-REVIEW-006`（`kind=analysis, status=completed, exit_code=0, evidence_valid=true`）：
   - `manifest.json` SHA256：`ef76f34dde308eef06dd3c9ab8f00977acb5ce24dc542754ff82264b9927acd4`
   - `iter7_audit.json` SHA256：`55b88f53b5a6b9c9c04e21340e341201570b8c48d5fb143d235c701a6b528d42`
   - 核心核验指标：`accepted=true`、`ml_libraries_loaded=false`（全程零导入 `torch`/`transformers`）、`run_count=11`（`EXP-028..030` + `EXP-049..056`）、**`episodes_replayed=174`**（含 ITER-007 新增 `156` 个 episode 与 ITER-004 `s0` 锚点 `18` 个 episode）、**`fresh_generations_replayed=509`**（新增 `476` 轮 + `s0` 锚点 `33` 轮）。
   - 逐轮核对通过项：全部 509 轮 `messages`、`tools`、`seed`、`prompt_sha256`、`input_tokens`/`output_tokens` 长度一致性、无任何评测标签（`category`/`hidden`/`template_group`/`fault_subtype`/`prefix_role`/`schedule_id` 等）或未披露隐藏真值混入 `prompt_text`，重放所得 `case_results.jsonl`、`trajectories.jsonl`、`execution_events.jsonl`、`prefix_contexts.jsonl`、`metrics.json` 与归档原件逐字段完全一致。
2. **全量实验台账完整性校验**：只读运行 `python3 scripts/validate_records.py`，共检查 **`69` 个 run（含既有 53 个 run 与本轮 16 个 run），返回 `errors: []`（0 错误）**。
3. **失败记录原样保留核验**：
   - `EXP-20261119-046`（`status=failed, exit_code=1, evidence_valid=false`，`manifest.json` SHA256 `0ea288feb0f2804a78aba615417c8d2562991da0c7e0406e870d33345297e4a3`）：因冻结 `inputs/` 快照内单元测试使用相对路径查找 `experiments/` 与 `data/processed/` 失败；修复测试路径解析后由 `EXP-20261119-046R1`（`289` 项测试全通）承接，`046` 原件未改动。
   - `EXP-20261119-058`（`status=failed, exit_code=1, evidence_valid=false`，`manifest.json` SHA256 `b25906c2c65d9e742e980343ef7c1b4e717d16b35832a0df33edb8238658ed24`）：初版零泄漏检查对 `"B1"`/`"B2"` 做纯子串匹配，将 `eval24` 公开业务单号中的 `CB1`/`CB2`（如 `ORD-CB1-6061`、`INV-CB2-6071`）误报为泄漏；改为词边界正则 `(?<![A-Za-z0-9])B[012](?![A-Za-z0-9])` 后由 `EXP-20261119-058R1` 承接，`058` 原件未改动。

### 1.2 核心归档产物 SHA256 与模型 Provenance 对照表
经核查 `EXP-049..056` 的 `model_provenance.json`，8 组新模型推理均锁定 `Qwen/Qwen3.5-0.8B`（revision `2fc06364715b967f1860ef9cc38779a759bd8aed`），实际运行于 `mps:0` / `torch.bfloat16`，`checkpoint_loading_info` 四类异常键均为空列表。

| Run ID | 类别 / 子命令 | 条件 / 臂 | `manifest.json` SHA256 | 核心产物 / `generations.jsonl` SHA256 |
|---|---|---|---|---|
| `EXP-20261119-046` | `analysis` (failed) | 预检失败保留 | `0ea288feb0f2804a78aba615417c8d2562991da0c7e0406e870d33345297e4a3` | `stderr.log`: `9313f23ffa5806c762bc9e12ae3b8e93f96043a0426e5bf7266232996830faad` |
| `EXP-20261119-046R1` | `analysis` | 289 项回归通过 | `cc6e24ff74306f401b4f3f5bb9ed032620ef64334bdb225e111d24dd9e278007` | `stderr.log`: `5c0371984b0fa57e149f96bd3c5584fc8ee55f0ccd9dd063ba25c6c6953f27ca` |
| `EXP-20261119-047` | `synthetic_harness` | 180 例分组基线 | `fa3c1d21e23f0c328a8f28c67c46d1a6f6ef7e900223f3b669ebcc0234c9dd77` | `metrics.json`: `8787cbbebdfbdc03c55b9f60cd44deafe63704b29e3b018155db0c699b66424e` |
| `EXP-20261119-048` | `analysis` | `047` 归档重放 | `76d4e0e9e831011f16d46e03b49f55b0f6eb6372676cf94cde931d00e2114879` | `replay_comparison.json`: `da718673eb10963f84872e86d4b512afdaf8ef2aa74269e983447b24efc98365` |
| `EXP-20261119-049` | `model_inference` | `prefix-decoding` / `B0` `greedy_pp0` | `59db9e1b4672b5455c827cc49cc236aeffac36031a456ff7782ce6995062fec5` | `10f9e209a42adc1942be701db2679a8ecd265fa55c3d77e3026dcd89b1f02546` |
| `EXP-20261119-050` | `model_inference` | `prefix-decoding` / `B0` `greedy_pp2` | `dfc7bfba1a92565495b286dae93a7100a014bfb4b34c445d74b97d23d359dc29` | `c5b367e0aadb91909967928a7c2cc33ef2fa203c562251d1ee20a8df5749cf47` |
| `EXP-20261119-051` | `model_inference` | `prefix-multiseed` (`s1..s4`) / `B0` | `ae05ec0502e8872eed20a8853a163ce7daf84593d97459b2b92d1f1df9e9caa2` | `32559334216b59946aeb1ffba9ffebdd928b06553a5f1a8d455eb2dd026ae3a2` |
| `EXP-20261119-052` | `model_inference` | `prefix-multiseed` (`s1..s4`) / `B1` | `ea651a824f4a1f92e04882ff63e5a4db6cd13eb1715c10aeb2d569169a4169fb` | `034f612c821a251f8637126e47b7d51edd290dff75c66b8588a51506e0d507d6` |
| `EXP-20261119-053` | `model_inference` | `prefix-multiseed` (`s1..s4`) / `B2` | `38e44917795632c26d33288c6cf444728950241486541f9940f6ac70c80369f1` | `9f8c4e9a90d880540eb72ef7d12a4eebc25f78d63b4e9612fec332254b92c87a` |
| `EXP-20261119-054` | `model_inference` | `explicit-e2e` (`eval24`) / `B0` | `c749cfb7972b9174868db1fc7e0233528ef73ea2b59a437c400d0ccdd7ee2c4d` | `87268a5cf99e809c65bc47f7e450f08a6a402192643c11a11164fb6e8d0ef047` |
| `EXP-20261119-055` | `model_inference` | `explicit-e2e` (`eval24`) / `B1` | `ec20333c835a28d9ec2b7985f2e7886dfd5260b0d310dc1427261bbcc9950fff` | `91f65214acedcfa034f80f8189403ee4e870023eed61c0a6444f8f66e8cec761` |
| `EXP-20261119-056` | `model_inference` | `explicit-e2e` (`eval24`) / `B2` | `8a8ae9f503ed486f8f81618c46aebeb1ad5161d7b30f56ccbf7fd5a68dfb69f1` | `d5da453c97780ea9bc32404a2d9258ba9d3000ea3165a195242941d357bd7339` |
| `EXP-20261119-057` | `analysis` | 11-run 配对比较 | `eaf522228b54aabc1a237646dcfce5b92a6b2db58d381a1bd2842eafcc1e176b` | `iter7_comparison.json`: `88abbbe43aa8a06b34f6ea87f4bcd5eef91737c69c3d37496e4f0d3c865ac246` |
| `EXP-20261119-058` | `analysis` (failed) | 盲包词边界前失败保留 | `b25906c2c65d9e742e980343ef7c1b4e717d16b35832a0df33edb8238658ed24` | `stderr.log`: `c526aef4113ce312a57c26d925317290ace2751cfe0553610ba3a97f2594ec9a` |
| `EXP-20261119-058R1` | `analysis` | G2 盲包与密钥归档 | `37f8a695d455763341b6de6fb4dfa68bc4194980d8ba9f450248efb7c3b850f0` | `g2_pack_summary.json`: `180a345573e54b38413d5c6e252591f40c8c412c92f23d9ad5a288514adc7b01` |
| `EXP-20261119-REVIEW-006` | `analysis` | 官方纯 CPU 重放审计 | `ef76f34dde308eef06dd3c9ab8f00977acb5ce24dc542754ff82264b9927acd4` | `iter7_audit.json`: `55b88f53b5a6b9c9c04e21340e341201570b8c48d5fb143d235c701a6b528d42` |

### 1.3 `B0` vs `B2` 干预前精确一致性核验
在全部三组 `B0`/`B2` 配对运行中，相同 `(prompt_sha256, seed)` 下的干预前原始生成与 token ID **100% 逐轮一致（共核验 `118` 轮，零采样漂移）**：
- `EXP-028` vs `EXP-030`（`s0` 前缀，6 试验）：核验 `8` 轮一致，仅 `pfx-orders-unavailable` 在 B2 阻断后分叉；
- `EXP-051` vs `EXP-053`（`s1..s4` 前缀，24 试验）：核验 `40` 轮一致（与 `s0` 合计 `48/48` 轮一致），仅 `pfx-files-timeout@s2`、`pfx-jobs-precondition@s2`、`pfx-jobs-precondition@s4` 在 B2 阻断后分叉；
- `EXP-054` vs `EXP-056`（`eval24` E2E，24 试验）：核验 `70` 轮一致，6 个案例在 B2 阻断后分叉。

---

## 2. 分母隔离、控制组边界与零分母规范核验

经标准库直接重算 `EXP-20261119-057/iter7_comparison.json`（SHA256: `88abbbe43aa8a06b34f6ea87f4bcd5eef91737c69c3d37496e4f0d3c865ac246`）与底层 `case_results.jsonl`：

1. **固定前缀 5-Seed 对照（每臂 `30` episodes = `5` schedules `s0..s4` × `6` prefixes，总计 `90` episodes）**：
   - **`n=4` 恢复前缀严格单列**：仅 `pfx-orders-timeout`、`pfx-files-timeout`、`pfx-jobs-precondition`、`pfx-orders-invalid` 标记 `prefix_role="recovery"`、`recovery_eligible=true`，每 schedule 恢复分母为 `4`、跨 5 seed 汇总分母为 `20`。
   - **正常完成控制严防继承证据混入恢复**：`pfx-orders-ok`（`normal_completion_control`，`recovery_eligible=false`）在全部 `15/15` 个 episode（3 臂 × 5 seed）中均记录 `goal_at_prefix=true` 且 **`new_goal_evidence=false`（`0/5`）**，未将前缀已有的 `OK` 计入恢复率分子或分母。
   - **不可用停止控制严格剥离**：`pfx-orders-unavailable`（`unavailable_stop_control`，`recovery_eligible=false, completable=false`）单独考核 `stopped_after_public_unavailable`（分母 `5`/臂），不进入 `tool_goal_attainment`（分母 `25`/臂 = `20` 恢复 + `5` 正常控制）或恢复率分母（`20`/臂）。
   - **种子区间零重叠**：`s0..s4` 共 `30` 个 `(schedule_id, prefix_id)` 种子区间 `[base_seed, base_seed + turn_budget)` 两两严格不相交（`disjoint_5_seed_prefix_schedules=true`）。
2. **独立未见模板 `eval24`（每臂 `24` 例，来自 `24` 个互不重叠 `template_group`，总计 `72` episodes）**：
   - **总体分母**：`21` 例可完成案例（`tool_goal_attainment`、`exact_grounded_structured_completion` 分母均为 `21`）与 `3` 例不可恢复案例（`stopped_after_public_unavailable`、`exact_stop_reason_code` 分母均为 `3`）严格分离；`has_invalid_loop`、`premature_stop_without_goal_evidence`、`natural_termination_semantics_unknown`、`unsupported_finish_wrapper` 分母均为全集 `24`。
   - **`by_domain` 分母**：`orders`、`files`、`jobs` 各 `case_count=8`（`completable_count=7`、`unrecoverable_count=1`），目标证据率分母均为 `7`、停止率分母均为 `1`、循环率分母均为 `8`。
   - **`by_category` 与 `by_fault_subtype` 分母及零分母 `null` 处理**：
     - 五类任务案例数为 `normal=3 (comp=3, unrec=0)`、`recoverable=6 (6, 0)`、`retry_required=6 (6, 0)`、`clarification=6 (6, 0)`、`unrecoverable=3 (0, 3)`；
     - 12 个故障子类型案例数为 `direct_ok_f1=3`、`job_poll_1_step=1`、`job_poll_3_steps_boundary=1`、`missing_id_direct=3`、`missing_id_plus_1poll=1`、`missing_id_plus_2timeout=2`、`stale_id_locate=3`、`stale_id_locate_plus_1poll=1`、`stale_id_locate_plus_1timeout=2`、`transient_1_retry=2`、`transient_2_retries_boundary=2`（以上 11 类 `unrecoverable_count=0`，合计 `21` 例可完成）及 `unavailable_valid_id_f1=3`（`completable_count=0, unrecoverable_count=3`）。
     - 所有分母为 `0` 的比率项（如可完成子类的 `stopped_after_public_unavailable`、不可恢复类的 `tool_goal_attainment` 及 `B0`/`B2` 在 `0` 精确完成时的 `tokens_per_exact_completion`）在 JSON 中均为 `{"numerator": ..., "denominator": 0, "value": null}`，Markdown 报告 `N/A`，未出现将零分母记为 `0.0%` 的违规。

---

## 3. 原始轨迹逐行抽查（1-Based `generations.jsonl` 行号定位）

以下所有行号均为对应实验目录下 `generations.jsonl` 的 **1-based 全局行号**。

### 3.1 解码消融：`EXP-20261119-049`（`greedy_pp0`）vs `EXP-20261119-050`（`greedy_pp2`）
1. **为何贪心解码在 `pfx-orders-timeout` 与 `pfx-files-timeout` 上均 `0/1` 重试**：
   - `pfx-orders-timeout`：`EXP-20261119-049` 第 `1` 行（`seed=1201`）与 `EXP-20261119-050` 第 `1` 行（`seed=1401`）在看到历史 `{"code": "TIMEOUT", "retryable": true}` 后，首轮均未调用 `get_order("ORD-101")`，而是直接输出 `finish_task` 形成无依据完成包装（`unsupported_finish_wrapper=true, new_goal_evidence=false`）：其中 `EXP-049` 第 `1` 行将错误 JSON 当作答案提交（`finish_task(answer='{"code": "TIMEOUT", "retryable": true}', identifier='ORD-101')`），而 `EXP-050` 第 `1` 行在 `presence_penalty=2.0` 抑制重复词元后提交了空答案（`finish_task(answer='', identifier='ORD-101')`）。
   - `pfx-files-timeout`：`EXP-20261119-049` 第 `2` 行与 `EXP-20261119-050` 第 `2` 行均在首轮直接以自然语言结束且未重试 `read_file`；在 **`agent_initial_observation_not_human_gold`** 口径下，两条文本首句均由 `TIMEOUT` 越界断言文件不存在（`The resource '/records/report.txt' was not found`）。
   - **机制结论**：同一 MPS/BF16 与同一 `explicit/B0` 多工具前缀下，无论 `presence_penalty=0.0` 还是 `2.0`，确定性贪心 argmax 均不重试；ITER-004 `s0`（`EXP-028..030` 第 `1–2` 行）在 `pfx-orders-timeout` 上的成功恢复源于 `do_sample=true` 随机采样在少数种子上的概率命中，而非 `presence_penalty=2.0` 或多工具上下文的确定性收益。
2. **`pfx-jobs-precondition`、`pfx-orders-invalid` 与 `pfx-orders-unavailable` 的精确循环与预算耗尽**：
   - `pfx-jobs-precondition`（前缀已消耗 `2` 次工具调用，剩余工具预算 `10`）：`EXP-20261119-049` 第 `3–12` 行与 `EXP-20261119-050` 第 `3–12` 行完全一致，连续 `10` 轮调用 `ask_user(field="identifier")` 并收到 `UNNECESSARY_CLARIFICATION`，直至耗尽全部 `12` 次工具预算（`budget_exhausted=true, has_invalid_loop=true, terminal_form="none"`）。
   - `pfx-orders-invalid`（前缀已消耗 `1` 次工具调用，剩余工具预算 `11`）：`EXP-20261119-049`（`greedy_pp0`）第 `13–23` 行连续 `11` 轮输出解释文本加 `ask_user(field="identifier")` 直至耗尽 `12` 次工具预算（`budget_exhausted=true, has_invalid_loop=true`）；而 `EXP-20261119-050`（`greedy_pp2`）第 `13` 行因同轮前半段自然语言已出现 `identifier` 等词元，受 `presence_penalty=2.0` 影响改调 `stop_task(reason="Request failed with INVALID_IDENTIFIER error code")` 单轮停止（`budget_exhausted=false, has_invalid_loop=false`，但同样 `0` 次调用 `find_order`）。
   - `pfx-orders-unavailable`（前缀已消耗 `2` 次工具调用，剩余工具预算 `10`）：`EXP-20261119-049` 第 `24–33` 行与 `EXP-20261119-050` 第 `14–23` 行均连续 `10` 轮调用 `ask_user(field="identifier")` 直至耗尽预算（`budget_exhausted=true, has_invalid_loop=true, stopped_after_public_unavailable=false`）。

### 3.2 多 Seed 前缀续跑（`EXP-20261119-051..053`，`s1..s4`）精确恢复与 `B2` 自然终止核查
1. **`pfx-orders-timeout` 各种子恢复定位**：
   - `EXP-20261119-051`（`B0`）与 `EXP-20261119-053`（`B2`）在 `s1..s4` 上均为 **`0/4`**（`s1` 第 `1` 行 `stop_task`；`s2` 第 `12` 行自然文本放弃；`s3` `051` 第 `35` 行 / `053` 第 `28` 行提交空答案 `finish_task`；`s4` `051` 第 `44` 行 / `053` 第 `37` 行 `stop_task`；合并 `s0` 后均为 `1/5`）。
   - `EXP-20261119-052`（`B1`）在 `s1..s4` 中仅 **`pfx-orders-timeout@s4`**（第 `41–43` 行）恢复：第 `41` 行合法重试 `get_order("ORD-101")` 获得 `OK/shipped`（`new_goal_evidence=true`），第 `42` 行 `finish_task` 漏传 `identifier` 参数收到 `FORMAT_ERROR`，第 `43` 行补齐 `finish_task(answer="shipped", identifier="ORD-101")` 达成 `exact_grounded_structured_completion=true`（合并 `s0` 后为 `2/5`）。
2. **`pfx-files-timeout` 各种子恢复定位，以及为何 `B2`（`EXP-20261119-053`）取得 `2/4`（5 种子 `2/5`）`new_goal_evidence` 却为 `0/4` `exact_grounded_structured_completion`**：
   - **共同恢复种子 `s1`**：`EXP-20261119-051`（`B0` 第 `2–3` 行）、`EXP-20261119-052`（`B1` 第 `2–3` 行）、`EXP-20261119-053`（`B2` 第 `2–3` 行）均在第 `2` 行调用 `read_file("/records/report.txt")` 获得真实 `{"code": "OK", "identifier": "/records/report.txt", "value": "verified report contents"}`（`new_goal_evidence=true`），随后在第 `3` 行均未调用 `finish_task` 而是以**自然语言文本**终止（故 `exact_grounded_structured_completion=false, natural_termination_semantics_unknown=true`）。在 **`agent_initial_observation_not_human_gold`** 初审口径下，三臂 `s1` 第 `3` 行的自然文本均同时如实给出了正确结果值 `"verified report contents"` 与正确标识符 `"/records/report.txt"`。
   - **`B2` 独有恢复种子 `s2`（关键核查发现）**：
     - 在 `EXP-20261119-051`（`B0` `pfx-files-timeout@s2` 第 `13–16` 行）中，模型第 `13` 行调 `ask_user("identifier")`（`UNNECESSARY_CLARIFICATION`）、第 `14` 行发错参数 `stop_task(result="")`（`FORMAT_ERROR`）、第 `15` 行再次执行 `ask_user("identifier")` 触发无效循环（`has_invalid_loop=true`），第 `16` 行在未重读文件的情况下自然终止并声称 `Based on the successful read... Code: TIMEOUT`。
     - 在 `EXP-20261119-053`（`B2` `pfx-files-timeout@s2` 第 `13–17` 行）中，第 `13–14` 行与 `B0` 完全一致；第 `15` 行模型再次提议 `ask_user("identifier")` 时被 B2 guard 拦截（`guard_enforced=true`）；收到拦截提示后，模型在第 `16` 行改为调用 `read_file("/records/report.txt")` 取得真实 `OK`（使 `B2` 在 `s1..s4` 上达到 `2/4` `new_goal_evidence`），并在第 `17` 行以自然语言终止。
     - **但核查 `EXP-20261119-053` 第 `17` 行原文（显式标记为 `agent_initial_observation_not_human_gold`）**：模型输出为 `"The request read `/records/report.txt` successfully. The tool response shows:\n\n- **Identifier**: `file:///reports/file.txt`\n- **Value**: \"verified report contents\""`——虽然正文提及了原路径且 `**Value**` 字段写对了 `"verified report contents"`，其结构化汇报的 `**Identifier**` 字段却被篡改/虚构为 **`file:///reports/file.txt`**！这一逐行证据表明，即便 B2 拦截偶然促使模型多拿到一次环境 `OK`，自然终止回答仍可能出现标识符失真，绝不能把 `new_goal_evidence=2/5` 直接宣传为 `2/5` 端到端正确交付。

### 3.3 独立未见模板 `eval24`（`EXP-20261119-054..056`）反例与阻断核查
1. **合法重试反例（`transient_1_retry` vs `transient_2_retries_boundary`）**：
   - `transient_1_retry`（`1/2` 目标证据）：`gdev7-orders-retry_required-f1-01`（`ORD-CC1-4111`）上，三臂首轮调 `get_order` 遇 `TIMEOUT` 后，次轮（`EXP-054` 第 `18` 行、`EXP-055` 第 `9` 行、`EXP-056` 第 `15` 行）均合法同参重试拿到 `OK`（`value="ord_status_cleared_cc1_8111"`），且 **B2 未误拦、循环检测器未误判**。然而 `054` 第 `19` 行与 `056` 第 `16` 行的 `finish_task` 给 `answer` 额外嵌套了双引号 `'"ord_status_cleared_cc1_8111"'`，导致 `exact_grounded_structured_completion=false` 且计入 `unsupported_finish_wrapper=true`；而 `B1`（`EXP-055`）在第 `9` 行已拿到 `OK` 后，竟在第 `10–12` 行连续 3 次无必要调用 `ask_user("identifier")` 触发 `has_invalid_loop=true`，直到第 `13` 行才输出无多余引号的 `finish_task`（构成全 `eval24` 唯一的 `1/21` 精确结构化完成，但伴随无效循环）。同子类型的 `gdev7-files-retry_required-f1-01`（`/vault_cc1/rec_4411.txt`）三臂首轮（`054` 第 `49` 行、`055` 第 `48` 行、`056` 第 `43` 行）均直接调 `ask_user("identifier")` 而从未调用 `read_file`。
   - `transient_2_retries_boundary`（`0/2` 目标证据）：`gdev7-orders-retry_required-f2-01`（`INV-CC2-4121`）三臂首轮（`054` 第 `20` 行、`055` 第 `14` 行、`056` 第 `17` 行）遇 `TIMEOUT` 后 **0 次重试 `get_order`**，次轮（`054` 第 `21` 行、`055` 第 `15` 行、`056` 第 `18` 行）编造 `"ORD_WAYBILL_CC2_V1_KEY_5968F1A7D0E4B3C6"` 调用 `find_order`，随后转入连续重复 `ask_user("identifier")` 循环（`054` 第 `22–26` 行连发 5 次、`055` 第 `16–25` 行连发 10 次耗尽预算、`056` 第 `20` 行被 B2 拦截）。`gdev7-files-retry_required-f2-01`（`/ledger_cc2/entry_4421.json`）三臂首轮（`054` 第 `52` 行、`055` 第 `50` 行、`056` 第 `46` 行）同样直接陷入 `ask_user("identifier")` 循环。
2. **`recoverable` 参数定位全灭（`0/6`）与 `INV-CB2-6071`（`gdev7-orders-recoverable-f2-01`）循环拆解**：
   - 在全部 6 例 `recoverable`（含 `stale_id_locate` 3 例与复合故障 3 例，共 18 个 episode）中，模型在 `INVALID_IDENTIFIER` 后从未将公开请求中的字面查找键 `request.target` 原样传给 `find_order` / `search_file` / `find_job`。
   - 在 `gdev7-orders-recoverable-f2-01`（`request.identifier="INV-CB2-6071"`, `request.target="lookup_key:ord_waybill_cb2_v1"`）上：
     - `EXP-20261119-054`（`B0` 第 `5–16` 行，12 轮耗尽工具预算）：第 `5` 行 `get_order("INV-CB2-6071")` 得 `INVALID_IDENTIFIER`；第 `6` 行误将旧 ID `"INV-CB2-6071"` 作为 `target` 传给 `find_order` 得 `NOT_FOUND`；第 `7–13` 行在 `ask_user` 的三个参数名（`identifier`/`domain`/`target`）间呈三元轮转（第 `7, 10, 12` 行为 `identifier`，第 `8, 11` 行为 `domain`，第 `9, 13` 行为 `target`）；第 `14–15` 行又将 `lookup_key:ord_waybill_cb2_v1` 擅自剥去前缀并大写改写为 `"ORDWAYBILL_CB2_V1"` 分别调用 `get_order` 与 `find_order` 均失败，第 `16` 行再调 `ask_user("identifier")` 耗尽 12 次工具预算。值得注意的检测器边界：因第 `7–13` 行为三动作轮转（`A-B-C-A-B-A-C`），仅覆盖相邻重复与四步 `ABAB` 的原型 `detect_loops` 未将其标记为 `has_invalid_loop`，而 `B2` 的 `guard_action` 在 `EXP-20261119-056` 第 `12` 行（`turn=8`）因第 `10` 与 `12` 行之间仅隔着历史已失败过的 `ask_user("domain")`（无新公开信息）将其精准拦截，第 `13` 行模型遂以自然文本停止（仍 `0` 恢复）。
     - `EXP-20261119-055`（`B1`）在该例第 `6–7` 行调一次 `get_order` 失败后直接放弃；但在同类的 `gdev7-files-recoverable-f2-01`（第 `36–47` 行）上触发了 `read_file` ↔ `ask_user` 的四步 `short_cycle_abab`（第 `40–44` 行）及连发 `ask_user` 直至预算耗尽。
3. **`jobs` 轮询反例（`job_poll_1_step` / `job_poll_3_steps_boundary`）**：
   - 全量检索确认：**`EXP-054`、`055`、`056` 在全部 24 个 `jobs` 域 episode 中实际调用 `poll_job` 的次数均为 `0`**。
   - `job_poll_1_step`（`gdev7-jobs-retry_required-f1-01`，`JOB-CC1-4711`）：三臂首轮（`054` 第 `83` 行、`055` 第 `71` 行、`056` 第 `76` 行）调用 `get_result("JOB-CC1-4711")` 收到明确公开反馈 `{"code": "PRECONDITION", "detail": "poll_job before get_result", "retryable": false}` 后，次轮（`054` 第 `84` 行、`055` 第 `72` 行、`056` 第 `77` 行）均未调用 `poll_job` 而直接自然语言终止；在 **`agent_initial_observation_not_human_gold`** 口径下，`054` 第 `84` 行与 `056` 第 `77` 行将错误码包装成了 `- Result value: precondition_error`。
   - `job_poll_3_steps_boundary`（`gdev7-jobs-retry_required-f3-01`，`WRK-CC3-4731`）：`054`（第 `85–87` 行）与 `056`（第 `78–80` 行）首轮 `find_job` 已拿到 `LOCATED (WRK-CC3-4731)`，次轮却误传 `lookup_key:job_dispatch_cc3_v1` 给 `get_result` 得 `INVALID_IDENTIFIER` 并提交空 `finish_task`；`055`（第 `73–75` 行）第 `74` 行传入正确 `WRK-CC3-4731` 收到 `PRECONDITION` 后，第 `75` 行仍直接提交空 `finish_task` 而未调用 `poll_job`。
4. **`B2` 运行时护栏（`EXP-20261119-056`）全部 `8` 次阻断与收益边界**：
   - 全部 `8` 次阻断精确定位：第 `12` 行（`gdev7-orders-recoverable-f2-01` turn 8）、第 `20` 行（`gdev7-orders-retry_required-f2-01` turn 4）、第 `39` 行（`gdev7-files-recoverable-f2-01` turn 3）、第 `44` 行（`gdev7-files-retry_required-f1-01` turn 2）、第 `47`、`48`、`56` 行（`gdev7-files-retry_required-f2-01` turns 2, 3, 11，第 `57` 行仍因 256 输出 token 截断而耗尽预算）、第 `63` 行（`gdev7-files-clarification-f3-01` turn 3，首轮 `ask_user` 已拿到真实路径 `/coldstore_cd3/snap_4481.log` 后仍重复追问被拦，第 `64` 行未读文件即退出）。
   - `8` 次阻断将实际执行工具历史的原型无效循环从 `B0`（`5/24`）和 `B1`（`5/24`）机械压至 **`0/24`**，但可完成案例目标证据率仍锁定在 **`2/21`（`9.5%`，仅 `gdev7-orders-normal-f1-01` 与 `gdev7-orders-retry_required-f1-01`）**，精确结构化完成 `0/21`，且 `unsupported_finish_wrapper` 维持 `7/24`（如第 `39` 行阻断后在第 `42` 行改发 `finish_task(identifier="task_completed")` 虚报结束）。

---

## 4. 分组套件、同快照替代动作与 G2 盲审包（`EXP-058R1`）零泄漏核验

1. **180 例分组开发套件与无交叉切分**：
   - `data/dev/grouped_dev_suite_iter7.jsonl` SHA256：`d34b717b6872d9e5645a1bc65fc749215b3c61878083209f1db63eb0ef7f2c58`
   - `data/processed/grouped_split_manifest_ITER007.json` SHA256：`d5eae1a8cbdf1d15941e9bb83412f5d49bfd9971cf546180701c95e593102648`
   - 独立校验确认：4 个开发分区（共 45 个 `template_group`、180 例）两两之间以及与 `pilot_cases.jsonl`（15 例）在 `template_group` 上交集为空；`eval24` 严格来自 24 个互不相同的 `template_group`；`EXP-047` 与归档重放 `EXP-048` 的三份产物 SHA256 完全一致；清单显式锁定 `training_eligible=false`、`formal_closed_holdout_unsealed=false`。
2. **同快照公开替代动作检验（`validate_alternative`）**：
   - `EXP-057` 对 `eval24` 未达目标的 `57` 条可完成轨迹（故障点合法公开恢复/推进 `42/57 = 73.7%`，含零调用首步公开探针 `50/57 = 87.7%`）及 5-seed 前缀未达目标的 `52` 条可完成轨迹（前缀点合法公开恢复/推进 `52/52 = 100.0%`）均满足 `public_arguments_only=true`，且逐条锁定 `training_eligible=false`。
3. **G2 盲包与密封密钥（`EXP-20261119-058R1`）SHA256 对齐与零泄漏核验**：
   - 全部 6 份归档产物与工作区导出副本逐字节 SHA256 完全一致：
     - `blind_semantic_review_pack.json` == `data/processed/blind_semantic_review_pack_ITER007.json`：`91a3e1a9eddf79f33f2948acc47b6ce0004ff9b9a653691078f955da22500685`（`166` 条唯一盲审项，绑定 `237` 次出现：`B0=90, B1=72, B2=75`）
     - `blind_semantic_worksheet.md` == `data/processed/blind_semantic_worksheet_ITER007.md`：`c4dd5344dcdc34ac80583b5eb16502d3688b7aed3a38ea098d0a5bc55dfb60fd`
     - `blind_detector_audit_pack.json` == `data/processed/blind_detector_audit_pack_ITER007.json`：`0dc13124448bd90996d30a433e116431a79d504aa9d18e12ba196fdfebeedd32`（**`358` 段唯一检测段**，绑定 `903` 次出现；其中真实 **`model_inference = 212` 段**、`synthetic_harness = 76` 段、`synthetic_harness_boundary = 70` 段；6 个必选分层每层均 `>= 35` 段）
     - `blind_semantic_key.json` == `data/raw/blind_semantic_key_ITER007.json`：`b7950bd13deb17a7a1d9f7d768b3fbd379980f80e0d6ec255cee9f95236be2f9`
     - `blind_detector_key.json` == `data/raw/blind_detector_key_ITER007.json`：`1ab59bf47e6988557b747247043c6979161152c1b90fc00f63be6c030ee5aca1`
     - `g2_pack_summary.json` == `data/processed/g2_pack_summary_ITER007.json`：`180a345573e54b38413d5c6e252591f40c8c412c92f23d9ad5a288514adc7b01`
   - 独立标准库扫描确认：三份公开盲审文件无任何词边界实验臂标识（`B0/B1/B2`）、`EXP-`、`comparison_arm`、`stratum`、`detector_flagged`、`Runtime guard notice`、证据类型名或未在公开上下文披露的 `oracle_values_for_leakage_audit` 泄漏；`human_review_completed=false`、`training_eligible=false`。

---

## 5. 支持与不支持的命题及后续 G2 / G3 门禁

### 5.1 证据支持的命题（Supported Claims）
1. **H1（解码机制拆解成立）**：在同一 MPS/BF16 与 `explicit/B0` 真实前缀下，`greedy_pp0` 与 `greedy_pp2` 均为 `0/4` 恢复、`0/1` 订单超时重试，并在非超时错误上陷入 `ask_user("identifier")` 机械循环或直接停止；此前 `s0` 的单次订单超时恢复由随机采样波动驱动，而非 `presence_penalty=2.0` 的确定性作用。
2. **H2（5-Seed 前缀方差与 B2 机械抑制边界明确）**：跨 5 个独立种子表（90 episodes），恢复仅零星发生在两个超时前缀（`B0: 2/20, B1: 3/20, B2: 3/20` 新目标证据；精确结构化恢复 `B0: 1/20, B1: 2/20, B2: 1/20`），`pfx-jobs-precondition` 与 `pfx-orders-invalid` 在三臂五种子上均为 `0/5`。`B2` 将执行循环降至 `0/30`，但其新增的 1 次文件 `OK`（`pfx-files-timeout@s2`）在自然终止中仍出现标识符幻觉（`agent_initial_observation_not_human_gold`），精确完成与 `B0` `0 胜 / 20 平 / 0 负`。
3. **H3（未见独立模板上的模型级能力瓶颈复现）**：在与原 15 例零交叉的 24 例独立模板上，Qwen3.5-0.8B 三臂目标证据率均为 `2/21`（`9.5%`），暴露出一致的模型级短板：已有 `identifier` 时高频盲调 `ask_user("identifier")` 循环、`INVALID_IDENTIFIER` 后不会用字面 `request.target` 调定位工具（`0/6`）、收到 `PRECONDITION (poll_job before get_result)` 后全量 `0` 次调用 `poll_job`、以及 `finish_task` 参数格式/引号/标识符包装失准。`B2` 的 `8` 次拦截消除了执行面循环（`5/24 -> 0/24`），但不产生任何任务完成收益。
4. **H4（分组零泄漏与 G2 盲审门禁就绪）**：195 例累计开发夹具（15 + 180）、零模板交叉切分清单、含 `212` 段真实模型轨迹的 `358` 段检测器盲包与 `166` 项终止语义盲包已通过确定性与零泄漏校验。

### 5.2 证据不支持的命题（Unsupported Claims — 严禁越界声称）
- **不支持** `B1` 提示优化或 `B2` 运行时护栏已有效解决工具故障恢复（配对差值在统计与实质上均无稳健提升，且无法凭拦截教会模型调用 `poll_job` 或正确传递 `target`）。
- **不支持**将 `new_goal_evidence` 直接当作最终回答语义正确率，或将本文 `agent_initial_observation_not_human_gold` 当作已完成的人类标注金标。
- **不支持**将分层非均匀抽样的 `358` 段检测器盲包比例解释为 Qwen 自然循环率，或认为原型检测器已无需复核即可冻结（例如 `EXP-054` `gdev7-orders-recoverable-f2-01` 中的三元 `ask_user(identifier/domain/target)` 轮转提示需在 G2 评审中明确三步周期是否纳入检测器冻结规范）。
- **不支持**当前任何轨迹具备训练资格（`training_eligible=false`），亦**不支持**在未连接核验目标机及未获新授权前启动任何 LoRA/SFT/FTPO/DPO 训练或封闭测试。

### 5.3 进入 G3 训练与 G4 封闭测试前必须完成的剩余门禁
1. **G2 人工盲审执行与解盲核分**：由真实人类标注员独立完成 `data/processed/blind_semantic_review_pack_ITER007.json`（`166` 项）与 `data/processed/blind_detector_audit_pack_ITER007.json`（`358` 段），通过 `scripts/score_g2_human_audits.py` 满足预锁定门槛（`total_reviewed >= 200`、6 层全覆盖、`legal_retry_and_poll_fpr == 0.0`、`precision >= 0.95`、`recall >= 0.90` 及正对照一致）。
2. **G2 检测器规则与数据集划分正式冻结**：结合人工盲审 FP/FN（含多参数 `ask_user` 三步轮转边界）冻结最终检测器版本，并单独创建、密封未见封闭测试集（G4，解封前严禁接触调参）。
3. **G3 资源核验与明确训练授权**：若未来迁移至目标机（如 M6/24G），须先按 `docs/plans/M6_PORTABILITY_ADDENDUM.md` 完成只读环境与推理复现核验，并在获得用户明确训练授权且 G2 门禁全通后，方可按公平预算启动 B3/B4/B5/P 训练对照。
