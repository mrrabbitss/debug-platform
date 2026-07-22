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
- 按章节和段落切分；
- 错误码、函数名、文件路径、中文语义共同参与的本地混合检索；
- 设备类型、模块和可信等级元数据；
- 证据 ID、支持证据、反证、不确定性和缺失信息；
- Qwen、GLM 和内部模型的统一 OpenAI-Compatible 适配器；
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

该脚本使用仓库内的公开 npm registry 配置和 `package-lock.json` 执行可复现安装；依赖安装失败时会停止并保留错误提示，不会继续启动残缺的前端。

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

解析结果会保留命令采集边界，识别 `TRACE/DEBUG/INFO/NOTICE/WARN/ERROR/CRITICAL` 等级，并把日志时间转换为标准时间。文本中低于 1% 的孤立 NUL/控制字节不会再导致整个文件被判为二进制；解析时会清理 NUL，上传的原始文件保持不变。原始文件仍可在“日志浏览”中查看。

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

企业环境中必须确认：

- 模型服务是否经过公司批准；
- 日志和源码是否允许发送到该端点；
- Base URL 是否位于内网或受控网络；
- API Key 不得写入前端、Git 或报告。

## 6. 核心数据流

```text
创建案例
  → 上传 collectDebuginfo
  → 安全解压和文件清单
  → 解析器注册表选择日志解析器
  → 标准化 LogEvent
  → 事件时间线和规则诊断
  → 检索协议/产品/历史案例/代码符号
  → 受控 LLM 综合分析
  → 结构化诊断 JSON
  → HTML / PDF / Word 报告
```

原始日志、结构化事实、知识库证据和 LLM 推测在数据库中分开保存。LLM 不能直接修改原始日志或代码。

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

    def parse(self, path, relative_path, text):
        return [ParsedEvent(...)]

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
npm install
npm run build

cd ../vscode-extension
npm install
npm run compile
```

## 11. 已知限制

- 内置解析器是面向常见 GW/AP 语义的通用实现，真实产品日志格式仍需根据公司内部样例扩展；
- 本地检索为可运行的 BM25/精确词项混合实现，生产环境可替换为 Qdrant、OpenSearch 或内部检索服务；
- 静态分析是否可运行取决于后端环境是否安装工具和项目是否具备编译数据库；
- 报告中的根因候选用于辅助人工排查，不替代工程师确认；
- 示例仓库故意包含不完整校验和 `sprintf`，用于演示静态分析及候选补丁流程。
