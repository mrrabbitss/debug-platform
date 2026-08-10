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
- `model_profiles`：Chat、Embedding、Reranker 配置和加密后的 API Key。

配置 `QDRANT_URL` 后，向量也会按模型配置写入独立 Qdrant collection。SQLite 向量仍是本地可靠回退，因此 Qdrant 临时不可用不会阻止知识正文和分块入库。

原始模型 API Key 不会通过查询接口返回。后端使用 Fernet 加密后保存密文：

- 本地模式默认密钥文件：`backend/data/model_secret.key`；
- 生产或多实例部署：通过 `MODEL_SECRET_KEY` 注入同一把 Fernet key；
- `backend/data` 和 `.env` 已被 Git 忽略，不会上传到仓库；
- 如果密钥文件丢失，旧 API Key 无法解密，需要在前端重新填写。

API 模式会传输业务内容：诊断大模型接收案例证据，Embedding API 在重建索引时接收
知识分块，在 Agentic Search 中还可能接收记忆或源码候选；Reranker API 接收检索问题
和跨模块候选。只能配置公司批准且允许接收这些数据的端点；生产环境应同时启用
HTTPS 和后端鉴权。

后端会在保存、启用和每次实际调用前验证 Base URL：

- 仅支持 `http://` 和 `https://`，不允许 URL 内嵌账号密码、查询串或片段；
- 云元数据、链路本地、未授权的回环/私网地址会被拒绝；
- HTTP、回环地址以及 `APP_ENV=prod` 下的所有 API 地址必须显式加入 `MODEL_ENDPOINT_ALLOWLIST`；
- 内网主机较多时可临时设置 `MODEL_ALLOW_PRIVATE_ENDPOINTS=true`，但回环和危险系统地址仍受限制，生产环境优先维护精确白名单。

示例：

```env
MODEL_ENDPOINT_ALLOWLIST=model-gateway.corp.example,.approved-models.corp.example
MODEL_ALLOW_PRIVATE_ENDPOINTS=false
```

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

内置分类不能删除，但可以在其下继续增加公司自己的层次。

## 4. 添加和切换诊断大模型

打开“系统设置 → 诊断大模型 → 添加诊断大模型”，填写：

- 配置名称；
- 运行方式：API；
- 模型名称；
- OpenAI-Compatible Base URL；
- API Key；
- Temperature 和超时。

保存后先点击“测试”，成功后点击“切换使用”。系统允许保存多套 Qwen、GLM 或内部兼容网关配置，但同一时间只有一个诊断模型处于激活状态。已有 `.env` 中的 `LLM_*` 配置会在首次升级启动时导入为一个模型配置，作为兼容路径。

诊断结果不是直接信任模型返回值：后端会检查固定 JSON 结构、置信度范围以及每个事实/假设引用的 `evidence_id`。如果模型引用不存在的证据、返回非法 JSON 或调用失败，诊断会保留规则与 RAG 的确定性结果并记录警告。历史诊断还会保存当时模型名称、配置 ID、Base URL 和非密钥参数快照，API Key 永远不会进入该快照。

## 5. 本地模型自动发现与 Embedding

新 Agent Runtime 不再把固定 BGE/Qwen 下载脚本作为本地模型前置条件。已经下载好的模型可以直接放到项目 `models/`，也可以在 `.env` 配置多个目录：

```env
MODEL_ROOTS=D:\AI\models;E:\shared-models
```

然后在“系统设置 → 本地模型自动发现”点击扫描，或使用：

```bat
gwap models scan
gwap models list
```

扫描器只读取有界 JSON/Tokenizer/Sentence-Transformers 元数据和文件名，并统计权重大小，不读取权重正文。确定性分类置信度不足时，显式扫描动作可以调用当前 Chat Profile 对经过敏感字段清理的 metadata 做复核；模型权重、日志和源码不会进入该请求。

Embedding 候选通常会识别为 `sentence_transformers` loader。对候选执行真实加载验证：

```bat
gwap models validate LM_xxx --device cpu
gwap models activate LM_xxx --device cpu
```

只有 smoke test 产生有效向量后才标记 `VALIDATED`。激活新的 Embedding 后必须执行“重建向量索引”，让知识库生成新的向量 generation。BGE v1.5 等 Sentence Transformers 模型仍支持查询指令、归一化和批量大小配置。

旧 `scripts\check_hf_model_access.bat` / `scripts\install_local_models.bat` 仅作为已经采用旧固定目录的历史环境兼容工具保留；自动发现、匹配、验证和激活逻辑不依赖它们。

## 6. Embedding API

Embedding API 使用 OpenAI-Compatible `/embeddings` 接口。Base URL 应填写到 API 的版本根路径，例如：

```text
https://your-approved-endpoint.example/v1
```

模型名可以是公司网关暴露的名称。部分模型支持可选向量维度；修改模型名或维度后，旧向量会自动失效，必须重新构建。

阿里云百炼的 Embedding OpenAI-Compatible 调用和维度说明见：[Embedding API](https://help.aliyun.com/en/model-studio/embedding)。

## 7. 本地 Reranker

Reranker 与 Embedding 共用本地模型自动发现入口。扫描器会根据目录名、`config.json`、Sentence-Transformers `modules.json` 和 architecture 区分两类 loader：

- Sentence-Transformers CrossEncoder → `sentence_transformers_cross_encoder`；
- 普通 Transformers SequenceClassification → `transformers_sequence_classifier`。

当前 Qwen3-Reranker 的 Sentence-Transformers 兼容格式会走 CrossEncoder；其他 SequenceClassification 模型不会被强行当成 CrossEncoder。候选必须执行真实 query/document pair 打分 smoke test，成功后才可激活。

```bat
gwap models scan
gwap models validate LM_xxx --device cpu
gwap models activate LM_xxx --device cpu
```

CPU 可以运行小型 Reranker，但速度和内存取决于模型规模。资源有限时应保持小 batch，或者使用公司批准的 Reranker API。

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
