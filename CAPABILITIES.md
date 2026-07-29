# GW/AP Debug Platform 功能、需求与兼容性总账

> 本文件是项目功能范围的唯一总账（Single Source of Truth）。
> 新需求、在研能力、已交付能力、约束和冲突处理都必须同步更新本文件，防止跨迭代遗忘或重复建设。

最后更新：2026-07-29

## 1. 状态说明

| 状态 | 含义 |
| --- | --- |
| `AVAILABLE` | 已实现并有自动化测试或运行验证 |
| `IN_PROGRESS` | 正在实现，接口或数据结构仍可能调整 |
| `PLANNED` | 已确认进入路线，但尚未开始编码 |
| `LIMITED` | 可以使用，但存在明确的输入或环境限制 |
| `DEPRECATED` | 仅为兼容保留，不应继续扩展 |

## 2. 已有能力

### 2.1 Windows 11 本地运行与运维

| 能力 | 状态 | 入口 | 说明 |
| --- | --- | --- | --- |
| 双击启动前后端 | `AVAILABLE` | `scripts/start_local.bat` | 自动准备 Python/Node 依赖并启动 FastAPI、Vue |
| 本地环境检测 | `AVAILABLE` | `scripts/doctor_local.bat` | 检测版本、依赖、端口和服务根路径 |
| 隔离运行冒烟 | `AVAILABLE` | `scripts/runtime_smoke.bat` | 使用临时数据库和独立端口验证前后端 |
| SQLite 备份恢复 | `AVAILABLE` | `scripts/backup_local.bat`、`restore_local.bat` | 带清单、哈希校验和回滚保留 |
| 本地模型网络检测 | `AVAILABLE` | `scripts/check_hf_model_access.bat` | 检查镜像、CLI 和 curl 回退并生成脱敏报告 |
| 本地模型安装 | `AVAILABLE` | `scripts/install_local_models.bat` | BGE Embedding、Qwen3 Reranker，支持断点续传和哈希校验 |

### 2.2 日志接入与解析

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 无后缀日志上传 | `AVAILABLE` | 上传时规范化为 `.txt`，保留原始名称 |
| Huawei collectDebuginfo 识别 | `AVAILABLE` | 内容探测并选择 Huawei Parser |
| 压缩包与文本安全检查 | `AVAILABLE` | 路径穿越、文件数、深度、单文件和展开大小限制 |
| 大日志流式解析 | `AVAILABLE` | 已覆盖 110,904 行样本规模，批量写入、稀疏行索引 |
| 日志浏览与任意行读取 | `AVAILABLE` | 支持分页、原文搜索、事件跳转源行 |
| 事件与时间线 | `AVAILABLE` | 事件分页、级别/模块聚合、时间线数据 |
| 原子解析版本 | `AVAILABLE` | 新解析失败时保留最后一次成功结果 |
| 持久化任务 | `AVAILABLE` | 支持进度、重启恢复、取消、重试和应用生命周期安全关闭 |

### 2.3 诊断、RAG 与模型

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 规则诊断 | `AVAILABLE` | 基于事件码产生事实、假设、行动建议和限制 |
| 证据约束 LLM 诊断 | `AVAILABLE` | 只允许引用已提供 evidence ID，非法输出回退到确定性结果 |
| 模型网关 | `AVAILABLE` | 前端管理并切换 Chat、Embedding、Reranker 配置 |
| 本地/API Embedding | `AVAILABLE` | 内置 Hashing、本地 Sentence Transformers、兼容 API |
| 本地/API Reranker | `AVAILABLE` | 本地 CrossEncoder、Qwen Rerank API |
| 混合检索 | `AVAILABLE` | 有界 BM25 候选、Dense top-K、加权 RRF、模块均衡和单次 Reranker |
| Qdrant 镜像 | `AVAILABLE` | 数据库向量为权威存储，按 generation 镜像并执行有界 top-K |
| 案例对话 | `AVAILABLE` | 基于当前案例、最新诊断和检索证据回答 |

### 2.4 分层知识库

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 树形分类 | `AVAILABLE` | 诊断规则、历史问题、参考资料，可增加和修改分类 |
| 文档 CRUD | `AVAILABLE` | 新增、后台上传、查看、修改、删除和启停 |
| Markdown 分块 | `AVAILABLE` | 按标题和段落切分，超长单段继续分片并限制 chunk 大小 |
| 自动向量索引 | `AVAILABLE` | 文档变更后更新活动 generation；全量重建失败保留上一版 |
| 故障案例结构化 Markdown | `AVAILABLE` | 错误形式、日志分析、错误定位、解决方案、验证结果 |
| 错误分析 Skill | `AVAILABLE` | 以 Markdown 保存可复用的错误分析技能 |
| 分析方法提炼 | `AVAILABLE` | 从故障案例或错误分析 Skill 提取输入信号、步骤、决策点和验证方法 |

### 2.5 代码与工程协同

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 代码仓归档上传 | `AVAILABLE` | 上传后返回后台任务，ZIP/TAR/Git Bundle 解压不会阻塞 API 事件循环 |
| C/C++ 符号索引 | `AVAILABLE` | 函数、宏、结构体、签名、源码范围和调用名称 |
| 静态分析 | `AVAILABLE` | cppcheck、clang-tidy，工具不可用时明确报告 |
| 补丁建议 | `AVAILABLE` | 只生成候选 unified diff，不自动覆盖代码 |
| VS Code 扩展 | `AVAILABLE` | 上传日志/工作区、关联案例、询问选中代码 |
| 代码语义图谱 | `LIMITED` | 建模四类关系；generation 旁路构建并原子切换，解析仍是多语言静态启发式 |
| Commit 意图图谱 | `LIMITED` | query → commit → changed file → 当前 HEAD 代码符号；最多索引 2,000 个 Commit |
| Git Bundle 导入 | `AVAILABLE` | 在保留完整历史的情况下安全导入仓库 |

### 2.6 安全、权限与部署

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| RBAC | `AVAILABLE` | ADMIN、ENGINEER、VIEWER 和案例成员权限 |
| 个人令牌 | `AVAILABLE` | 哈希保存、到期、撤销和最后使用时间 |
| API Key 加密 | `AVAILABLE` | 模型密钥加密保存且不通过查询接口回显 |
| 审计 | `AVAILABLE` | 身份、管理操作、模型出站元数据，避免记录正文和密钥 |
| 健康检查 | `AVAILABLE` | liveness、readiness、管理员系统状态 |
| Docker/Compose | `AVAILABLE` | PostgreSQL、Qdrant 和前后端镜像 |
| GitHub CI | `AVAILABLE` | Linux/Windows 后端、前端、扩展、依赖审计、外部服务、Docker |

## 3. 新一代认知检索能力

### 3.1 代码图谱

- 状态：`LIMITED`
- 定位：语义升维，从语法符号跨越到程序关系。
- 必须建模：
  - `CALLS`：函数/方法调用；
  - `REFERENCES`：变量、类型、宏、函数和文件内引用；
  - `INHERITS`：类或结构的继承；
  - `IMPLEMENTS`：接口实现、声明到定义或可确认的覆写实现。
- 输出：
  - 图谱节点、边和统计；
  - 关键词到起始符号的语义定位；
  - 1～3 跳邻居扩展和可解释路径；
  - 与 Commit 文件变更的连接。
- 当前边界：无编译数据库时使用安全的静态语法启发式；后续可接入
  Clang/Language Server 提升跨宏、模板和条件编译精度。

### 3.2 Commit 图谱

- 状态：`LIMITED`
- 定位：意图追溯。
- 核心路径：`用户 query → Commit 意图 → 变更文件 → 代码符号 → 代码关系`。
- 数据：
  - commit hash、父提交、作者时间、主题和正文；
  - 新增/修改/删除/重命名文件；
  - 文件到当前代码符号的关联；
  - 可解释的匹配词和多跳路径。
- 输入限制：
  - 普通 ZIP/TAR 通常不包含 `.git`，只能建立代码图谱；
  - 要建立 Commit 图谱，应上传包含 `.git` 的安全归档或项目支持的 Git Bundle。

### 3.3 记忆系统

- 状态：`AVAILABLE`
- 定位：经验复用。
- 三类记忆：
  - `EPISODIC`：某次案例、上下文、结论和结果；
  - `PROCEDURAL`：可复用分析顺序、工具使用和验证步骤；
  - `FAILURE`：失败尝试、缺失信息、错误原因和避免方式。
- 生命周期：
  1. 每轮诊断任务结束或失败时提炼；
  2. 使用证据和来源 ID 建立可审计记忆；
  3. 指纹去重，重复经验增加出现次数；
  4. 下次检索按相似度、置信度、结果和复用次数召回；
  5. 记录复用，不把“曾经成功”误判为“当前必然正确”。

### 3.4 Agentic Search

- 状态：`AVAILABLE`
- 定位：认知中枢。
- 可调度模块：
  - 分层知识库混合检索；
  - 代码图谱；
  - Commit 图谱；
  - 三类记忆。
- 检索组件：
  - BM25；
  - Dense Embedding；
  - Reranker；
  - 图关系多跳扩展；
  - 跨模块结果融合。
- 性能与一致性：
  - 知识模块在 Agentic Search 中只生成原始候选，Dense/Reranker 不重复调用；
  - 不同模块按加权 RRF 和候选配额进入统一语义排序；
  - Qdrant 只取有界 top-K；无 Qdrant 时只扫描紧凑向量行，不加载全部文档正文。
- 编排原则：
  1. 先识别 query 的日志、代码、历史、回归、处理步骤等意图；
  2. 动态选择模块，不对每次请求盲目执行所有昂贵步骤；
  3. 第一跳找候选，后续跳沿代码边或 commit/file/symbol 边扩展；
  4. 使用融合和 Reranker 统一排序；
  5. 返回执行计划、各阶段耗时/候选数、最终证据和可解释路径。

## 4. 依赖与冲突约束

| 约束 | 决策 |
| --- | --- |
| 新检索与现有 RAG 重复 | 复用同一 Embedding/Reranker Profile；现有混合检索作为 Agentic Search 的知识模块 |
| 图谱结果没有 evidence ID | 每个节点、关系、Commit、记忆都必须使用稳定数据库 ID |
| Commit 历史与普通代码压缩包冲突 | 代码图谱可独立建立；Commit 图谱明确显示 `UNAVAILABLE`，不伪造历史 |
| 记忆可能放大错误结论 | 保存置信度、结果、来源和失败类型；召回时只作证据候选 |
| 不同案例之间的数据隔离 | 代码仓、Commit 和情景记忆继承案例访问控制；全局程序记忆只保存脱敏方法 |
| 外部模型数据出站 | 沿用模型网关、端点校验和内容无关审计；无 API 时必须有本地确定性回退 |
| SQLite 与 PostgreSQL 差异 | 所有新表通过 Alembic 建立，查询不依赖数据库专有 JSON 运算 |
| 大仓库性能 | 上传与解压分离；图谱使用 generation 分批旁路构建；查询先做数据库候选过滤 |
| 源码安全 | 图谱和补丁只读；不自动修改、执行或编译上传代码 |
| 归档安全 | 保持路径穿越、文件数、深度和大小限制；Git 导入禁用交互凭据和 hooks |
| 功能入口冲突 | “知识库”管理事实和方法；“认知检索”负责跨知识/代码/Commit/记忆查询 |
| 删除一致性 | 案例、仓库、文档删除时派生图谱、Commit、记忆和索引必须级联或解除关联 |

## 5. 本轮验收清单

- [x] 故障案例 Markdown 模板及四个必需章节检查；
- [x] 上传/编辑故障案例后保存结构化字段；
- [x] 从故障案例或错误分析 Skill 一键提炼分析方法；
- [x] 代码关系表、稳定 evidence ID、图谱构建、图谱查询和多跳路径；
- [x] Commit 历史、父子关系、文件变更及 query → commit → code 路径；
- [x] EPISODIC / PROCEDURAL / FAILURE 记忆生成、去重、查询和复用计数；
- [x] Agentic Search 动态计划、BM25、Dense、Reranker、图扩展和 RRF 融合；
- [x] 前端知识库入口和独立“认知检索”页面；
- [x] Alembic `0008` generation/报告唯一约束迁移、幂等升级和 SQLite 兼容测试；
- [x] RBAC、案例隔离、删除级联、备份恢复和审计回归；
- [x] 更新 README、架构文档、API 使用说明和本总账；
- [x] 后端、前端、扩展、依赖审计、Win11 启动闭环和真实页面验证。

本轮本地验证基线（2026-07-29）：

- 后端：Ruff、compileall、`102 passed, 1 skipped`；跳过项是需要外部
  PostgreSQL/Qdrant 的服务集成测试；
- 前端：干净 `npm ci` 后通过 `vue-tsc` 和 Vite production build；
- VS Code 扩展：干净 `npm ci` 后通过 TypeScript 编译，Archiver 8 依赖链审计为 0；
- 安全审计：pip-audit、前端 npm audit、扩展 npm audit 均无已知漏洞；
- Windows 运行：隔离 SQLite、独立端口的前后端/API 闭环通过；
- 页面：故障案例模板、认知检索计划/结果、任务记忆、模型与索引状态均已实测，
  浏览器控制台无错误。

外部 PostgreSQL/Qdrant 和 Linux/Windows 矩阵由每次 push 的 GitHub Actions
继续验证；本机未安装 Docker，因此不把未执行的外部服务项伪装成本地通过。

## 6. 后续候选

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Clang AST/compile_commands 深度图谱 | `PLANNED` | 提升 C/C++ 宏、模板、重载和条件编译解析精度 |
| 增量代码/Commit 索引 | `PLANNED` | 按 commit 增量更新，避免全仓重建 |
| 图数据库后端 | `PLANNED` | 数据规模达到阈值后评估 Neo4j/AGE；当前关系表保持可迁移 |
| 记忆衰减与人工审核 | `PLANNED` | 过期、冲突记忆提示和人工批准 |
| 检索评测集 | `PLANNED` | 建立 query、期望证据、Recall/MRR/NDCG 和多跳正确率基线 |
| 分布式任务租约 | `PLANNED` | 多实例部署时引入集中队列、心跳、可见性超时和死信 |
| 知识版本审核 | `PLANNED` | DRAFT/REVIEWED/ACTIVE 生命周期、差异、回滚和乐观锁 |
