# 多 agent 任务板

协调者管理本文件。任务必须有所有者和验收标准，文件范围不重叠。

| ID | Owner | 范围 | 状态 | 验收 |
|---|---|---|---|---|
| T001 | root | 根目录文档、scripts/、tests/test_records*.py、experiments/、iterations/ | 完成本轮 | 9 run 留档，51 测试，2 次冻结重放，完整性 0 errors |
| T002 | simulator | toolloop_fix/、tests/test_harness.py、configs/smoke.json、data/dev/pilot_cases.jsonl | 完成本轮 | 15 夹具、2 策略、40 harness 测试；预算/重试/归因修复 |
| T003 | source_audit | docs/research/、sources/references.json、独立记录审查 | 完成本轮 | 核心来源核验，记录器 R1–R3 复核关闭；模型证据仍空 |
| T004 | protocol | docs/protocols/、独立评测审查 | 完成本轮 | SOP/模板及 REVIEW-001/002；有界 G0 accept |

## 后续任务
- T005/T006/T009：在 ITER-002 已完成，见下表。
- T007：扩展开发模板与检测器复核；由人工审核样本，不能让 agent 自我审核冒充人工。
- T008：分组切分及封闭测试隔离；不把现有 15 夹具当作 held-out。
- T011（ITER-003 已完成）：终止语义/格式校准与通用 B1/B2 对照；旧 pilot 不重打分。
- T012（ITER-004 已完成固定前缀续跑部分）：真实prefix重放/配对和显式seed已实现；训练候选完整恢复验证仍待后续，不与该工程验收混同。

## 2026-11-16 后续授权与本轮分工
用户追加明确要求：继续多 agent，安装、下载权重并开始运行。

| ID | Owner | 范围 | 状态 | 验收 |
|---|---|---|---|---|
| T005 | root + source_audit | scripts/bootstrap_local.py、download_model.py、operations/、runtime/、models/、本地运行方案 | 完成 | 隔离安装成功、官方9文件1.77GB哈希验证、MPS实测 |
| T006 | simulator | toolloop_fix/model_*.py、tests/test_model*.py、configs/model_preflight.json | 完成 | 28模型测试；实际加载/生成/完整历史、无脚本接管 |
| T009 | protocol | scripts/record_run.py、validate_records.py、tests/test_records_model.py | 完成 | 26记录测试、schema3模型/外部清单契约，17run兼容校验 |
| T010 | root | configs 派生运行配置、experiments 新 run、PROGRESS/iteration/总交接 | 完成本轮 | CPU/MPS预检+15开发pilot+独立107轮复核；任务失败如实保留 |

不含付费服务、云 GPU、上传/发布授权；训练仍须满足 P1/P2 门禁。

## 领取格式
`ID / owner / 输入证据 / 允许修改范围 / 验收命令 / 依赖 / 状态 / handoff 路径`。
共享文档由协调者单写；工作者只提交独立 handoff/review，合并后更新任务板。

## ITER-003 领取（运行前）
| ID | Owner | 修改范围 | 状态 | 验收 |
|---|---|---|---|---|
| T011a | protocol | 新 calibrated_protocol、测试/说明；audit_calibrated_runs及审计测试 | 完成 | 协议18测试、审计6测试；REVIEW-004正式回放45案例/134轮 |
| T011b | simulator | 新 baselines、对应测试、比较脚本测试、handoff | 完成 | guard20测试、比较17测试，审查缺陷修复后通过 |
| T011c | root | 新 calibrated_run、测试、configs、analysis scripts、run/总文档 | 完成 | 同配置/种子45次案例真实推理，172联合测试，分类记录及交接 |
| T011d | source_audit | iteration3 独立设计/结果 review与handoff | 完成 | 三项实现审查关闭、干预前39轮匹配、语义风险/无收益如实记录 |

本轮明确不训练；T012 固定前缀真实模型对照不混入本次 E2E 结论。

## ITER-003 完成后的待办（未执行）
- T013：待人工复核自然终止/停止解释及公开任务措辞；42项队列不是42个失败。高优先级B2不可用订单无依据成功措辞。
- T012（此为ITER-003结束时计划，现已在ITER-004执行）：TIMEOUT/PRECONDITION固定prefix恢复对照；保持不训练。
- T007/T008：开发模板扩充、多种子、检测器人工复核及分组/封闭集仍未完成。

## ITER-004 领取（先语义、后固定前缀，不训练）
| ID | Owner | 文件范围 | 状态 | 验收 |
|---|---|---|---|---|
| T013a | protocol | 新ITER004语义review JSON/文档、独立prefix审计及交接 | 完成 | 42项+3正对照初审；REVIEW-005回放36案例/62轮/36首提示 |
| T012a | simulator | 新prefix运行器/测试/SOP、六格比较/测试/交接 | 完成 | 30运行器+16比较测试，真实36起点同快照/预算，历史成本分离 |
| T012b | root | prefix来源构造脚本/数据、configs、新run、比较与总文档 | 完成 | 36真实续跑，240联合测试，40run完整性0errors，旧结果保留 |
| T014 | source_audit | M6训练计划/disabled配置；独立prefix设计/结果审查 | 完成规划/审查 | 未核验目标机、未训练；当前语义风险/无收益如实记录 |

ITER-004 后待办：T013b 人工语义/检测器复核、T015 独立模板与多seed非训练诊断；目标机路径映射/环境复现待明确访问与授权。T007/T008数据划分未过，任何训练保持hold。

## ITER-005 领取
- root：单独获准的一台 Colab L4 生命周期、墙钟时间、已有实例保护、资料回收与交接。
- probe：固定版本公开玩具脚本与本地测试；不直接调用云资源。
- audit：本机 Colab CLI 行为/关机及预算边界只读审查。
- probe：035 校验六份固定源，037 从旧025–030取两个 TIMEOUT 首步配对；仅离线，不重评分。
- transport：新传输工具及测试（不改已上机的脚本），由 root 经036归档。验收：完整公开回执可逐字节复原，错误回执拒绝，不执行模型。

结项：034 预检 245 项测试通过；035 核验已释放、云输出缺失，不能验收模型；036 离线回传与回归共 251 项通过；037 保留分析脚本失败、037R1 在全部 12 行计入无效解析后完成。操作闭环 100.07 秒、没有补跑或训练。交接：`agents/handoffs/ITER005_colab.md`；未来云尝试需新授权。待办 T013b/T015 与训练数据门禁原样保留。

## ITER-006 运行前领取（2026-11-19）
- root：操作002 + 038冻结预检，验收真实回执/释放和迭代交接；依赖035及036。
- checker6：新 verify_l4_output.py/测试，仅离线重建 tokenizer，分开验证生成与提案。
- runtime6：独立新 review，核查 notebook exec、回传与资源边界，无云操作。
- research6：新 timeout_crossed 夹具、预检/交接及042；属下一阶段设计，与本次玩具推理分开。
- 追加：002冷启动传输超时后已核验释放，003/039 切分冷启动与传输；checker6 负责040独立原件归档。research6 从042公开载荷派生043本地 CPU（不使用本地 GPU / 云额度）12格单步真实提案，先单独登记协议。不将单步等同恢复。
- 结项：004/041 改为以 nonce 激活的单次后台执行及独立文件回传；044 真实生成1份，tokenizer/解析复核通过；045 256项本地测试通过。002–004每台均释放，最后0实例/0 CU每小时。042→043的12条真实 CPU 首步已归档，独立审查 `agents/reviews/iter6_043_semantics.md`。交接 `agents/handoffs/ITER006_l4_and_timeout.md`。待人工盲评、独立模板/种子；不训练。

## ITER-007 领取与结项（2026-11-19，不训练、不触碰云实例）
| ID | Owner | 输入证据 | 允许修改范围 | 验收命令 | 依赖 | 状态 | handoff 路径 |
|---|---|---|---|---|---|---|---|
| T015a | runner7 | EXP-028..030、EXP-043、prefix_run.py、calibrated_run.py | toolloop_fix/iter7_runners.py、scripts/compare_iter7_runs.py、scripts/audit_iter7_runs.py、tests/test_iter7_runners.py、docs/protocols/ITER007_MULTISEED_AND_TEMPLATES.md | `python3 -m unittest tests/test_iter7_runners.py -v` | 无（不改既有核心模块） | 完成本轮 | `agents/handoffs/iter7_runners.md` |
| T007a/T008a | dataset7 | harness.py、pilot_cases.jsonl、EVALUATION_SPEC.md | scripts/build_grouped_dev_suite.py、data/dev/grouped_dev_suite_iter7.jsonl、data/processed/grouped_split_manifest_ITER007.json、tests/test_grouped_suite_iter7.py | `python3 -m unittest tests/test_grouped_suite_iter7.py -v` | harness.py（只读） | 完成本轮 | `agents/handoffs/iter7_grouped_suite.md` |
| T013b/T007b | auditpack7 | EXP-016..018、EXP-025..030、EXP-043、EVALUATION_SPEC.md | scripts/build_g2_audit_packs.py、scripts/score_g2_human_audits.py、tests/test_g2_audit_packs.py、docs/protocols/G2_BLIND_AUDIT_PROTOCOL.md | `python3 -m unittest tests/test_g2_audit_packs.py -v` | 既有实验归档（只读） | 完成本轮 | `agents/handoffs/iter7_audit_packs.md` |
| T015b | root + auditor7 | Phase A 冻结代码与配置 | configs/iter7_*.json、experiments/EXP-20261119-046..059、EXP-20261119-REVIEW-006、agents/reviews/iteration7_independent_review.md、总文档与交接 | `python3 scripts/validate_records.py` | T015a、T007a/T008a、T013b/T007b | 完成本轮 | `agents/handoffs/ITER007_coordinator.md`、`agents/handoffs/iter7_audit.md` |

ITER-007 结项与后续门禁：共 70 个 run 完整性校验 `0 errors`（保留工程失败 `046` 与 `058`），290 项本地测试通过；REVIEW-006 纯 CPU 重放 174 个 episode / 509 轮新生成且 B0/B2 干预前 `118/118` 轮一致。待办：**T013c** 由真实人类完成 `blind_semantic_review_pack_ITER007.json`（166项）与 `blind_detector_audit_pack_ITER007.json`（358段，含212段真实模型轨迹）盲审标注并通过 `score_g2_human_audits.py` 冻结检测器规则；G3 训练与 G4 封闭测试继续保持 `hold` 直至 G2 人工门禁与明确训练授权就绪。

## ITER-008 领取与结项（2026-11-20：双盲银标 + 用户委托仲裁 + 检测器v2/G3数据冻结）
| ID | Owner | 输入证据 | 允许修改范围 | 验收命令 | 依赖 | 状态 | handoff 路径 |
|---|---|---|---|---|---|---|---|
| T016a | blind_silver_A | blind_A_semantic.json、blind_A_detector.json、G2_BLIND_AUDIT_PROTOCOL.md | data/processed/iter8_silver_semantic_A.json、iter8_silver_detector_A.json、agents/handoffs/iter8_silver_A.md | JSON schema & payload SHA 校验 | EXP-058R1 盲包 | 完成本轮 | `agents/handoffs/iter8_silver_A.md` |
| T016b | blind_silver_B | blind_B_semantic.json、blind_B_detector.json、G2_BLIND_AUDIT_PROTOCOL.md | data/processed/iter8_silver_semantic_B.json、iter8_silver_detector_B.json、agents/handoffs/iter8_silver_B.md | JSON schema & payload SHA 校验 | EXP-058R1 盲包 | 完成本轮 | `agents/handoffs/iter8_silver_B.md` |
| T017 | g2_freeze_engineer | EVALUATION_SPEC.md、harness.py（只读）、grouped_split_manifest_ITER007.json | toolloop_fix/loop_detector_v2.py、scripts/adjudicate_g2_silver_and_human.py、scripts/build_g3_preference_dataset.py、tests/test_iter8_g2_and_dataset.py、docs/protocols/ITER008_G2_SILVER_HUMAN_FREEZE.md | `.venv/bin/python -m unittest tests/test_iter8_g2_and_dataset.py -v` | 无 | 完成本轮 | `agents/handoffs/iter8_g2_engineer.md` |
| T018 | root | 双盲银标 + 用户委托银标仲裁 | configs/iter8_*.json、EXP-20261119-060、EXP-20261120-061..063 | `python3 scripts/validate_records.py` | T016a、T016b、T017 | 完成本轮 | `iterations/ITER-008_g2_silver_human_and_dataset_freeze.md` |

## ITER-009 领取与收官（2026-11-20：Colab L4 四臂/消融/多种子训练 + G4 封闭测试与最终报告）
| ID | Owner | 输入证据 | 允许修改范围 | 验收命令 | 依赖 | 状态 | handoff 路径 |
|---|---|---|---|---|---|---|---|
| T019 | lora_trainer9 | model_backend.py、iter7_runners.py、loop_detector_v2.py | toolloop_fix/lora_adapter.py、toolloop_fix/lora_eval_runner.py、tests/test_lora_and_losses.py | `.venv/bin/python -m unittest tests/test_lora_and_losses.py -v` | EXP-062 | 完成 | `agents/handoffs/iter9_lora_trainer.md` |
| T020 | colab_ops9 | COLAB_RECEIPT_WORKFLOW.md、COLAB-004/lifecycle.py、EXP-062 bundle | scripts/colab_g3_train_worker.py、scripts/run_colab_g3_lifecycle.py、scripts/verify_colab_g3_training.py、tests/test_colab_g3_pipeline.py | `.venv/bin/python -m unittest tests/test_colab_g3_pipeline.py -v` | T019 接口约定、EXP-062 | 完成 | `agents/handoffs/iter9_colab_ops.md` |
| T021 | g4_eval_suite9 | grouped_split_manifest_ITER007.json、build_g3_preference_dataset.py、EVALUATION_SPEC.md | scripts/build_g4_closed_holdout.py、data/dev/g4_closed_holdout_iter9.jsonl、data/processed/g4_holdout_manifest_ITER009.json、scripts/compare_g3_g4_results.py、tests/test_g4_holdout_and_comparator.py | `.venv/bin/python -m unittest tests/test_g4_holdout_and_comparator.py -v` | EXP-062 隔离池 | 完成 | `agents/handoffs/iter9_g4_suite.md` |
| T022 | root + auditor9 | Phase A 冻结代码与 G4 密封集 | configs/iter9_*.json、operations/COLAB-20261120-005..011R1、models/adapters/iter9_g3_wave{1,2,3}/、EXP-20261120-064..106、EXP-20261120-REVIEW-009R2、FINAL_REPORT.md、总文档 | `.venv/bin/python scripts/validate_records.py --write-index` | T019、T020、T021 | 完成 | `agents/handoffs/ITER009_final_handoff.md` |

ITER-009 全项目收官核验：`10/10` Colab L4 LoRA 适配器训练并离线验真（`EXP-070R1`/`075`/`089`），所有自有 `tlfix-*` 云会话永久释放；`eval24`（9 臂）+ G4 E2E（13 模型 / 468 episodes）+ G4 Prefix（13 模型 / 234 episodes）全部完成；`EXP-20261120-105R1` 产出正式统计对比；`EXP-20261120-REVIEW-009R2` 纯 CPU 重放 32 个模型实验（`846/846` episodes、`4,029/4,029` 轮生成，`0` 不一致）；`EXP-20261120-106` 通过 `326/326` 项测试；台账共 `123` runs / `0` errors。最终报告见 `FINAL_REPORT.md`。
