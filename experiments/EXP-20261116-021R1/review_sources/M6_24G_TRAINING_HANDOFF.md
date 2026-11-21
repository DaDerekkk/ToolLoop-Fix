# M6 / 24G 延期训练交接计划（ITER-004）

状态：**plan_only；enabled=false；现在不训练。** 负责人：source_audit；报告日期：2026-11-16（Asia/Shanghai）。

## 0. 先说结论与边界

- 将“M6、24G”仅当作用户描述的未来目标，尚未连接、未核验型号/内存/系统，也没有该机器上的运行结果或官方特定型号支持证明。**24GB 统一内存不等于 24GB 独立显存。**
- 稳妥优先级：保留现有官方未量化权重与 PyTorch CPU/MPS 基线 → 目标机离线复现 → 数据/评测门禁 → 另获授权后短程 LoRA 可行性探针 → B3/B4/B5/P。全量微调、量化、MLX 都不是默认捷径。
- 当前 M5 / 16GiB 的推理成功只证明该环境推理路径可用，不证明 M6、反向传播、PEFT/TRL 集成或训练内存可行。本文件不是可运行训练脚本；配置中的 `enabled=false` 是声明，不是已实现的启动防护。未来入口必须先实现并测试 fail-closed 门禁。
- 本次只读本地记录/权重头和官方文档，写计划；没有安装依赖、下载权重、连接远程机器、反向传播、优化器更新、上传或训练。

## 1. 不能丢失的现有基线

| 项目 | 当前已核验记录 / 未来要求 |
|---|---|
| 模型 | `Qwen/Qwen3.5-0.8B@2fc06364715b967f1860aea9cf38778875588b17`；不可静默改 revision |
| 权重 | `models/Qwen3.5-0.8B/<revision>/model.safetensors-00001-of-00001.safetensors`；1,746,942,600 bytes；SHA256 `04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696` |
| 外部清单 | `models/manifests/Qwen3.5-0.8B-<revision>.json`；SHA256 `049c3d159e86cf1492a8020d5a95f8d0b92c13091fc82891ce12979c26899d2d`；9 文件共 1,769,916,415 bytes |
| 已运行环境 | 项目隔离 Python 3.12.13、torch 2.15.0、transformers 5.23.0；完整版本见 `operations/SETUP-20261116-001/freeze.stdout.log` |
| 加载 | 内置 `Qwen3_5ForCausalLM`、本地 safetensors、`trust_remote_code=false`、eager；检查文本权重 missing/mismatched/error 与意外键，不把视觉/MTP 的明确排除当作漏载文本 |
| 推理 | CPU FP32（EXP-011）；MPS BF16（EXP-012、016–018）；MPS fallback=0。保留旧 run，不用目标机复现覆盖它们 |
| 协议 | 官方 tokenizer/template；当前 effective EOS `[248044,248046]`；非思考；校准协议 `qwen_xml_or_native_terminal_v1`；自然语言终止语义保持 unknown，不能当错误或成功自动入训 |
| 采样 | temperature=1、top_p=1、top_k=20、min_p=0、presence_penalty=2、repetition_penalty=1、do_sample=true；presence 仅当前生成片段的去重 token，不作用于整个提示/历史 |
| 当前研究证据 | ITER-003 B0/B1/B2 各 15 已知开发案例：目标证据均 3/12，精确有依据结构化完成均 1/12；不是总体任务成功率；B2 阻断不是学会恢复 |

`experiments/EXP-20261116-020/comparison.json`、`agents/reviews/iteration3_results_review.md` 和 REVIEW-004 为上一轮比较依据。当前 42 项待审队列、其中 33 项自然终止，以及旧 13 项队列均**不是训练就绪偏好标签**。G1 问题确认与 G2 数据/评测冻结尚未通过；本计划不提前认定 ITER-004 未结束实验的结论。

## 2. 分阶段执行单：每一步另建 run，不自动串联

| 门禁 | 将来执行内容 | 通过条件 / 停损 |
|---|---|---|
| T0 授权与硬件 | 用户确认目标机与允许范围；在目标机本地记录 `sw_vers`、`uname -m`、`sysctl -n hw.memsize`、必要的芯片信息、空闲磁盘；不收集序列号/凭据 | 实际型号、系统、内存字节数与访问权限可核验。若不是描述配置，更新独立计划版本，不假装 M6 结果 |
| T1 隔离与迁移 | 用户批准后迁移项目/已有权重，源/目标逐文件 SHA；新建独立环境，先尝试现有固定版本，不复制机器相关 `.venv`；新增 PEFT/TRL 或 MLX 先审批并锁精确版本与 wheel/hash | 原模型与旧实验只读；离线 import、MPS built/available、dtype、实际加载完整性通过；不能因新版本“更快”静默升级 |
| T2 非训练复现 | 先标准库测试和归档分析回放，再新 run 复跑 CPU 小样及 MPS 固定提示/工具调用；冻结运行输入、token IDs、EOS、采样与预算；B0/B1/B2 同版本重新配对 | 分开报告确定性数据回放与随机模型生成。不同设备/框架即使种子相同也不保证相同输出；记录首处分歧，不改旧结果。若协议/正常任务不可靠，回到校准 |
| T3 数据与科学门禁 | 先完成下节 G1/G2，再获得**新的明确训练授权**；审查训练入口、loss mask、模块匹配、reference 和恢复逻辑 | 无授权、未冻结划分、候选只可执行但无恢复/停止依据、unknown 冒充负例、存在泄漏，任一项即 blocked |
| T4 有界训练可行性 | 获批后先用已审小子集做 forward/backward、梯度有限/非零检查、5–10 optimizer steps；随后最多 50 steps 稳定性探针；单进程、无并行模型占用 | 记录实际峰值、token/s、步时、损失和参数变化；冻结基座应不变、仅预期 adapter 变化；checkpoint 重载与中断恢复可验证。探针仅工程证据，不算方法收益 |
| T5 开发比较 | B3 → P → B4/B5 的工程实现可依次做，但比较矩阵最终必须完整；先每臂一训练种子的 exploratory 筛查，再按冻结预算补重复 | 不用一次最低 loss/最佳 seed 选结论；若 SFT 相当或提示/规则足够，收缩贡献；不强迫 P 获胜 |
| T6 正式评估 | 开发集选定方案、冻结检测器/统计计划后才解封；固定前缀恢复与端到端分表；主方法及最强训练基线多训练种子 | 未达到样本量/能力保持证据则 inconclusive；不能看封闭集再调参。发布/上传另需授权 |

T0–T6 均为未来步骤，不代表已执行。所有训练探针，包括一次反向传播/优化器更新，都不在本次授权内。

## 3. 24G 内存可行性：算术预算，不是 benchmark

### 3.1 本次实际只读测得的文件头

使用标准库 `struct.unpack('<Q', first_8_bytes)` 读 safetensors 的 59,840-byte JSON 头，再对每个 tensor 的 `shape` 求积、`data_offsets` 求差；**没有将模型载入内存或 GPU**。

| 文件键组 | tensor 数 | 存储标量数 | 存储字节 |
|---|---:|---:|---:|
| `model.language_model.*` | 320（284 BF16、36 F32） | 752,393,024 | 1,504,791,232 |
| `model.visual.*` | 153 BF16 | 100,592,896 | 201,185,792 |
| 其他 MTP | 15 BF16 | 20,452,864 | 40,905,728 |

这是文件内容计数，不是实测 live/trainable 参数数；目标机加载后须核对 tied weights、排除的视觉/MTP 分支、实际 dtype 和 unique parameter storage。

### 3.2 保守估算公式

用文本存储标量数 P≈752.4M 作**规划代理**，GiB=2^30 bytes：

| 项目 | 规划量级 | 未包括什么 |
|---|---|---|
| 冻结 BF16 基座 | 约 1.40 GiB（文件中的少量 F32 略增） | 激活、临时副本、缓存、框架与系统 |
| FP32 基座 | 约 2.80 GiB | 同上 |
| 全量 AdamW：BF16 参数+梯度、FP32 两个 moment | 12P≈8.41 GiB | 激活；若另保留 FP32 master 则 16P≈11.21 GiB；实现可能不同 |
| 全量 AdamW：FP32 参数+梯度+两个 moment | 16P≈11.21 GiB | reference 模型、临时 optimizer 缓冲、激活/logits、系统 |
| LoRA | 冻结基座 + A×约16 bytes 的 FP32 adapter/梯度/moments | A 须根据实际匹配模块统计；A=1M/10M 对应约15/153 MiB仅为算例，不是本模型 rank 的实测值；激活仍可能大 |
| 一份 FP32 logits，batch=1，vocab=248,320 | L=512：0.474 GiB；1024：0.947 GiB；2048：1.895 GiB | DPO chosen/rejected 合并批次可约翻倍；loss/log-softmax 等可能另分配缓冲；实际 trainer 可避免完整物化，须测 |

因此“小于 1B”不等于“24G 下全量 DPO 稳定”。统一内存还要供 macOS/前台应用；DPO 若另驻留 reference，会再增加一份基座与前向成本。

**计划性资源上限**（不是实测推荐值）：先为系统/其它进程留 6–8 GiB，训练进程初始目标峰值约 14–16 GiB，并以目标机实际内存、系统压力和 MPS recommended working set 取更保守上限。RSS 与 MPS driver allocation 可能重叠，分别记录，不直接相加为峰值。先预留约 30–50 GiB 空闲磁盘用于隔离环境、数据、日志与 adapter/checkpoint；全量 checkpoint 则重新预算。不能据此承诺速度/训练小时数。

**稳妥停损**：一次 OOM、NaN/Inf、持续系统内存压力/新增大量 swap、磁盘接近预留线、意外 CPU fallback、数据/加载不一致即停止并保留失败；不循环重启、不同时加载多臂、不将 `PYTORCH_MPS_HIGH_WATERMARK_RATIO` 设为 0 来绕过限制。该变量为 0 会取消 allocator 硬上限，官方明确提示系统级 OOM 风险。[PyTorch MPS 环境变量](https://docs.pytorch.org/docs/2.15/mps_environment_variables.html)

## 4. 训练后端与初始参数候选（均未验证）

### A. 首选：现有 Transformers + PyTorch MPS，条件通过后加 PEFT/TRL

- 保留 CPU FP32 作小张量/短前缀数值参考，MPS BF16 作为候选；推理成功**不能替代 backward 支持**。PyTorch 文档提供 MPS 检查及训练设备接口，但没有为本目标机/混合线性注意力模型保证整条训练链。[PyTorch 2.15 MPS](https://docs.pytorch.org/docs/2.15/notes/mps.html)
- 第一个候选：LoRA rank=8、alpha=16、dropout=0，microbatch=1，gradient accumulation=8；训练序列上限先 512 的**兼容性短例**，再依实际完整前缀长度测试 1024/2048。短例探针不允许删除错误反馈或砍掉 chosen/rejected 行动来伪装真实数据适配；真实样本超长则分桶/重新设计有公开依据的上下文表示或拒收，并报告拒收比例。历史评测 8192 上下文上限不随训练探针静默降低。
- 候选学习率 SFT=1e-4、DPO=1e-5，beta=0.1、AdamW、max_grad_norm=1；仅是待预注册起点，不是已调好的值。正式网格、epoch/token 上限、正常/重试混合比须在数据统计后锁定。`use_cache=false`；gradient checkpointing 是否可用必须逐版本验证，并记录额外计算开销。
- **先枚举再注入模块**：现有文件包含 `self_attn.{q,k,v,o}_proj`、`linear_attn.{in_proj_qkv,in_proj_z,in_proj_a,in_proj_b,out_proj}`、`mlp.{gate,up,down}_proj`。初始方案审查各 Linear 是否匹配；不盲套 Llama 的 q/v 列表，不意外训练 embedding/lm_head/vision/MTP。输出每个命中路径/shape、trainable A、基座冻结检查。若缩减模块省内存，所有训练臂共同改变并登记新配置。
- PEFT 有 LoRA 及 tied-weight 配置接口；本次官方入口重定向到 v0.21.0，但未安装/验证它与当前 Qwen/torch 的组合。训练前锁精确依赖。不要为省内存默认添加 CUDA bitsandbytes、FlashAttention、vLLM 或在线教师。[PEFT LoRA](https://huggingface.co/docs/peft/v0.21.0/package_reference/lora)
- TRL 文档支持 PEFT、工具对话偏好数据与 reference log-prob 预计算。它当前限制 PEFT target `lm_head`，且预计算不兼容 `IterableDataset` / `sync_ref_model=true`。候选采用本地有限数据、冻结 reference、无 `lm_head` adapter；reference 缓存须绑定模型/模板/精度/完整 token IDs/mask/截断策略/实现 SHA。改变任何绑定项即失效，不能复用 stale log-probs。这个能力不是本地已跑通证明。[TRL DPOTrainer](https://huggingface.co/docs/trl/en/dpo_trainer)
- loss 使用**原始、未加采样变换的条件 token log-prob**；训练/reference 不套 temperature、top-k、presence penalty。那些参数属于候选采样与评测生成，并独立冻结。用可手算短例测试 causal shift、prompt/padding mask、实际模板 EOS、chosen/rejected 共享前缀与 sum/mean 约定；不要将 DPO implicit reward 当作环境完成率。

### B. 备选：MLX，不与原基线混称同一运行

MLX-LM 官方 LoRA 文档提供 LoRA/full、累积梯度、checkpointing 和 prompt masking；默认全 token loss 不满足本项目完整行动的 completion-only 目标，必须显式核对 mask。模型源码 `main` 有 Qwen3.5 text 路径与视觉/MTP 排除，但**源码存在不等于所选 release、Qwen3.5 backward、DPO/FTPO 或 M6 已验证**。本次读取的 `main` 未固定 commit；DPO trainer 猜测路径返回 404，不能据此断言支持或不支持。[MLX-LM LoRA](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md)、[Qwen3.5 实现](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/models/qwen3_5.py)

若 PyTorch 梯度/内存不通过且用户同意另试：
1. 固定 MLX/MLX-LM release/commit、依赖与转换脚本；从同一官方权重**不量化**转换，输出新目录，保留原文件；映射、排除键、dtype、全 tensor 清单与 SHA 存档，不上传。
2. 同 prompts/tools/messages 的渲染字节与 token IDs 必须相同；EOS、presence penalty 作用域/次序、top-k/top-p、预算、stop/parser 一致。
3. 用未训练基座比较固定 token 序列的 logits/log-probs/完整行动分数与 next-token top-k，先规定 dtype 容差、top-1/排序检查与异常阈值；不把随机序列逐字相同设为普遍保证。零 adapter、adapter 重载/禁用均不得改变基线语义。
4. 若数值差异足以改变行动偏好，记为不同 backend condition，重跑该 backend 的 B0/B1/B2 和全部训练对照；不能将 MLX 训练后结果直接减去旧 MPS B0。当 tokenizer/信息/预算不等价时拒绝比较。
5. 反向传播、梯度 checkpointing、SFT mask、DPO reference 与 B4 loss 都要独立测试。没有成熟实现时标 pending，不临时以 SFT 冒充 DPO。量化另起研究臂，不能合并未量化主结果。

## 5. 数据就绪门禁：先证据，后训练

1. **G1**：至少完成提案约 200 开发案例的真实 B0/B1/B2 问题确认（规模可经预注册调整），协议/公开任务语义清楚，正常能力不是主要瓶颈；真实固定错误前缀和 E2E 分开。不因当前低完成率直接推断训练必要。
2. **G2**：至少 200 段检测器人工复核，包含检出/未检出、合法 retry/poll、新进展、ABAB、正确停止；输出抽样分层及 TP/FP/FN/TN、分歧裁决和阈值。agent 自审不能冒充人工；检测样本数不是训练样本数。
3. 每个偏好对保存真实来源 run/case/step、可见 prefix hash、隐藏快照 hash（仅验证器）、预算/RNG、候选生成来源、同快照执行结果及审查状态。定位“已有足够反馈后本可避免的动作”，不是自动惩罚第一次工具失败。可有多个正例；完整行动可执行≠已验证恢复成功。
4. 公共训练输入使用白名单：请求、工具 schema、已发生的完整可见历史、完整下一行动。hidden category/可恢复标签/标准答案/未来状态/评测分数只在受限 sidecar；不能把 review queue 整对象喂给模型或候选生成器。没有当前教师授权；未来若用教师也须另批，评测时不得接管。
5. 保留同动作在 TIMEOUT 下应重试、在明确无效参数下应改变的相反上下文；加入正常执行、轮询、必要澄清与有依据停止。自然终止 unknown、失败说明套 finish wrapper、单纯 parser 错误均先审，不自动当 rejected/虚报成功。
6. 以流程模板/接口家族/故障组合分组，再 train/dev/holdout；仅换 ID/措辞/seed 的变体同组；两个域训练/开发、第三域整体留出。先冻结 split 再挖掘/生成，held-out 数据不得进入 reference cache、候选选择或调参。
7. 约 1,000 个高质量决策点是提案初始规模，不是当前已有数据，也不是为了凑数的硬指标；3,000–5,000 仅在质量/收益值得时另批扩展。冻结数据 manifest、许可证/来源、去重/分组检查和样本 exclusion 原因。没有高质量正例就暂停。

## 6. B3 / B4 / B5 / P 公平比较

| 臂 | 唯一核心差别 | 必须保留的控制 |
|---|---|---|
| B3 纠错 SFT | 同失败现场学习完整 chosen 行动；loss 只在目标行动及规定 EOS | 与 P/B4 尽可能同 prefix/正例、正常/重试配额、LoRA 模块/rank；不对工具反馈/提示计算训练 loss |
| B4 单 token FTPO 风格 | 冻结 tokenizer 下真实行动分歧处的局部偏好 | 保留完整可验证候选；不统一压低 `<tool_call>`；写清精确 token mask、多正例/约束。未复现 AntiDoom 多正例与 logit 约束时明确叫“简化 FTPO 风格”，不是完整复现 |
| B5 一般行动 DPO | 一般专家轨迹的行动偏好，而非专项失败挖掘 | 数据来源差别显式标记；同基座、参数/调参预算、可比 token 规模与语义验证；不偷用测试轨迹 |
| P 专项完整行动 DPO | 同失败可见上下文下 chosen/rejected 完整行动偏好 + 正常/合法重试保持 | reference 固定、completion-only likelihood、相同 EOS；定义 sum/length 归一化与混合目标，先冻结，不能借 parser 修复给正例隐藏信息；主评测不加 B2 guard |

所有臂尽量匹配训练决策点/正例和可训练参数/搜索次数；不同 loss 的实际 token 与计算量不会天然相等，要同时报实际量，资源差异不藏在“同 epoch”里。正常行为保持如用 SFT 混合，须写明比例/权重并加入对应消融；合法重试和正常保持分别消融，但研究主臂不可先丢掉这些反例。

LoRA 不通过时先定位模块、backward、长度和内存瓶颈，不能自动升级到全量 FT；后者只有额外授权、独立预算和同等比较控制通过后才立新实验。

**统计与结论**：当前 15 个已知夹具、单 seed schedule 只作工程检查。正式评测建议每例 5 sampling seeds，主方法和最强训练基线至少 3 training seeds；按同任务配对、模板聚类区间，不将 5 次采样视为 5 个独立任务，不报 best-of-5。提案 1,200 测试案例仍须预实验方差/功效分析，尤其“正常/必要重试退化≤2pp”不能由 3 个案例证明。

训练前冻结主要恢复/任务完成指标、unknown 处理和停止语义评审口径；未审核自然回答时继续分报 tool_goal_attainment / exact_grounded_structured_completion / unknown，不能包装成总体 task success。副指标包括执行循环和重复提案（分开）、必要重试、过早终止、经过审查的无依据成功、预算耗尽及所有失败成本。预案目标为循环相对降≥30%、对最强非训练基线恢复+5pp、正常/必要重试非劣界2pp，均需绝对差和配对区间；基线循环为零则相对量 N/A。达不到证据就 reject/inconclusive，不宣称训练路线有效。

## 7. 记录、checkpoint 与回滚

- 继续使用 `scripts/record_run.py` 冻结输入；kind 分清 analysis/model_inference/model_training。**现有记录器的训练专属完整性契约尚需实现/测试**，不能因接受 kind 名称便认为 optimizer/checkpoint 证据齐全。
- 每次训练保存：授权/门禁审查引用、代码/环境/模型/数据/分组 manifest SHA、tokenizer/template/token IDs/mask、模块名单与 A、loss 与 reference/cache 版本、随机种子/RNG、optimizer/scheduler、dtype/后端、实际样本/token/step/epoch、训练/验证 loss、梯度范数、吞吐、RSS/MPS 内存、系统压力、时间、失败原因。
- adapter 和原基座分离，原权重只读；每个 checkpoint 新目录，保存 adapter safetensors、配置、optimizer/scheduler/RNG/step（仅本项目可信 checkpoint）、校验清单与父 run。若只保存 adapter 不能声称精确恢复训练；MLX 的 resume-adapter 也须单独验证 optimizer/RNG 恢复能力。
- 先在短程探针验证中断→新 run 恢复，与不中断参照的 step/loss/参数差异；不允诺 MPS 跨版本逐 bit 一致。每个阶段预先限定 checkpoint 数/频率并预算磁盘；已归档失败和 checkpoint 不覆盖、不静默删除。
- 评估用独立 `model_inference` run，明确 checkpoint SHA 与 adapter 是否启用；开发集选择 best pointer，不替换旧基座。最终 holdout 冻结前不得轮流选最优 checkpoint。
- OOM/NaN/断电/停止保留原 incomplete/failed 状态；恢复另建 run 并引用父 run，禁止“补写成功”。触发能力/可信度退化时回到原基座与上一已验收配置；rollback 是切换明确路径，不是改写失败记录。
- 本地日志，不默认 WandB/云端追踪/Hub 上传。依赖版本、目标路径和统计阈值的任何实质变化均新 iteration/config，重新判门禁。

## 8. 本次核验记录与交接

- 读取 AGENTS/用户协议、当前 README/PROGRESS/TASK_BOARD、实验 SOP/评测规范、ITER-003/交接、提案方法和现有模型/runtime 清单；本地只用 stdlib 读取 safetensors 头与配置，没有模型执行。
- agent-reach 网页路线的 Jina Reader `curl` 因 `Could not resolve host: r.jina.ai` 失败；未安装/配置新渠道，改用现有 web 只读工具。上文官方来源访问日期均 2026-11-16；PEFT 文档重定向版本已注明，TRL 与 MLX main 是可变页面，未来执行必须重核并固定版本。没有核验任何“M6 专属官方支持”声明。
- `configs/future_training_plan.json` 是机器可读待办，不传给当前推理入口或训练器。目标机测量、运行速度、训练峰值、训练集路径、PEFT/TRL/MLX 选定版本均保持 null/pending，不能填推测值。
- 下一 agent 首先确认授权和 G1/G2，不是执行本文件里的候选参数。协调者维护总进度；本 agent 不修改既有实验、当前运行配置或权重。
