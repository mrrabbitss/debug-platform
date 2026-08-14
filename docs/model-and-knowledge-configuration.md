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

“运行轨迹”中的 Tokens 显示总量及输入/输出明细。日志规划的格式纠正、失败回退和最终诊断合成
都会保留供应商返回的 usage；供应商只给输入/输出而没有总量时，后端与前端都按两者之和回算。

## 5. 本地 BGE Embedding

默认启动不会安装 PyTorch 或下载大型模型。先启动过一次项目以建立 `.venv`，关闭服务窗口。可先运行不会下载权重的网络检测：

```bat
scripts\check_hf_model_access.bat
```

检测会分别验证镜像 API、`curl.exe` 小文件下载和 Hugging Face CLI，并生成 `hf_model_access_report_*.txt`。`PASS_HF_CLI` 和 `PASS_CURL_FALLBACK` 都表示正式安装器存在可用下载路径。

然后运行：

```bat
scripts\install_local_models.bat
```

该脚本会设置 `HF_ENDPOINT=https://hf-mirror.com`、关闭 `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`，固定兼容版 Hub 和模型 revision，再优先使用 `.venv\Scripts\hf.exe download --local-dir` 下载并验证：

```text
BAAI/bge-base-zh-v1.5
→ models/embedding/bge-base-zh-v1.5

Qwen/Qwen3-Reranker-0.6B
→ models/reranker/Qwen3-Reranker-0.6B
```

如果 CLI 因公司代理、TLS 检查或镜像 HEAD 元数据响应报 `LocalEntryNotFoundError`，`Auto` 模式会自动改用 Win11 自带的 `curl.exe`。回退路径从镜像 API 读取固定 revision 的文件清单，以 `.partial` 文件断点续传，拒绝不安全路径，并校验大小和 LFS 权重 SHA-256。也可以使用 `-DownloadMode Curl` 强制走该路径。

然后在“系统设置 → Embedding 模型”中测试并激活“本地 BGE Base 中文向量（项目 models 目录）”。激活后必须执行“重建向量索引”。项目会把仓库相对路径稳定地解析到项目根目录，不受从 BAT、终端或 IDE 启动的当前目录影响。

系统只会给检索问题添加 `为这个句子生成表示以用于检索相关文章：`，知识正文不会添加该指令；向量默认归一化。查询指令和批量大小可以在前端修改。也可以填写其他 Sentence Transformers 兼容的 BGE 模型或本地绝对路径。

BGE v1.5 的 Sentence Transformers、查询指令及归一化用法见官方模型卡：[BAAI/bge-base-zh-v1.5](https://huggingface.co/BAAI/bge-base-zh-v1.5)。

## 6. Embedding API

Embedding API 使用 OpenAI-Compatible `/embeddings` 接口。Base URL 应填写到 API 的版本根路径，例如：

```text
https://your-approved-endpoint.example/v1
```

模型名可以是公司网关暴露的名称。部分模型支持可选向量维度；修改模型名或维度后，旧向量会自动失效，必须重新构建。

阿里云百炼的 Embedding OpenAI-Compatible 调用和维度说明见：[Embedding API](https://help.aliyun.com/en/model-studio/embedding)。

## 7. 本地 Qwen3 Reranker

运行本地模型安装脚本后，可以测试并激活“本地 Qwen3 Reranker 0.6B（项目 models 目录）”：

```text
Qwen/Qwen3-Reranker-0.6B
→ models/reranker/Qwen3-Reranker-0.6B
```

该适配器使用 Sentence Transformers `CrossEncoder` 和自定义网络诊断排序指令。安装器会真实加载模型并通过项目适配器对两个示例文档执行排序，只有返回有效分数才会报告成功。CPU 可以运行，但速度和内存占用取决于模型大小；公司电脑资源有限时保持批量大小 `1`–`4`，或使用批准的 API。

Qwen 官方模型卡列出了 0.6B、4B、8B Reranker，并提供 CrossEncoder 和自定义指令用法：[Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)。

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
