# ITER-005｜云端小回执传输复核

**类型：** 离线源码／操作记录复核（analysis）；不是第二次推理。**结论：** COLAB-20261119-001 的原始推理 JSON 无法从本轮获准检查的本地证据还原，也不能证明生成成功或失败。不要把下一轮同种子输出补到本轮。

### 证据与限制

- `operations/COLAB-20261119-001/lifecycle.jsonl:13–22`：exec 客户端退出 0，download 触发 30 秒客户端墙钟超时；随后出现终止确认、目标 assignment 消失、原有 assignments 保留。本地操作目录只有 lifecycle 程序和事件，无 `toolloop_probe.json`。不知道超时起因；不把路径错误、网络阻塞、缺失文件中的任何一种当成定论。
- 冻结 probe（`experiments/EXP-20261119-034/inputs/scripts/colab_inference_probe.py:132–152`）向 VM 的独占文件写 JSON，但 stdout 只打印 status/stage，之后调用 `sys.exit(main())`。调用端 `lifecycle.py:26–37,79–85` 又只留退出码与白名单 reason，未保存这段 stdout。没有模型原文、token ID、文件长度或摘要。静态模板／本机旧实验不能补齐它们。
- 本机 google-colab-cli 0.8.0：`colab_cli/commands/execution.py:118–139,266–282` 将 kernel 的 stream 分流到本地 stdout/stderr，`colab_cli/runtime.py:259–275` 可将单元异常作为 output 返回，并非所有 kernel 出错都变成 CLI 非零退出。故退出 0 不等于 probe 成功。按源码执行事件可能写 CLI 历史，但未读取任何账号态文件／原始 CLI 历史；旧 probe 本来就没有打印完整文件。
- `colab_cli/contents.py:30–46,75–93` 使用独立的 Contents API：去除远端路径开头的斜杠，HTTP 调用未设请求超时，收到响应才写本地；若响应为 JSON 格式，CLI 会重新序列化而非保证原字节不变。仅据此不能推断 Contents 根目录与 VM 绝对路径的映射，也不能据本地文件不存在判断 VM 内无文件。

### 下一次：一次生成，两条**读取同一份字节**的取证路径

1. 另开新实验编号；先用极短假文件测试 stdout 传输，而不是用再次生成为传输测试。为会话约定唯一 run ID 和回执路径，只传可公开的合成输入／输出和允许的运行环境字段。模型单次执行结束（失败也同样）后，先关闭回执文件，然后以其**原始字节**打印一行：`TLFIX_RECEIPT_V1 <run_id> <byte_count> <sha256> <base64>`，flush。受控 runner 打印，不能将不受控模型原文直接拼进日志。Notebook 内无须用 `sys.exit` 向上游传达成功。
2. 本地 stdout/stderr 分开，**只从 stdout** 按完整行抽取唯一预期 marker；严格 Base64、大小上限（如 64 KiB 原文）、run ID、长度和 SHA256，全部核准再将原字节独占写入新目录；不留携带签名 URL 等风险的整段客户端日志。再做语义校验：失败回执仅作为失败证据；完成回执核对模型 revision、9 文件哈希、L4/CUDA/dtype、公共 prompt 及 token 数、raw_text、终止字段、训练/工具执行均为 false。模型是否真的提出正确调用另判。传输校验不是实验验收。
3. 若第一通道缺失/损坏且仍有预算，只在**同一活会话**另用一次短 `colab exec` **读已知回执路径**并打印同样 marker（不导入 model、不调用 generate、不再上传工程目录）。如要测 download，用无隐私 sentinel 先校验 Contents 相对路径；或传一个自带长度/摘要的 ASCII 封装，再按原始载荷检验。保留外层墙钟上限；任何回执未验过均不得宣称模型成功。无论哪一步失败都先释放己方实例并确认；被释放后不得为“修复原记录”重新分配。

**本次检查：** 定向 `nl`/`rg` 核读上述源文件；`find` 核对操作目录；`shasum -a 256` 确认工作区和冻结 probe 均为 `99172529772f33e31d955c9f5dacfee448aa6f15326b3c030c6dbf3c8832401b`。未读 CLI 原始历史／认证数据、未联系云、未改已结束记录、未执行推理或训练。上述传输设计尚未云验收。
