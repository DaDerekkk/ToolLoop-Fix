# 真实模型记录契约 v3：实现与验证

日期：2026-11-16（Asia/Shanghai）；owner：protocol agent。

## 范围与决定
在用户明确授权依赖安装、权重下载和真实运行后，为 `model_inference` 增加专用记录门禁。没有安装依赖、下载模型、调用模型或改动模拟器/模型 runner。修改范围仅为两份记录脚本、新增标准库测试及本 review/handoff。

**决定：记录契约可进入联调；不代表真实模型接入已经通过。** 正式运行由 root 使用新 wrapper，模型本体与实际软件版本的采集/核验由 simulator 编写的 runner 执行。

## 与 root / simulator 对齐的配置
```json
{
  "evidence_kind": "model_inference",
  "evidence_class": "model_inference",
  "model": {
    "model_id": "Qwen/Qwen3.5-0.8B",
    "revision": "固定的40位commit",
    "backend": "transformers",
    "model_path": "绝对模型目录",
    "device": "cpu",
    "dtype": "float32"
  },
  "sampling": {
    "do_sample": true, "temperature": 1, "top_p": 1,
    "top_k": 20, "max_new_tokens": 256, "seed": 0,
    "enable_thinking": false
  },
  "cases_path": "data/dev/pilot_cases.jsonl",
  "external_inputs": [
    {"name": "model_manifest", "path": "绝对JSON清单路径", "sha256": "该清单SHA256"}
  ]
}
```
示例中的中文占位值不能直接运行。实际配置可附加 min_p、presence_penalty、repetition_penalty 等参数，但最终 provenance.sampling 必须完整匹配冻结配置；runner 另记实际生效参数。

- 已出现的 evidence_kind/evidence_class 均须匹配 wrapper `--kind`；analysis/synthetic 原有无标签配置仍兼容，model 至少要有一种明确标签。
- model 的六个身份/运行字段必填，revision 必须 pinned commit，model_path 必须绝对；sampling 的上述核心字段必填并检查类型/范围，拒绝 NaN/Infinity。
- model 的 cases_path 必须在 frozen inputs 清单中，不能偷偷使用未归档的 live 数据文件。
- external_inputs 接收多个唯一安全命名的 JSON 引用，必须恰好有一份 `model_manifest`；可另加 `runtime_install_manifest` 保留安装清单。每份 ≤2 MiB，归档内容与 SHA，不复制或解引用 GB 级权重，不克隆安装环境。
- model manifest 形状：model_id / revision / model_path 与配置一致；files 为非空 `{path, sha256, size_bytes}` 列表。相对文件路径不可逃逸模型目录，且不可重复。实际权重字节在 runner 加载前核验，而非离线 validator 每次读取 GB 文件。

## 模型输出契约
必须有：`model_provenance.json`、`generations.jsonl`、`trajectories.jsonl`、`case_results.jsonl`、`metrics.json`。JSONL 必须非空，逐行以及 metrics/provenance 的 evidence_class 均为 model_inference。

provenance 必填：
- model / sampling：与冻结配置完全一致；device/dtype 使用显式实际值，不靠 `auto` 隐藏差异。
- runtime：`executable`、`python_version`、非空 packages 版本映射；transformers 后端至少包括 torch 与 transformers。版本须由**执行模型的解释器**获取，不用系统 Python 的包版本代替。
- `model_manifest_sha256`、`model_files_verified=true`、`chat_template_sha256`。true 只能在 runner 实际校验文件后写入。

wrapper 保存 child_executable，validator 对比 provenance 解释器与实际命令首项。正式模型命令应直接使用目标 Python，不套未登记的 shell 中转。

## 状态与兼容性
- 新 run schema_version=3；旧 v1/v2 原样保留，按其历史规则校验，未回写旧 manifest。
- 进程 exit0 但缺模型产物/身份不一致：仍如实记录 process `completed`、child exit0，但 evidence_valid=false，contract_errors 保存原因，wrapper 返回2。不能以 kind 标签证明模型运行。
- child 非零失败：保留失败记录与日志，不要求完整成功产物；不补造 generations 或 metrics。
- frozen config SHA 同时对应 config.json 与 inputs 中原始配置副本；小模型清单也有独立存档和哈希。外部清单在运行中改变使证据无效。
- 离线归档审计不要求 live 权重仍在原机器；依赖已存档 manifest 和当时 runner 核验声明。清单不是签名或密码学远程证明。

## 实际验证
```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_records*.py' -v
python3 scripts/validate_records.py
```
- 定向检查依次为20项、24项通过；补充多外部清单和安全命名测试后：**26 tests passed**。
- 现有 **9 runs checked，0 errors**，包括历史失败与旧 review 记录；未修改这些 run。
- 新测试使用临时目录、微型占位文件和明确的 fake runner / `fixture-only` 包版本；**没有执行真实模型**。它们只证明结构/完整性门禁可工作，不是软件版本真实存在的证据。
- root 后续应把 combined tests 通过正式 analysis run 归档，再运行真实接入预检；本地定向测试结果不能替代其模型执行回执。

## 留待真实接入核对的边界
1. metadata 检查不是防恶意代码的沙箱、软件包签名或不可伪造证明；信任项目 runner 与清单。代码仍可能显式读取未登记外部资源，新增数据/模型依赖需扩展清单。
2. 本契约验证产物存在、JSON 可读、身份与证据类别一致；不验证生成内容确由某硬件计算，也不替代任务成功断言、逐例配对或研究统计审查。
3. 参数执行是否符合框架版本、chat template 的实际内容、文件全量校验和 tokenizer/model 加载仍必须通过真实 runner 接入试验；任何失败保留并分类，不将失败当模型缺陷。
4. model_training 仍只有一般记录类型，不具备训练专用产物契约；本轮不据此宣称可训练或已训练。
