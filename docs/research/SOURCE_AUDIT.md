# 一手来源核验与研究边界

- 日期：2026-11-16（Asia/Shanghai）；执行者：`/root/source_audit`。
- 证据类别：`analysis`，仅文献/元数据核验；没有运行外部项目、模型推理或训练。
- 输入：用户提案、用户给定 AntiDoom 博客；二者是研究材料，不是环境变更授权。
- 机器可读来源与版本：`sources/references.json`。本记录不取代最终实验的依赖锁定。

## 1. 核验结论

| 来源 | 本次确认 | 对项目的影响 |
|---|---|---|
| [AntiDoom 博客](https://www.liquid.ai/blog/antidoom) | 2026-07-07；定位重复起始 token，使用单位置、多正例及 logit 空间约束的 FTPO；结果针对其指定模型和文本推理任务 | 只能借鉴局部失败定位。没有证明 Qwen3.5-0.8B 的跨轮工具循环已经可被同法修复 |
| [AntiDoom 仓库](https://github.com/Liquid4All/antidoom) | 官方博客所链仓库；README 区分生成、训练、合并流程；CUDA 默认、ROCm 独立环境 | 不直接执行全流程；未核验 upstream 的 macOS 运行支持 |
| [Agentic-DPO v1](https://arxiv.org/html/2607.10601v1) | 2026-07-12；专家动作状态下构造一步动作偏好，配合 SFT 稳定项和策略保持增强 | 行动级 DPO 不是本项目新发明；必须比较专家轨迹偏好与真实失败现场挖掘 |
| [Fission-GRPO](https://aclanthology.org/2026.acl-long.1880/) | ACL 2026 元数据和摘要已核；把错误反馈与 on-policy 恢复采样纳入 RL | 错误恢复训练已有前作；本项目需论证离线、轻量方案的价值，不能泛称首次错误恢复 |
| [Feedback That Backfires v1](https://arxiv.org/abs/2608.23651v1) | 2026-08-24；作者在指定小模型/环境中研究失败调用的呈现方式与重复倾向 | B1 应包含保留错误语义、改变逐字失败动作呈现的对照；不是删掉全部错误历史。是否适用于目标模型仍待测 |
| [BFCL V3 多轮说明](https://gorilla.cs.berkeley.edu/blogs/13_bfcl_v3_multi_turn.html) | 同时检查执行后状态及响应所需调用证据；强制终止不算正确；初态不直接暴露给模型 | 当前自建环境不是 BFCL；正式外部回归须固定版本、子集、执行器和许可证 |
| [DPO](https://arxiv.org/abs/2305.18290v3)、[PEFT LoRA](https://huggingface.co/docs/peft/main/en/package_reference/lora)、[τ-bench](https://arxiv.org/abs/2406.12045v1) | 标题/基本方法与提案引用相符；PEFT 当前链接是可变的 main 文档 | 这里只确认引用，不声称复现或完整代码审计；训练时固定具体软件版本 |

提案中的 ToolSandbox 只有名称、没有独立参考链接；本轮未完成其来源核验。上述论文仅核验指定页面/摘要及必要方法段，均未复现实验。

## 2. Qwen 接入需要锁定的事实

官方 [模型卡](https://huggingface.co/Qwen/Qwen3.5-0.8B) 提供非思考模式（0.8B 默认）、工具调用及 Qwen-Agent 示例。当前文本非思考建议：`temperature=1.0, top_p=1.0, top_k=20, min_p=0.0, presence_penalty=2.0, repetition_penalty=1.0`。早先基准说明段参数不同；本项目必须记录所采用段落、后端是否真正支持/应用参数，不能混用。

卡片给出 SGLang/vLLM 的 `qwen3_coder` 工具解析配置；Qwen-Agent 示例则要求原始 API、不叠加后端响应解析。两条路径择一验证，避免双重解析。思考循环警告不等于非思考工具循环已经成立。卡片建议的 main/nightly 安装不是本地兼容性证明，也不是安装授权。

由 [HF 模型 API](https://huggingface.co/api/models/Qwen/Qwen3.5-0.8B) 读得 revision：`2fc06364715b967f1860aea9cf38778875588b17`，`lastModified=2026-03-02T11:26:58.000Z`。本轮未下载权重；后续必须将 revision、tokenizer、chat template、量化/精度及框架版本写入 run。

## 3. AntiDoom 版本与 FTPO 对照边界

观察到可引用 [commit bd6a126476e18554b0cacaea3fd9f258fdde1f97](https://github.com/Liquid4All/antidoom/commit/bd6a126476e18554b0cacaea3fd9f258fdde1f97)。提交页是缓存结果；GitHub API 当前请求失败，因此这是**已观察的固定版本**，不是实时最新版本保证。

[pyproject.toml](https://github.com/Liquid4All/antidoom/blob/main/pyproject.toml) 声明 Python ≥3.12，并依赖 torch、vLLM、PEFT、TRL 等；依赖下限不保证新模型兼容。未克隆、安装或执行 upstream。README 的经验早停建议与博客并非唯一固定阈值，不应当作目标模型的已验证最优参数。

以下为本项目的设计推论，不是引用文献的实验结果：
- B4 必须在真实 tokenizer 上记录 chosen/rejected 的分歧位置；若工具标记相同而参数后部不同，不得直接压低通用工具起始 token。
- 单 token 正例不能只凭“读起来合理”入库；需在同一快照验证它对应的完整候选行动可执行且不偷看隐藏标签。
- 若只实施单 token DPO，而未实现多正例和 logit 约束，应明确标记“FTPO 风格简化对照”，不能称完整 AntiDoom 复现。
- 不把 upstream 的低温挖掘配置直接移作 B0 公平基线；先按目标模型合理配置观察真实失败，再预注册温度敏感性对照。

## 4. 下一次真实实验：有边界的接入预检

以下仅为计划；当前没有 B0 结果、训练数据集或修复权重。

1. 获得用户同意的本地现有模型/服务，或批准隔离环境与必要下载；不擅自租 GPU、购买调用或使用未知端点。
2. 固定上节 revision 和单一路径的工具协议；先使用纯文本模拟工具。记录完整原始请求/响应、解析后动作与工具结果，排除解析及历史丢失。
3. 先跑少量正常完成、永久错误、暂时错误、轮询、澄清和终止案例，检查预算/结果断言。此批是开发接入检查，不计入封闭测试。
4. 接入通过后扩至提案约 200 个开发案例，比较 B0/B1/B2。严格配对相同任务、采样设置和预算；只在开发集选策略，不提前访问正式测试。
5. 循环检出/未检出均取样复核，保留合理重试反例；先冻结检测规则，再采集可训练决策点。

门禁：若正常工具协议尚不可靠、实际推理环境未确认、主要失败来自框架，或合理设置下目标循环稀少，则不进入训练阶段。24–48GB 是提案资源建议，不是实测需求；推理/训练可行性需分别测试。

## 5. 可支持与不可支持的主张

- 可支持：提案所列核心相关工作有可访问的一手来源；“错误现场、执行验证、合法重试保持”的对照路线具有可检验性。
- 尚不可支持：算法新颖性已成立、目标模型确有循环缺陷、FTPO/行动偏好训练有效、本机可完成训练、提案目标已达到。
- 可能的贡献是失败样本及评测设计和受控比较；是否有研究增量，须以与强提示词、guard、SFT、一般动作 DPO 的结果区分。

## 6. 访问与失败记录

- 父线程 Jina Reader 请求 DNS 失败；本 agent 的 `gh repo view ...` 因 `gh` 不存在失败。没有安装 CLI。
- 可用 web 只读工具成功读取上述页面、模型 API 和 commit 页面；GitHub commits API 不可访问。
- 没有使用社交讨论作为技术事实来源，没有下载或重发布全文。
- `agent-reach check-update` 返回本地 v1.5.0，但更新查询 DNS 失败（工具自行重试 3 次）；没有更新环境。
