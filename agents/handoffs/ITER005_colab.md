# ITER-005 交接｜Colab 没有残留新实例；模型输出未取回

本次许可仅限另开一台 L4、一次单轮公开玩具请求，接受少量 CU、目标 10 分钟内释放；不充值、不训练，不操作既有实例。此次许可已使用。不要用另一次生成填补缺失回执，也不要对旧运行补写结果。

| 记录 | 核验了什么 | 不能据此声称什么 |
|---|---|---|
| EXP-20261119-034 | 运行前配置/上传白名单冻结；本地 245 项测试通过 | 模型已在云端生成 |
| operations/COLAB-20261119-001 | 服务端 L4 分配及清理、一次探针 exec 返回 0、取回文件超时；控制器最终退出 1 | CUDA runtime、checkpoint 校验通过，或算出了工具调用 |
| EXP-20261119-035 | 离线核验上项的六个固定摘要来源；初始端点集合已恢复；总历时 100.07 秒 | 单实例精确账单、云模型性能或自由额度 |
| EXP-20261119-036 | 本地 251 项测试通过，其中 6 项针对新回传方式；假 JSON 可逐字节还原，截断/篡改/错配拒收 | 旧云端结果已找回，或新方式已经过真实 Colab 验收 |
| EXP-20261119-037 → 037R1 | 首步必须解析成功的假设有误，037 失败保留；修正后两件 TIMEOUT 旧前缀 × 六条件的首步均可定位 | 12 个独立任务、领域因果效应、新推理或 B2 已修好 |

实际操作：项目内冻结 `scripts/colab_inference_probe.py` → 本地 `/usr/bin/python3 operations/COLAB-20261119-001/lifecycle.py`（**历史命令，不重跑**）→ 运行器执行 CLI 的 new/status/install/exec/download/stop → 复核 sessions 与 usage。远端模型请求固定为公开的 DEMO-001，工具不执行，模型 revision、生成参数和 9 个预期哈希见 034 的冻结脚本；预期哈希不是此次云端权重的实测哈希。没有上传旧轨迹、工作目录、凭据或本地权重。

本机复核只需 `PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_records.py`；再读 `experiments/EXP-20261119-035/receipt_audit.json`、其 `operation_sources/lifecycle.jsonl` 与 `agents/reviews/iteration5_receipt_transport.md`。CLI 层的 0 不能证明运行单元成功；未知下载超时的根因，已经释放的实例不能用作旧回执来源。

下次若另获云资源授权，用新操作号，先用公开小文件验证回执能完好归档，再运行一次生成；同一字节串按唯一标识、长度和 SHA 验证，如超时仅在原实例读取已存在的文件，不再 generate。仍须白名单、停损和 stop 后服务端复查。工程就绪不解除人工语义标签、数据切分与训练授权门禁。旧 M6/24G disabled 计划与 ITER-004 的 B0/B1/B2 科学结论留在原处。

**立即可做，不产生 CU：** `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests -p test_colab_receipt_transport.py -v`。实际实现的 stdout 标记为 `TLP_RECEIPT_V1:`；使用本次专属 32 位小写 hex nonce，收端仅接受 stdout，不保存混合日志；输出文件拒绝覆盖。源与测试见 036 的 `inputs/`。仅应用于审过的公开探针：CLI 自身可能记执行历史，Base64 不是加密。若要再上云，先把新工具纳入新的上传白名单并连接到新的生命周期控制器，不照抄旧控制器。

**研究接手：** 读 `experiments/EXP-20261119-037R1/timeout_pair.json`。订单在六格都首步同参重试→OK→获新证据，文件六格都不重试、最终无新证据。文件 explicit/B1 的首步无效解析保留，不能跳到下一个有效回合。固定两个开发前缀不是域级效果。先设计同反馈、不同工具名/标识符的配对反例，仍保留正常响应和不可用时合理停止；不得因该观察直接训练。总索引 `data/raw/iteration5_colab_index.json`；本轮收尾 45 个 run 完整性 0 errors（不代表每个实验成功）。
