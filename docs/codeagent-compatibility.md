# 华为 CodeArts/CodeAgent 的 Skill 导入、兼容探测与脚本化运行

最后更新：2026-08-10

本文面向由 OpenCode 改造、但具体改动范围未知的华为 CodeAgent/码道 CodeArts Agent CLI。
目标不是根据产品名称猜测兼容性，而是把完整链路拆成四层逐项验证：

1. CodeAgent 能否发现 `SKILL.md`；
2. CodeAgent 能否运行 Skill 附带的 PowerShell CLI fallback；
3. CodeAgent 能否启动本地 stdio MCP，并看到 `debug_*` 工具；
4. MCP 能否访问本机 GW/AP Debug Runtime HTTP 数据面。

只要第 1、2、4 层通过，就能使用 **Skill + CLI fallback**，不依赖 CodeAgent 的 MCP
配置格式。第 3 层也通过后，可以使用体验更好的 **Skill + MCP**。

华为云当前公开文档说明，码道 CLI 使用项目级
`.codeartsdoer/skills/<skill-name>/SKILL.md` 或个人级
`~/.codeartsdoer/skills/<skill-name>/SKILL.md`；本地 MCP 支持 stdio，并支持码道原生配置和
Claude Code 兼容配置。本分支按这两个公开契约实现，同时保留 OpenCode V1/V2 配置生成能力。
对应官方资料：

- [码道 CLI：技能](https://support.huaweicloud.com/usermanual-cli/codeartsagent_cli_0019.html)
- [码道 CLI：默认 MCP 配置](https://support.huaweicloud.com/usermanual-cli/codeartsagent_cli_0017.html)
- [码道 CLI：Claude Code MCP 格式](https://support.huaweicloud.com/usermanual-cli/codeartsagent_cli_0013.html)
- [码道 CLI：MCP 命令与连接状态](https://support.huaweicloud.com/usermanual-cli/codeartsagent_cli_0028.html)
- [OpenCode Agent Skills](https://opencode.ai/docs/skills)
- [OpenCode V2 MCP servers](https://opencode.ai/v2/docs/mcp-servers)

## 1. 不要混淆三个名称和两个端口

| 项目 | vNext 默认值 | 是否可以改 | 作用 |
| --- | --- | --- | --- |
| Skill ID | `gw-ap-debug` | 不建议 | Agent 按需加载的诊断流程与安全规则 |
| MCP 配置名 | `gw-ap-debug-vnext` | 可以 | CodeAgent 配置文件中的 server key |
| MCP server 自报名称 | `gw-ap-debug` | 当前固定 | MCP `initialize` 返回的 `serverInfo.name` |
| MCP 传输 | `stdio` | 当前固定 | CodeAgent 拉起本地 Python 子进程；**没有 TCP 端口** |
| Debug Runtime HTTP | `127.0.0.1:8766` | 可以 | MCP/CLI 实际读取案例、日志、证据和图谱的数据面 |
| CodeAgent `serve`/Web | 产品自身决定；公开码道默认常见为 `4096` | 可以 | CodeAgent 自己的 UI/attach 服务，与 `8766` 无关 |

因此，看到 CodeAgent 在 `4096` 监听不代表 Debug Runtime 应改成 `4096`。标准链路是：

```text
CodeAgent LLM
  ├─ load gw-ap-debug Skill
  ├─ stdio -> python -m app.agent_runtime.mcp_server   （无端口）
  │             └─ HTTP -> http://127.0.0.1:8766       （Debug Runtime）
  └─ 或 PowerShell -> Skill/scripts/gwap.ps1 -> 8766  （CLI fallback）
```

## 2. 新电脑的一条命令安装

先克隆并进入独立 vNext 分支：

```powershell
$Repo = 'D:\GWAP\debugplatform-vnext'
git clone --branch codex/agent-runtime-vnext-opencode --single-branch `
  https://github.com/mrrabbitss/debug-platform.git $Repo
Set-Location $Repo
```

如果华为客户端命令是公开版本使用的 `codearts`：

```powershell
scripts\setup_codeagent_vnext.bat -AgentCommand codearts
```

如果公司魔改版的命令名是 `codeagent.exe`：

```powershell
scripts\setup_codeagent_vnext.bat -AgentCommand codeagent
```

也可以传绝对路径：

```powershell
scripts\setup_codeagent_vnext.bat `
  -AgentCommand 'C:\Program Files\Huawei\CodeAgent\codeagent.exe'
```

如果公司魔改版的可执行文件仍叫 `opencode`，但使用 CodeArts 的
`.codeartsdoer` 目录和配置契约，请显式指定客户端家族：

```powershell
scripts\setup_codeagent_vnext.bat `
  -AgentCommand opencode `
  -TargetClient CodeArts
```

只有目标确实是标准 OpenCode 时才使用 `-TargetClient OpenCode`。

默认脚本会：

- 在 `%LOCALAPPDATA%\GWAPDebugVNext` 建立独立 Python 环境和运行目录；
- 安装锁定的后端依赖，不复用或覆盖 `%LOCALAPPDATA%\GWAPDebug`；
- 把 Skill 复制到当前项目的 `.codeartsdoer\skills\gw-ap-debug`；
- 写入不含密钥的 `runtime-config.json`，使 Skill 的 CLI wrapper 不依赖全局 `gwap`；
- 把该 Skill 标记为启用；
- 根据 `-TargetClient` 选择 Skill 路径和 MCP schema，默认 MCP 名为
  `gw-ap-debug-vnext`；
- 启动独立 Runtime，默认端口 `8766`；
- 生成可供 IDE/企业控制台导入的 Skill zip；
- 运行兼容探针，并把脱敏 JSON 报告写入 Runtime 的 `logs` 目录。

可选 Web UI 默认不构建。需要 UI 时：

```powershell
scripts\setup_codeagent_vnext.bat -AgentCommand codearts -BuildFrontend
```

### 只使用 Skill + CLI，不写 MCP 配置

这是最不依赖 CodeAgent 魔改细节的模式：

```powershell
scripts\setup_codeagent_vnext.bat `
  -AgentCommand codearts `
  -McpSchema None
```

Skill 中的 `scripts\gwap.ps1` 会读取安装器生成的 `runtime-config.json`，调用指定 Python 和
`http://127.0.0.1:8766`。前提是 CodeAgent 允许执行该 PowerShell 脚本。

### 强制指定 MCP 配置格式

自动判断不符合公司魔改版时，可以明确指定：

```powershell
# 码道/OpenCode V1 风格：mcp.<server>
scripts\setup_codeagent_vnext.bat -AgentCommand codeagent -McpSchema CodeArtsNative

# 华为当前文档中的 Claude Code 兼容文件：mcpServers.<server>
scripts\setup_codeagent_vnext.bat -AgentCommand codeagent -McpSchema CodeArtsClaude

# 标准 OpenCode V1
scripts\setup_codeagent_vnext.bat -AgentCommand opencode -McpSchema OpenCodeV1

# 标准 OpenCode V2：mcp.servers.<server>
scripts\setup_codeagent_vnext.bat -AgentCommand opencode -McpSchema OpenCodeV2
```

脚本不会覆盖已有的不同名 MCP server。如果同名配置内容不同，默认停止；人工确认后传
`-Force` 只替换该 server entry，并先保留配置备份。

## 3. 在安装前后运行兼容探针

安装前先盘点客户端：

```powershell
scripts\probe_codeagent_compatibility.bat `
  -AgentCommand codearts `
  -ClientFamily CodeArts `
  -ProjectRoot $PWD
```

安装后严格验收：

```powershell
$Python = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext\venv\Scripts\python.exe'
scripts\probe_codeagent_compatibility.bat `
  -AgentCommand codearts `
  -ClientFamily CodeArts `
  -ProjectRoot $PWD `
  -PythonExe $Python `
  -RuntimeUrl 'http://127.0.0.1:8766' `
  -Strict
```

探针执行以下只读或临时检查：

- 查找 `codearts`、`codeagent`、`opencode` 或显式命令路径；
- 有超时地执行 `--version`、`help`、`mcp list`；
- 检查已知项目级/个人级 Skill 路径；
- 识别 CodeArts 原生、Claude 兼容、OpenCode V1 和 OpenCode V2 schema；
- 只报告 MCP server key 和 loopback URL，不打印配置密钥或完整配置值；
- 探测 `8765`、`8766` 及配置中出现的 loopback Runtime URL；
- 直接完成 MCP `initialize` 和 `tools/list` stdio 握手；
- 返回 MCP server 自报名称、协议版本及 12 个原始工具名；
- 检查 CodeAgent 的 `mcp list` 是否真的显示预期 server 已连接。

结果分级：

| `status` | 含义 |
| --- | --- |
| `FULL_SKILL_MCP` | Skill、Runtime、MCP server 和 CodeAgent 注册全部确认 |
| `SKILL_CLI_READY_MCP_CLIENT_UNCONFIRMED` | Skill + CLI 已可用，MCP server 正常，但客户端尚未确认连接 |
| `PORTABLE_ASSETS_READY_RUNTIME_NOT_CONFIRMED` | 文件和 MCP 协议正常，Runtime 尚未启动或端口不对 |
| `PARTIAL` | 至少一项基础契约缺失，按 `recommendations` 修复 |

严格模式只有 `FULL_SKILL_MCP` 返回退出码 0；普通模式始终生成报告，适合第一次排查。
如果公司魔改版的可执行文件仍叫 `opencode`，但实际遵循 CodeArts 的 `.codeartsdoer` 目录，必须传
`-ClientFamily CodeArts`，避免仅根据命令名误判 Skill 搜索路径。

### 一键诊断 `nga` 与 `bin\codeagent.exe`

若公司的实际入口是安装根目录下的 `nga`，内部程序是 `bin\codeagent.exe`，从 vNext 仓库根目录运行：

```powershell
scripts\collect_codeagent_diagnostics.bat
```

脚本会自动：

- 解析 PATH 中的 `nga`，并优先查找其同级 `bin\codeagent.exe`；
- 分别执行有超时的版本、帮助和 `mcp list` 探测；
- 直接完成两次 stdio MCP `initialize`/`tools/list` 握手；
- 只用布尔值记录 `nga debug config` 是否真正包含 `gw-ap-debug-vnext`，不保存完整生效配置；
- 运行 `nga debug paths`，替换用户、仓库和 Runtime 路径并过滤密钥及非 loopback URL；
- 生成明确区分“可以分享”和“仅本机保留”的诊断文件。

自动定位失败时显式传路径：

```powershell
scripts\collect_codeagent_diagnostics.bat `
  -NgaCommand 'C:\实际安装目录\nga.exe' `
  -CodeAgentExe 'C:\实际安装目录\bin\codeagent.exe'
```

默认输出目录为：

```text
%LOCALAPPDATA%\GWAPDebugVNext\logs\codeagent-diagnostics\<timestamp>
```

可以检查并分享：

- `codeagent-diagnostics-shareable.json`；
- `codeagent-diagnostics-shareable.txt`；
- 若生成，`nga-debug-paths-sanitized.txt`。

不要分享 `nga-probe.json`、`codeagent-exe-probe.json`、完整 `debug config`、环境变量或 CLI 原始日志。
汇总状态会直接区分 `FULL_NGA_MCP`、仅内部 exe 可连接、MCP 本体正常但客户端配置未生效，以及
MCP 握手失败。

## 4. 手工确认 Skill 与 MCP

启动 CodeArts TUI 后输入：

```text
/skills
```

应能看到 `gw-ap-debug`。如果看不到，依次检查：

```powershell
Test-Path .\.codeartsdoer\skills\gw-ap-debug\SKILL.md
Get-Content .\.codeartsdoer\skills\ProjectSkillStatus.txt
```

然后检查 MCP：

```powershell
codearts mcp list
```

或在 TUI 输入：

```text
/mcps
```

应看到 `gw-ap-debug-vnext` 已连接。若公司命令不是 `codearts`，替换为实际命令。

### 不经过 MCP，直接确认 CLI fallback

```powershell
powershell -NoProfile -ExecutionPolicy Bypass `
  -File .\.codeartsdoer\skills\gw-ap-debug\scripts\gwap.ps1 status
```

成功输出中应包含 External Agent Mode 状态。再检查 MCP server 本体而不经过 CodeAgent：

```powershell
$Python = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext\venv\Scripts\python.exe'
scripts\probe_codeagent_compatibility.bat `
  -SkipClientCommands `
  -PythonExe $Python
```

如果直接 MCP 握手通过、但 `codearts mcp list` 失败，问题在 CodeAgent 配置发现或 schema，不在
GW/AP Debug MCP server。

## 5. 工具名称发生魔改时怎么判断

MCP server 原始工具名固定为：

```text
debug_status
debug_create_case
debug_ingest
debug_wait_job
debug_inspect
debug_search
debug_evidence_bundle
debug_attach_workspace
debug_code_context
debug_diagnose
debug_generate_report
debug_open_ui
```

客户端可能给工具添加 MCP server 前缀，或者把 `-` 归一化成 `_`。例如以下名字都可能指向同一
原始工具：

```text
debug_status
gw-ap-debug-vnext_debug_status
gw_ap_debug_vnext_debug_status
```

判断时不要只比较完整字符串，应比较 `debug_status` 后缀和工具 description。兼容探针中的
`mcp_handshake.tool_names` 是 server 的权威原始名称；客户端界面显示的是客户端转换后的名称。

## 6. 合成数据验收提示词

在 CodeAgent 中显式选择或加载 `gw-ap-debug` Skill，然后发送：

```text
使用 gw-ap-debug Skill，只处理仓库 sample_data 中的合成数据。
先调用 debug_status；创建一个 AP 测试案例；导入
sample_data/collectDebuginfo_demo.zip；把 sample_data/repository 以只读 workspace 关联并建立索引；
执行 deterministic diagnose 和 evidence bundle；最后由你自己的 LLM 完成根因推理。

最终答复必须：
1. 输出 CODEAGENT_GWAP_E2E_PASS；
2. 输出真实 CASE_ID；
3. 至少引用一个真实 EVT-* evidence ID；
4. 分开标注 CONFIRMED、PROBABLE、UNKNOWN；
5. 不修改源码，不访问真实公司日志，不调用未经批准的外部模型端点。
```

若 MCP 工具不可见，改为：

```text
加载 gw-ap-debug Skill，并使用 Skill 目录中 scripts/gwap.ps1 的 CLI fallback 完成相同步骤。
不要猜测 MCP 工具名；每一步都保留 JSON 输出中的 case、job 和 evidence ID。
```

## 7. Skill zip 导入

生成标准目录结构的 zip：

```powershell
scripts\package_codeagent_skill.bat
```

默认输出：

```text
artifacts\codeagent\gw-ap-debug-codeagent-skill.zip
```

zip 根目录包含 `gw-ap-debug/SKILL.md`、`scripts/`、`references/` 和 `assets/`。它不包含机器路径、
模型密钥、Runtime 数据或真实日志，可以用于 CodeArts IDE/企业控制台的 Skill 导入入口。

导入 zip 只完成 Skill 安装；目标电脑仍需运行 `setup_codeagent_vnext.bat` 或以等价方式准备本地
Runtime。Skill 本身不携带 Python 虚拟环境和诊断数据库。

## 8. 停止与重启

```powershell
scripts\stop_codeagent_vnext.bat
scripts\start_codeagent_vnext.bat
```

自定义目录或端口时，停止和启动必须传相同参数：

```powershell
$RuntimeRoot = 'D:\GWAPRuntime\VNext'
scripts\stop_codeagent_vnext.bat -RuntimeRoot $RuntimeRoot
scripts\start_codeagent_vnext.bat `
  -RuntimeRoot $RuntimeRoot `
  -RepositoryRoot $PWD `
  -Port 18766
```

随后重新运行兼容探针，并把 `-RuntimeUrl` 改为实际端口。

## 9. 仍然无法自动判断的项目

下列能力只能在公司实际 CodeAgent 环境中确认：

- 是否被企业策略禁用 PowerShell/shell；
- 是否被企业策略隐藏或禁止自定义 Skill；
- 是否允许启动本地 stdio MCP 子进程；
- 企业版对工具名前缀、审批和模型权限做了哪些二次修改；
- 所选公司模型是否真正执行了工具调用，而不是只复述 Skill 文本。

因此，最终上线门槛是：兼容探针为 `FULL_SKILL_MCP`，再用本节合成提示词完成一次真实 LLM
工具链验收。未获得企业数据出站授权前，不要把真实日志交给外部模型。
