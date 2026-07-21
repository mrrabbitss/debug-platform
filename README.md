# GW/AP Intelligent Debug Platform

面向 GW、AP、全光网关等网络设备的 collectDebuginfo 日志解析、证据关联、RAG 检索、LLM 综合诊断、代码仓库关联和报告生成平台。

本仓库是可运行的完整工程，不依赖真实企业数据。默认使用规则引擎和 Mock LLM，因此没有模型密钥也能完成演示；配置公司批准的 Qwen、GLM 或其他 OpenAI-Compatible API 后，会启用受证据约束的 LLM 综合分析与候选补丁生成。

## 1. 已实现能力

### P0：基础闭环

- 创建 GW/AP 故障案例；
- 上传 ZIP/TAR/TGZ 或单个日志；
- 安全解压，防止 Zip Slip、符号链接和超限压缩包；
- collectDebuginfo 文件清单与原始日志浏览；
- 日志编码识别、时间戳标准化、敏感信息脱敏；
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

要求：Python 3.11+、Node.js 20+。

### Windows

```bat
scripts\start_local.bat
```

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
npm install
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
