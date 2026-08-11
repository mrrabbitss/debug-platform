# 认知检索、故障知识与图谱使用说明

本文说明结构化故障案例、分析方法提炼、领域/代码/Commit 图谱、任务记忆和
Agentic Search 的实际使用方式、数据边界与验证方法。知识审核、GraphRAG generation、
评测和人工反馈的完整流程另见
[`quality-governance-and-evaluation.md`](quality-governance-and-evaluation.md)。
项目全部功能状态仍以根目录
[`CAPABILITIES.md`](../CAPABILITIES.md) 为唯一总账。

## 1. 能力关系

```text
Markdown 故障案例 / 错误分析 Skill
              │
              ├─ 结构检查、分块、BM25、Embedding
              ├─ 确定性提炼 → 可复用分析方法
              └─ 审核发布 → 领域实体/关系 → GraphRAG

代码仓归档 ──→ 代码符号 ──→ CALLS / REFERENCES / INHERITS / IMPLEMENTS
     │
Git Bundle ──→ Commit ──→ 变更文件 ──→ 当前代码符号

诊断 / 问答 / 检索任务
              └─ EPISODIC / PROCEDURAL / FAILURE 记忆

用户 query
  └─ Agentic Search 动态计划
       ├─ 知识库混合检索
       ├─ 领域知识图谱多跳
       ├─ 任务记忆
       ├─ 代码图谱多跳
       └─ Commit → 文件 → 代码路径
            ↓
       RRF 融合 → Dense Embedding → 可选 Reranker
            ↓
       证据、阶段追踪和可解释路径
```

这些图谱当前使用 SQLAlchemy 关系表保存在 SQLite 或 PostgreSQL 中，不要求部署图
数据库。领域/代码实体、关系、Commit 和文件变更使用由来源及语义身份生成的确定性 ID，
同一仓库在内容未变时重建索引不会让已有 evidence ID 漂移。SQLite 仍是单机默认，
Qdrant 只作为知识向量的可选镜像。

## 2. 结构化 Markdown 故障案例

在前端打开“知识库”，点击“故障案例模板”。四个必需章节是：

1. 错误形式；
2. 日志分析；
3. 错误定位；
4. 解决方案。

“验证结果”和“适用范围与限制”为推荐章节。系统检查的是章节内是否已有实质内容，
只有字段名、冒号或空列表的原始模板不会被误判为完整案例。前端列表会显示结构完整
度和缺失章节。

也可以上传 UTF-8 `.md` 文件，选择知识类型“结构化故障案例”。推荐格式：

```markdown
# 故障案例：AP 认证超时

## 错误形式

- 用户可见现象：终端连接 AP 时认证超时。
- 影响范围：指定固件版本的 WPA2-Enterprise。

## 日志分析

- EAP 成功后没有进入四次握手。
- 首次异常发生在认证重试计时器到期后。

## 错误定位

1. 对齐正常与异常样本时间线。
2. 检查 hostapd 状态和认证重试计时器。
3. 定位相关文件、函数和 Commit。

## 解决方案

1. 修复计时器重置逻辑。
2. 增加回归测试并准备回退 Commit。

## 验证结果

- 连续认证 100 次均成功，相关回归测试通过。
```

解析结果保存在知识文档的 `metadata.markdown_structure`，原始 Markdown 仍是权威
内容。修改文档后会重建分块和当前活动 Embedding Profile 的向量。

## 3. 从案例或错误分析 Skill 提炼方法

错误分析 Skill 也以 Markdown 文档保存，上传时选择“错误分析 Skill”。系统识别
“适用场景、输入信号、日志检查、证据收集、分析步骤、排查步骤、决策流程、工作流、
解决方案、验证”等中英文标题。

在知识列表点击“提炼方法”后，系统会：

1. 读取来源 Markdown 的标题和步骤；
2. 整理输入信号、日志检查、定位步骤、分支、解决与验证；
3. 创建“提炼分析方法”文档；
4. 保存来源文档与派生文档的 lineage；
5. 建立知识分块和向量索引。

提炼采用确定性重组，不调用 LLM，不补造来源中不存在的根因或解决结论。来源案例
修改后，未被人工改写的派生方法会自动刷新；如果工程师改过派生方法，系统会保留
人工内容并标记“需复核”，再次点击“提炼方法”并确认覆盖后才会明确重新生成。删除
来源案例时对应派生方法一并清理。删除派生方法不会删除来源案例，之后可以重新提炼。

## 4. 构建代码图谱

### 4.1 支持范围

当前索引器支持：

- C/C++：函数、函数声明、宏、结构体、类；
- Python：函数、异步函数、方法、类；
- Java：类、接口、方法；
- JavaScript/TypeScript：类、接口、命名函数；
- Go：函数和带 receiver 的方法。

关系类型：

| 关系 | 含义 |
| --- | --- |
| `CALLS` | 函数或方法调用另一个符号 |
| `REFERENCES` | 代码引用已解析的函数、类型、宏或其他符号 |
| `INHERITS` | 类继承基类 |
| `IMPLEMENTS` | 类实现接口，或函数定义对应声明 |

上传代码归档后，在案例“代码仓库”页点击“建立索引”。任务完成后仓库会分别显示
`graph_status` 和 `commit_graph_status`。在“认知检索 → 代码与 Commit 图谱”中可按
符号、文件或错误词查询，并展开最多 1～3 跳关系。

### 4.2 精度边界

默认实现不会执行、编译或导入上传代码，而是使用 Python AST 和安全的多语言语法
启发式。它能建立可解释候选关系，但不等同于编译器语义：

- C/C++ 宏展开、重载、模板和条件编译可能产生缺边或歧义边；
- JavaScript/TypeScript 箭头函数和动态调用覆盖有限；
- 无法解析的调用会保留低置信度名称边，不能当作确定事实；
- 同名符号优先按同文件、同模块解析，仍需要工程师复核。

需要更高精度时，后续路线是接入 `compile_commands.json`、Clang AST 或 Language
Server，并保留当前关系表作为统一输出契约。

## 5. 构建 Commit 图谱

普通 ZIP/TAR 通常没有 `.git` 历史，只能建立代码图谱，Commit 图谱会显示
`UNAVAILABLE`。推荐在待分析源码仓根目录执行：

```bat
git bundle create repository.bundle --all
```

然后在案例“代码仓库”上传 `repository.bundle` 并点击“建立索引”。Bundle 导入会：

- 禁用交互式凭据、全局/系统 Git 配置、LFS 自动下载和仓库 hooks；
- 拒绝对象 alternates 和符号链接；
- 继续执行项目统一的文件数、单文件和展开总大小限制；
- 保存分支、HEAD、最多 2,000 个 Commit 及其文件变更。

查询路径为：

```text
query
  → Commit 主题、正文和变更路径的 BM25 匹配
  → changed file
  → 该文件在当前 HEAD 中的代码符号
  → 可继续沿代码关系图扩展
```

Commit 图谱保存 hash、父提交、作者、时间、主题、正文以及新增/修改/删除/重命名
路径。它关联的是“该 Commit 修改的文件”和“当前 HEAD 中该文件的符号”，不是历史
版本的逐行 AST，也不等同于 `git blame`。

## 6. 三类任务记忆

| 类型 | 内容 | 范围 |
| --- | --- | --- |
| `EPISODIC` | 某次诊断、问答或检索的情景、结果和证据 | 当前案例 |
| `PROCEDURAL` | 可复用的排查顺序、动作和结束条件 | 案例或脱敏后的全局方法 |
| `FAILURE` | 任务失败、证据缺失、模型/索引限制和避免方式 | 当前案例 |

诊断完成或失败、每次案例问答、每次 Agentic Search 都会按结果提炼适合的记忆。记忆
使用内容指纹去重；相同案例、相同 query 和相同模块计划的重复检索会更新同一条检索
情景，而不会递归制造“检索自己的检索记忆”。系统保存出现次数、置信度、结果、来源、
复用次数和最后复用时间。

案例情景和失败记忆不会跨案例召回。只有可复用程序记忆可以全局使用，写入前会遮盖
密码、Token、API Key、MAC、IP 和序列号。召回的记忆只是候选证据，“以前成功”不会
被系统自动转换成“本次根因已经确认”。

## 7. Agentic Search

前端“认知检索”页可以选择案例并输入自然语言问题。自动规划器默认使用知识库和
记忆；存在活动领域图谱时增加 GraphRAG；检测到函数、调用、继承、实现等意图时
增加代码图谱；检测到 Commit、变更、回归、版本或“谁改的”等意图时增加 Commit
图谱。

也可以关闭自动规划并手动选择模块。执行结果包含：

- `plan`：选中的模块、意图信号、算法和选择理由；
- `trace`：每个模块、图扩展、RRF、Dense、Reranker 的状态、耗时和候选数；
- `results`：统一 evidence ID、来源、分数、元数据和局部路径；
- `paths`：代码关系路径及 query → Commit → 文件 → 代码路径；
- `summary`：各模块候选数、融合数和最终返回数。

统一排序流程：

1. 知识模块先生成有界 BM25/精确词原始候选，不在模块内重复调用模型；
2. 领域图谱沿已审核知识关系扩展，记忆和 Commit 模块执行 BM25，代码模块执行词法定位和关系扩展；
3. 使用加权 Reciprocal Rank Fusion 合并不同模块，并以轮询配额避免单模块挤占候选；
4. 使用当前活动 Embedding 对跨模块候选统一计算余弦相似度；
5. 当前活动 Reranker 只执行一次最终排序；未配置或失败时保留融合结果。

知识 Dense 检索在配置 Qdrant 时直接执行有界 top-K；SQLite/PostgreSQL 回退只扫描
向量行并在取回正文前裁剪候选。全量 Embedding、代码图谱和领域图谱重建都使用
generation 旁路构建，只有完整成功后才切换活动版本。领域图谱还会检测构建期间
已审核知识是否变化，过期构建不会发布。

Embedding 或 Reranker 使用 API Profile 时，候选知识、记忆或源码片段可能发送到该
模型端点。企业环境必须先确认日志和源码的数据出站策略；需要完全本地处理时使用
Hashing/BGE 与本地 Qwen Reranker。系统审计只记录端点来源、用途、条目数、字符数、
耗时和结果，不记录请求正文。

## 8. LLM 日志规划与综合诊断

案例页“智能日志筛查”不再把所有结构化事件直接当成同等重要。已解析日志的处理顺序是：

1. 加载知识库中 `ACTIVE + active=true` 且适用当前设备的故障树、分析方法、日志规则和历史案例；
2. 如果项目根目录存在 Git 忽略的 `故障树.md`、`日志分析.md`，把它们作为本机只读方法一并加载；
3. 将每份完整方法和编译后的日志 Pattern 发送给当前 Chat 模型，要求模型回报全部已读文档 ID、相关 Pattern ID、追加字面量关键词、假设、检查步骤和停止条件；
4. 后端验证文档/Pattern ID，验证失败或模型不可用时回退到确定性规划；
5. 在本机逐行扫描完整提取文本，不只扫描 Parser 已结构化的事件；
6. 发布三层证据：`LLM_RELEVANT`、`METHOD_REQUIRED`、`OTHER`。

第一层包含模型结合问题描述选择的 Pattern 和追加关键词；第二层包含所有适用方法中没有进入
第一层的 Pattern，因此模型不能让必查规则消失；第三层是未命中前两层的结构化事件。前两层会
按规范化消息聚类，但保留原始文件、首末行号、出现次数、方法版本、Pattern 和排序理由。没有被
Parser 识别成事件的命令输出也能出现在前两层；第三层只对结构化事件分页。

原始日志全文不会发送到 Chat 模型。模型只规划“本机要查什么”，完整扫描和命中内容保留在本机。
真实 API Profile 还要求案例明确开启“模型出站授权”。方法正文仅用于当次推理，Agent 轨迹保存
文档 ID、版本、哈希和轮次，不保存正文；分析快照同样省略方法正文。

“综合诊断”会在确定性检索基线之后执行至少两轮、最多三轮 LLM Planning。每轮都会重新提供全部
适用方法、三层日志证据、以前轮次和检索观察，并要求每个检查引用有效方法 ID。Planner 每轮最多
产生四个去重检索 query；证据足够、达到轮次上限、校验失败、取消或预算耗尽都会产生明确停止原因。
最终合成仍只能引用系统提供的 evidence ID，非法引用会回退到确定性结果。

“交互问答”返回 `202 Accepted` 和持久化 Job。前端轮询消息/Job、可取消，并在刷新页面后恢复等待；
模型、检索或超时错误会保存在本轮消息和轨迹中，不再表现为浏览器长请求无回复。

## 9. API

```text
GET  /api/v1/knowledge/templates/fault-case
POST /api/v1/knowledge/{document_id}/extract-method
GET  /api/v1/knowledge/graph/status
POST /api/v1/knowledge/graph/rebuild
POST /api/v1/knowledge/graph/search

POST /api/v1/cases/{case_id}/repositories
GET  /api/v1/jobs/{job_id}
POST /api/v1/repositories/{repository_id}/index
GET  /api/v1/repositories/{repository_id}/graph
GET  /api/v1/repositories/{repository_id}/graph/search
GET  /api/v1/repositories/{repository_id}/commit-graph

POST /api/v1/cases/{case_id}/agentic-search
GET  /api/v1/cases/{case_id}/memories
POST /api/v1/cases/{case_id}/artifacts/{artifact_id}/triage
GET  /api/v1/cases/{case_id}/log-triage
GET  /api/v1/cases/{case_id}/log-triage/{triage_run_id}
GET  /api/v1/cases/{case_id}/log-triage/{triage_run_id}/evidence
POST /api/v1/cases/{case_id}/analyses
POST /api/v1/cases/{case_id}/chat
GET  /api/v1/cases/{case_id}/conversations
GET  /api/v1/cases/{case_id}/agent-runs/{run_id}
GET  /api/v1/system/retrieval
```

仓库上传返回 `202 Accepted`，响应中的 `job` 完成后才能调用索引接口。知识文件上传
同样返回后台导入任务，避免大文件读取、解压、切块和模型推理占用 FastAPI 事件循环。

Agentic Search 示例：

```json
{
  "query": "哪个 Commit 引入了 WLAN 认证回归，相关调用链和以前的处理经验是什么？",
  "top_k": 12,
  "max_hops": 2
}
```

手动模块示例：

```json
{
  "query": "auth_retry 调用了哪些函数？",
  "modules": ["code"],
  "top_k": 20,
  "max_hops": 3
}
```

## 10. 验证

基础验证命令：

```bat
.\.venv\Scripts\python.exe -m ruff check backend
.\.venv\Scripts\python.exe -m pytest backend\tests -q
cd frontend
npm.cmd run build
```

专项自动化覆盖：

- 故障模板空值检查、方法提炼、来源更新和删除级联；
- C/Python/Java 图谱的四类关系和多跳路径；
- 真实临时 Git 仓的 Commit、Unicode 路径和 Git Bundle 导入；
- 三类记忆生成、去重、脱敏、案例隔离和复用计数；
- Agentic Search 计划、知识/记忆融合、Dense、Reranker 回退和失败记忆；
- 完整日志文本扫描、方法 Pattern 编译、三层聚类、原始行号和结构化事件回退；
- 方法文档强制阅读、至少两轮综合规划、证据 ID 校验和确定性回退；
- 持久化异步问答、任务恢复和增量 Agent 轨迹；
- Playwright 合成日志上传、三层 UI、两轮诊断、后台问答与浏览器控制台零错误；
- Alembic `0001`～`0014` 新建数据库、旧数据库升级、generation 回滚、报告版本唯一性和诊断规划状态。

真实企业源码和日志仍应在内部环境建立经过脱敏的 Golden Corpus，用 Recall、MRR、
NDCG、多跳路径准确率和工程师复核结果持续评估。
