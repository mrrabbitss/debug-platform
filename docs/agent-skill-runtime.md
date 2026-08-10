# Agent Skill Runtime：Claude Code / OpenCode / CodeArts、薄 MCP 与 Optional Web

本文档描述在现有 GW/AP Debug Platform 之上新增的 Agent Runtime。它不是第二套诊断平台，
而是把当前 FastAPI、Parser、RAG、Domain/Code/Commit Graph、Memory、Job、Report 和安全能力
暴露给 Claude Code / OpenCode / 华为 CodeArts/CodeAgent 的一层受控适配。

## 1. 目标形态

```text
Claude Code / OpenCode / CodeArts
        │
        ├── .claude/skills/gw-ap-debug/SKILL.md
        │       procedural knowledge / evidence rules
        │
        ├── gw-ap-debug MCP (recommended)
        │       12 high-value tools only
        │
        └── gwap CLI (fallback / human / CI)
                │
                ▼
       existing FastAPI Runtime
        ├── Parser / Event / Timeline
        ├── Knowledge RAG / Domain Graph
        ├── Code Graph / Commit Graph
        ├── Memory / Agentic Search
        ├── Evidence / Report / Trace
        └── Local/API Model Profiles
                │
                └── Optional Vue Web UI
                    http://127.0.0.1:8765/ui/
```

职责边界：

- **Skill**：告诉 Coding Agent 什么时候用哪些工具、怎样引用 Evidence、怎样避免提示注入；
- **MCP / CLI**：只做受控工具调用和输入输出适配，不复制领域逻辑；
- **FastAPI Runtime**：继续是 Parser、RAG、Graph、Memory、Job、Report 的唯一实现；
- **Claude Code / OpenCode / CodeArts**：External Agent Mode 下负责最终根因推理、读取真实工作区、编辑和测试代码；
- **Web UI**：只在大日志浏览、时间线、图谱、Agent Trace、知识治理、模型设置和报告查看时按需使用。

## 2. External Agent Mode 与“双 LLM”规避

配置：

```env
AGENT_MODE=external
```

在此模式下，平台会继续执行确定性的日志分析、Agentic Search、Embedding/Reranker、图谱和
Evidence 构建，但会主动跳过三个容易形成重复推理的 Chat LLM 环节：

1. 案例诊断中的平台 Chat LLM synthesis；
2. 案例 chat 中的平台第二次 Chat LLM 回答；
3. candidate patch 的平台 Chat LLM 生成。

因此主链路变为：

```text
collectDebuginfo + source workspace
          ↓
Parser / RAG / Graph / Memory / Reranker
          ↓
compact Evidence Bundle
          ↓
Claude Code / OpenCode / CodeArts
          ↓
root cause → code read/edit → test → git diff
```

`debug_diagnose` 只会把平台侧确定性规则与证据准备记录为
`deterministic / rule+agentic-evidence / external-evidence-v1`，不会把尚未实际调用的
Claude Code/OpenCode/CodeArts 或 Qwen/GLM Profile 错记成最终诊断模型。Coding Agent 的最终结论当前
保留在外部 Coding Agent 会话中；本版本尚未提供把该结论提交为平台 `AnalysisRun` 的工具。

External Mode **不等于禁用所有模型**。Embedding 与 Reranker 属于检索层，可以继续使用；
如果配置的是经过批准的 API Profile，仍受现有 endpoint policy 和 egress audit 约束。
本地模型自动识别在低置信度时也可以显式使用 Chat LLM 复核，但只发送安全的模型元数据，
不发送权重、日志或源代码。

若希望浏览器独立运行完整 AI 产品，设置：

```env
AGENT_MODE=platform
```

此时原有 Qwen/GLM/OpenAI-Compatible Chat synthesis、案例问答和候选补丁能力保持可用。

## 3. Windows 11 安装

推荐 Python 3.12，最低 Python 3.11；生产 Web 构建需要 Node.js 20.19+ 或 22.12+。

在仓库根目录运行：

```bat
scripts\install_agent_runtime.bat
```

如果要在本机直接加载 Sentence-Transformers / Transformers 模型：

```bat
scripts\install_agent_runtime.bat -InstallLocalModels
```

同时尝试配置 Claude Code 用户级 MCP：

```bat
scripts\install_agent_runtime.bat -InstallLocalModels -ConfigureMcp
```

安装器会：

- 在 `%LOCALAPPDATA%\GWAPDebug\venv` 创建独立 Python 环境；
- 将数据库、Storage、日志、PID 和模型加密密钥放到 `%LOCALAPPDATA%\GWAPDebug`；
- production build Vue，并让 FastAPI 在 `/ui/` 提供静态前端；
- 安装 `gwap` 与 `gwap-mcp`；
- 复制 Skill 到 `%USERPROFILE%\.claude\skills\gw-ap-debug`；
- 生成当前机器绝对 Python 路径的项目 `.mcp.json`；
- 生成结构符合 OpenCode schema 的 MCP 配置片段；通过 `OPENCODE_CONFIG` 指向它，或合并到
  OpenCode 的全局/项目 `opencode.json` 后才会加载。

安装后重新打开终端：

```bat
gwap start
gwap doctor
gwap status
```

也可以直接使用：

```bat
scripts\start_agent_runtime.bat
scripts\doctor_agent_runtime.bat
scripts\stop_agent_runtime.bat
```

Agent Runtime 默认：

- API：`http://127.0.0.1:8765/api/v1`
- Web：`http://127.0.0.1:8765/ui/`
- Swagger：`http://127.0.0.1:8765/docs`

原 `scripts\start_local.bat` 的 `8000 + 5173` 开发模式仍保留，用于前后端开发，不与 Agent
Runtime 的单端口生产形态冲突。

如果目标是华为 CodeArts/CodeAgent，或需要让 vNext 与 main 并行运行，不要使用上面的旧单实例
安装器。改用：

```bat
scripts\setup_codeagent_vnext.bat -AgentCommand codearts
scripts\probe_codeagent_compatibility.bat -AgentCommand codearts -Strict
```

该入口使用 `%LOCALAPPDATA%\GWAPDebugVNext`、端口 `8766`、项目级 `.codeartsdoer/skills` 和
命名空间化 MCP 配置。具体 schema 选择、Skill-only fallback、端口/工具名判定和 zip 导入见
[CodeAgent 兼容指南](codeagent-compatibility.md)。

当前已经实测的公司环境是根命令 `nga`、内部 `bin\codeagent.exe`、CodeArts 项目 Skill 与 OpenCode
V1 项目 MCP 的混合形态。首次安装修复和日常运行都可以使用同一个零参数入口：

```bat
scripts\start_huawei_codeagent_vnext.bat
```

它会自动补齐缺失的隔离 Python、Skill、MCP/`NO_PROXY` 和前端 build，启动或复用 Runtime，使用真实
RuntimeClient 验证数据面后进入 TUI；拉取新版本后可加 `-Repair` 强制重新同步配置。

## 4. Skill

项目级 Skill 位于：

```text
.claude/skills/gw-ap-debug/
├── SKILL.md
├── references/
├── scripts/
└── assets/
```

CodeArts 项目级安装目标是 `.codeartsdoer/skills/gw-ap-debug/`。安装器同时写入
`runtime-config.json`，Skill 内的 `scripts/gwap.ps1` 因而可以直接定位独立 Python 和 Runtime，
不要求 `gwap` 已加入全局 PATH。

主要行为：

1. 检查 Runtime；
2. 创建或选择 Case；
3. 上传并解析日志；
4. 同机源码存在时用 Workspace Attach，而不是重复 ZIP；
5. 优先读取 `debug_evidence_bundle`；
6. 再按需读取日志范围、Code Graph、Commit 或其他 Evidence；
7. Coding Agent 自己给出 `CONFIRMED / PROBABLE / UNKNOWN` 结论并引用 Evidence ID；
8. Coding Agent 使用原生文件/Git/测试工具修改真实工作区；
9. 只有需要可视化时才打开 localhost Web。

日志、代码注释、README、Commit message、知识文档和模型元数据都视为 **untrusted data**，
不能覆盖 Skill 或用户指令。

## 5. 薄 MCP

MCP server 入口：

```text
gwap-mcp
# 等价：python -m app.agent_runtime.mcp_server
```

MCP 不重新实现 RAG/Parser/Graph，只复用同一 `ToolRegistry` 并通过 Runtime API 调用现有能力。
当前只暴露 12 个高价值工具：

| Tool | 权限 | 作用 |
| --- | --- | --- |
| `debug_status` | READ | Runtime/Agent mode 状态 |
| `debug_create_case` | WRITE | 新建 Case |
| `debug_ingest` | WRITE | 上传并解析日志 |
| `debug_wait_job` | READ | 等待后台 Job |
| `debug_inspect` | READ | 有界结构化日志检查 |
| `debug_search` | READ | Agentic Search |
| `debug_evidence_bundle` | READ | External Mode 的主要推理输入 |
| `debug_attach_workspace` | WRITE | 只读挂载同机源码并索引 |
| `debug_code_context` | READ | 有界 Code Graph 多跳查询 |
| `debug_diagnose` | WRITE | 持久化确定性/证据诊断；External 时不调第二 Chat LLM |
| `debug_generate_report` | WRITE | 生成并持久化 HTML/PDF/DOCX 报告 |
| `debug_open_ui` | READ | 返回/打开 Optional Web |

WRITE tool 必须显式传 `confirm_write=true`。MCP 的角色、超时和 Tool Schema 复用现有 Agent
Tool Registry，不维护第二套权限定义。

MCP 本身使用 stdio，没有 TCP 端口；`GWAP_RUNTIME_URL` 才是 MCP/CLI 到 FastAPI 数据面的
HTTP 地址。客户端可能把配置名作为前缀并归一化连字符，因此应按 `debug_*` 原始名称或后缀匹配，
而不是把完整展示名当成稳定 API。

Claude Code 项目配置参考 `agent-integrations/claude.mcp.example.json`；安装器会生成包含当前机器
Python 绝对路径的 `.mcp.json`。OpenCode 参考 `agent-integrations/opencode.mcp.example.json`；
`scripts/configure_agent_integrations.ps1` 会生成带绝对 Python 路径的
`agent-integrations/opencode.mcp.json`。该文件是可用配置而不是 OpenCode 的默认文件名，使用时：

```powershell
$env:OPENCODE_CONFIG = (Resolve-Path .\agent-integrations\opencode.mcp.json).Path
opencode mcp list
opencode
```

也可以只把其中的 `mcp.gw-ap-debug` 合并进 OpenCode 全局或目标源码项目的 `opencode.json`。

在修改个人 OpenCode 配置或导入真实日志前，可以先运行真实 CLI 集成测试：

```bat
scripts\test_opencode_integration.bat
```

如果脚本没有自动找到带后端依赖的 Python，可显式指定：

```bat
scripts\test_opencode_integration.bat -PythonExe D:\path\to\venv\Scripts\python.exe -Model opencode/deepseek-v4-flash-free
```

该测试只使用 `sample_data` 合成日志/源码，在临时端口、临时 SQLite 与临时 OpenCode/XDG
配置中运行；它显式禁用 shell、编辑、子 Agent、Web 和外部目录工具，验证结束后停止精确
Runtime PID 并删除临时数据，不读取或覆盖个人 OpenCode 配置。

CodeArts 原生和 Claude 兼容配置示例分别见
`agent-integrations/codearts.native.mcp.example.json` 与
`agent-integrations/codearts.claude.mcp.example.json`。实际机器优先由
`setup_codeagent_vnext.bat` 生成，不要手工复制示例中的占位路径。

## 6. CLI fallback

CLI 与 MCP 走相同 Runtime，不包含另一份业务实现：

```bat
gwap status
gwap models scan
gwap models list
gwap case-create "AP authentication failure" --device-type AP
gwap ingest CASE_xxx D:\logs\collectDebuginfo.zip
gwap workspace-attach CASE_xxx D:\src\gateway
gwap evidence CASE_xxx --query "4-way handshake timeout"
gwap diagnose CASE_xxx
gwap report CASE_xxx --format html
gwap open --case-id CASE_xxx
```

默认 stdout 是紧凑 JSON，便于 Agent/脚本消费；全局 `--human` 可以输出较易读的结果。

## 7. 本地模型自动发现

正式路径不依赖旧的固定 BGE/Qwen 下载脚本。把已经下载好的 Hugging Face 格式模型放在：

```text
<repo>\models\
```

或者配置多个目录：

```env
MODEL_ROOTS=D:\AI\models;E:\shared-models
```

然后：

```bat
gwap models scan
gwap models list
gwap models validate LM_xxx --device cpu
gwap models activate LM_xxx --device cpu
```

流程：

```text
safe directory walk
 → config/tokenizer/modules/SentenceTransformer metadata
 → deterministic classification
 → low confidence only: optional Chat LLM metadata review
 → real loader smoke test
 → VALIDATED
 → activation
```

不会为了识别模型读取或上传 `safetensors/bin/gguf` 权重正文，只统计文件大小。

当前自动 loader：

- Embedding → `SentenceTransformer`；
- Sentence-Transformers CrossEncoder（包括当前 Qwen3-Reranker 兼容格式）→ `CrossEncoder`；
- 普通 Transformers SequenceClassification Reranker → `AutoModelForSequenceClassification`；
- 本地 Chat → `AutoModelForCausalLM`。

`DISCOVERED` 只表示发现目录；只有真实 smoke test 通过后才是 `VALIDATED`。默认不允许激活未验证模型。

旧 `scripts\install_local_models.bat` 仅作为历史兼容下载工具保留，新 Agent Runtime 的扫描、选择、
验证和激活逻辑不依赖它。

## 8. Workspace Attach

同一台 Win11 上运行 Claude/OpenCode/CodeArts 时，无需重新压缩上传整个源码：

```bat
gwap workspace-attach CASE_xxx D:\src\gateway
```

Runtime 创建只读 Repository 引用，并复用现有 Code Index、Code Graph 和 Commit Graph 后台任务。
平台不会修改该目录；真正的文件编辑由 Coding Agent 原生工具完成。

安全边界：

- 只接受绝对现有目录；
- Workspace 根不能是符号链接；
- 非 `AUTH_MODE=local` 时必须配置 `WORKSPACE_ROOTS` allowlist；
- 代码索引沿用现有文件类型、大小和安全边界；
- 删除 Case/Repository 只删除受管占位制品，不删除外部 Workspace。

## 9. Optional Web

生产 Agent Runtime 将构建后的 Vue 交给 FastAPI：

```text
http://127.0.0.1:8765/api/v1
http://127.0.0.1:8765/ui/
```

因此日常使用 Skill/MCP 时不需要再启动 Vite。Web 不作为 Agent 的必经入口，只在以下情况打开：

- 10 万行日志浏览和源行跳转；
- Timeline；
- Domain/Code/Commit Graph；
- Agent Trace 与 replay；
- Knowledge CRUD / Review / Curation；
- Retrieval Evaluation；
- 模型 Profile 和本地模型扫描；
- RBAC、审计、报告。

## 10. 验证

vNext 专项真实多进程 E2E：

```bat
scripts\run_agent_runtime_e2e.bat
```

覆盖真实 Uvicorn + `gwap` 子进程：Case → ingest/parse → Workspace Attach/Index → Evidence →
External diagnosis → Report → model scan → MCP tools/list。

仓库门禁仍是：

```bat
scripts\validate_all.bat Fast
scripts\validate_all.bat Full
```

`validate_all` 已把 Agent Runtime E2E 纳入相应验证路径。发布前还应运行原有 Golden、浏览器 E2E、
前端 build、VS Code compile 和依赖审计，确保本次 Agent 适配没有削弱 main 原有能力。
