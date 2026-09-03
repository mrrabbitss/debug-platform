# 模型网关与分层知识库使用说明

## 1. 当前实际使用的检索技术

诊断检索由 Agentic Search 编排以下阶段：

1. 按 query 意图选择知识、领域图谱、记忆、代码图谱和 Commit 图谱；
2. 各模块使用 BM25、精确错误码、函数名、路径、标题或图关系生成候选；
3. 使用 RRF 融合不同模块；
4. 使用当前激活的 Embedding 对跨模块候选计算余弦相似度；
5. 如果启用了 Reranker，则对融合候选重新排序；
6. 把最终证据和可解释路径连同结构化日志事件交给规则诊断和当前诊断大模型。

默认 Embedding 是无需下载模型的 384 维字符 Hashing 向量，主要用于保证新克隆的电脑开箱可用。它不是训练过的语义模型。切换到本地 BGE 或 Embedding API 并重建索引后，系统才会使用相应的语义向量。

默认不启用 Reranker。激活本地 Qwen3 Reranker 或 Qwen Rerank API 后，Reranker 会参与每次知识检索，不需要重建知识索引。

## 2. 知识储存形式

默认数据都位于 `backend/data/gw_ap_debug.db`：

- `knowledge_documents`：完整知识正文和设备、模块、可信等级等元数据；
- `knowledge_chunks`：按 Markdown 标题和段落生成的检索分块；
- `knowledge_categories`：可分层的知识分类；
- `knowledge_document_categories`：文档与分类的关联；
- `knowledge_derivations`：来源案例/Skill 与派生分析方法的 lineage；
- `knowledge_revisions`：不可变版本快照、内容哈希和变更说明；
- `knowledge_embeddings`：按 Embedding 配置隔离保存的向量缓存；
- `knowledge_graph_states`、`knowledge_entities`、`knowledge_entity_mentions`、
  `knowledge_relations`：领域图谱 generation、实体、证据提及和关系；
- `code_relations`、`commit_records`、`commit_file_changes`：代码与 Commit 工程图谱；
- `agent_memories`：情景、程序和失败记忆；
- `diagnosis_feedback`、`retrieval_evaluation_*`：人工反馈与检索评测；
- `model_profiles`：Chat、Embedding、Reranker 配置、加密后的 API Key 和 Chat 代理 URL。

配置 `QDRANT_URL` 后，向量也会按模型配置写入独立 Qdrant collection。SQLite 向量仍是本地可靠回退，因此 Qdrant 临时不可用不会阻止知识正文和分块入库。

原始模型 API Key 和含凭据的代理 URL 不会通过查询接口返回。后端使用 Fernet 加密后保存密文：

- 本地模式默认密钥文件：`backend/data/model_secret.key`；
- 生产或多实例部署：通过 `MODEL_SECRET_KEY` 注入同一把 Fernet key；
- `backend/data` 和 `.env` 已被 Git 忽略，不会上传到仓库；
- 如果密钥文件丢失，旧 API Key 和代理 URL 无法解密，需要在前端重新填写。

API 模式会传输业务内容：诊断大模型接收案例证据，Embedding API 在重建索引时接收
知识分块，在 Agentic Search 中还可能接收记忆或源码候选；Reranker API 接收检索问题
和跨模块候选。只能配置公司批准且允许接收这些数据的端点；生产环境应同时启用
HTTPS 和后端鉴权。

后端会在保存、启用和每次实际调用前验证 Base URL：

- 仅支持 `http://` 和 `https://`，不允许 URL 内嵌账号密码、查询串或片段；
- 云元数据、链路本地、未授权的回环/私网地址会被拒绝；
- 开发/本地环境中的普通 HTTP 与 HTTPS 地址均可直接配置，不因使用 HTTP 强制要求白名单；
- 回环地址以及 `APP_ENV=prod` 下的所有 API 地址必须显式加入 `MODEL_ENDPOINT_ALLOWLIST`；
- 内网主机较多时可显式设置 `MODEL_ALLOW_PRIVATE_ENDPOINTS=true`，但回环和危险系统地址仍受限制，生产环境仍要求精确白名单。

Chat 模型代理执行相同的危险地址和生产白名单策略。代理 URL 仅支持 `http://` 或
`https://`，可以包含认证信息，但不能包含路径、查询串或片段；返回给前端的提示会移除
用户名和密码。私网和单标签代理主机应优先加入 `MODEL_ENDPOINT_ALLOWLIST`；受控开发环境
无法枚举时可显式启用私网地址。本机/回环代理仍必须精确加入白名单。

开发电脑直接使用受信任的内网 HTTP/HTTPS 模型时，推荐配置：

```env
MODEL_ALLOW_PRIVATE_ENDPOINTS=true
```

受控公司 Win11 电脑确认需要允许私网模型/代理地址时，可运行一次：

```bat
scripts\enable_private_model_endpoints.bat
```

脚本只把 `MODEL_ALLOW_PRIVATE_ENDPOINTS=true` 持久写入本机 Git 忽略的 `.env`，重复运行
不会产生重复配置，也不会输出 `.env` 内容或密钥。后续启动无需再次设置；执行后必须完全
关闭并重启后端。该开关不放行回环、链路本地、云元数据地址，也不绕过生产白名单。
HTTP 连接本身不再触发白名单校验；是否接受无传输加密的 HTTP 由部署方决定。

## 3. 知识分类层次

首次启动会自动建立以下分类：

```text
诊断规则
├─ 日志与错误码规则
├─ 协议诊断规则
├─ 产品诊断规则
└─ 安全诊断规则
历史问题诊断
├─ 故障树
├─ 解决方案
├─ 已知问题与案例
└─ 结构化故障案例
分析方法与 Skill
├─ 错误分析 Skill
└─ 提炼分析方法
参考资料
├─ 产品文档
├─ 协议文档
└─ 测试规范
```

前端支持：

- 新增根分类和子分类；
- 修改分类名称、父级、说明和排序；
- 删除没有子分类、没有文档的自定义分类；
- 按分类及所有下级分类查看知识；
- 新增文本知识或上传 Markdown/TXT/JSON/LOG；
- 修改标题、正文、分类、设备、模块、固件范围、可信等级和可见级别；
- 修改正文时重新切分并重建当前 Embedding 的向量；
- 提交审核、发布、驳回、归档，以及查看/恢复历史版本；
- 使用模板维护结构化故障案例并检查必需章节；
- 从故障案例或错误分析 Skill 提炼可追溯的分析方法；
- 删除正文、分块和对应向量。

### 3.1 一个或多个 Markdown 的智能归类

本轮新增的智能归类与“把多份材料提炼成一个故障案例”是两条独立流程。智能归类只接受
1–20 个 `.md`/`.markdown` 文件，保留每份文件的原正文，并按文件创建一个独立知识文档和
一个独立持久任务；它不会把多份文件合并。模型只能从当前活动的叶子分类中选择一个位置，
`source_type` 随分类语义确定，可同时建议 `GW`、`AP`、`GENERAL`、`OTHER` 设备范围和简短模块。

网页端由管理员在“知识库”点击“AI 智能导入 MD”，选择一个或多个文件、启用的 Chat Profile、
可信等级和可见级别。API 模型需要显式确认内容外发；原文件先保存在本地，分类请求只发送经过
敏感信息遮蔽和长度限制的标题/正文片段。服务器为每个文件分别调用选择的 Profile，校验返回
Schema 和活动叶子分类后再写入知识库。

CLI 不使用平台 Chat Profile 做分类。先用 Skill 中的确定性助手把完整 Markdown 通过 REST
multipart 数据面上传：

```powershell
& .\agent-skills\gw-ap-debug\scripts\upload-knowledge-markdown.ps1 `
  -Path @('D:\knowledge\fault-tree.md', 'D:\knowledge\protocol-notes.markdown')
```

助手优先使用显式 `-ApiBaseUrl` 或 `DEBUGPLATFORM_API_BASE_URL`；未提供时可从
`DEBUGPLATFORM_MCP_URL` 去掉 `/mcp` 后推导 `/api/v1`，并等待每个持久任务完成。随后当前
Claude Code/Codex 会话模型调用 `debug_get_knowledge_routing_context`，读取活动分类以及每个草稿
的脱敏、限长片段、`expected_lock_version` 和正文 SHA-256，再调用
`debug_apply_knowledge_routing` 提交逐文件决策。完整 Markdown 不进入 MCP JSON；Host 路径后端
生成式 Chat 调用固定为零。

两条路径都要求管理员权限，并且每个输入文件最终只得到一个 `active=false`、
`review_status=DRAFT` 的文档。分类本身不会提交审核或发布；管理员仍需检查标题、分类、设备、
模块和正文，再按 `DRAFT → IN_REVIEW → ACTIVE` 的既有状态机处理。Host 写回还会校验 lock
version、正文哈希和叶子分类，文件在分类后被修改时必须刷新上下文重新判断，不能覆盖新版本。

2026-09-03 当前源码验收中，浏览器多文件场景和真实 Codex CLI Host 分类均通过；Codex batch
`KRBATCH-28b4b098d8984ae2` 分别选择 `history.fault_trees` 与
`diagnosis.protocol_rules`，两个文档均保持 DRAFT/inactive，后端 Chat 与模型外发均为零。
当前主机未安装 Claude CLI，OpenCode 按用户要求未测试，这两项不属于上述 Codex 通过结论。

知识文档的“设备类型”是适用范围元数据，不是左侧分类树。可选值为 `GW`、`AP`、
`通用` 和 `其他`；新建跨产品知识时应选择“通用”。`GENERAL` 通用文档会参与 GW 和 AP
案例检索，历史 `OTHER` 文档继续按共享知识处理，避免升级后丢失召回。

内置分类不能删除，但可以在其下继续增加公司自己的层次。

## 4. 添加和切换诊断大模型

打开“系统设置 → 诊断大模型 → 添加诊断大模型”，填写：

- 配置名称；
- 运行方式：API；
- 模型名称；
- OpenAI-Compatible Base URL；
- API Key；
- 可选模型代理 URL，留空表示该 Profile 直连；
- Temperature 和超时；
- Thinking 模式和 `max_tokens`。Thinking 可选择“跟随模型默认”“强制开启”或“强制关闭”：
  GLM-5.1/5.2 开启时发送 `thinking: {"type": "enabled"}`，关闭时显式发送
  `thinking: {"type": "disabled"}`，跟随默认时不发送该字段。旧 Profile 的
  `thinking_enabled=true/false` 会分别迁移为开启/关闭。`max_tokens=0` 表示沿用端点默认值，
  长诊断可按批准的模型配额填写例如 `65536`。

新建 API Profile 的超时默认是 300 秒，允许在 5–600 秒之间调整。GLM-5.1/5.2 读取完整联合
方法并生成长结构时可能超过 120 秒；已有 Profile 的显式超时不会被升级覆盖，如仍为 120 秒，
建议在确认公司网关策略后改为 300–600 秒。
当前私有故障树的真实 GLM-5.2 回归中，`max_tokens=16384` 会以 `finish_reason=length` 截断首轮
结构化规划，`max_tokens=65536` 可完成 27 节点闭环；使用相同规模方法文档时建议配置 65536，
同时通过后台轨迹监控实际 Token 和耗时。

智能日志筛查的 `LLM LOG PLAN` 是 Pattern/关键词的有界 JSON 提取，而不是最终综合推理。
该阶段无论 Profile 的 Thinking 设置为何，都会显式发送 `thinking.type=disabled`；综合诊断和
报告修订仍遵循 Profile 设置。这样可以避免 GLM 的思考内容占满结构化输出预算，也降低企业
代理长请求超时的概率。失败时前端会区分代理、超时、TLS、鉴权、权限、限流、BadRequest、
模型不存在、上游错误、连接失败、输出截断和无效 JSON，而不再全部显示为
`MODEL_REQUEST_FAILED`。

保存后先点击“测试”，成功后点击“切换使用”。系统允许保存多套 Qwen、GLM 或内部兼容网关配置，但同一时间只有一个诊断模型处于激活状态。已有 `.env` 中的 `LLM_*` 配置会在首次升级启动时导入为一个模型配置，作为兼容路径。

需要结构化结果的 Chat 请求会使用 OpenAI-Compatible `response_format={"type":"json_object"}`，
并在系统指令中给出字段契约；明确不支持该参数的旧网关会自动退回纯提示 JSON。GLM 偶尔会把
结构名称作为单一外层字段，日志规划器会安全解包后继续执行相同 Schema、必读文档 ID 和 Pattern ID
校验，不会因此放宽证据门禁。

代理是逐 Profile 配置，只应用于该 Chat 模型的诊断、案例问答和知识提炼请求。代理地址
和其中的账号密码使用与 API Key 相同的 Fernet 密钥加密，列表只显示不含凭据的
`scheme://host:port`。启用代理时客户端不再继承进程或浏览器代理，并强制清除 TLS CRL
吊销检查标志；`CERT_REQUIRED`、证书链校验和主机名校验保持开启。代理留空时前端创建的
Profile 明确直连；历史 `MODEL-chat-env` 环境变量 Profile 仍保留读取系统代理的兼容行为。

跳过吊销检查不能解决“不受信任的签发机构”。公司中间人代理的根证书仍必须进入系统
信任源，或由管理员通过 `SSL_CERT_FILE` 提供受控 CA bundle。禁止使用 `verify=false`。

诊断结果不是直接信任模型返回值：后端会检查固定 JSON 结构、方法/Pattern/故障树节点 ID、置信度范围以及每个事实/假设引用的 `evidence_id`。综合诊断在同一后台任务和同一异步事件循环中执行两至二十轮原生只读工具循环，避免异步 HTTP 客户端跨事件循环复用造成后续轮次 `APIConnectionError`。每轮最多调用四次方法目录/全文、知识检索、全部持久化日志筛查证据检索或 evidence 读取工具；重复调用复用已有结果。故障树流程、判断点和根因分支必须逐项检索并形成“证据支持、已排除或证据不足”结论，未执行的节点不能提前写终态。工具参数、方法/Pattern/节点/evidence ID 在调用前校验，单轮最多纠正两次；未完成覆盖时规划与最终合成都不能被标记为通过。GW/AP 诊断使用联合知识范围，普通认知检索仍可保持单设备过滤。如果模型引用不存在的证据、返回非法 JSON 或调用失败，诊断会保留规则与 RAG 的确定性结果，并在前端显示内容安全的错误码、字段路径和 `finish_reason`。通过问答发起的诊断/报告修订也必须保留同一故障树覆盖账本并通过证据校验，经人工确认后才创建新诊断版本。历史诊断还会保存当时模型名称、配置 ID、Base URL 和非密钥参数快照，API Key 永远不会进入该快照。

二十轮是穷尽排查的上限，不是必须消耗的轮数。系统默认允许单次综合诊断规划累计 2,000,000
Tokens、三小时、80 次只读工具调用，以及最多三轮连续无进展；只有完全重复且没有新覆盖、查询、
证据或唯一工具调用才计入停滞。可在本机 Git 忽略的 `.env` 调整：

```env
DIAGNOSTIC_AGENT_MAX_TOTAL_TOKENS=2000000
DIAGNOSTIC_AGENT_MAX_DURATION_SECONDS=10800
DIAGNOSTIC_AGENT_MAX_TOTAL_TOOL_CALLS=80
DIAGNOSTIC_AGENT_MAX_STAGNANT_ROUNDS=3
DIAGNOSTIC_CONTEXT_WINDOW_TOKENS=131072
DIAGNOSTIC_CONTEXT_RESERVED_OUTPUT_TOKENS=32768
DIAGNOSTIC_CONTEXT_SAFETY_MARGIN_TOKENS=2048
DIAGNOSTIC_CONTEXT_TARGET_OCCUPANCY=0.85
DIAGNOSTIC_CONTEXT_MAX_ITEM_TOKENS=10000
DIAGNOSTIC_CONTEXT_PREVIEW_TOKENS=768
DIAGNOSTIC_CONTEXT_SPILL_CHUNK_TOKENS=3000
```

触达边界不会把未完成结果标记为通过，而是保留故障树覆盖状态、显示明确停止原因并回退确定性诊断。
已经在边界调用中完整达到成功条件的结果仍可正常完成，不会因恰好用完预算而被丢弃。

Chat Profile 可在前端额外填写“上下文窗口 Tokens”和“预留输出 Tokens”。前者是模型真实的
输入+输出总窗口，后者为结构化诊断答案预留；留空时使用上面的默认值，预留输出会优先沿用
Profile 的 `max_tokens`。系统只把剩余容量的目标 85% 用作输入，避免代理/模型端因 Token
估算差异拒绝请求。被压缩正文使用本次运行内存句柄分段续读，不持久化原始日志。

“运行轨迹”中的 Tokens 显示总量及输入/输出明细。日志规划的格式纠正、失败回退和最终诊断合成
都会保留供应商返回的 usage；供应商只给输入/输出而没有总量时，后端与前端都按两者之和回算。

## 5. Embedding 运行方式

标准源码安装和 Win11 便携包均不包含 PyTorch、Sentence Transformers 或模型权重。默认
`Hashing Embedding` 完全离线可用，不依赖模型下载，也不会触发原生 DLL 初始化问题。

仓库过去的一体化安装器会把本地模型运行依赖装进平台主 `.venv`。这使 FastAPI 进程同时
承担 Web 服务和 Torch 初始化，在部分 Win11 设备上会出现
`Model connection failed: WinError 1114`，因此相关下载、网络检测和安装脚本已经删除。

推荐顺序如下：

1. 普通离线部署使用内置 Hashing；
2. 有批准的模型网关时，在“系统设置 → Embedding 模型”选择 API；
3. 必须本地运行 BGE 时，在平台之外建立独立虚拟环境或容器，启动 OpenAI-Compatible
   Embedding 服务，再按 API 方式接入平台；
4. “系统设置 → 本地模型权重下载”可以准备 BGE/Qwen 文件，支持显式代理、将 revision 固定
   到不可变 Commit、`.partial` 断点续传和任务恢复；同模型下载串行执行，新 generation 全量
   校验后才原子切换，失败或取消保留上一版本。它不安装运行时，完成后应由独立模型服务加载；
5. `backend[local-models]` 只保留旧式进程内适配器兼容性，不属于便携部署支持面，也不建议
   安装到正在运行平台的主环境。

根目录本机 `A.py` 已被 Git 忽略。其下载思路已经重构为受管后台任务：目标目录不可由浏览器
任意指定，代理密文持久化，远端路径、文件数和大小受限，镜像 Commit、同模型锁、分代暂存和
完整 SHA-256 门禁保证完成前不会切换正式版本。前端显示的是当前活动 generation 路径；版本
更新后独立模型服务需要自行重新加载。它仍不提供运行环境、服务进程或健康检查，因此不能替代独立模型服务安装方案。便携部署详见
[Win11 便携部署与本地模型隔离](windows-portable-deployment.md)。

## 6. Embedding API

Embedding API 使用 OpenAI-Compatible `/embeddings` 接口。Base URL 应填写到 API 的版本根路径，例如：

```text
https://your-approved-endpoint.example/v1
```

模型名可以是公司网关暴露的名称。部分模型支持可选向量维度；修改模型名或维度后，旧向量会自动失效，必须重新构建。

阿里云百炼的 Embedding OpenAI-Compatible 调用和维度说明见：[Embedding API](https://help.aliyun.com/en/model-studio/embedding)。

## 7. Reranker 运行方式

便携包默认使用 `Disabled Reranker`，保持 BM25 与 Embedding 融合结果，不加载任何原生模型。
需要 Qwen Reranker 时优先配置 API；必须本地运行时，应把 Qwen 服务放在独立进程/容器，
由平台通过 API 调用。前端仍显示旧式本地 Profile 入口以兼容已有高级部署，但会明确提示便携
运行时不含所需依赖，且后端遇到 WinError 1114 时会返回模型隔离指引。

## 8. Qwen Reranker API

选择“Reranker 模型 → API”，适配器会调用：

```text
POST {Base URL}/reranks
```

例如 Base URL 可以填写到：

```text
https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-api/v1
```

默认模型名为 `qwen3-rerank`。请求会发送 `query`、候选 `documents`、`top_n` 和诊断检索指令。官方接口格式见：[Qwen Text Rerank API](https://help.aliyun.com/en/model-studio/text-rerank-api)。

## 9. 关于知识图谱

当前版本同时包含领域知识图谱、代码关系图谱和 Commit 意图图谱，均通过 SQL 关系表
保存，不强制依赖图数据库。

领域图谱只读取已审核发布的知识，从元数据和结构化 Markdown 确定性提取设备、版本、
模块、症状、日志模式、事件码、根因、诊断步骤、方案、验证和范围关系。它使用 generation
旁路构建，发布前校验输入签名；失败或构建期间知识变化时继续保留上一版本。

代码图谱使用 `CALLS`、`REFERENCES`、`INHERITS`、`IMPLEMENTS` 关系；Commit 图谱支持
query → Commit → 变更文件 → 当前代码符号。三类图谱均由 Agentic Search 与知识、
记忆和向量检索融合。领域自动提取关系是候选检索路径，不会越过知识审核直接成为
确定性诊断事实。完整使用方式见 [认知检索与图谱使用说明](cognitive-retrieval.md) 和
[知识治理、领域图谱与检索评测](quality-governance-and-evaluation.md)。

## 10. 推荐使用顺序

1. 保持内置 Hashing Embedding 和关闭 Reranker，确认所有基础功能正常；
2. 添加并测试公司批准的诊断大模型 API；
3. 根据数据是否允许出网，选择本地 BGE 或 Embedding API；
4. 激活 Embedding 后重建向量索引，确认向量数等于知识分块数；
5. 审核并发布确认过的知识，再重建领域图谱；
6. 启用 Qwen Reranker，并用固定评测集对比切换前后的检索指标；
7. 生产环境使用 HTTPS、统一后端密钥、SSO/RBAC 和数据库备份。
