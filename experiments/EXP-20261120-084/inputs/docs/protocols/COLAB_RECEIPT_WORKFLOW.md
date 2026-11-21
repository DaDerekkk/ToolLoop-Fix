# Colab L4：先验证传输，后核验真实推理（ITER-006）

**已跑通的参考**：004 的 `lifecycle.py` 和 `worker.py`；对应冻结配置/代码 SHA 在 EXP-041，实际回执和离线验收在 EXP-044。不要对已有操作目录执行原脚本；它们是一次性记录。

1. **授权与冻结**：限定设备/用途、可发送的公开代码、公开模型 revision、样本/种子、时限及停损。先记 iteration/run，再冻结代码、哈希和配置。禁止上传整个快照；研究数据与隐藏评分不能随包上云。
2. **创建及隔离**：获取服务端原有分配集合和账号费率；只创建并操作独占名称的新 L4，确认硬件和增量费率。不可因失败停别人实例或升级机型。
3. **先通路，再放行**：先短 kernel ping。用短单元写入已审查代码，启动带排他锁的后台 worker；独立 nonce 的公开假回执须通过 stdout 和 Contents 两途径逐字节比对。只在确认 `waiting_for_arm` 后上传本次公开激活标记，激活不是生成成功。不要将完整训练/推理塞进一个必须保持连接的 notebook 调用。
4. **读同一个结果**：轮询专属目录的阶段文件；worker 成功/失败均先关闭 JSON，再原子发布带 nonce、长度、SHA 的帧文件。查询暂时失败≠模型失败，先重读同一结果，不盲目再次 generate。只归档白名单公开材料和必要状态，不转存 CLI 历史、令牌、签名 URL 或任意异常原文。
5. **释放与内容验收分开**：拿到完整回执或触发停损后，只停止自己新建的分配；用服务端集合和账号 usage 双重核对。随后用固定离线 tokenizer 重建 prompt/输入 ID、解码输出 ID、检查 EOS/上限、对照原文和解析参数；只把模型提出调用记作提案。解析错也保留，不因答案不满意重采。
6. **归档**：`operations/` 放真实运行原件；`experiments/` 中离线校验定为 analysis，报告明确指向远程 model_inference 来源，避免冒充本地 E2E 模型轨迹。最后更新 iteration、分类索引和 handoff。

本地重验示例（仅针对已下载的公开回执，不访问云）：以项目内 Python 运行 `scripts/verify_l4_output.py --receipt <044/operation_sources/toolloop_probe.json> --model-dir <本机同revision目录> --expected-sha256 <044中的收据摘要> --output <全新JSON路径>`。不能以此代替新的 GPU 实验或正式恢复率评估。
