# GW/AP Intelligent Debug Platform

面向 GW、AP、全光网关等网络设备的 collectDebuginfo 日志解析、证据关联、RAG 检索、LLM 综合诊断、代码仓库关联和报告生成平台。

本仓库是可运行的完整工程，不依赖真实企业数据。默认使用规则引擎和 Mock LLM，因此没有模型密钥也能完成演示；配置公司批准的 Qwen、GLM 或其他 OpenAI-Compatible API 后，会启用受证据约束的 LLM 综合分析与候选补丁生成。

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
- 可持久恢复的后台任务，以及安全取消、失败/取消后重试；
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
- 证据 ID、支持证据、反证、不确定性和缺失信息；
- Qwen、GLM 和内部模型的统一 OpenAI-Compatible 适配器；
- 前端保存多套模型配置、连接测试和运行时切换；
- Mock 降级、模型连接检测、超时和重试；
- 基于当前案例的多轮问答。

### P2：代码仓库关联

- 上传 C/C++ 项目压缩包；
- 提取函数、宏、结构体、签名、文件路径和调用函数；
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

### 运维、安全与协作

- `local`、单 API Key、个人令牌 RBAC 三种鉴权模式；
- ADMIN、ENGINEER、VIEWER 角色，以及 OWNER、EDITOR、VIEWER 案例级权限；
- 前端用户、一次性令牌、案例成员、运行状态和脱敏审计管理；
- 模型请求只记录端点来源、模型、用途、字符数、耗时和结果，不记录日志/提示词正文；
- `/health/live` 进程存活探针、`/health/ready` 数据库/存储就绪探针；
- SQLite、文件存储和模型密钥的带清单/哈希备份，以及保留旧数据的回滚式恢复；
- Windows/Ubuntu 后端与前端 CI、Win11 启动冒烟、Docker 构建和 PostgreSQL/Qdrant 集成测试。

## 2. 工程结构

```text
gw_ap_debug_platform/
├── backend/                 FastAPI、数据库、解析器、RAG、LLM、报告
├── frontend/                Vue 3 + TypeScript + Element Plus
├── vscode-extension/        私有 VS Code 客户端
├── workflow/                内部 Skill / Workflow 接口定义
├── sample_data/             可直接演示的日志、知识和 C 代码
├── scripts/                 Windows/Linux 启动及 Demo 初始化
├── docker-compose.yml
└── .env.example
```

## 3. 本地运行

要求：Python 3.11+、Node.js 20.19+ 或 22.12+。Python、Node.js 和 npm 需要加入 `PATH`。

### Windows

```bat
scripts\start_local.bat
```

首次运行会自动调用 `scripts\bootstrap_local.bat` 创建 `.venv`、执行可复现的 `npm ci` 并安装后端依赖；以后仅当 `backend\pyproject.toml` 或 `frontend\package-lock.json` 改变时才重新安装。脚本会等待后端健康检查通过后再启动前端，并拒绝把占用 8000 端口的其他服务误当成本项目后端。

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
pip install -e "./backend[dev]"
cd backend
uvicorn app.main:app --reload --port 8000
```

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

本地 BGE Embedding 和 Qwen3 Reranker 属于可选大型依赖。先启动过一次项目以建立 `.venv`，关闭服务窗口。公司网络、代理或镜像情况不确定时，可以先双击以下检测脚本：

```bat
scripts\check_hf_model_access.bat
```

它只下载 BGE 的 `config.json`，不会下载模型权重，并在项目根目录生成 `hf_model_access_report_*.txt`。结果为 `PASS_HF_CLI` 表示官方 CLI 可用；`PASS_CURL_FALLBACK` 表示 CLI 的元数据请求失败，但正式安装器可以自动使用 `curl.exe` 回退。

然后运行：

```bat
scripts\install_local_models.bat
```

安装器默认使用 `https://hf-mirror.com` 和 `Auto` 下载模式。它会关闭误继承的离线模式、安装固定兼容版 Hugging Face Hub、锁定两个模型的 revision，并先用 `hf download --local-dir` 下载小型 `config.json` 作为预检。若公司代理或镜像导致 `LocalEntryNotFoundError`，则自动切换到 Win11 内置 `curl.exe`：从镜像 API 读取文件清单，支持 `.partial` 断点续传，并校验文件大小和权重 SHA-256。完成后还会执行项目适配器真实加载测试。它会创建以下目录：

```text
models/
├─ inference/                         # 预留给后续本地诊断推理模型
├─ embedding/bge-base-zh-v1.5/       # BAAI/bge-base-zh-v1.5
└─ reranker/Qwen3-Reranker-0.6B/     # Qwen/Qwen3-Reranker-0.6B
```

对应的首选下载命令如下。为了保持“推理 / Reranker / Embedding”三级目录，Qwen 模型放在 `models\reranker` 下，而不是直接放在 `models` 根目录：

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"

.\.venv\Scripts\hf.exe download BAAI/bge-base-zh-v1.5 `
  --local-dir .\models\embedding\bge-base-zh-v1.5

.\.venv\Scripts\hf.exe download Qwen/Qwen3-Reranker-0.6B `
  --local-dir .\models\reranker\Qwen3-Reranker-0.6B
```

`models` 已被 Git 忽略，模型权重不会被提交或上传。建议至少预留 6 GiB 磁盘空间；CPU 可以运行，但首次加载 Qwen Reranker 可能需要几分钟。下载中断后重新运行同一个 BAT 文件即可复用已经完成的文件。

如需强制指定下载路径：

```powershell
# 跳过 hf CLI，直接使用可续传并校验哈希的 curl.exe 路径
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_local_models.ps1 -DownloadMode Curl

# 只允许 hf CLI；CLI 失败时不自动回退
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_local_models.ps1 -DownloadMode HfCli
```

安装完成后重新运行 `scripts\start_local.bat`，打开“系统设置”：

1. 在“Embedding 模型”中测试并激活带“项目 models 目录”的 BGE Base 配置；
2. 点击“重建向量索引”；
3. 在“Reranker 模型”中测试并激活带“项目 models 目录”的 Qwen3 配置。

只检查已下载文件和适配器、不重新下载：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_local_models.ps1 -VerifyOnly
```

如果公司使用其他 Hugging Face 镜像，可通过 `-Mirror` 指定；如果 Python 包已经由管理员统一安装，可增加 `-SkipRuntimeInstall`。检测报告只记录软件版本、端点、离线开关、代理是否存在以及下载错误，不记录代理地址、API Key、日志或数据库内容。本地 Qwen3 Reranker 的“排序指令”和“推理批量”、BGE 的“检索查询指令”和批量大小均可在系统设置中调整。普通 Win11 CPU 建议先保持默认小批量。

详细的数据结构、分类、切换方式、离线模型目录和重建索引说明见 [模型网关与分层知识库使用说明](docs/model-and-knowledge-configuration.md)。项目的完整架构、技术栈、优缺点、迭代历程和后续路线见 [项目架构与迭代说明](docs/project-architecture-and-evolution.md)。

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
  → 检索协议/产品/历史案例/代码符号
  → 受控 LLM 综合分析与 evidence_id 校验
  → 结构化诊断 JSON
  → HTML / PDF / Word 报告
```

原始日志、结构化事实、知识库证据和 LLM 推测在数据库中分开保存。LLM 不能直接修改原始日志或代码；模型返回的结构、置信度和证据编号会由后端校验，引用不存在证据时自动保留确定性规则结果。每次诊断会保存模型配置快照（不含 API Key），后续切换模型不会改变历史诊断的审计信息。

后台解析、索引和诊断任务的状态保存在数据库中。后端重启后会恢复未完成任务；同一种输入的活动任务会去重，失败任务会恢复案例/制品状态并保留可读错误信息。

## 7. API 概览

```text
POST /api/v1/cases
POST /api/v1/cases/{case_id}/artifacts
POST /api/v1/cases/{case_id}/artifacts/{artifact_id}/parse
GET  /api/v1/cases/{case_id}/events
GET  /api/v1/cases/{case_id}/timeline
POST /api/v1/cases/{case_id}/analyses
POST /api/v1/cases/{case_id}/chat
POST /api/v1/knowledge/upload
PATCH /api/v1/knowledge/{document_id}
GET   /api/v1/knowledge/categories
POST  /api/v1/knowledge/reindex
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
POST /api/v1/cases/{case_id}/repositories
POST /api/v1/repositories/{repository_id}/index
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

```bash
cd backend
pytest -q

cd ../frontend
npm ci
npm run build

cd ../vscode-extension
npm ci
npm run compile

# Windows 隔离启动闭环
cd ..
scripts\runtime_smoke.bat
```

GitHub Actions 会在 Windows/Ubuntu 构建前后端，在 Windows 执行隔离启动闭环，并在 Linux 服务容器中验证 PostgreSQL 迁移和 Qdrant 写入/检索。后端启动时会自动执行 Alembic 数据库迁移；升级前请运行备份工具，不要手工修改 `alembic_version` 表。

## 11. 已知限制

- 内置解析器是面向常见 GW/AP 语义的通用实现，真实产品日志格式仍需根据公司内部样例扩展；
- 本地检索使用 BM25/精确词项、当前 Embedding 向量和可选 Reranker；SQLite 保存向量回退，配置 Qdrant 后会同步写入按模型隔离的 collection；
- 当前知识层次是分类树和文档型故障树，尚未构建实体—关系知识图谱；
- 本地后台任务适合当前单机/单后端进程；若扩展为多实例部署，应换用带租约的 Redis/Celery 或专用队列；
- 静态分析是否可运行取决于后端环境是否安装工具和项目是否具备编译数据库；
- 报告中的根因候选用于辅助人工排查，不替代工程师确认；
- 示例仓库故意包含不完整校验和 `sprintf`，用于演示静态分析及候选补丁流程。
