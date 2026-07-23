# 模型网关与分层知识库使用说明

## 1. 当前实际使用的检索技术

诊断检索由以下阶段组成：

1. SQLite 中读取启用的知识文档、知识分块和代码符号；
2. 使用 BM25、精确错误码、函数名、路径和标题匹配生成候选结果；
3. 使用当前激活的 Embedding 配置计算向量相似度并加入混合分数；
4. 如果启用了 Reranker，则对前一阶段的候选结果重新排序；
5. 把最终知识证据连同结构化日志事件交给规则诊断和当前激活的诊断大模型。

默认 Embedding 是无需下载模型的 384 维字符 Hashing 向量，主要用于保证新克隆的电脑开箱可用。它不是训练过的语义模型。切换到本地 BGE 或 Embedding API 并重建索引后，系统才会使用相应的语义向量。

默认不启用 Reranker。激活本地 Qwen3 Reranker 或 Qwen Rerank API 后，Reranker 会参与每次知识检索，不需要重建知识索引。

## 2. 知识储存形式

默认数据都位于 `backend/data/gw_ap_debug.db`：

- `knowledge_documents`：完整知识正文和设备、模块、可信等级等元数据；
- `knowledge_chunks`：按 Markdown 标题和段落生成的检索分块；
- `knowledge_categories`：可分层的知识分类；
- `knowledge_document_categories`：文档与分类的关联；
- `knowledge_embeddings`：按 Embedding 配置隔离保存的向量缓存；
- `model_profiles`：Chat、Embedding、Reranker 配置和加密后的 API Key。

配置 `QDRANT_URL` 后，向量也会按模型配置写入独立 Qdrant collection。SQLite 向量仍是本地可靠回退，因此 Qdrant 临时不可用不会阻止知识正文和分块入库。

原始模型 API Key 不会通过查询接口返回。后端使用 Fernet 加密后保存密文：

- 本地模式默认密钥文件：`backend/data/model_secret.key`；
- 生产或多实例部署：通过 `MODEL_SECRET_KEY` 注入同一把 Fernet key；
- `backend/data` 和 `.env` 已被 Git 忽略，不会上传到仓库；
- 如果密钥文件丢失，旧 API Key 无法解密，需要在前端重新填写。

API 模式会传输业务内容：诊断大模型接收案例证据，Embedding API 在重建索引时接收知识分块，Reranker API 接收检索问题和候选知识。只能配置公司批准且允许接收这些数据的端点；生产环境应同时启用 HTTPS 和后端鉴权。

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
└─ 已知问题与案例
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

## 5. 本地 BGE Embedding

默认启动不会安装 PyTorch 或下载大型模型。先启动过一次项目以建立 `.venv`，关闭服务窗口，然后运行：

```bat
scripts\install_local_models.bat
```

该脚本使用 `hf-mirror.com` 下载并验证：

```text
BAAI/bge-base-zh-v1.5
→ models/embedding/bge-base-zh-v1.5
```

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

当前版本没有构建实体—关系知识图谱，也没有图数据库。分类树、历史故障树文档和代码符号的调用信息不等于知识图谱。

目前优先保证可解释的分层文档 RAG：每个结果都能返回知识分块、文档和日志证据 ID。后续如果要增加图谱，建议单独设计设备、版本、模块、事件码、症状、根因、解决方案以及它们的关系，再增加图检索与现有混合检索的融合；不要仅从自由文本自动生成关系后直接用于确定性诊断。

## 10. 推荐使用顺序

1. 保持内置 Hashing Embedding 和关闭 Reranker，确认所有基础功能正常；
2. 添加并测试公司批准的诊断大模型 API；
3. 根据数据是否允许出网，选择本地 BGE 或 Embedding API；
4. 激活 Embedding 后重建向量索引，确认向量数等于知识分块数；
5. 最后启用 Qwen Reranker，对比启用前后的历史案例召回结果；
6. 生产环境使用 HTTPS、统一后端密钥、SSO/RBAC 和数据库备份。
