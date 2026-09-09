# Windows 11 便携部署与本地模型隔离

## 1. 推荐选择

普通使用电脑当前优先使用已经完成发布验证的 Windows x64 Core 便携包。该包包含：

- 64 位 CPython 运行时；
- FastAPI 后端及基础运行依赖；
- 已编译的 Vue 前端；
- SQLite 数据库迁移和内置知识；
- 固定合成 GW/AP 演示、脱敏运行快照及仅绑定该案例的公开诊断方法；
- 标准 `gw-ap-debug` Skill 及 Claude Code/Codex MCP 安装脚本；
- 双击启动、自检及真实 HTTP 冒烟入口。

目标电脑不需要安装 Python、Node.js、pip、npm 或 Docker，也不需要配置 pip/npm
代理。源码方式继续用于开发、修改代码和运行完整测试，不再作为普通使用电脑的首选部署方式。

| 方式 | 目标电脑依赖 | 适用场景 |
| --- | --- | --- |
| Windows Core 便携包 | 无额外开发运行时 | 当前稳定的 Win11 单机使用、内网交付、快速升级 |
| [全 GGUF E/R 离线安装器](windows-offline-gguf-installer.md) | 无额外开发运行时 | 内置 BGE/Qwen 检索模型；当前为实验验证包，尚未 release-ready |
| `scripts\start_local.bat` | Python 3.11+、Node 20.19+/22.12+、pip/npm 网络 | 源码开发与调试 |
| Docker Compose | Docker Desktop / WSL2 | PostgreSQL、Qdrant、多容器验证 |

两种无开发依赖的交付共享同一个 Core。Core 便携包始终排除模型权重；全 GGUF 版也不把
Torch/Sentence Transformers 装进平台 Python，而是把固定 GGUF 和独立 llama.cpp
sidecar 作为组件封装。后者的真实运行时语义冒烟和 Inno Setup 6.7.1 单文件编译已通过；
安装源码已经改为临时完整解包后复用 ZIP 的 staging+backup 原子发布，避免升级遗留旧文件。
当前完整候选已通过本机 ZIP/Setup 首装、覆盖升级、孤儿备份恢复、安装后双 GGUF/API 冒烟
与精确卸载；本地 sidecar 流量也不会继承公司代理。项目代码签名、独立全新 Win11 安装
矩阵和上游等价 Golden 门禁仍未完成。

## 2. 获取与启动

在 GitHub 的 `Windows Portable Package` 工作流中手工运行构建，下载名为
`debug-platform-windows-x64` 的 Artifact；版本标签 `v*` 会同时创建包含 ZIP 和
SHA-256 文件的 GitHub Release。

解压完整 ZIP 后，进入自动生成的 `debug-platform-windows-x64` 目录，再双击：

```bat
start.bat
```

默认只监听 `127.0.0.1:8080`，后端就绪后自动打开浏览器。可用参数：

```bat
start.bat --check
start.bat --no-browser
start.bat --port 18080
start.bat --data-root D:\DebugPlatformData
```

本轮 WebSkillMcp 0.2.0 重建开始同时提供 `start_codeagent.bat`：双击后使用包内 Python
启动后端和已选 E/R，再进入你原有 CodeAgent，不要求先开网页或导入用户级 Skill。
默认追加 `gw-ap-debug`，不屏蔽已有 MCP，也不改 `.cac`、CLI 模型或代理。
首次找不到程序时输入完整路径即可保存；具体组件选择与命令见
[全 GGUF 安装指南](windows-offline-gguf-installer.md)。旧下载包不自动获得新入口。

`/mcp` 与网页复用同一个 loopback 端口；启动器会按实际 `--port` 覆盖
`MCP_PUBLIC_BASE_URL`。例如 `--port 18080` 对应
`http://127.0.0.1:18080/mcp`，不会继续引用 `.env.example` 的默认端口。

`--check` 不访问外部网络，会检查包完整性、数据目录写入、后端导入以及本地模型
运行库隔离。业务数据默认写入 `%LOCALAPPDATA%\GWAPDebugPlatform\data\`；本机 `.env`
默认写入 `%LOCALAPPDATA%\GWAPDebugPlatform\.env`，首次启动时从包内 `.env.example`
创建。程序目录只读，解压新版本不会覆盖数据或模型密钥。

升级前备份 `%LOCALAPPDATA%\GWAPDebugPlatform\`。也可以显式指定企业批准的数据盘：

```bat
start.bat --data-root D:\DebugPlatformData
```

这样升级时可以解压到新目录并继续指定同一个数据目录。`--data-root` 只覆盖数据目录；
需要自定义配置位置时再传 `--env-file D:\DebugPlatformData\.env`。

## 3. 构建与验证

便携包应由 GitHub Actions 或一台受控构建电脑生成，而不是在每台使用电脑上重复安装
依赖：

```bat
scripts\build_windows_portable.bat
```

构建器会执行 `npm ci` 和生产前端构建，只安装 `backend` 的基础依赖到包内 Python，
并通过 `pythonXY._pth` 禁止读取目标电脑的全局/用户 Python 包。复制后端源码与迁移后，
继续运行：

- 包结构和可写性自检；
- 解压文件大小与 SHA-256 清单校验；
- `torch`、`sentence_transformers` 必须不存在的隔离门禁；
- SQLite 迁移与 FastAPI readiness；
- 首页静态资源和 Vue 深层路由回退；
- API liveness；
- 六个演示日志/快照/方法文件的固定 SHA-256；
- 包内标准 Skill、PowerShell/BAT 安装器及按实际动态端口执行的安装器 dry-run。

输出位于被 Git 忽略的 `artifacts\portable\`。已有解压包也可以单独验证：

```bat
scripts\verify_windows_portable.bat D:\path\debug-platform-windows-x64
```

2026-09-02 的本机 MVP 重建位于
`artifacts\portable\skill-mcp-20260902\debug-platform-windows-x64.zip`，共 10,040 个清单文件，
大小 119,176,048 字节，SHA-256 为
`70e20e3825bda219081989ed7acf1699a4de75c57930febc94cc53123d845e54`，上述 smoke 全部通过。
该包从未提交的当前工作树生成且 `source_dirty=true`，只用于本地验证；正式交付应从确认后的
提交重新构建并使用新哈希。

## 4. 在便携包上连接 Claude Code / Codex

首次启动会在 `%LOCALAPPDATA%\GWAPDebugPlatform\.env` 创建本机配置。为 MCP 设置一个
`MCP_BEARER_TOKEN` 后重启平台；再在启动 CLI 的同一 PowerShell 中设置相同令牌并运行包内
安装器：

```powershell
$env:DEBUGPLATFORM_MCP_TOKEN = '<与 .env 中 MCP_BEARER_TOKEN 相同的值>'
.\scripts\install_agent_skill_mcp.ps1 `
  -Client All `
  -McpUrl 'http://127.0.0.1:8080/mcp'
```

若使用 `start.bat --port 18080`，安装地址也改为
`http://127.0.0.1:18080/mcp`。`-Client Claude` 或 `-Client Codex` 可只安装一个客户端；
已有同名 Skill/MCP 配置时，核对目标后显式加 `-Replace`。完整客户端验证与更新方式见
[Windows 11 Claude Code / Codex Skill + MCP 部署](agent-skill-mcp-deployment.md)。

## 5. 为什么不再把本地模型装入平台 Python

旧安装器同时做了两件不同生命周期的工作：下载 BGE/Qwen 权重，以及把
`torch`、`sentence-transformers`、`transformers` 安装进平台主 `.venv`。FastAPI
随后在自己的进程内加载模型原生 DLL。不同 Python、CPU/GPU 包、VC++ 运行库或安全软件
组合都可能使 DLL 初始化失败，在 Windows 上常表现为 `WinError 1114`。

因此以下旧入口已删除：

- `install_local_models.bat/.ps1`；
- Hugging Face 镜像检测和 curl 下载辅助脚本；
- 下载文件校验及进程内模型加载验证脚本。

稳定 Core 便携包固定使用无需模型的 Hashing Embedding，并默认关闭 Reranker。需要自行
维护模型服务时，可以在“系统设置 → 本地模型权重下载”选择受管镜像、revision 和可选代理；
文件默认写入 `%LOCALAPPDATA%\GWAPDebugPlatform\data\models`。该入口只下载和校验权重，
不安装 Torch。随后应由独立模型服务加载权重，再在“系统设置 → 模型网关”中创建
Embedding API 或 Reranker API Profile。模型服务发生 DLL/显存/内存问题时不会破坏平台
主进程，也可以独立升级和回滚。

正在验证的全 GGUF E/R 版把上述“独立模型服务”固定为两个包内 llama.cpp sidecar，并由
启动器自动选择动态 loopback 端口、临时令牌和受管 Profile；它仍然遵守进程隔离原则，
不是被删除的旧 Torch 一体化安装链的恢复。当前状态和发布阻断条件见
[Windows 11 全 GGUF E/R 离线安装器](windows-offline-gguf-installer.md)。

代码中仍保留 `sentence_transformers` Provider，目的是不破坏已有人工维护的高级源码
安装；它不属于标准部署承诺，便携包也不会包含其依赖。若复用旧数据库，便携启动策略会
停用旧的活动进程内 Profile，并自动回退到 Hashing Embedding / Disabled Reranker，避免旧配置
再次触发 1114；源码部署默认不做这个切换。

## 6. 本机 `A.py` 的边界

根目录 `A.py` 已被 Git 忽略，不会进入便携包或 GitHub。它的文件清单、断点续传和进度
思路已经重构到前端受管下载器，但原文件仍不能当作平台安装器，原因包括：

- 保存路径硬编码到某一台电脑；
- `verify=False` 关闭完整 TLS 校验；
- 已存在文件只检查“大小大于零”，不能确认完整性或 revision；
- 只下载 Qwen Reranker，不安装或验证推理运行时；
- 不提供本地模型 HTTP 服务。

受管下载器已将目标目录固定在平台数据根、将可用镜像放入 `MODEL_DOWNLOAD_MIRRORS`、
支持显式代理密文，并把输入 revision 解析为镜像返回的不可变 Commit。每个 Commit 先下载到
独立 `.staging` generation，同模型线程锁和跨进程文件锁禁止多个平台实例交错写入；全量
大小/SHA-256 校验通过后，仅原子替换当前 generation 指针。失败、取消或进程中断时，外部
模型服务仍可继续使用上一代目录；重试会复用同一 staging 中的完整文件和 `.partial`。

前端“服务器保存目录”显示当前活动 generation 的实际路径。更新成功后该路径会变化，独立
模型服务需要按其自身机制重新加载；旧 generation 不会在发布过程中被覆盖。下载器保留证书链
和主机名校验，使用代理时仅跳过吊销检查。不要把内网地址、代理凭据或模型权重提交到本仓库。

## 7. 网络与代理边界

Core 便携包的启动、日志解析、Hashing 检索和规则诊断均不需要 pip/npm 网络。全 GGUF
实验包在权重已封装的前提下，Embedding/Reranker 推理同样只访问 loopback，不需要联网；
只有用户主动配置 API 模型后，平台才访问对应模型端点。Chat Profile 的代理继续在前端
逐模型配置。

2026-09-09 迭代后的 API 地址策略在生产和开发环境一致：任何有效 HTTP(S) Base URL
及 Chat 代理都可直接使用，包括内网和 localhost。无需配置 `MODEL_ENDPOINT_ALLOWLIST`
或 `MODEL_ALLOW_PRIVATE_ENDPOINTS`，也无需运行旧私网放行脚本；URL 格式、TLS、模型出站
授权和托管 GGUF sidecar 身份校验仍保留。已安装旧发行包需升级到包含该迭代的版本后适用。

普通用户可管理自己的私有 Chat API。管理员/专家默认创建共享模型，也可选择私有；任何人
都不能使用他人的私有配置。系统设置的个人选择优先，未选择时跟随共享默认，API Key 不回显。
全局 Embedding/Reranker、GGUF 和模型下载配置仅管理员可改，专家可使用并重建知识索引。

源码开发仍需下载依赖。此时 pip/npm 代理属于构建电脑配置，不应写入仓库、便携包或
公司日志。对完全离线的环境，应在联网 CI 生成便携 ZIP，再通过公司批准的文件渠道分发。
