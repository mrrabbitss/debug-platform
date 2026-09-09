# Windows 11 Claude Code / Codex Skill + MCP 部署

0.5.0 的慢模型配置见[等待策略](slow-model-timeouts-20260909.md)：新安装的 Codex 平台 MCP 工具
等待 7200 秒，CodeAgent 分机会话的模型请求与流等待至少 900000 毫秒，平台 MCP 等待 7200000 毫秒。
旧 Codex 配置可通过 `install_agent_skill_mcp.ps1 -Client Codex -Replace` 更新；模型 provider 配置不自动覆盖。

本方案在不改变现有网页调用链的前提下，为 Claude Code 和 Codex 增加第二条诊断入口：

```text
Vue Web ── /api/v1 ───────────────┐
                                  ├─ FastAPI 服务、任务、数据库、检索、证据、报告
Claude Code / Codex 当前会话模型 ─┤
  ├─ gw-ap-debug Skill            │
  └─ Streamable HTTP /mcp ────────┘
```

网页端继续使用现有平台模式及其模型配置。MCP 创建的是独立的
`host_cli_mcp` 诊断运行：推理、规划和综合由当前 Claude Code/Codex 会话模型完成；
服务器只负责解析、检索、证据约束、状态与预算校验、持久化和报告。两者不能通过全局
开关互相切换。

截至 2026-09-03，registry 的 17 个业务工具、主应用装配、身份解析、诊断 Host 运行及新增
Markdown 路由门禁均已通过完整回归。最新 `20260903-155255-full` 的 18 个阶段全部通过：
后端 `416 passed, 1 skipped`、前端生产构建和 runtime smoke 通过，5 个浏览器 E2E 全部通过，
其中包含多 Markdown Web 路由和综合诊断。此前 `20260903-014606-full` 的 330 项结果仅为
历史基线；当前完整记录见 [VALIDATION.md](../VALIDATION.md)。

真实 Codex CLI 也已完成当前源码两条 Host 路径：batch `KRBATCH-28b4b098d8984ae2` 的两个
Markdown 分别归入故障树与协议诊断规则，均保持 DRAFT/inactive 且后端 Chat/model egress 为零；
综合诊断 session `HASESS-6bbdc379f6a04e7f` 用 4 轮完成 27/27 节点
（20 支持/3 排除/4 证据不足）、119 条证据和报告 `RPT-b6fa95b301634af7`，后端模型调用为零。
此前 Codex CLI 0.152.1 在全新数据库、空知识库和固定公开方法上的 6 轮诊断回归也继续保留。
当前主机未安装 Claude CLI，真实远程 HTTPS/RBAC 和 Claude Code 真机全案例端到端仍待验证，
因此组合部署能力整体状态继续为 `IN_PROGRESS`；这不表示上述 Codex 当前源码路径未完成。

OpenCode 当前仅为辅助客户端规划项，按用户要求未消耗额度测试，安装器和真实 MCP E2E 均未
覆盖，本页不宣称其兼容性。

## codeagent 最短启动流程（Windows 11 源码）

根目录 `start_codeagent.bat` 为兼容 Claude Code 会话级 MCP 参数的 `codeagent` 提供启动
编排；它不是新的模型服务，也不改变上面的 Web / Host 推理分工。该入口单独验收，既有真实
Codex 诊断结果不等同于魔改 `codeagent` 真机验证。本机未安装该客户端，当前状态为
`LIMITED`，不能宣称已完成其真实模型端到端测试。

2026-09-03 首版启动器黑盒测试 `10 passed`（68.40 秒）：使用模拟 CLI 配合真实后端 REST/MCP
握手，覆盖模拟 Program Files 自动发现、`.ps1`/`.cmd` 中文及空格路径、配置/DPAPI 二次复用、
更换端点不复用旧令牌、仓库迁移后的工作目录与 Skill 路径，以及失败退出回收和已有服务保留。
新增门禁验证原生 stderr warning 配合退出码 `0` 可以继续，真实非零退出码 `17` 不被吞掉，
并覆盖 `.cmd --help` 输出 warning 的启动路径。
这些结果证明启动编排，不证明魔改客户端模型已执行 Skill 推理；后者仍须在装有该客户端的
Windows 11 电脑单独验收。

新增入口及 PowerShell 兼容修复后的完整回归 `20260903-105852-full` 也已通过：18/18 阶段，
后端 `340 passed, 1 skipped`、覆盖率 80.44%，5 项网页 E2E 全部通过，包括综合诊断与多
Markdown 归类；详细记录见 [VALIDATION.md](../VALIDATION.md)。这些仍不替代真实 codeagent 验收。

客户端必须支持 `--mcp-config` 和 `--append-system-prompt`；启动前会
检查其 `--help`，不支持时明确停止。它不是任意 CLI 的通用适配器，不要把 `-CliCommand`
直接改成原生 `codex`；原生 Codex 的部署仍使用本页后文对应流程。

当前默认是**额外加载** `gw-ap-debug`，不再传 `--strict-mcp-config`，因此不主动排除
CodeAgent 原有的 MCP。启动器不读取或修改用户 `%USERPROFILE%\.cac`，也不覆盖其代理、模型、
登录配置；原有配置仍由客户端自身加载。若已有同名 `gw-ap-debug` 配置，请在客户端 `/mcp`
中核对实际地址，避免把旧服务当成当前服务。魔改客户端的最终合并语义仍以实机结果为准。

2026-09-03 追加模式相关黑盒 `12 passed`（70.97 秒），新增验证仅有包内 `agent-skills`
时的 Skill 定位、`-ConnectOnly` 禁止源码依赖安装、用户合成配置字节不变，以及模型/代理环境
保持不变。测试客户端为模拟 CLI，不消耗模型额度，也不替代真实 CodeAgent 验收。

Windows PowerShell 5.1 下，pip 或 CLI `--help` 写入 stderr 的 warning 不会单独判为失败；
原生命令按实际退出码判断，`0` 可继续，非零仍失败，依赖准备输出继续保存在启动日志中。
该兼容处理不会跳过客户端参数检查或 REST/MCP 校验，也不会吞掉真正的安装或客户端错误。

### 首次与日常使用

1. 拉取当前源码，在 Windows 11 安装并登录你要使用的 `codeagent`。需要自建本地后端时还需
   Python 3.11 或更新版本；首次准备后端依赖需要可访问锁定依赖的下载源。连接已有服务时
   不需要在客户端准备平台 Python 环境。
2. 双击仓库根目录的 `start_codeagent.bat`。它会检查已保存路径、PATH 及
   `C:\Program Files\CodeAgentCLI` 等常见安装目录，不要求安装目录已加入 PATH；找不到时在
   交互终端提示手动输入任意安装位置的程序完整路径（不是图形文件选择器）。随后自动准备
   后端、连接令牌及本次会话 MCP，再进入 CLI。
3. 后续仍双击同一文件；已保存的客户端路径、连接地址和当前 Windows 用户的令牌会复用。
   在 CLI 中用自然语言要求“使用当前仓库的 gw-ap-debug Skill 分析案例 CASE-123”，或调用
   本页后面的日志上传、Markdown 归类流程。

如果程序名不是 `codeagent`，首次在仓库 PowerShell 中显式指定；路径含中文或空格也需作为
一个带引号的参数传入。该参数是命令名或程序路径，不是附加参数组成的命令字符串：

```powershell
.\start_codeagent.bat -CliCommand 'D:\Tools\CodeAgentCLI\codeagent.exe'
```

支持现有 `.exe`、`.cmd`、`.bat` 或 `.ps1` 入口；脚本不会安装、修改或替换客户端。只保存或
修改设置并检查连接、不进入模型交互时使用：

```powershell
.\start_codeagent.bat -Configure
.\start_codeagent.bat -Configure -CliCommand 'D:\Tools\CodeAgentCLI\codeagent.exe' `
  -McpUrl 'https://debug.example.internal/mcp'
```

`-Configure` 仍需可用的 CLI 路径；远程模式需服务器事先部署 `/mcp` 并提供访问令牌，按提示
输入令牌。令牌不是模型 API Key。非本机地址要求 HTTPS；远程模式只连接，不安装或启动远端
后端。令牌与连接地址绑定，切换服务器不会向新地址发送已保存的旧令牌；重新配置远程或已有
服务时，未从当前进程或既有共享凭据配置取得令牌则会要求重新输入。非交互调用需提前设置
`DEBUGPLATFORM_MCP_TOKEN`，不要把值放进命令参数。恢复本地模式时把 `-McpUrl` 改回
`http://127.0.0.1:8000/mcp`。

### 不调用模型的检查

```powershell
.\start_codeagent.bat -DryRun
.\start_codeagent.bat -Check
```

`-DryRun` 只展示解析后的启动计划，不写配置、不联网、不准备依赖、不启动后端或 CLI。
`-Check` 不要求安装 CLI，会实际验证 REST 与 MCP `debug_status`，但不会启动推理模型；默认
本地服务未运行时仍会准备依赖、启动服务并在检查后回收。检查通过只证明连接与后端职责边界，
不能代替真实客户端的方法读取、多轮诊断、finalize、报告和网页可见性验收。

### 数据、配置与进程边界

- 默认连接 `http://127.0.0.1:8000/mcp`。端口空闲时只启动一个隐藏、无 reload 的后端；
  首次仅安装受 `backend/constraints.lock` 约束的后端依赖，不安装 Node/npm 或构建前端。
  若已有 `frontend/dist`，后端可直接托管；否则网页开发仍用原 `scripts/start_local.bat`。
- 业务数据库和 Storage 遵从现有环境变量、仓库 `.env` 与后端默认设置，默认分别位于
  `backend/data/gw_ap_debug.db` 和 `backend/data/storage`，与相同配置的源码 Web 共享数据。
  启动器不覆盖 `.env`、平台 Chat Profile 或现有 Web 鉴权设置；它也不会把便携包的
  `%LOCALAPPDATA%` 数据自动迁入源码库。
- 默认启动状态目录为仓库 `.agent-runtime/codeagent-launcher`，已受 Git 忽略；
  `config.json` 只保存客户端路径和 MCP URL，`token.dpapi` 使用 Windows CurrentUser DPAPI
  保存令牌，`sessions/session-<GUID>.json` 只引用进程环境变量。该目录还保存启动日志和
  依赖指纹 `backend-dependencies.sha256`，不是业务数据目录；依赖失败看 `backend-bootstrap.log`，
  后端启动失败看 `backend-<GUID>.out.log` / `backend-<GUID>.err.log`。可用 `-StateDirectory`
  显式指定另一目录。DPAPI 凭据不能直接复制给另一台电脑或 Windows 用户使用，应重新配置。
- MCP 配置只注入本次会话，并显式指向当前仓库 Skill；不写用户全局 CLI/MCP 配置，不覆盖
  已安装的旧 Skill，也不改变 CLI 原有模型登录。下面的用户级安装器是另一种可选部署方式，
  使用一键入口时无需先运行它。
- 只有本次启动器创建的后端会在 CLI 正常退出、自检结束或启动失败时回收；已运行且检查
  通过的后端及远程服务会复用并保留；`-Configure` 自建的后端也在配置检查结束后回收。
  临时 session MCP 文件随正常清理删除。请正常退出 CLI，让清理流程完成；关闭进程窗口或
  强制终止不应视为已验证的正常退出。不会杀掉占用端口的其他程序。
- 启动等待默认 90 秒，可用 `-BackendStartupTimeoutSeconds` 调整；初始化失败会保留可定位的
  启动日志，不会把握手失败降为“已连接”。

## 仓库结构

- `agent-skills/gw-ap-debug`：唯一可手工编辑的 Skill 源。
- `.claude/skills/gw-ap-debug`：Claude Code 的已生成仓库镜像。
- `.agents/skills/gw-ap-debug`：Codex 的已生成仓库镜像。
- `scripts/sync_agent_skills.ps1`：在 Windows 不依赖符号链接地同步并检查两个镜像。
- `scripts/install_agent_skill_mcp.ps1`：安装用户级 Skill，并添加远端 MCP 配置。
- `scripts/install_agent_skill_mcp.bat`：适合从 `cmd.exe` 调用的包装器。
- `start_codeagent.bat` / `scripts/start_codeagent.ps1`：源码 Windows 11 的 codeagent
  一键入口；只为本次会话准备后端连接与 MCP 配置。

旧 Core/GGUF 安装产物不包含 codeagent 一键入口；本轮 0.2.0 组件化构建开始纳入包内入口。
它使用包内 Python 和已编译前端，不会创建源码 `.venv`；具体安装、双击入口与模型边界见
[全 GGUF 安装指南](windows-offline-gguf-installer.md)。

Windows Core 便携构建把上述标准源和两个安装器放在
`agent-skills/gw-ap-debug` 与 `scripts/`，无需另外下载源码仓库。便携启动器会把
`MCP_PUBLIC_BASE_URL` 设为实际监听地址；例如 `start.bat --port 18080` 对应
`http://127.0.0.1:18080/mcp`。本机令牌配置和安装命令见
[Windows 11 便携部署与本地模型隔离](windows-portable-deployment.md#4-在便携包上连接-claude-code--codex)。
本机最新验证包位于 `artifacts/portable/skill-mcp-20260902/`，其 ZIP 已通过完整 smoke；正式
分发仍应从目标提交重新构建，不能把这个 `source_dirty=true` 的本地验证包当成签名 Release。

不要直接编辑两个镜像。修改标准源后运行：

```powershell
.\scripts\sync_agent_skills.ps1
.\scripts\sync_agent_skills.ps1 -Check
```

## 服务器部署边界

服务器仍按现有 Web 部署流程安装数据库、后端和前端。额外要求如下：

1. 执行当前版本的数据库迁移，并确认原有 `/api/v1` 回归通过。
2. 在同一 FastAPI 服务发布 Streamable HTTP `/mcp`，通过后端的 Bearer resolver
   将令牌映射到真实用户、角色和案例范围。模型服务 API Key 不能作为 MCP 用户令牌。
3. 非本机访问必须在受信任反向代理后使用 HTTPS。代理需转发 `Authorization`，并允许
   MCP 所需的 `POST`、`GET`、`DELETE` 以及流式响应；不要把大文件请求转到 `/mcp`。
4. 网页路径保留当前平台模型设置。`host_cli_mcp` 路径必须有自动测试证明不会调用
   后端生成式模型 provider。
5. Markdown 完整文件与原始日志一样走 `/api/v1` multipart 数据面，不进入 `/mcp`。
   `/api/v1/knowledge-routing/import` 允许工程师或管理员；工程师仅能处理自己的草稿。Web 可使用平台 Chat Profile，CLI 请求固定
   `reasoning_owner=host_cli`，再由当前客户端模型通过两个知识路由工具完成判断和 DRAFT 写回。
6. 为每位操作者签发最小权限令牌，并将最终外部地址记录为
   `https://debug.example.internal/mcp` 这类完整 URL。

服务器端使用以下环境变量；远程发布必须把默认 loopback 值替换为实际受信任地址：

```dotenv
MCP_ENABLED=true
MCP_PUBLIC_BASE_URL=https://debug.example.internal
MCP_ALLOWED_HOSTS=debug.example.internal
MCP_ALLOWED_ORIGINS=https://debug.example.internal
MCP_MAX_REQUEST_BODY_BYTES=4194304
# 可选的专用共享凭据；也兼容现有 API_KEY 和数据库 Personal Access Token。
MCP_BEARER_TOKEN=
```

`MCP_BEARER_TOKEN` 是服务器验证的可选共享凭据；客户端始终从
`DEBUGPLATFORM_MCP_TOKEN` 读取实际 Bearer 值。两个变量名称不同是预期设计，令牌值由部署者
决定是否对应。生产环境更推荐数据库 Personal Access Token，以便保留用户身份、到期、撤销和
案例 RBAC。`MCP_ALLOWED_HOSTS`、`MCP_ALLOWED_ORIGINS` 与
`MCP_PUBLIC_BASE_URL` 必须按反向代理的外部主机和 Origin 配置；4 MiB MCP body 上限不是日志
上传上限，大文件仍走 `/api/v1` multipart。

开发机上的 `scripts\start_local.bat` 只监听 `127.0.0.1`，适合单机验证，不是远程
发布命令。生产网络、TLS、证书和令牌签发方式由部署环境决定，不应硬编码进仓库。

## 新 Windows 11 电脑完整流程

### 1. 安装客户端并下载仓库

安装 Git，以及计划使用的客户端。Claude Code 是本项目在 Windows 11 的主要入口；
Codex CLI 可同时安装作为补充。然后在 PowerShell 中执行：

```powershell
$sourceRoot = Join-Path $env:USERPROFILE 'source'
New-Item -ItemType Directory -Force -Path $sourceRoot | Out-Null
git clone --branch WebSkillMcp --single-branch `
  'https://github.com/mrrabbitss/debug-platform.git' `
  (Join-Path $sourceRoot 'debugplatform')
Set-Location (Join-Path $sourceRoot 'debugplatform')
```

使用 codeagent 一键入口时，安装 Python 3.11+ 并登录客户端后，在仓库目录直接运行
`.\start_codeagent.bat` 即可；不需要执行下面的手动令牌与用户级 MCP 安装步骤。
下面第 2 步起适用于连接已部署服务器、手动安装原生 Claude Code / Codex 的方式。

### 2. 在当前 PowerShell 会话设置连接变量

```powershell
$env:DEBUGPLATFORM_MCP_URL = 'https://debug.example.internal/mcp'
$credential = Get-Credential -UserName 'mcp-token' -Message '输入 Debug Platform MCP token'
$env:DEBUGPLATFORM_MCP_TOKEN = $credential.GetNetworkCredential().Password
```

第二条命令不会把令牌写入命令历史。安装器和 Skill 都只引用
`DEBUGPLATFORM_MCP_TOKEN`，不会把值写入仓库或生成的 MCP 配置。若希望每次登录后自动
设置变量，应使用组织批准的凭据启动脚本或秘密管理器；不要把明文写进 PowerShell
profile、`.mcp.json` 或 `config.toml`。

### 3. 一次安装两个客户端

先预览将执行的操作：

```powershell
.\scripts\install_agent_skill_mcp.ps1 -Client All -DryRun
```

确认后安装：

```powershell
.\scripts\install_agent_skill_mcp.ps1 -Client All
```

也可以只装主客户端：

```powershell
.\scripts\install_agent_skill_mcp.ps1 -Client Claude
```

安装器会把标准 Skill 复制到 `%USERPROFILE%\.claude\skills` 和/或
`%USERPROFILE%\.agents\skills`。Claude 配置保留 URL 与令牌的环境变量引用；Codex
使用已解析 URL，并以 `bearer_token_env_var = "DEBUGPLATFORM_MCP_TOKEN"` 方式取令牌。

已经存在同名配置时，脚本会停止而不是覆盖。核对地址后显式更新：

```powershell
.\scripts\install_agent_skill_mcp.ps1 -Client All -Replace
```

从 `cmd.exe` 部署时可使用：

```bat
scripts\install_agent_skill_mcp.bat -Client All
```

### 4. 验证 Claude Code

关闭并重新打开终端，重新注入上述两个环境变量，然后执行：

```powershell
claude mcp get gw-ap-debug
claude mcp list
claude
```

在 Claude Code 内通过 `/mcp` 确认 `gw-ap-debug` 已连接，然后提出例如：

```text
使用 gw-ap-debug 分析案例 CASE-123，先读取所有要求的诊断方法，再给出有证据引用的结论。
```

### 5. 验证 Codex CLI

```powershell
codex mcp get gw-ap-debug
codex mcp list
codex
```

在 Codex 内通过 `/mcp` 检查连接，并显式调用 `$gw-ap-debug` 完成首次冒烟诊断。
Codex、Claude Code 的模型登录与 `DEBUGPLATFORM_MCP_TOKEN` 是两套独立身份：前者支付和
执行推理，后者只授权访问 Debug Platform 数据。

若要在不写入 `%USERPROFILE%\.codex\config.toml`、不保留任务会话的情况下做一次性连接
检查，可使用：

```powershell
codex exec --ephemeral --ignore-user-config --strict-config --approve-for-me `
  -c ('mcp_servers.gw-ap-debug.url="{0}"' -f $env:DEBUGPLATFORM_MCP_URL) `
  -c 'mcp_servers.gw-ap-debug.bearer_token_env_var="DEBUGPLATFORM_MCP_TOKEN"' `
  -c 'mcp_servers.gw-ap-debug.required=true' `
  '请使用 $gw-ap-debug Skill，仅调用 debug_status，并报告 inference_owner、backend_chat_allowed 和 backend_chat_calls。'
```

`DEBUGPLATFORM_MCP_TOKEN` 的值只存在于启动该命令的 PowerShell 环境；上述 `-c` 参数只对
这一次运行生效。Codex CLI 0.152.1 的本机验证中，`approval_policy=never` 会阻断模型通过
PowerShell 读取 Skill 文件；`--approve-for-me` 可自动审核该安全读取，实际会话仍显示
`sandbox: read-only`。完整案例验收仍应继续执行方法读取、至少两轮规划、证据门禁、finalize 和
网页可见性检查，不能用 `debug_status` 冒烟替代。

## 大日志上传

原始归档不能经过 MCP JSON 或模型上下文。可以在网页上传，也可以调用 Skill 的确定性
上传助手：

```powershell
& .\agent-skills\gw-ap-debug\scripts\upload-debug-artifact.ps1 `
  -CaseId CASE-123 `
  -Path 'D:\logs\collectDebuginfo.tar.gz' `
  -SourceDeviceType AP `
  -SourceDeviceRole PRIMARY `
  -DryRun
```

去掉 `-DryRun` 后，助手使用现有 `/api/v1` multipart 接口直接流式上传，并默认启动
解析。若 REST 数据面与 `/mcp` 不在相同反向代理路径，可另外设置：

```powershell
$env:DEBUGPLATFORM_API_BASE_URL = 'https://debug.example.internal/api/v1'
```

上传输出是包含 `artifact` 与 `parse_job` 的 JSON；随后让 Skill 通过
`debug_get_case_context` 等待解析状态，而不是将原日志粘贴给模型。

## Markdown 知识智能归类

网页和 CLI 共享分类树、知识状态机和持久任务，但推理模型所有权不同：

| 入口 | 完整文件传输 | 分类模型 | 服务端职责 |
|---|---|---|---|
| Web | `/api/v1/knowledge-routing/import` multipart | 用户选择或当前激活的平台 Chat Profile | 本地保存，发送脱敏限长片段，校验模型结果，每文件创建一个 DRAFT |
| Claude Code / Codex | 同一 REST multipart，`reasoning_owner=host_cli` | 当前 CLI 会话模型 | 先暂存每文件一个 DRAFT，再通过 MCP 返回脱敏限长上下文并校验 Host 决策；后端 Chat 调用为零 |

两条路径均只接受 1–20 个 `.md`/`.markdown` 文件、要求工程师或管理员身份、只允许选择现有活动叶子
分类，并且绝不自动提交审核或发布。多个输入不会被拼成一个文档；每个文件都有独立 artifact、
后台 job、知识文档、版本与审核生命周期。

### Web 流程

1. 在“知识库”点击“AI 智能导入 MD”，选择一个或多个 Markdown。
2. 选择已启用且非 Mock 的 Chat Profile；API 模型需确认脱敏片段外发。
3. 提交后等待所有逐文件任务结束，并检查每个结果的分类、设备范围、模块、置信度和理由。
4. 在知识文档中人工复核；需要进入正式检索时，再单独执行提交审核和批准发布。

### CLI 流程

Skill 的上传助手会从 `DEBUGPLATFORM_MCP_URL` 自动推导 REST API base；若数据面地址不同，可显式
传入 `-ApiBaseUrl` 或设置 `DEBUGPLATFORM_API_BASE_URL`：

```powershell
& .\agent-skills\gw-ap-debug\scripts\upload-knowledge-markdown.ps1 `
  -Path @(
    'D:\knowledge\ap-offline-fault-tree.md',
    'D:\knowledge\cwmp-protocol-rule.markdown'
  )
```

助手使用 REST 上传并等待每个 job 到达终态，输出 `document_id` 等内容安全元数据。接着在
Claude Code 或 Codex 中提出：

```text
使用 $gw-ap-debug 对刚上传的这些 Markdown 做知识归类。由当前会话模型判断每份文档的方向，
先调用 debug_get_knowledge_routing_context，再逐份给出分类并调用
debug_apply_knowledge_routing；只保留 DRAFT，不提交审核或发布。
```

`debug_get_knowledge_routing_context` 只返回活动叶子分类、脱敏限长片段、lock version 与正文
SHA-256；当前客户端模型必须对每个 `document_id` 恰好给出一次决定。
`debug_apply_knowledge_routing` 会重新校验分类、并发版本和正文哈希，再原子更新这些 DRAFT。
任何 stale/hash/category 校验错误都应重新读取上下文后判断，不能绕过门禁。整个 Host 路径不
调用平台 Chat Profile，也不会改变网页端激活模型。

## 更新与卸载

一键入口使用者拉取新版本后检查仓库 Skill 镜像，再双击 `start_codeagent.bat`；已保存的
连接配置会复用，不需重装用户级 Skill。此前使用用户级安装器的部署，按下面流程更新：

```powershell
git pull --ff-only
.\scripts\sync_agent_skills.ps1 -Check
.\scripts\install_agent_skill_mcp.ps1 -Client All -Replace
```

移除 MCP 配置：

```powershell
claude mcp remove gw-ap-debug --scope user
codex mcp remove gw-ap-debug
```

用户级 Skill 是普通目录，可以在确认精确路径后删除；删除 MCP 配置不会影响网页服务、
案例或已有分析。

## 故障定位

| 现象 | 检查 |
|---|---|
| codeagent 找不到或参数预检失败 | 用 `-CliCommand` 指定现有程序完整路径；该构建必须提供本页列出的两个 Claude 兼容参数，脚本不会安装或改造客户端。 |
| 一键入口发现已有后端但 MCP 校验失败 | 已有服务会保持运行。用 `-Configure` 提供同时可用于 REST/MCP 的令牌；若服务尚未配置 MCP 凭据，由操作者确认后手动停止它，再用一键入口启动，脚本不强杀服务或改写其鉴权。 |
| 客户端提示缺少环境变量 | 必须在启动 `claude`/`codex` 的同一进程树设置 URL 与令牌。 |
| HTTP 401/403 | 令牌已过期、未映射用户，或用户没有目标案例权限；不要改成模型 API Key。 |
| `/mcp` 为 404 | 确认完整地址包含 `/mcp`，并检查反向代理是否保留路径。 |
| MCP 正常但网页异常 | 这是独立入口；按原 Web 回归检查 `/api/v1`、前端和平台模型配置。 |
| 上传 401 而 MCP 正常 | 检查 REST 层是否接受同一 scoped token，或由管理员提供批准的数据面令牌映射。 |
| Markdown 路由返回 403 | 需要 `ENGINEER` 操作自己的草稿或 `ADMIN`；案例成员权限不能替代文档归属。 |
| Web 分类提示缺少模型或同意 | 选择已启用且非 Mock 的 Chat Profile；API 模式需显式同意发送脱敏限长片段。 |
| CLI 分类提示 stale/hash 不匹配 | 文档已在读取上下文后发生变化；重新调用 `debug_get_knowledge_routing_context` 并重新判断。 |
| Finalize 拒绝 evidence ID | 刷新 host run，只使用本次运行工具返回的证据，不能复用旧案例 ID。 |
| CLI 断开 | 用原 `session_id` 调 `debug_get_host_run` 恢复，不要立即创建重复运行。 |

客户端配置格式分别遵循 [Codex MCP 官方文档](https://developers.openai.com/codex/mcp)
和 [Claude Code MCP 官方文档](https://code.claude.com/docs/en/mcp)。

## 发布前校验

```powershell
.\scripts\sync_agent_skills.ps1 -Check
python "$env:USERPROFILE\.codex\skills\.system\skill-creator\scripts\quick_validate.py" `
  .\agent-skills\gw-ap-debug
python -m pytest backend\tests\test_agent_skill_contract.py -q
.\scripts\install_agent_skill_mcp.ps1 `
  -Client All `
  -McpUrl 'https://debug.example.invalid/mcp' `
  -DryRun
& .\agent-skills\gw-ap-debug\scripts\upload-debug-artifact.ps1 `
  -CaseId DRY-RUN `
  -Path .\sample_data\demo_ap_frequent_offline\AP_collectDebuginfo_demo.txt `
  -ApiBaseUrl 'https://debug.example.invalid/api/v1' `
  -DryRun
& .\agent-skills\gw-ap-debug\scripts\upload-knowledge-markdown.ps1 `
  -Path .\agent-skills\gw-ap-debug\references\knowledge-routing.md `
  -ApiBaseUrl 'https://debug.example.invalid/api/v1' `
  -DryRun
```

`quick_validate.py` 的实际位置由本机 Codex 安装决定；CI 应使用固定的 Skill 校验工具。
