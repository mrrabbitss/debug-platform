# Windows 11 便携部署与本地模型隔离

## 1. 推荐选择

普通使用电脑优先使用 Windows x64 便携包。该包包含：

- 64 位 CPython 运行时；
- FastAPI 后端及基础运行依赖；
- 已编译的 Vue 前端；
- SQLite 数据库迁移和内置知识；
- 双击启动、自检及真实 HTTP 冒烟入口。

目标电脑不需要安装 Python、Node.js、pip、npm 或 Docker，也不需要配置 pip/npm
代理。源码方式继续用于开发、修改代码和运行完整测试，不再作为普通使用电脑的首选部署方式。

| 方式 | 目标电脑依赖 | 适用场景 |
| --- | --- | --- |
| Windows 便携包 | 无额外开发运行时 | Win11 单机使用、内网交付、快速升级 |
| `scripts\start_local.bat` | Python 3.11+、Node 20.19+/22.12+、pip/npm 网络 | 源码开发与调试 |
| Docker Compose | Docker Desktop / WSL2 | PostgreSQL、Qdrant、多容器验证 |

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
- API liveness。

输出位于被 Git 忽略的 `artifacts\portable\`。已有解压包也可以单独验证：

```bat
scripts\verify_windows_portable.bat D:\path\debug-platform-windows-x64
```

## 4. 为什么不再提供一体化本地模型安装器

旧安装器同时做了两件不同生命周期的工作：下载 BGE/Qwen 权重，以及把
`torch`、`sentence-transformers`、`transformers` 安装进平台主 `.venv`。FastAPI
随后在自己的进程内加载模型原生 DLL。不同 Python、CPU/GPU 包、VC++ 运行库或安全软件
组合都可能使 DLL 初始化失败，在 Windows 上常表现为 `WinError 1114`。

因此以下旧入口已删除：

- `install_local_models.bat/.ps1`；
- Hugging Face 镜像检测和 curl 下载辅助脚本；
- 下载文件校验及进程内模型加载验证脚本。

便携包固定使用无需模型的 Hashing Embedding，并默认关闭 Reranker。需要 BGE 或 Qwen
Reranker 时，可以在“系统设置 → 本地模型权重下载”选择受管镜像、revision 和可选代理；
文件默认写入 `%LOCALAPPDATA%\GWAPDebugPlatform\data\models`。该入口只下载和校验权重，
不安装 Torch。随后应由独立模型服务加载权重，再在“系统设置 → 模型网关”中创建
Embedding API 或 Reranker API Profile。模型服务发生 DLL/显存/内存问题时不会破坏平台
主进程，也可以独立升级和回滚。

代码中仍保留 `sentence_transformers` Provider，目的是不破坏已有人工维护的高级源码
安装；它不属于标准部署承诺，便携包也不会包含其依赖。若复用旧数据库，便携启动策略会
停用旧的活动进程内 Profile，并自动回退到 Hashing Embedding / Disabled Reranker，避免旧配置
再次触发 1114；源码部署默认不做这个切换。

## 5. 本机 `A.py` 的边界

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

## 6. 网络与代理边界

便携包启动、日志解析、Hashing 检索和规则诊断均不需要 pip/npm 网络。只有用户主动配置
API 模型后，平台才访问对应模型端点；Chat Profile 的代理继续在前端逐模型配置。

源码开发仍需下载依赖。此时 pip/npm 代理属于构建电脑配置，不应写入仓库、便携包或
公司日志。对完全离线的环境，应在联网 CI 生成便携 ZIP，再通过公司批准的文件渠道分发。
