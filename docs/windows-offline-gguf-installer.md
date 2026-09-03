# Windows 11 全 GGUF E/R 离线安装器

> 当前状态：`IN_PROGRESS / experimental_unverified`。运行时、模型供应链、后端
> Provider、真实 GGUF 语义/API、完整 ZIP/Setup 构建，以及当前 Win11 的首装、覆盖升级、
> 孤儿备份恢复和卸载边界均已通过。仍需项目代码签名、独立 clean Win11 矩阵、上游模型
> 等价 Golden 和正式 Release 门禁。不要把当前构建称为正式发布版。

## 1. 目标与版本边界

这个版本面向希望在一台 Win11 x64 电脑上双击安装、且不想单独配置 Python、Node、
Torch、Embedding 服务或 Reranker 服务的用户。安装包计划内置：

- 自包含 FastAPI/Vue/SQLite Core；
- 固定 `llama.cpp b10729` CPU 运行时；
- 微软官方固定 VSIX 中的 VC143 x64 release CRT，以 app-local 方式随运行时分发；
- `BAAI/bge-base-zh-v1.5` 自转换 F16 GGUF Embedding；
- `Qwen/Qwen3-Reranker-0.6B` Q8_0 GGUF Reranker；
- 可在案例列表一键导入的 AP 频繁离线纯合成演示（两份日志、三级筛查、逐行跳转、
  综合诊断和报告快照）；其中筛查计划、两轮综合规划、工具轨迹、Token/耗时和结论来自此前
  通过 `wawapii.com` 成功完成的真实 GLM-5.2 运行，导入过程不再次调用模型；
- 模型许可证、组件锁、完整文件哈希和来源证明。

该版本不包含本地 Chat 模型。日志 LLM Planning、综合诊断、案例问答和知识提炼仍需
用户配置远程 OpenAI-compatible Chat Profile。Embedding 和 Reranker 只负责 Dense
检索与候选重排，不能代替生成模型。

稳定的 [Core 便携包](windows-portable-deployment.md) 仍然保留：它不含模型权重，默认
使用 Hashing Embedding 并关闭 Reranker。全 GGUF 版是独立交付形态，不会把 Torch 或
Sentence Transformers 再装进平台 Python 进程。

## 2. 运行架构

安装后仍由 `start.bat` 作为统一入口。启动器先读取受包清单保护的
`model-components.json`，再按以下顺序运行：

1. 在两个动态 `127.0.0.1` 端口分别启动 Embedding 和 Reranker `llama-server`；
2. 每次启动生成新的随机 Bearer Token，并通过只存在于本次运行期的 key 文件传给
   sidecar，令牌不写入模型 Profile 数据库，也不出现在命令行；
3. 等待两个 `/health` 端点后，才向 FastAPI 进程注入
   `BUNDLED_GGUF_EMBEDDING_URL`、`BUNDLED_GGUF_RERANKER_URL` 和
   `BUNDLED_GGUF_API_KEY`；
4. 首次安装且用户没有自定义选择时，平台自动启用受管 GGUF Profile；已有明确的
   API/本地选择不会被启动器抢占；
5. 单个 sidecar 启动失败时只回退对应能力：Embedding 回退 Hashing，Reranker 回退
   Disabled，Core 和日志解析仍可启动；
6. 正常关闭时终止 sidecar 并删除 key 文件；Win11 Job Object 尽可能保证启动器异常
   退出时子进程也不会残留。

公司代理不会截获本机 E/R 通信：启动器的 loopback 健康检查使用禁代理连接，传给
sidecar 的环境保留原有代理设置并给 `NO_PROXY/no_proxy` 补齐
`127.0.0.1,localhost`；后端只对安装器受管 Profile 强制 `trust_env=False`，用户配置的
外部 Chat/Embedding/Reranker Profile 仍按其自身代理设置运行。

sidecar 日志和不含正文/令牌的状态文件写入：

```text
%LOCALAPPDATA%\GWAPDebugPlatform\data\logs\local-models\
```

可临时绕过本地检索组件启动 Core：

```bat
start.bat --no-local-retrieval
```

## 3. 安装与产物

2026-09-01 已生成并验证两种包含相同组件的实验产物：

- `GWAP-Debug-Platform-Setup-<version>-x64.exe`：按用户安装，无需管理员权限；
- `debug-platform-offline-gguf-<version>-windows-x64.zip`：解压后双击
  `Install.bat`，适用于不允许运行安装器的环境。

应用文件安装到：

```text
%LOCALAPPDATA%\Programs\GWAPDebugPlatform
```

案例、数据库、上传文件、设置和加密凭据继续位于：

```text
%LOCALAPPDATA%\GWAPDebugPlatform
```

因此升级应用不会覆盖业务数据。Setup 不再逐文件合并到 `{app}`：它先把完整 payload
释放到自己的 `%TEMP%\GWAPDebugPlatformPayload`，最后释放
`package-manifest.json`，再在 `[Files]` 阶段调用与 ZIP 共用的 `install_local.ps1`。
脚本先复制到同级 staging、运行完整清单自检，然后用目录改名切换；已有版本在成功前保留
为 backup。这样新版本已经删除的旧文件不会残留，发布失败也会恢复旧应用树。

发布操作还使用 Win11/PowerShell 5.1 可用的命名互斥体串行化多个 Setup/ZIP 安装进程；
默认最多等待 120 秒，超时明确失败，进程崩溃时 Windows 自动释放锁。拿锁后若 app tree
缺失且同级仅存在一个符合 `GWAPDebugPlatform.backup-<32 hex>` 的备份，脚本会先核对其
manifest 结构、启动器、模型组件描述和 Python runtime，再用目录改名恢复；存在多个候选或
唯一候选不完整时拒绝猜测并保持现场不变。

Setup 调用时使用 `-NoLaunch -NoShortcuts`，并等待 PowerShell 结束；无法启动或非零退出
会通过 Inno `RaiseException` 使安装失败。快捷方式和卸载登记仅由 Inno 管理。安装器自身
的卸载文件位于独立目录，不会进入受清单保护的应用树；卸载器只删除精确的
`%LOCALAPPDATA%\Programs\GWAPDebugPlatform`，不会删除业务数据根。

当前权重约为 BGE 195.3 MiB、Qwen 609.5 MiB，另有 Core 与 CPU 运行时。本次完整构建的
ZIP 为 937,032,215 bytes（893.62 MiB），Setup 为 902,853,773 bytes（861.03 MiB）。
项目 Setup 仍未签名；代码签名会再次改变最终安装器哈希，正式发布时必须重新记录。

## 4. 受控构建

模型权重和生成安装包均被 Git 忽略。构建输入以
`scripts/model-runtime/model-assets.json` 为唯一固定清单，记录上游 commit、文件大小、
SHA-256、许可证和转换命令。准备缓存需要 Python 3.12：

```bat
python scripts\model-runtime\prepare_assets.py --all
scripts\build_windows_gguf_installer.bat -ValidateCacheOnly
scripts\build_windows_gguf_installer.bat -Version 0.1.0
```

准备脚本会下载并校验固定 llama.cpp/模型/许可证，使用固定 llama.cpp converter 生成
BGE F16 GGUF，然后生成 `components.lock.json`。安装器构建会再次把组件锁绑定到仓库清单，
逐文件复核大小与 SHA-256，禁止遗漏 CPU runtime DLL，并生成：

- 离线 ZIP 与 SHA-256；
- Inno Setup 单文件安装器与 SHA-256；
- component provenance 与 SHA-256；
- 包内完整文件清单。

VC143 CRT 固定输入为微软不可变 `base.vsix`；清单锁定实际下载大小、SHA-256、
10 个 release DLL 的路径/大小/哈希和 Microsoft 签名者。解压只接受精确白名单并拒绝
`debug_nonredist`，构建时逐 DLL 验证 Authenticode；真实 sidecar 冒烟还枚举加载模块，
强制 `MSVCP140.dll`、`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll` 来自包内
`runtime/llama`。其许可证记录为
`LicenseRef-Microsoft-Visual-Studio-2022-Redistributable`，不是开源 SPDX 许可，
发布者必须自行满足[微软 VS 2022 再分发条款](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution)。

构建电脑需要 Python 3.12、Node 22、Git 和 Inno Setup；目标电脑不需要这些工具。
模型准备脚本支持 `--hf-endpoint https://hf-mirror.com`，镜像只替换 Hugging Face origin，
资产路径、commit、大小和 SHA-256 仍由固定清单决定。

GitHub 的 `Windows Full GGUF Installer` 工作流只接受手工触发或已发布 Release 触发，
固定 Windows/Python/Node/Inno Setup 和所有 Action revision。手工触发可生成实验验证包；
Release 事件额外执行 `--strict-release`，在模型转换哈希或质量状态没有正式提升前会主动
失败，而不是发布一个未经验证的安装器。

## 5. 当前验证证据

2026-09-01 在当前 Win11 的 Git 忽略构建缓存上，仓库入口
`scripts/model-runtime/smoke_runtime.py` 使用固定 llama.cpp b10729 启动两个真实 sidecar，
得到：

| 检查 | 当前结果 |
| --- | --- |
| BGE HTTP 合同 | 返回 3 个向量，每个 768 维、全部有限且 L2 归一化 |
| BGE 最小语义检查 | 诊断相关文本相似度 `0.5609`，高于无关文本 `0.1655` |
| Qwen `/v1/rerank` | 诊断相关候选 `0.9997`，高于无关候选 `0.0001` |
| 网络与凭据 | 两个动态 loopback 端口、临时 key 文件、检查结束后进程和临时目录清理 |
| 组装后 Core | 完整 self-check 与临时全新 data root 启动通过 |
| 组装后受管 Profile | Embedding/Reranker 自动激活；平台 API 返回 `2 x 768` Embedding，Reranker 首项 `index=0` |
| Inno Setup 编译 | 固定 6.7.1 portable compiler 用当前原子发布源码在 183.922 秒内生成完整单文件候选 |
| 安装器签名 | 编译器下载器为 `Valid / Pyrsys B.V.`；项目 Setup.exe 本身为 `NotSigned`，两者不可混同 |
| ZIP 安装/升级 | 隔离目录首装、第二次原子升级、安装后真实 E/R/API 冒烟均通过 |
| 崩溃恢复 | 目标缺失且仅有一个有效 backup 的真实模拟已恢复旧树并继续升级，无 staging/backup 残留 |
| Setup 安装/升级/卸载 | 当前 Win11 默认用户目录的静默首装、既有覆盖升级、安装后 E/R/API 冒烟及精确卸载均通过；最新首装还验证了内置演示的三级筛查、跳转和诊断 |

2026-09-02 从当前源码重新执行组件锁、真实 GGUF 推理、完整包冒烟、ZIP 和 Setup 编译。
该版本还把此前经 `wawapii.com` 成功运行的 GLM-5.2 脱敏结果与两份合成日志固化入包；
安装时不再次调用模型，但会校验两侧 66 个筛查组/149 个逐行位置、两轮规划、27/27
故障树结论、321,453 Token/146,082 ms 使用记录、诊断报告和源行跳转。最终生成：

```text
debug-platform-offline-gguf-0.1.0-windows-x64.zip
937,032,215 bytes
SHA-256 3fd4ab0699ea8778f01816133f98b728391d04e064ceed9bed87e129200f028e

GWAP-Debug-Platform-Setup-0.1.0-x64.exe
902,853,773 bytes
SHA-256 d6736ce0d2943b378f253d92e4c75b643cdf2453802664e8dadeb3783d23ddaa

debug-platform-offline-gguf-0.1.0-windows-x64.provenance.json
26,631 bytes
SHA-256 1a97f0f5a229d3a395d72b4608bc77258299d93e241efb8fca7482d70f4fdf7f
```

本轮真实 Setup 已在 `%LOCALAPPDATA%\Programs\GWAPDebugPlatform` 静默首装，并从安装树再次
通过包完整性、真实 BGE/Qwen 推理、受管 Profile、前后端 API 和上述 GLM 演示校验；静默卸载
随后清除了应用树、卸载器与开始菜单项，未删除业务数据。项目 Setup 本身仍为 `NotSigned`。

下面的 934,065,370-byte ZIP 和 899,941,808-byte Setup 仅保留为原子发布修复前的历史证据。

同日在复用已有 `frontend/dist`、不构建 Setup.exe 的明确实验条件下运行：

```powershell
scripts/build_windows_gguf_installer.ps1 ... -Version 0.1.0 -SkipFrontendBuild -SkipSetupExe
```

生成的实验 ZIP 为 `934,065,370` bytes，SHA-256：

```text
3cbb5fd72f80bfeed429fd05abf2cdb252535abb266ca62df3839075fb5b15f6
```

该构建证明组装后的 Core、启动器、受管 Profile 和真实平台 E/R API 可以协同工作；
`-SkipFrontendBuild` 与 `-SkipSetupExe` 也意味着它不是一次 clean release build。

随后使用从不可变 GitHub Release 获取的 Inno Setup 6.7.1 portable compiler 完成单文件
编译。用于获取编译器的下载器 Authenticode 状态为 `Valid`，签名者为 `Pyrsys B.V.`；
编译耗时 213.469 秒。生成的
`GWAP-Debug-Platform-Setup-0.1.0-x64.exe` 为 899,941,808 bytes，SHA-256：

```text
08499a54aabc7bf7e7e4f0e5b0256b1b79d3d60126e70bcfd7341b62c669fb1a
```

Windows 文件元数据显示 `FileVersion=0.1.0`、
`ProductName=GWAP Debug Platform`。该项目安装器自身的 Authenticode 状态仍为
`NotSigned`；不能把 Inno 下载器的有效签名当作项目安装器的代码签名。

高风险升级复核随后确认该历史候选仍会逐项覆盖 `{app}`，旧版本中不再被新版本包含的文件可能
残留，并被新的 `package-manifest.json` 判定为意外文件。仓库源码已改为上文所述的临时
完整释放 + staging/backup 原子发布；固定 Inno 6.7.1 已成功编译小型合同 fixture，静态
合同同时检查 manifest 必须最后释放、PowerShell 必须等待、非零必须抛出、快捷方式职责
分离和卸载数据边界。当前完整候选已经按新源码重建，并通过上表所列的真实安装、升级、
孤儿备份恢复和卸载验证；旧候选哈希不代表当前产物。

该结果证明当前两个 GGUF 与固定 llama.cpp 能完成最小真实推理，不等于以下尚未完成的
发布门禁：

- 使用包内受管 Profile 完成知识全量索引和混合 RAG 的质量/故障注入验证；
- 对项目 `Setup.exe` 做发布代码签名并验证签名信任；
- 一台没有开发环境和缓存的全新 Win11 x64 电脑完成安装、冷启动、升级、回退和卸载；
- BGE GGUF 与固定上游模型的向量余弦/Recall 门槛；
- Qwen GGUF 与固定上游模型的排序/NDCG 门槛；
- 最终 BGE 构建哈希写入正式发布锁，`bundle_status` 从
  `experimental_unverified` 经审核提升；
- GitHub 手工 clean build 和正式 Release 工作流实际全绿。

在这些项目全部完成并写入 `VALIDATION.md` 前，全 GGUF 安装器只可作为实验验证包。

## 6. 故障定位

先运行不加载模型的包完整性检查：

```bat
start.bat --check
```

再运行会真实加载两个模型的检查：

```bat
start.bat --check --check-models
```

如果内存、CPU 指令集、安全软件或原生 DLL 导致某个模型失败，查看
`data\logs\local-models` 下对应日志。可用 `--no-local-retrieval` 启动 Core，随后继续使用
Hashing/Disabled，或在系统设置中切换到外部 Embedding/Reranker API。不要向平台主
Python 环境安装 Torch 来规避 sidecar 错误，这会重新引入本方案专门隔离的 DLL 风险。
