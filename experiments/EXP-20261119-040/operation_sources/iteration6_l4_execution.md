# ITER-006：Colab L4 执行链／结果回收独立复核

2026-11-19（Asia/Shanghai）；审查者 runtime6。范围：本地已安装 google-colab-cli 0.8.0 源码、已冻结公开探针和回传工具、本次控制器。不读 CLI 历史或凭据，不联系云、不运行模型、不训练。此文件本身不是 L4 推理或费用证据。

## 运行前：能证明和不能证明什么

- `colab_cli/commands/execution.py:118–139,190–207,225–227,266–282`：普通 `-f .py` 和管道 stdin 是**内核代码单元**，不是带用户 argv 的远端脚本进程；工作目录主动设为 `/content`。不要依赖 notebook 的 `__name__`、`__file__`、`sys.argv` 或 `sys.exit` 传结果。更稳妥的是显式传参数到函数；若需与已加载模块隔离，用同 VM 的 `sys.executable` 建全新 Python 子进程。将冻结源码装在独立命名空间可避免同名 `main` 被覆盖。
- 本机 CLI 把 kernel 的 `stream:stdout` 原样写 stdout（**没有行前缀**），stderr 单独写入，不要将二者合并；普通字符串表达式是 display data，不等同 stdout，请显式 `print(...,flush=True)`。kernel 单元发生异常时 `runtime.py:259–275` 能把错误作为 output 返回，CLI 退出码 0 **不证明**成功。CLI 自身可能留存 code/outputs：只传审过的公开探针与公开输出，Base64 不是加密。
- `commands/automation.py:271–286` 的安装回执代表安装工具返回，不保证新包已被当前 kernel 正确 import。预检目标解释器、库版本、`Qwen3_5ForCausalLM` 可导入及 torch CUDA/L4/BF16；同 VM 主动读回证明，而不是推定 torch/driver 可以工作。固定 revision、9 个文件哈希、`token=False`、`trust_remote_code=False` 保持不变，禁用隐式凭据/遥测；网络异常只写 stage／类名（如确需定位，可另取白名单模块名或数字 HTTP status）。不导出完整异常或重定向 URL。
- 证明执行前先用独立目录/nonce 的公开小回执验证完整 stdout 路径；禁止污染真实单次输出的独占路径。真结果传输失败，只在同一台存活实例读取已落盘的同一份字节，验证 nonce、大小、SHA、字节一致和 schema；传输验证不能替代 tokenizer 重编码/解码及工具参数核验。保护墙钟／stop 按本次名称，服务端独立复查既有分配。原 ITER-005 的失败阶段只能定位到客户端取回超时，不能倒推是 HF、生成或路径问题。

本地验证：用 CLI 自带解释器导入 `commands.execution.display_output`，在不同 `__name__` 命名空间加载两份源码；临时目录显式传入抛出 ValueError 的假 probe，取得退出 1 的公开失败 JSON。把带帧 stdout 分两段、额外投送一条 stderr 通过真实 display_output，收端校对 stdout 与原始帧完全一致、stderr 独立、落盘字节与源文件相同；无模型、无网络。仅证实本机库分流和帧解析，不证实平台连接、CUDA、模型生成或费用。

## 本次控制器静态核验

被审文件 `operations/COLAB-20261119-002/lifecycle.py` SHA256 `9976b8c749500fd33b66529b4ff3369670367ab0db1cdd6dc1de3f648c28b0ac`。

- `INIT` 独占随机路径，分开 sentinel 目录与真实输出路径、独立 nonce；主机侧需核验 sentinel 原字节。用 `runpy.run_path(...,run_name='review_only')`（不调用 `go`）本地构造四段实际代码，`compile` INIT/DRIVER/RUN/READ 全部通过，仅把 INIT 目标换到临时目录执行，回传解码与参照字节一致。**未执行 RUN 或访问 Colab。**
- `RUN` 启动一次新解释器调用冻结的 probe；在远端独占落盘后同一内核发 stdout frame。第一次载荷不完整只调用 `READ`，不启动新模型。原始日志不带回；可用白名单异常代码和收到的 JSON 定位阶段，但成功与否依据 JSON 和本地 tokenizer/提案复核。特别地，DRIVER 未用返回值设置进程退出码，因此成功写下**失败回执**时也可能出现 `child_exit=0`。
- 分配前留存 before 指纹，分配后本次名称+新端点独特性/L4 与费用检查；停止前重新检查当前指纹及名称，然后专名 stop、比对最终集合/计数。它将“己方已释放”和“最初集合完全相同”作为**两个不同字段**，确认原分配未变必须单读后者。首次查询、GIL/网络可能失败，墙钟目标不是计费上限；未知时须保留未核实状态。脚本在已获失败回执时会按设计立即清理，无隐式训练或同配置多次采样。
