# GW/AP Intelligent Debug Platform

面向 GW、AP、全光网关等网络设备的 collectDebuginfo 日志解析、证据关联、RAG 检索、LLM 综合诊断、代码仓库关联和报告生成平台。

本仓库是可运行的完整工程，不依赖真实企业数据。默认使用规则引擎和 Mock LLM，因此没有模型密钥也能完成演示；配置公司批准的 Qwen、GLM 或其他 OpenAI-Compatible API 后，会启用受证据约束的 LLM 综合分析与候选补丁生成。

## 1. 已实现能力

### P0：基础闭环

- 创建 GW/AP 故障案例；
- 上传 ZIP/TAR/TGZ、常见单个日志或无后缀纯文本 collectDebuginfo；
- 安全解压，防止 Zip Slip、符号链接和超限压缩包；
- collectDebuginfo 文件清单与原始日志浏览；
- 日志编码识别、时间戳标准化、敏感信息脱敏；
- 按内容识别华为 GW/AP 采集包中的 `Start run collect command:` 命令段和 `NOTICE 2026-... 03:29:17.483` 运行日志；
- hostapd、WLAN、DHCP、PPPoE、PON、OMCI、TR-069、内核和进程异常规则；
- 关键事件提取与时间线；
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

Docker 镜像额外安装 cppcheck 和 clang-tidy。数据库使用 PostgreSQL，文件保存在 Docker Volume 中。

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

本地 BGE Embedding 和 Qwen3 Reranker 属于可选大型依赖，先运行：

```bat
scripts\install_local_models.bat
```

详细的数据结构、分类、切换方式、离线模型目录和重建索引说明见 [模型网关与分层知识库使用说明](docs/model-and-knowledge-configuration.md)。

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
- API Key 可选鉴权；
- 原始文件与报告哈希；
- 静态工具白名单和执行超时；
- IP、MAC、SN、密码和 Token 基础脱敏；
- 模型网关协议、主机白名单和私网/元数据地址校验；
- LLM 输出结构、置信度范围和 evidence_id 完整性校验；
- 案例代码检索和候选补丁的跨案例隔离；
- SQLite 外键、WAL、忙等待和 Alembic 自动迁移；
- 补丁不自动应用。

生产部署仍需补充：

- 公司 SSO/OIDC；
- 项目级 RBAC 和文档 ACL；
- 对象存储和数据库加密；
- 杀毒/恶意文件检测；
- 完整审计平台；
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
```

后端启动时会自动执行 Alembic 数据库迁移。升级前仍建议备份 `backend\data`；不要手工修改 `alembic_version` 表。

## 11. 已知限制

- 内置解析器是面向常见 GW/AP 语义的通用实现，真实产品日志格式仍需根据公司内部样例扩展；
- 本地检索使用 BM25/精确词项、当前 Embedding 向量和可选 Reranker；SQLite 保存向量回退，配置 Qdrant 后会同步写入按模型隔离的 collection；
- 当前知识层次是分类树和文档型故障树，尚未构建实体—关系知识图谱；
- 本地后台任务适合当前单机/单后端进程；若扩展为多实例部署，应换用带租约的 Redis/Celery 或专用队列；
- 静态分析是否可运行取决于后端环境是否安装工具和项目是否具备编译数据库；
- 报告中的根因候选用于辅助人工排查，不替代工程师确认；
- 示例仓库故意包含不完整校验和 `sprintf`，用于演示静态分析及候选补丁流程。
