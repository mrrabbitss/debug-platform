# GW/AP Intelligent Debug Platform

项目所有已交付能力、在研需求、依赖关系和冲突约束统一维护在
[CAPABILITIES.md](CAPABILITIES.md)。工程 Harness 的现状和后续顺序见
[HARNESS_ENGINEERING.md](HARNESS_ENGINEERING.md)，专题文档入口见
[docs/README.md](docs/README.md)，最近一次可复核验证见
[VALIDATION.md](VALIDATION.md)。新增或调整功能时必须同步更新相应总账。

面向 GW、AP、全光网关等网络设备的 collectDebuginfo 日志解析、证据关联、RAG 检索、LLM 综合诊断、代码仓库关联和报告生成平台。

本仓库是可运行的完整工程，不依赖真实企业数据。默认使用规则引擎和 Mock LLM，因此没有模型密钥也能完成演示；配置公司批准的 Qwen、GLM 或其他 OpenAI-Compatible API 后，会启用受证据约束的 LLM 综合分析与候选补丁生成。

### Agent Runtime vNext：Skill + Tools + Optional Web

在保留上述 Web 独立产品全部能力的基础上，仓库现在同时支持 Claude Code、OpenCode 和华为
CodeArts/CodeAgent 作为主交互与
最终推理层：`.claude/skills/gw-ap-debug` 提供 Agent Skill，`backend/app/agent_runtime` 提供 12 个
高价值 Tool 的薄 MCP 与 `gwap` CLI，FastAPI 继续作为唯一诊断/RAG/Graph Runtime。
`AGENT_MODE=external` 会跳过平台 Chat LLM 的诊断 synthesis、案例 chat 和候选 patch 二次推理，
避免“Claude → Qwen/GLM → Claude”的双 LLM 链；Embedding/Reranker 仍可作为检索模型使用。
生产 Agent Runtime 将构建后的 Vue 挂在同一 `127.0.0.1:8765/ui/`，浏览器只在需要大日志、
图谱、Trace、知识治理、设置或报告时按需打开。完整说明见
[Agent Skill Runtime 文档](docs/agent-skill-runtime.md)。针对 OpenCode 魔改版，仓库提供 CodeArts
项目级 Skill 安装、四种 MCP schema 生成、stdio 握手与端口/工具名探测，详见
[CodeAgent Skill 导入与兼容探测](docs/codeagent-compatibility.md)。

## 1. 已实现能力

### P0：基础闭环

- 创建 GW/AP 故障案例；
- 上传 ZIP/TAR/TGZ、常见单个日志或无后缀纯文本 collectDebuginfo；
- 安全解压，防止 Zip Slip、符号链接和超限压缩包；
- collectDebuginfo 文件清单与原始日志浏览；
- 10 万行以上文本的稀疏行索引、任意行跳转和原始日志关键字搜索；
- 日志编码识别、时间戳标准化、敏感信息脱敏；
- 按内容识别华为 GW/AP 采集包中的 `Start run collect command:` 命令段和 `NOTICE 2026-... 03:29:17.483` 运行日志；
- hostapd、WLAN、DHCP、PPPoE、PON、OMCI、TR-069、内核和进程异常规则；
- 关键事件提取与时间线；
- 解析结果按代次原子发布，失败重解析不会覆盖上一次可用结果；
- 可持久恢复的后台任务，以及幂等键、lease/heartbeat、安全取消、退避重试、dead-letter 和资源预算；
- 确定性规则诊断；
- HTML、PDF、Word 报告。

### P1：可信诊断与知识增强

- 产品文档、协议文档、测试规范、历史案例知识库；
- 诊断规则、故障树、解决方案和参考资料的分层分类管理；
- 知识正文新增、上传、修改、删除和修改后自动重建索引；
- 按章节和段落切分；
- 错误码、函数名、文件路径、中文语义共同参与的本地混合检索；
- 可切换的内置 Hashing、本地 BGE、OpenAI-Compatible Embedding；
- 可切换的本地 Qwen3 Reranker 和 Qwen Rerank API；
- 设备类型、模块和可信等级元数据；
- 结构化 Markdown 故障案例（错误形式、日志分析、错误定位、解决方案和验证）；
- 上传包含日志、错误现象、分析和解决方案的文件夹，由大模型生成带来源行号的案例草稿；
- 在独立工作台与模型多轮纠错、人工编辑和恢复历史版本，确认后才创建待审核知识草稿；
- 错误分析 Skill 管理，以及从案例/Skill 确定性提炼可复用分析方法；
- 知识草稿、待审核、发布、驳回、归档状态机，不可变版本、回滚和数据库乐观锁；
- 固定 query、预期证据和预期根因的检索评测集，以及 Recall@K、Precision@K、MRR、NDCG@K；
- 人工诊断反馈审核，通过后只生成待二次审核的知识草稿；
- 证据 ID、支持证据、反证、不确定性和缺失信息；
- Qwen、GLM 和内部模型的统一 OpenAI-Compatible 适配器；
- 前端保存多套模型配置、连接测试和运行时切换；
- Mock 降级、模型连接检测、超时和重试；
- 基于当前案例的多轮问答。

### P2：代码仓库、图谱与认知检索

- 上传代码归档，或上传保留完整历史的 Git Bundle；
- 提取 C/C++、Python、Java、JavaScript/TypeScript、Go 符号；
- 建模 `CALLS`、`REFERENCES`、`INHERITS`、`IMPLEMENTS` 代码关系；
- 建立 Commit、父提交、变更文件和当前代码符号的追溯图谱；
- 从已审核知识提取症状、设备、事件码、日志模式、诊断步骤和方案的领域图谱；
- Agentic Search 调度领域 GraphRAG，并返回实体、关系和证据多跳路径；
- 对未变的图谱内容使用稳定 evidence ID，重建索引后仍可追溯历史引用；
- 情景、程序、失败三类任务记忆，支持去重、案例隔离和复用计数；
- Agentic Search 动态编排知识、记忆、代码、Commit、BM25、Dense 和 Reranker；
- 返回多阶段执行计划、候选统计和 query → Commit → 文件 → 代码解释路径；
- 日志模块与代码符号关联；
- 代码符号搜索；
- 在综合诊断报告中列出疑似相关文件和函数；
- 预留 branch/commit 元数据字段。

### P3：工具链和开发流程接入

- cppcheck 调用；
- clang-tidy 探测，存在 `compile_commands.json` 时执行；
- 工具白名单、超时和隔离工作目录；
- 基于诊断证据和代码符号生成候选 unified diff；
- 候选补丁默认不自动应用；
- 私有 VS Code 扩展：创建案例、上传日志、关联工作区、选中代码问答、打开报告；
- 内部 AI Workflow 的 Skill 和 OpenAPI 定义。
- 类型化 Tool Registry、角色白名单、写操作审批、步骤/跳数/tokens/成本/时间预算；
- Agent 失败重试、熔断、取消、显式停止原因和确定性检索回退；
- 每个 Agent 任务可创建独立 worktree、端口、数据库、Storage 和日志目录。

### 运维、安全与协作

- `local`、单 API Key、个人令牌 RBAC 三种鉴权模式；
- ADMIN、ENGINEER、VIEWER 角色，以及 OWNER、EDITOR、VIEWER 案例级权限；
- 前端用户、一次性令牌、案例成员、运行状态和脱敏审计管理；
- 模型请求只记录端点来源、模型、用途、字符数、耗时和结果，不记录日志/提示词正文；
- `/health/live` 进程存活探针、`/health/ready` 数据库/存储就绪探针；
- SQLite、文件存储和模型密钥的带清单/哈希备份，以及保留旧数据的回滚式恢复；
- Windows/Ubuntu 后端与前端 CI、Win11 启动冒烟、Docker 构建和 PostgreSQL/Qdrant 集成测试。
- 合成 Golden Dataset、Fake OpenAI 服务、Playwright 浏览器 E2E、75% 后端覆盖率和架构 ratchet；
- 脱敏 Agent 运行轨迹、前端轨迹查看器和不写记忆的只读安全重放。

## 2. 工程结构

```text
gw_ap_debug_platform/
├── AGENTS.md                Agent/开发者项目地图与工程护栏
├── HARNESS_ENGINEERING.md   可执行验证、评测、轨迹和有界 Agent 路线
├── .claude/skills/          Claude/OpenCode/CodeArts Agent Skill 源
├── backend/                 FastAPI、数据库、解析器、RAG、LLM、报告
├── frontend/                Vue 3 + TypeScript + Element Plus
├── vscode-extension/        私有 VS Code 客户端
├── workflow/                Runtime Machine Contract / allowlisted OpenAPI
├── agent-integrations/      Claude/OpenCode/CodeArts MCP 配置示例
├── harness/                 Golden、覆盖率、架构和性能阈值
├── sample_data/             可直接演示和回归的纯合成数据
├── scripts/                 Windows/Linux 启动及 Demo 初始化
├── docker-compose.yml
└── .env.example
```

## 3. 本地运行

要求：Python 3.11+、Node.js 20.19+ 或 22.12+。Python、Node.js 和 npm 需要加入 `PATH`。

### Windows Agent Runtime（Claude Code / OpenCode 推荐）

```bat
scripts\install_agent_runtime.bat -InstallLocalModels
gwap start
gwap doctor
```

如果机器上已有 main 版本的 Agent Runtime，请不要直接执行当前默认安装器：它仍会复用
`%LOCALAPPDATA%\GWAPDebug`、全局 Skill 名和 MCP 名。先用完全隔离的合成数据实测 OpenCode：

```bat
scripts\test_opencode_integration.bat
```

该命令不会修改 main、个人 OpenCode 配置或真实源码。CodeArts/CodeAgent 的 vNext 命名空间化
安装已由 `setup_codeagent_vnext.bat` 提供；旧的通用安装器仍是单实例入口。
新电脑从零安装、固定 OpenCode 版本、独立运行目录/端口、真实 LLM + Skill + MCP 验收和常见排错，
见 [vNext OpenCode 新电脑完整指南](docs/new-pc-vnext-opencode-setup.md)。

华为 CodeArts/CodeAgent 或其他 OpenCode 魔改版使用：

```bat
scripts\setup_codeagent_vnext.bat -AgentCommand codearts
scripts\probe_codeagent_compatibility.bat -AgentCommand codearts -Strict
```

如果公司可执行文件名不是 `codearts`，可传实际命令名或绝对路径。Skill-only、MCP schema
切换、Skill zip 导入和完整排查流程见
[CodeAgent 兼容指南](docs/codeagent-compatibility.md)。

默认 API 与 Optional Web 共用 `127.0.0.1:8765`。运行数据放在 `%LOCALAPPDATA%\GWAPDebug`；
本地模型直接放入 `models/` 或通过 `MODEL_ROOTS` 指定，不需要先运行固定模型下载脚本。
详见 [docs/agent-skill-runtime.md](docs/agent-skill-runtime.md)。

### Windows 开发模式

```bat
scripts\start_local.bat
```

首次运行会自动调用 `scripts\bootstrap_local.bat` 创建 `.venv`、按 `backend\uv.lock`/`backend\constraints.lock` 安装锁定的 Python 依赖，并执行可复现的 `npm ci`；以后仅当 Python 配置/锁文件或 `frontend\package-lock.json` 改变时才重新安装。脚本会等待后端健康检查通过后再启动前端，并拒绝把占用 8000 端口的其他服务误当成本项目后端。

换电脑、更新代码或启动失败时，可先双击：

```bat
scripts\doctor_local.bat
```

它会检查 Win11、Python/Node/npm 版本、依赖指纹、后端导入、数据目录写权限和 8000/5173 端口，并在仓库根目录生成 `local_doctor_result.txt`。该报告不读取 `.env` 的值、API Key、数据库正文或日志正文。

需要做完整但不污染现有数据库的启动冒烟测试时，可运行：

```bat
scripts\runtime_smoke.bat
```

它会在系统临时目录创建隔离数据库，使用 18000/15173 端口启动后端和前端，验证迁移、前端 API 代理以及案例创建/读取闭环，然后自动停止进程。成功时输出一行 `"ok":true` 的 JSON。需要切换测试端口时可直接运行 `runtime_smoke.ps1` 并传入参数。

日常修改、完整交付和带 Docker 的外部验证统一使用：

```bat
scripts\validate_all.bat Fast
scripts\validate_all.bat Full
scripts\validate_all.bat External
```

`Fast` 复用已安装依赖做静态检查、仓库契约、关键后端回归和前端构建；`Full`
先按锁文件重新安装依赖，再运行完整后端、三类依赖审计、扩展编译、Doctor 和隔离
运行冒烟；`External` 继续检查 Compose 并构建前后端镜像。每次结果写入被 Git 忽略的
`artifacts\validation\<时间>-<模式>\summary.json` 和逐步日志，方便在另一台电脑复现。

### 启用个人账号和案例权限

本机单人使用保持默认 `AUTH_MODE=local` 即可。多人使用时，先在 `.env` 设置：

```env
AUTH_MODE=rbac
AUTH_ALLOW_LEGACY_ADMIN=false
```

第一次切换前用命令行创建管理员并领取只显示一次的个人令牌：

```bat
scripts\manage_users.bat create --username admin --display-name "Administrator" --role ADMIN
```

重启平台，在“安全与审计”页面粘贴令牌。管理员可在前端新建用户、切换角色、签发/撤销令牌；案例所有者可在案例概览添加可编辑或只读成员。服务端只保存令牌 SHA-256 摘要，原始令牌关闭弹窗后无法找回。已有升级案例的 `owner_id` 为空，为兼容旧版本仍按共享案例处理；新建案例会记录创建者为所有者。

VS Code 扩展使用个人令牌时，在命令面板运行 `GW/AP: Set or Clear Access Credential`；令牌会写入 VS Code SecretStorage。旧版 `gwap.apiKey` 明文设置仅保留为兼容回退，迁移后应清空。扩展的日志选择器支持无后缀文件，打包工作区时默认排除 `.env` 和常见私钥文件。

紧急迁移期也可以保留 `.env` 中的 `API_KEY` 并设置 `AUTH_ALLOW_LEGACY_ADMIN=true`，它会作为管理员凭据；个人令牌确认可用后应关闭该兼容入口。

### 备份和恢复

SQLite 本地版建议先关闭平台，再双击：

```bat
scripts\backup_local.bat
```

默认在被 Git 忽略的 `backups` 目录生成 ZIP。归档包含一致性 SQLite 快照、文件存储、清单和逐文件 SHA-256；存在模型密钥时也会一起保存，但不会复制 `.env`。归档含内部日志和可能用于解密模型 API Key 的密钥，必须放在受控位置。

恢复时停止所有平台进程，把备份 ZIP 拖到 `scripts\restore_local.bat`，并按提示输入大写 `RESTORE`。恢复前的数据库、存储和模型密钥会保存在 `backend\data\restore_rollbacks`，便于人工回滚。内置工具只支持 SQLite；Docker/PostgreSQL 部署应使用 `pg_dump`/`pg_restore`，Qdrant 使用其快照机制。

### Linux / macOS

```bash
./scripts/start_local.sh
```

启动后：

- 前端：http://127.0.0.1:5173
- API：http://127.0.0.1:8000
- Swagger：http://127.0.0.1:8000/docs

### 手动启动

```bash
cp .env.example .env
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux: source .venv/bin/activate
pip install --upgrade --constraint backend/constraints.lock pip
pip install --constraint backend/constraints.lock -e "./backend[dev]"
cd backend
uvicorn app.main:app --reload --port 8000
```

维护者修改 `backend/pyproject.toml` 后，应运行 `scripts\refresh_python_lock.bat` 更新跨平台锁文件；`scripts\refresh_python_lock.bat -Check` 只检查、不修改。

新终端：

```bash
cd frontend
npm ci
npm run dev
```

### 初始化演示案例

服务启动后：

```bash
python scripts/seed_demo.py
```

该脚本会自动：

1. 创建 AP 故障案例；
2. 上传并解析 `sample_data/collectDebuginfo_demo.zip`；
3. 上传并索引示例 C 仓库；
4. 运行综合诊断。

### 上传华为 GW/AP 无后缀日志

在案例概览中选择日志并点击“上传并解析”。无后缀日志会由后端自动追加 `.txt` 后缀，原始文件名和是否改名会保存在制品元数据中；前端、VS Code 扩展和直接调用上传接口都使用同一规则。以下两类内容可以位于同一个文件中：

```text
Start run collect command:WAP:get wlan basic laninst 1 wlaninst6
NOTICE 2026-03-02 03:29:17.483[90][DC]...
```

解析结果会保留命令采集边界，识别 `TRACE/DEBUG/INFO/NOTICE/WARN/ERROR/CRITICAL` 等级，并把日志时间转换为标准时间。文本中低于 1% 的孤立 NUL/控制字节不会再导致整个文件被判为二进制；解析时会清理 NUL，上传的原始文件保持不变。解析器逐行读取并分批写入数据库，不会把 110,904 行文件一次性载入内存；前端事件、时间线和原始日志均采用服务端分页，并可从事件直接跳转到对应原始行。

仓库中的 `sample_data\logs\collectDebuginfo_extensionless_demo` 是可直接上传验证自动追加后缀和专用解析器的无后缀示例。

如果没有任何可读取文本文件，任务会明确失败并把制品状态设置为 `PARSE_FAILED`，不会再出现“解析成功但解析文件数为 0”。

### 内网电脑一键检查日志

仓库提供了不输出日志正文的离线检查工具。最便捷的用法是把日志文件拖到以下文件上：

```text
scripts\inspect_log_file.bat
```

也可以双击该文件并粘贴日志完整路径，或者在终端执行：

```bat
scripts\inspect_log_file.bat "D:\logs\your_collectDebuginfo_file"
```

检查完成后，仓库根目录会生成被 Git 忽略的 `log_check_result.txt`。报告只包含：

- 文件大小、行数、扩展名和自动追加后的名称；
- 前 4 个字节、编码推测、前 64 KiB 的 NUL/控制字节统计；
- 128 MiB 解析限制和 512 MiB 单文件安全限制；
- 前 1 MiB 中是否出现采集命令、WLAN 配置和等级日志标记；
- 预计使用的解析器；
- 当前 Git 提交、分支、与本地 `origin/main` 是否一致；
- 8000 端口进程、Python 路径、Uvicorn 入口和服务根接口检查；
- 根据检查结果生成的简短处理建议。

报告不包含日志正文，但文件路径和文件名也可能属于内部信息，对外发送前仍应人工检查。PowerShell 用户也可以直接运行 `scripts\inspect_log_file.ps1`，并使用 `-SkipLineCount` 跳过完整行数统计。

### 从案例文件夹提炼知识草稿

先在“系统设置 → 诊断大模型”配置并启用公司批准的 OpenAI-Compatible Chat 模型，
然后打开顶部“AI 案例提炼”。选择一个同时包含日志、错误现象、人工分析和解决方案的
文件夹，确认脱敏证据可以发送到所选模型 API 后开始提炼。

平台只向模型发送本地脱敏、限长并带来源行号的文本证据，不直接发送原始文件。生成后可
查看来源、编辑 Markdown、与模型多轮讨论纠错、查看或恢复不可变历史版本。只有章节和
`[SRC-xxxx:Lx-Ly]` 引用校验通过并经人工确认后，系统才会创建不可检索的知识库
`DRAFT`；该草稿仍需提交审核并发布，才能参与检索和诊断。

可直接读取文本、无后缀文本、HTML/HTM、Word `.docx` 和带文本层的 PDF；旧式
`.doc` 需先转换为 `.docx`，扫描 PDF 和图片目前需要先做 OCR。
完整操作、安全边界、大小限制、状态流转和接口说明见
[大模型文件夹案例提炼与人工校正](docs/llm-knowledge-curation.md)。

## 4. Docker 部署

```bash
cp .env.example .env
docker compose up --build
```

访问：http://127.0.0.1:8080

Docker Desktop 需要支持 Compose 2.24+。镜像额外安装 cppcheck 和 clang-tidy；数据库使用 PostgreSQL，向量服务使用 Qdrant，数据库、向量和文件分别保存在 Docker Volume 中。宿主机端口只绑定 `127.0.0.1`，Qdrant/PostgreSQL 不直接暴露。SQLite 中的 Embedding 向量仍是权威回退，因此 Qdrant 暂时不可用不会阻断基本检索。

## 5. 配置 Qwen / GLM

编辑 `.env`：

```env
LLM_PROVIDER=openai_compatible
LLM_API_KEY=your-approved-key
LLM_BASE_URL=https://your-approved-openai-compatible-endpoint/v1
LLM_MODEL=your-model-name
```

Qwen、GLM 或内部模型只要提供兼容的 `/chat/completions` 接口即可接入。业务代码不会直接依赖厂商 SDK。

也可以直接在“系统设置 → 模型网关”中添加多套诊断模型、Embedding 和 Reranker 配置并切换。前端提交的模型 API Key 由后端加密保存，不会通过查询接口回显。`.env` 的 `LLM_*` 配置保留为首次升级和无人值守部署的兼容入口。

本地 Embedding、Reranker 和 Chat 模型的 **vNext 正式入口是自动发现**，不要求采用固定模型名称或固定三级目录，也不依赖旧下载脚本。把已经下载好的 Hugging Face 格式模型放到项目 `models/`，或设置多个模型根目录：

```env
MODEL_ROOTS=D:\AI\models;E:\shared-models
```

然后在 Agent Runtime 中执行：

```bat
gwap models scan
gwap models list
gwap models validate LM_xxx --device cpu
gwap models activate LM_xxx --device cpu
```

设置页“本地模型自动发现”提供同样能力。扫描器只读取有界 `config.json`、Tokenizer、Sentence-Transformers 等安全元数据并统计权重文件大小；不会读取或向 LLM 上传模型权重。确定性规则无法高置信判断时，显式扫描动作可以调用当前 Chat Profile 对脱敏后的元数据做二次分类。只有真实 loader smoke test 通过、状态为 `VALIDATED` 后，默认才允许激活。

自动 loader 覆盖 SentenceTransformer Embedding、Sentence-Transformers CrossEncoder、普通 Transformers SequenceClassification Reranker 和 Transformers CausalLM Chat。切换 Embedding 后仍需重建知识库向量 generation。

<details>
<summary>历史兼容：固定 BGE/Qwen 下载器</summary>

旧 `scripts\check_hf_model_access.bat` / `scripts\install_local_models.bat` 仍保留，便于已经按旧目录部署的环境维护，但它们不再是 Agent Runtime 模型发现、匹配、验证或激活的前置条件，也不是新实现的模型目录假设来源。已有旧数据库中的固定 Model Profile 不会被自动删除。

</details>

详细的数据结构、分类、切换方式、离线模型目录和重建索引说明见 [模型网关与分层知识库使用说明](docs/model-and-knowledge-configuration.md)。从案例文件夹生成并多轮校正知识草稿见 [大模型文件夹案例提炼与人工校正](docs/llm-knowledge-curation.md)。故障案例、代码/Commit 图谱、三类记忆和 Agentic Search 见 [认知检索与图谱使用说明](docs/cognitive-retrieval.md)。知识审核、领域 GraphRAG、检索评测和人工反馈见 [知识治理、领域图谱与检索评测](docs/quality-governance-and-evaluation.md)。Golden、Playwright、轨迹和有界 Agent 见 [质量评测、Agent 轨迹与有界执行](docs/quality-harness-and-agent-runtime.md)。项目的完整架构、技术栈、优缺点、迭代历程和后续路线见 [项目架构与迭代说明](docs/project-architecture-and-evolution.md)。

企业环境中必须确认：

- 模型服务是否经过公司批准；
- 日志和源码是否允许发送到该端点；
- Base URL 是否位于内网或受控网络；
- API Key 不得写入前端、Git 或报告。

模型网关地址会在保存、启用和实际请求前校验。默认只允许公开 HTTPS 地址，并拒绝 `file://`、云元数据、链路本地、未授权回环和私网地址。公司内网模型请在 `.env` 明确列出主机名：

```env
MODEL_ENDPOINT_ALLOWLIST=model-gateway.corp.example,.approved-models.corp.example
MODEL_ALLOW_PRIVATE_ENDPOINTS=false
```

白名单中的端点可以使用内网 HTTP（仍建议优先 HTTPS）。`APP_ENV=prod` 时所有 API 模型端点都必须在白名单内；不要为了省事开启整个私网，优先逐个列出批准的网关主机。

## 6. 核心数据流

```text
创建案例
  → 上传 collectDebuginfo
  → 安全解压和文件清单
  → 解析器注册表选择日志解析器
  → 标准化 LogEvent
  → 事件时间线和规则诊断
  → Agentic Search 编排知识/领域 GraphRAG/记忆/代码图谱/Commit 图谱
  → BM25 + RRF + Dense Embedding + 可选 Reranker 统一排序
  → platform 模式：受控 Qwen/GLM/OpenAI-Compatible LLM 综合分析与 evidence_id 校验
    或 external 模式：Evidence Bundle → Claude Code / OpenCode / CodeArts 最终推理
  → 结构化诊断 JSON / 外部 Agent 可追溯诊断
  → HTML / PDF / Word 报告
```

原始日志、结构化事实、知识库证据和 LLM 推测在数据库中分开保存。LLM 不能直接修改原始日志或代码；模型返回的结构、置信度和证据编号会由后端校验，引用不存在证据时自动保留确定性规则结果。每次诊断会保存模型配置快照（不含 API Key），后续切换模型不会改变历史诊断的审计信息。

后台解析、索引和诊断任务的状态保存在数据库中。原子 lease 领取和 heartbeat 允许多个后端实例避免重复执行；幂等键、超时、指数退避、dead-letter 和资源预算约束失败路径。后端重启后会恢复可重试任务，失败任务会恢复案例/制品状态并保留可读错误信息。

知识文件和代码仓上传采用两阶段协议：HTTP 请求只流式保存原文件并返回
`202 Accepted` 及 `job`；Markdown 读取/切块/Embedding、归档解压和 Git Bundle
clone 在后台任务中执行。前端和 VS Code 扩展会跟踪该任务，完成后才允许建立代码图谱。

代码图谱、领域知识图谱和全量向量重建均使用 generation：新版本在旁路构建，
成功后一次切换 active generation；构建失败时查询继续使用最后一次成功结果。
领域图谱还会在发布前校验已审核知识的输入签名，构建期间知识发生变化时拒绝发布
过期图谱。

需要 Commit 意图追溯时，普通 ZIP/TAR 不够；请在待分析源码仓根目录生成并上传
Git Bundle：

```bat
git bundle create repository.bundle --all
```

上传后点击“建立索引”。普通归档仍可建立代码图谱，但 Commit 图谱会明确显示
`UNAVAILABLE`，不会伪造历史。

## 7. API 概览

```text
POST /api/v1/cases
POST /api/v1/cases/{case_id}/artifacts
POST /api/v1/cases/{case_id}/artifacts/{artifact_id}/parse
GET  /api/v1/cases/{case_id}/events
GET  /api/v1/cases/{case_id}/timeline
POST /api/v1/cases/{case_id}/analyses
POST /api/v1/cases/{case_id}/chat
POST /api/v1/knowledge/upload                       # 202 + import job
PATCH /api/v1/knowledge/{document_id}
GET   /api/v1/knowledge/templates/fault-case
POST  /api/v1/knowledge/{document_id}/extract-method
GET   /api/v1/knowledge/{document_id}/revisions
POST  /api/v1/knowledge/{document_id}/review/submit
POST  /api/v1/knowledge/{document_id}/review/approve
POST  /api/v1/knowledge/{document_id}/revisions/{version}/rollback
GET   /api/v1/knowledge/categories
POST  /api/v1/knowledge/reindex
GET   /api/v1/knowledge/graph/status
POST  /api/v1/knowledge/graph/rebuild
POST  /api/v1/knowledge/graph/search
GET   /api/v1/evaluation/datasets
POST  /api/v1/evaluation/datasets/{dataset_id}/runs
POST  /api/v1/cases/{case_id}/diagnosis-feedback
GET   /api/v1/system/models
POST  /api/v1/system/models
POST  /api/v1/system/models/{profile_id}/activate
POST  /api/v1/system/models/{profile_id}/test
GET   /api/v1/system/auth-info
GET   /api/v1/system/me
GET   /api/v1/system/status
GET   /api/v1/system/audit
GET   /api/v1/system/users
GET   /api/v1/cases/{case_id}/access
PUT   /api/v1/cases/{case_id}/members/{user_id}
GET   /api/v1/health/live
GET   /api/v1/health/ready
POST  /api/v1/jobs/{job_id}/cancel
POST  /api/v1/jobs/{job_id}/retry
POST /api/v1/cases/{case_id}/repositories           # 202 + import job
POST /api/v1/repositories/{repository_id}/index
GET  /api/v1/repositories/{repository_id}/graph
GET  /api/v1/repositories/{repository_id}/graph/search
GET  /api/v1/repositories/{repository_id}/commit-graph
POST /api/v1/cases/{case_id}/agentic-search
GET  /api/v1/cases/{case_id}/memories
POST /api/v1/repositories/{repository_id}/static-analysis
POST /api/v1/cases/{case_id}/patch-suggestions
POST /api/v1/cases/{case_id}/analyses/{analysis_id}/reports/{format}
```

完整接口和请求结构见 Swagger。

## 8. 增加新的日志解析器

实现 `Parser` 协议并注册：

```python
class VendorGwParser:
    parser_id = "vendor-gw"
    parser_version = "1.0"

    def probe(self, path, sample):
        return 0.95 if "vendor-marker" in sample else 0.0

    def parse_lines(self, path, relative_path, lines):
        for line_number, text in enumerate(lines, start=1):
            yield ParsedEvent(line_start=line_number, raw_text=text, ...)

registry.register(VendorGwParser())
```

厂商专用日志格式、错误码和模块映射应单独维护，不建议直接修改通用解析器。

## 9. 安全边界

已实现：

- 上传大小限制；
- 解压总大小、文件数、单文件大小和目录深度限制；
- 路径穿越防护；
- 忽略符号链接和非普通文件；
- 本机、共享 API Key、个人令牌 RBAC 三种鉴权模式；
- 角色和案例成员隔离，令牌仅保存哈希，最后一个管理员受防误锁保护；
- HTTP 变更、敏感读取、用户/令牌操作和模型外发的脱敏审计；
- 原始文件与报告哈希；
- 静态工具白名单和执行超时；
- IP、MAC、SN、密码和 Token 基础脱敏；
- 模型网关协议、主机白名单和私网/元数据地址校验；
- LLM 输出结构、置信度范围和 evidence_id 完整性校验；
- 案例代码检索和候选补丁的跨案例隔离；
- SQLite 外键、WAL、忙等待和 Alembic 自动迁移；
- 补丁不自动应用。

生产部署仍需补充：

- 公司 SSO/OIDC（当前为本地账号/令牌）；
- 知识文档细粒度 ACL（当前知识修改仅管理员可用）；
- 对象存储和数据库加密；
- 杀毒/恶意文件检测；
- 集中式不可篡改审计归档和告警（当前审计保存在应用数据库）；
- Kubernetes 资源隔离和 NetworkPolicy；
- 真实厂商日志解析器与回归数据集。

## 10. 测试

Windows 11 推荐直接运行统一入口：

```bat
scripts\validate_all.bat Fast
scripts\validate_all.bat Full
```

下面的分项命令用于只调试某一层：

```bash
cd backend
python ..\scripts\run_backend_tests.py

cd ../frontend
npm ci
npm run build

cd ../vscode-extension
npm ci
npm run compile

# Windows 隔离启动闭环
cd ..
scripts\runtime_smoke.bat
scripts\run_golden_evals.bat
scripts\run_browser_e2e.bat
```

GitHub Actions 会在 Windows/Ubuntu 构建前后端并执行 75% 覆盖率门禁，运行九类 Golden 评测和 Playwright E2E，在 Windows 执行隔离启动闭环，并在 Linux 服务容器中验证 PostgreSQL 迁移和 Qdrant 写入/检索。后端启动时会自动执行 Alembic 数据库迁移；升级前请运行备份工具，不要手工修改 `alembic_version` 表。

## 11. 已知限制

- 内置解析器是面向常见 GW/AP 语义的通用实现，真实产品日志格式仍需根据公司内部样例扩展；
- 本地检索使用 BM25/精确词项、当前 Embedding 向量和可选 Reranker；SQLite 保存向量回退，配置 Qdrant 后会同步写入按模型隔离的 collection；
- 当前领域知识图谱使用已审核 Markdown/元数据的确定性抽取，已支持设备—症状—事件码—诊断步骤—方案关系和 GraphRAG，但复杂同义词、冲突消解和人工实体合并仍待增强；
- 仓库已提供纯合成 Golden Dataset 和 CI 阈值；真实厂商日志仍只能在企业内网脱敏、审批后建立私有回归集；
- 数据库 lease 已支持多实例安全领取，但执行单元仍是各 FastAPI 实例内的线程池；大规模弹性 Worker 可进一步迁移到专用队列；
- 生产 Agentic Search 仍默认使用确定性 Planner；类型化循环 Agent 已具备预算、审批、轨迹和回退基础，但尚未默认接管线上检索；
- 静态分析是否可运行取决于后端环境是否安装工具和项目是否具备编译数据库；
- 报告中的根因候选用于辅助人工排查，不替代工程师确认；
- 示例仓库故意包含不完整校验和 `sprintf`，用于演示静态分析及候选补丁流程。
