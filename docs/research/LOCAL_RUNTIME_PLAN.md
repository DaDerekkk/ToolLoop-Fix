# 本地 Qwen3.5-0.8B 运行兼容性核验

- 日期：2026-11-16，Asia/Shanghai；owner：`/root/source_audit`。
- 用户已授权继续安装、下载及真实实验；本 agent 仅研究与交接，实际执行由协调者负责。
- 结论是**最少转换、较容易核验的起步路线**，不是测出的最快运行时。没有据此声称本机模型推理成功。

## 1. 建议首选：隔离 Python 3.12 + Transformers / PyTorch

| 组件 | 本次一手核验 | 选择 |
|---|---|---|
| [Transformers PyPI](https://pypi.org/project/transformers/5.23.0/) | 5.23.0，2026-11-13；Python ≥3.11.0 | 固定 `transformers==5.23.0`，不是浮动 main/nightly |
| [torch PyPI](https://pypi.org/project/torch/2.15.0/) | 2.15.0，2026-10-28；Python ≥3.11；有 cp312/macOS 14.0+/arm64 wheel | 固定 `torch==2.15.0`，不安装 CUDA/vLLM |
| Python | 现有 3.9 不满足上述版本要求 | 使用已具备的 uv 创建独立 3.12 环境；不替换系统 Python |

建议安装范围仅 torch、transformers 及解析出的必要传递依赖。纯文本、显式 `.to(device)` 无需先引入 accelerate、torchvision、PEFT、TRL、FlashAttention 或 causal-conv1d。安装完成后记录全部实际 resolved 版本、wheel 来源、Python 路径和运行平台；以上是候选固定版本，不是已经完成的安装清单。

## 2. 官方权重及类选择

- 仓库：`Qwen/Qwen3.5-0.8B`；revision：`2fc06364715b967f1860aea9cf38778875588b17`，此前由 [HF API](https://huggingface.co/api/models/Qwen/Qwen3.5-0.8B) 核验。
- [固定版本文件列表](https://huggingface.co/Qwen/Qwen3.5-0.8B/tree/2fc06364715b967f1860aea9cf38778875588b17) 显示完整仓库约 **1.77 GB**，单个未量化 safetensors 权重；没有 `generation_config.json`。
- [权重原始 pointer](https://huggingface.co/Qwen/Qwen3.5-0.8B/raw/2fc06364715b967f1860aea9cf38778875588b17/model.safetensors-00001-of-00001.safetensors) 给出精确文件大小 **1,746,942,600 bytes**，SHA256 `04b1c301231dd422b8860db31311ab2721511346a32cb1e079c4c4e5f1fe4696`。本次只读取小型 pointer，没有下载权重。
- [safetensors index](https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/2fc06364715b967f1860aea9cf38778875588b17/model.safetensors.index.json) 的 `total_size=1,746,882,752` 是 tensor payload，不是完整文件字节数。全部选定下载文件总字节尚未取得精确值，不能拿 UI 四舍五入值充当下载验收；执行者须汇总实际文件清单。

固定 [config](https://huggingface.co/Qwen/Qwen3.5-0.8B/raw/2fc06364715b967f1860aea9cf38778875588b17/config.json) 架构是 `Qwen3_5ForConditionalGeneration`，text_config 为 `qwen3_5_text`。但 [官方 Transformers 文档](https://huggingface.co/docs/transformers/model_doc/qwen3_5) 明确提供 `AutoTokenizer + Qwen3_5ForCausalLM.from_pretrained` 的纯文本用法。5.23.0 的 [类实现](https://raw.githubusercontent.com/huggingface/transformers/v5.23.0/src/transformers/models/qwen3_5/modeling_qwen3_5.py) 和 [权重转换映射](https://raw.githubusercontent.com/huggingface/transformers/v5.23.0/src/transformers/conversion_mapping.py) 支持忽略视觉分支并处理语言层前缀，无需自行删权重键或修改官方 config。

实现原则：明确使用该内置 causal 类，`trust_remote_code=False`、`use_safetensors=True`；本地固定目录加载时 `local_files_only=True`。先记录 loading diagnostics，遇到不合理 missing/mismatched language weights 不继续计入 B0。原始 BF16 权重可以加载成 FP32 计算，仍未量化，但必须标注实际计算精度而非声称等同 BF16 服务端数值。

## 3. CPU / MPS 先后顺序

建议先 batch=1、小上下文、短输出进行 CPU/FP32 一次协议探针，再在 MPS 可用时做配对设备探针，避免一开始铺开 200 个任务。

- 设 `attn_implementation="eager"`，不启用 `use_kernels=True`。上述 Qwen 实现含纯 torch 卷积及 Gated Delta fallback；这说明初始可不安装 CUDA kernel 包，但不证明每个 MPS 运算已支持。
- [PyTorch 2.15 MPS 说明](https://docs.pytorch.org/docs/2.15/notes/mps.html) 要求运行时检查 `torch.backends.mps.is_available()`，并在说明中列 macOS 15+；wheel 的 macOS 14+ 标签不等于 MPS 可用性。MPS 不支持 float64。
- 若启用 [PYTORCH_ENABLE_MPS_FALLBACK=1](https://docs.pytorch.org/docs/2.15/mps_environment_variables.html)，必须在导入 torch 前设置并记录，表示不支持算子可回退 CPU，不是纯 GPU 测速。不要关闭内存水位保护。
- MPS 探针失败时保留错误记录，退 CPU 是新的配置/run；不能静默混为同一后端结果。可先用 FP32 保守检查，再单独验证 BF16/FP16；NaN、EOS 不正常或 loading mismatch 必须停下排查。

## 4. 工具协议及停止 token：已向实现 agent 交接

固定 [chat_template.jinja](https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/2fc06364715b967f1860aea9cf38778875588b17/chat_template.jinja) 使用 Qwen3-Coder 风格工具输出，而不是把普通 JSON 当模型原生工具格式：

```text
<tool_call>
<function=lookup_order>
<parameter=order_id>
O-001
</parameter>
</function>
</tool_call>
```

- tools 传入函数 schema 列表；assistant 历史可用 `tool_calls=[{"type":"function","function":{"name":"lookup_order","arguments":{"order_id":"O-001"}}}]`。arguments 是映射，模板遍历 `.items`，不是 JSON 字符串。
- 工具结果使用 role=`tool`；模板自行产生 tool_response 包装。不要手动再包装或同时进行两次 parser 转换。
- `enable_thinking=False, add_generation_prompt=True`；历史与错误结果完整保留，raw 生成及解析错误均存档。

**重要差异**：[tokenizer_config](https://huggingface.co/Qwen/Qwen3.5-0.8B/blob/2fc06364715b967f1860aea9cf38778875588b17/tokenizer_config.json) 中 `<|im_end|>` 是 **248046**（tokenizer EOS），`<|endoftext|>` 是 **248044**（PAD）；模型 text_config EOS 却是 248044。工具起止 token 是 248058/248059，均非 special token。应读取本地 tokenizer/model generation config，记录有效停止 ID，通常取两种已确认终止 ID 的并集，防止模型继续生成下一轮。不要把工具闭合 token 当通用 EOS。

核验过程中曾将 config 的 248044 误注为 im_end；在取得 pinned tokenizer 后已立即向协调者与 simulator 更正，本文以该 tokenizer 证据为准。

## 5. 采样参数不能只写入配置、却没有生效

[Qwen 模型卡](https://huggingface.co/Qwen/Qwen3.5-0.8B) 的文本非思考建议为：temperature=1.0、top_p=1.0、top_k=20、min_p=0.0、presence_penalty=2.0、repetition_penalty=1.0。采用 sampling，不把 greedy 当主 B0。

5.23.0 [logits_process.py](https://raw.githubusercontent.com/huggingface/transformers/v5.23.0/src/transformers/generation/logits_process.py) 没有同名 PresencePenalty 处理器；不要给 generate 一个不支持的参数后声称它生效。[vLLM 参数源码](https://raw.githubusercontent.com/vllm-project/vllm/main/vllm/sampling_params.py) 区分：presence 只作用于本次已生成文本，而 repetition 可包括 prompt。

本地兼容实现建议：每次生成新建 processor，仅对 `input_ids[:, prompt_length:]` 已出现的 unique token 的 logits 减 2.0 一次，不随次数累加，不惩罚整段用户/工具历史；频率惩罚保持 0。用小张量测试未出现 token 不变、出现 1 次与 3 次同罚、prompt-only token 不变。记录 processor 代码/hash及顺序；这是文档语义兼容实现，尚未做跨运行时 logits 等价实测。

## 6. MLX 备选，不抢占首轮基线

[MLX LM](https://github.com/ml-explore/mlx-lm) 面向 Apple Silicon；[PyPI](https://pypi.org/project/mlx-lm/) 当前 0.32.2、Python ≥3.11。官方 [Qwen3.5 实现](https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/models/qwen3_5.py) 可读取语言子配置并移除视觉键；[convert](https://raw.githubusercontent.com/ml-explore/mlx-lm/main/mlx_lm/convert.py) 提供 revision、`quantize=False`、`trust_remote_code=False`，支持保持 BF16 的本地转换。

若 Transformers MPS 无法可靠运行或显著慢，再固定 MLX 版本、从**同一官方 revision**本地转换为未量化 BF16，并做 tokenizer/数值/任务探针对照。不要直接把社区 4bit 模型当 B0；不上传。MLX 源码本轮阅读的是 main，并非已证明与 PyPI 0.32.2 完全一致，切换前需核验所装版本。没有本机速度数据，不能宣称它在本任务一定更快。

## 7. 交接验收

下载完成 ≠ 加载成功 ≠ 模型会用工具。依次保留：版本/文件大小及哈希清单 → tokenizer/template 渲染探针 → loading diagnostics → 短生成及终止 → 单工具正常任务 → 失败历史续跑 → 扩展 B0/B1。任何阶段失败都留档；不因“已经允许安装”而跳过接入门禁。
