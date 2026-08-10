# vNext 在新电脑上的 OpenCode 安装、实测与并行运行指南

本文用于在一台新的 Windows 11 电脑上，从零拉取并验证 Agent Runtime vNext。目标是：

- 只使用分支 `codex/agent-runtime-vnext-opencode`；
- 不切换、不覆盖 main 的工作目录、运行数据或个人 OpenCode 配置；
- 先用仓库内合成数据证明 OpenCode 的 LLM、Skill、MCP 和 Runtime 能真正跑通；
- 验证通过后，再显式决定是否接入真实日志和真实源码工作区。

## 1. 先理解本版本的边界

vNext 采用 External Agent 架构，而不是把 OpenCode 当作 FastAPI 进程内的一个模型 Provider：

```text
OpenCode LLM（最终推理）
        │
        ├── gw-ap-debug Skill（流程、证据和安全规则）
        │
        └── gw-ap-debug-vnext MCP（受控工具调用）
                    │
                    ▼
          FastAPI Agent Runtime
          日志解析 / RAG / Graph / Evidence / Job
```

已经实测确认 OpenCode 可以调用 Runtime、导入合成日志和源码、建立证据，并用自己的 LLM 输出带
真实 evidence ID 的最终诊断。当前 OpenCode 的最终文本仍保留在 OpenCode 会话中，尚不会回写成平台
权威 `AnalysisRun`；平台数据库中保存的是确定性的证据分析基线。

本分支实测版本是 **OpenCode 1.18.15（V1 配置格式）**。为得到可复现结果，本指南固定该版本。
OpenCode V2 的 MCP 配置结构已经改为 `mcp.servers`，且不再接受 V1 的 `enabled` 字段；升级前请先按
[OpenCode V2 迁移说明](https://opencode.ai/v2/docs/migrate-v1)修改配置，不要直接复用本文的 V1 JSON。

## 2. 新电脑准备

### 2.1 安装必要软件

建议安装：

- Git for Windows；
- Python 3.12 x64（项目支持 Python 3.11+，3.12 是本指南建议的稳定选择）；
- Node.js 20.19+ 或 22.12+（只做后端与 OpenCode 联调时不是必需；构建 Web 或跑 Full 验证时需要）；
- OpenCode CLI 1.18.15。

在新的 PowerShell 中执行版本检查：

```powershell
git --version
py -3.12 --version
node --version
npm --version
```

安装经过本分支实测的 OpenCode 版本：

```powershell
npm install -g opencode-ai@1.18.15
opencode --version
```

预期最后一条输出包含 `1.18.15`。OpenCode 官方还提供安装脚本、Scoop、Chocolatey 等方式，见
[OpenCode 官方安装文档](https://opencode.ai/docs)。官方在 Windows 上建议 WSL 以获得最佳体验；本分支的
实测记录使用的是 Windows 原生 PowerShell + npm 安装。

如果公司策略禁止全局 npm 包，请按公司批准的方式安装，但最终必须保证 `opencode` 在当前终端的
`PATH` 中，并用 `opencode --version` 确认实际运行的版本。

### 2.2 选择彼此隔离的目录

下面示例约定：

```text
D:\GWAP\debugplatform-vnext              vNext Git 工作目录
%LOCALAPPDATA%\GWAPDebugVNext             vNext Python、数据库、存储和 OpenCode 配置
D:\src\gateway                            待分析源码，可按实际情况修改
```

如果新电脑也要运行 main，请让 main 使用它原有的仓库目录、`%LOCALAPPDATA%\GWAPDebug` 和端口
`8765`；vNext 使用上面的独立目录和端口 `8766`。

## 3. 只拉取 vNext 分支

```powershell
$Repo = 'D:\GWAP\debugplatform-vnext'
git clone --branch codex/agent-runtime-vnext-opencode --single-branch `
  https://github.com/mrrabbitss/debug-platform.git $Repo
Set-Location $Repo

git branch --show-current
git status --short --branch
git log -1 --oneline
```

验收点：

- 当前分支必须是 `codex/agent-runtime-vnext-opencode`；
- `git status` 不应出现本地修改；
- 不要把这个目录再切换到 `main`。如果还需要 main，请另建一个目录重新 clone。

## 4. 建立 vNext 专用 Python 环境

不要在仓库里复用 main 的 `.venv`，也不要把依赖安装到系统 Python。下面把环境放到独立的
`GWAPDebugVNext` 目录：

```powershell
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
$Venv = Join-Path $RuntimeRoot 'venv'
New-Item -ItemType Directory -Force -Path $RuntimeRoot | Out-Null

py -3.12 -m venv $Venv
$VenvPython = Join-Path $Venv 'Scripts\python.exe'

& $VenvPython -m pip install --upgrade `
  --constraint .\backend\constraints.lock pip setuptools wheel
& $VenvPython -m pip install `
  --constraint .\backend\constraints.lock -e ".\backend[dev]"

& $VenvPython -c "import fastapi, uvicorn, httpx, app; print('VNEXT_PYTHON_OK')"
```

预期最后输出 `VNEXT_PYTHON_OK`。如果电脑没有 `py` 启动器，可以先确认 `python --version` 为
3.11 或更高，再把创建命令替换为：

```powershell
python -m venv $Venv
```

以后每次打开新 PowerShell，重新执行以下三行即可恢复变量，无需激活虚拟环境：

```powershell
$Repo = (Resolve-Path 'D:\GWAP\debugplatform-vnext').Path
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
$VenvPython = Join-Path $RuntimeRoot 'venv\Scripts\python.exe'
```

## 5. 先单独确认 OpenCode 的 LLM 可用

列出当前 OpenCode 能看到的模型：

```powershell
opencode --version
opencode models
```

本分支的无密钥合成测试使用：

```powershell
Set-Location $Repo
opencode run --model opencode/deepseek-v4-flash-free --format json --dir $Repo `
  "Reply with exactly OPENCODE_LLM_OK and nothing else."
```

输出事件中应出现 `OPENCODE_LLM_OK`。如果该免费模型已下线、限流或当前网络无法访问：

1. 在 OpenCode 中执行 `/connect`，配置公司批准的 Provider；
2. 用 `opencode models` 查到精确模型 ID；
3. 将后续命令的 `-Model` 参数改成该 ID。

这一步会向所选模型发送上面的测试句。不要在尚未完成公司授权和 endpoint 审核时发送真实日志、
源码、密钥或内部地址。

## 6. 一条命令完成安全的真实链路验收

这是新电脑上最重要的验收步骤。脚本会真正启动本地 Runtime、让 OpenCode 加载 Skill、连接 MCP、
调用自己的 LLM，并完成六个诊断工具调用；它只使用 `sample_data` 下的合成数据。

```powershell
Set-Location $Repo
Set-ExecutionPolicy -Scope Process Bypass
& .\scripts\test_opencode_integration.ps1 `
  -PythonExe $VenvPython `
  -Model 'opencode/deepseek-v4-flash-free'
```

也可以使用批处理入口：

```bat
scripts\test_opencode_integration.bat -PythonExe "%LOCALAPPDATA%\GWAPDebugVNext\venv\Scripts\python.exe" -Model opencode/deepseek-v4-flash-free
```

一次完整调用通常需要几分钟。脚本会自动完成以下隔离：

- 从系统临时目录创建一次性工作区、SQLite、Storage 和模型目录；
- 随机选择 loopback 端口，不占用 main 的 `8765`；
- 使用临时 `OPENCODE_CONFIG` 和临时 XDG config/data/cache；
- 不读取或覆盖个人 OpenCode 配置；
- 禁止 shell、编辑、子 Agent、Web 和外部目录工具；
- 只导入 `sample_data/collectDebuginfo_demo.zip` 和合成源码；
- 结束后停止准确的 Runtime PID 并清理临时目录。

成功时最终 JSON 应至少包含：

```json
{
  "status": "PASS",
  "mcp_connected": true,
  "artifact_count": 2,
  "event_count": 10,
  "analysis_count": 1,
  "analysis_provider": "deterministic",
  "analysis_model": "rule+agentic-evidence",
  "external_llm_result_persisted": false
}
```

`tool_calls` 还应包含 `skill`、`debug_status`、`debug_create_case`、`debug_ingest`、
`debug_attach_workspace`、`debug_diagnose`、`debug_evidence_bundle`，最终摘要中应出现：

- `OPENCODE_GWAP_E2E_PASS`；
- 真实 `CASE_ID`；
- 引用 `EVT-*` 的 `CONFIRMED` 和 `PROBABLE`；
- `UNKNOWN` 或 missing-information 说明。

只有同时满足这些条件，才算“OpenCode 的 LLM 真正通过 Skill + MCP 使用了本系统”，而不只是
Runtime 或模型各自单独可用。

测试失败且需要保留临时日志时，增加 `-KeepArtifacts`：

```powershell
& .\scripts\test_opencode_integration.ps1 `
  -PythonExe $VenvPython `
  -Model 'opencode/deepseek-v4-flash-free' `
  -KeepArtifacts
```

脚本会在输出的 `artifacts_path` 指明临时目录。排错完成后手动删除该特定目录；不要对系统临时目录
做递归清理。

## 7. 运行仓库回归验收

### 7.1 无 Node 的后端/契约验收

```powershell
Set-Location $Repo
$env:PYTHONPATH = Join-Path $Repo 'backend'

& $VenvPython -m pytest .\backend\tests -q
& $VenvPython .\scripts\check_repo_harness.py
& $VenvPython .\scripts\check_architecture.py
& $VenvPython .\scripts\run_agent_runtime_e2e.py

Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
```

本分支在归档前的基准结果是：

- 后端：172 passed、1 skipped、0 failed，行覆盖率 75.46%；
- Repository Harness：13/13 PASS，Workflow/OpenAPI 共 25 个操作；
- Architecture gate：PASS；
- Windows Agent Runtime E2E：exit code 0。

### 7.2 带前端和依赖重装的标准验收

Node/npm 已安装时，可运行仓库标准入口：

```bat
scripts\validate_all.bat Fast
scripts\validate_all.bat Full
```

`Full` 会按锁文件重新安装依赖并比 `Fast` 花费更长时间。Docker/PostgreSQL/Qdrant 可用时才运行：

```bat
scripts\validate_all.bat External
```

验证报告写入被 Git 忽略的 `artifacts\validation\...`。不要提交这些运行产物。

## 8. 以并行、持久方式启动 vNext Runtime

完成合成 E2E 后，如需长期使用 vNext，请使用本节的显式隔离配置。**不要执行默认的
`scripts\install_agent_runtime.bat`**；该安装器仍使用 main 相同的全局 Skill/MCP 名和
`%LOCALAPPDATA%\GWAPDebug`。

### 8.1 终端 A：启动独立 Runtime

按实际源码位置修改 `$WorkspaceAllowlist`，只允许必要的最窄父目录；多个根目录用分号分隔。

```powershell
$Repo = (Resolve-Path 'D:\GWAP\debugplatform-vnext').Path
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
$VenvPython = Join-Path $RuntimeRoot 'venv\Scripts\python.exe'
$DataRoot = Join-Path $RuntimeRoot 'data'
$StorageRoot = Join-Path $RuntimeRoot 'storage'
$ModelRoot = Join-Path $RuntimeRoot 'models'
$WorkspaceAllowlist = 'D:\src'

New-Item -ItemType Directory -Force `
  -Path $DataRoot, $StorageRoot, $ModelRoot | Out-Null
$Db = (Join-Path $DataRoot 'gw_ap_debug_vnext.db').Replace('\', '/')

$env:PYTHONPATH = Join-Path $Repo 'backend'
$env:APP_ENV = 'dev'
$env:AUTH_MODE = 'local'
$env:AGENT_MODE = 'external'
$env:AGENT_RUNTIME_HOST = '127.0.0.1'
$env:AGENT_RUNTIME_PORT = '8766'
$env:SERVE_FRONTEND = 'false'
$env:DATA_ROOT_PATH = $DataRoot
$env:DATABASE_URL = "sqlite:///$Db"
$env:STORAGE_ROOT = $StorageRoot
$env:MODEL_ROOTS = $ModelRoot
$env:WORKSPACE_ROOTS = $WorkspaceAllowlist
$env:CORS_ORIGINS = 'http://127.0.0.1:8766,http://localhost:8766'

Set-Location $Repo
& $VenvPython -m uvicorn app.main:app --host 127.0.0.1 --port 8766
```

保持终端 A 打开。这里使用 `APP_ENV=dev` 是因为本机单用户 `AUTH_MODE=local` 在 `prod` 中会被安全
校验拒绝；正式多人环境应改用 `api_key` 或 `rbac`，不要为了启动而绕过校验。

### 8.2 终端 B：确认 Runtime 健康

```powershell
$Health = Invoke-RestMethod 'http://127.0.0.1:8766/api/v1/health'
$Health | ConvertTo-Json -Depth 6
```

预期 `status` 为 `ok`。失败时先查看终端 A 的完整异常，不要继续配置 MCP。

### 8.3 终端 B：生成隔离的 OpenCode V1 配置

下面配置不会写入用户全局 `opencode.json`。它使用独立 XDG 目录，并把 MCP 命名为
`gw-ap-debug-vnext`：

```powershell
$Repo = (Resolve-Path 'D:\GWAP\debugplatform-vnext').Path
$RuntimeRoot = Join-Path $env:LOCALAPPDATA 'GWAPDebugVNext'
$VenvPython = Join-Path $RuntimeRoot 'venv\Scripts\python.exe'
$ConfigPath = Join-Path $RuntimeRoot 'opencode.v1.json'
$OpenCodeConfigDir = Join-Path $RuntimeRoot 'opencode-config-dir'

$env:OPENCODE_CONFIG = $ConfigPath
$env:OPENCODE_CONFIG_DIR = $OpenCodeConfigDir
$env:XDG_CONFIG_HOME = Join-Path $RuntimeRoot 'opencode-xdg-config'
$env:XDG_DATA_HOME = Join-Path $RuntimeRoot 'opencode-xdg-data'
$env:XDG_CACHE_HOME = Join-Path $RuntimeRoot 'opencode-xdg-cache'

New-Item -ItemType Directory -Force -Path `
  $OpenCodeConfigDir, $env:XDG_CONFIG_HOME, $env:XDG_DATA_HOME, $env:XDG_CACHE_HOME | Out-Null

$Config = [ordered]@{
  '$schema' = 'https://opencode.ai/config.json'
  model = 'opencode/deepseek-v4-flash-free'
  permission = [ordered]@{
    '*' = 'deny'
    read = 'allow'
    glob = 'allow'
    grep = 'allow'
    list = 'allow'
    skill = 'allow'
    'gw-ap-debug-vnext_*' = 'allow'
    'gw_ap_debug_vnext_*' = 'allow'
    edit = 'deny'
    bash = 'deny'
    task = 'deny'
    webfetch = 'deny'
    websearch = 'deny'
    external_directory = 'deny'
  }
  mcp = [ordered]@{
    'gw-ap-debug-vnext' = [ordered]@{
      type = 'local'
      command = @($VenvPython, '-m', 'app.agent_runtime.mcp_server')
      enabled = $true
      timeout = 30000
      environment = [ordered]@{
        PYTHONPATH = (Join-Path $Repo 'backend')
        GWAP_RUNTIME_URL = 'http://127.0.0.1:8766'
        GWAP_AGENT_ROLE = 'ENGINEER'
      }
    }
  }
}

$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText(
  $ConfigPath,
  ($Config | ConvertTo-Json -Depth 12),
  $Utf8NoBom
)

Set-Location $Repo
opencode mcp list --pure
```

预期看到 `gw-ap-debug-vnext` 和 `connected`。OpenCode 的配置通常会按优先级合并；这里同时设置
`OPENCODE_CONFIG` 和独立 XDG 目录，是为了避免继承 main 或个人配置。配置规则见
[OpenCode V1 配置文档](https://opencode.ai/docs/config/)和
[OpenCode V1 MCP 文档](https://opencode.ai/docs/mcp-servers/)。

注意：独立 XDG data 目录也意味着它不会自动继承个人 Provider 登录状态。免费模型不可用时，请在
当前这些环境变量仍生效的终端里重新执行 `/connect`，或按公司批准方式配置 Provider。

### 8.4 终端 B：启动诊断会话

从 vNext 仓库根启动，使 OpenCode 只加载该分支中的 `.claude/skills/gw-ap-debug`，不会安装或覆盖
用户全局 Skill：

```powershell
Set-Location $Repo
opencode
```

可使用下面的首轮提示词，把路径改为已获准处理的真实文件：

```text
Load the gw-ap-debug skill. Use only the gw-ap-debug-vnext MCP tools as the
diagnostic data plane. First call debug_status and confirm External Agent Mode.

Then:
1. Create a case with debug_create_case and confirm_write=true.
2. Ingest the approved log/archive at D:\approved-data\collectDebuginfo.zip with
   debug_ingest, wait=true, timeout_seconds=180, confirm_write=true.
3. Attach D:\src\gateway read-only with debug_attach_workspace, index=true,
   wait=true, timeout_seconds=180, confirm_write=true.
4. Run debug_diagnose, then obtain debug_evidence_bundle with a focused query.
5. Perform your own final reasoning. Separate CONFIRMED, PROBABLE and UNKNOWN,
   cite only real evidence IDs, and state the missing evidence needed to confirm.
6. Do not generate a report or modify source unless I explicitly ask.
```

当前安全配置禁止 OpenCode 原生 shell/edit 和外部目录读取；源码由 Runtime 以只读方式 attach、索引并
通过 evidence 返回。这足以验证诊断链路，也能避免一次测试误改源码。如果后续确实需要 OpenCode
原生编辑与执行测试，应在源码仓库中另建 Git 分支/工作树，再由使用者明确调整权限；不要直接对
生产工作目录放开写权限。

## 9. 可选 Web UI

只在需要浏览大日志、时间线、图谱、Trace、设置或报告时构建前端：

```powershell
Set-Location (Join-Path $Repo 'frontend')
npm ci
$env:VITE_PUBLIC_BASE = '/ui/'
npm run build
```

停止终端 A（`Ctrl+C`），然后重新按 8.1 节启动，但改为：

```powershell
$env:SERVE_FRONTEND = 'true'
$env:FRONTEND_DIST = Join-Path $Repo 'frontend\dist'
```

浏览器访问 `http://127.0.0.1:8766/ui/`。不要把 Uvicorn 绑定到 `0.0.0.0`，除非已经完成鉴权、
防火墙和网络暴露评审。

## 10. 停止、重启和更新

- 前台 Runtime：在终端 A 按 `Ctrl+C`；
- 重启：重新执行 8.1 节；
- OpenCode：退出会话即可，Runtime 可以继续运行；
- 持久数据：位于 `%LOCALAPPDATA%\GWAPDebugVNext\data` 和 `storage`；
- main 数据：仍位于它自己的目录，不应被本流程访问。

更新 vNext 前先停止 Runtime，然后：

```powershell
Set-Location 'D:\GWAP\debugplatform-vnext'
git status --short
git pull --ff-only origin codex/agent-runtime-vnext-opencode
```

只有工作区干净时再 pull。依赖锁发生变化后，重新执行第 4 节的两条 pip 安装命令，然后重新跑第 6
节 E2E。

## 11. 常见故障定位

### `opencode` 不是预期版本

```powershell
Get-Command opencode -All
opencode --version
```

如果显示 V2，不要继续使用本文 V1 配置。可以固定回已验证版本：

```powershell
npm install -g opencode-ai@1.18.15
```

或按 [OpenCode V2 MCP 文档](https://opencode.ai/v2/docs/mcp-servers)迁移后另行验证。

### `mcp list` 显示 disconnected

依次检查：

```powershell
Invoke-RestMethod 'http://127.0.0.1:8766/api/v1/health'
$env:OPENCODE_CONFIG
Test-Path $env:OPENCODE_CONFIG
Test-Path $VenvPython
& $VenvPython -c "import app; print(app.__file__)"
```

确认配置中的 `PYTHONPATH` 指向当前 vNext 的 `backend`，端口与 Runtime 一致，并查看终端 A 的错误。

### `ModuleNotFoundError` 或 pytest 不存在

确认命令使用 `$VenvPython`，然后重新执行第 4 节安装。不要退回系统 Python。

### Runtime 拒绝 attach workspace

- 路径必须是绝对、存在的目录；
- 不能把符号链接当作工作区根；
- 路径必须位于 `WORKSPACE_ROOTS` 指定的 allowlist 内；
- 多个 Windows 根目录用分号分隔，例如 `D:\src;E:\approved-src`。

### 端口 8766 被占用

```powershell
Get-NetTCPConnection -LocalPort 8766 -ErrorAction SilentlyContinue
```

选择另一个未占用端口，并同时修改 Uvicorn、`AGENT_RUNTIME_PORT`、`GWAP_RUNTIME_URL`、MCP 配置和
健康检查 URL，不能只改其中一处。

### 模型不存在、限流或登录失败

先用 `opencode models` 取得当前真实 ID；在隔离 XDG 环境生效的同一终端重新 `/connect`。如果使用
公司模型，确认该 endpoint 已批准，并再次用合成数据完成 E2E 后再处理真实数据。

### OpenCode 运行成功但平台没有保存最终文字

这是当前已知边界，不是链路失败。`external_llm_result_persisted=false` 表示 OpenCode 最终结论仍在其
会话中；Runtime 已保存案例、制品、事件和确定性分析。External Agent 结论回写仍是计划能力。

## 12. 最终验收清单

- [ ] Git 当前分支为 `codex/agent-runtime-vnext-opencode`；
- [ ] vNext 仓库、运行目录、端口都与 main 分离；
- [ ] `opencode --version` 为本指南验证的 `1.18.15`，或已完成单独的 V2 迁移验证；
- [ ] Python import 输出 `VNEXT_PYTHON_OK`；
- [ ] OpenCode 单模型探针成功；
- [ ] `test_opencode_integration.ps1` 输出 `status=PASS`；
- [ ] 工具调用包含 Skill 和六步 MCP 链；
- [ ] 最终诊断引用真实 evidence ID，并区分 CONFIRMED/PROBABLE/UNKNOWN；
- [ ] 仓库后端、Harness、Architecture 和 Runtime E2E 通过；
- [ ] 真实数据使用前已获得明确同意，并确认所选模型 endpoint 符合公司策略；
- [ ] 没有执行默认单实例安装器覆盖 main 的全局 Skill/MCP 或运行目录。
