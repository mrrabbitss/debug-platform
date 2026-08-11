# GW/AP Debug Platform 功能、需求与兼容性总账

> 本文件是项目功能范围的唯一总账（Single Source of Truth）。
> 新需求、在研能力、已交付能力、约束和冲突处理都必须同步更新本文件，防止跨迭代遗忘或重复建设。

最后更新：2026-08-11

工程执行、验证、评测和 Agent 护栏的状态与优先级单独维护在
[Harness Engineering 路线与状态总账](HARNESS_ENGINEERING.md)。本文件只判断业务能力是否
可用；两份总账在每次相关迭代中必须同步。

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
| 统一仓库验证 | `AVAILABLE` | `scripts/validate_all.bat` | Fast/Full/External 三档，保存 JSON 摘要和逐步日志 |
| 仓库 Harness 契约 | `AVAILABLE` | `scripts/check_repo_harness.py` | 检查文档、CI、依赖治理和 Agent API 合同漂移 |
| SQLite 备份恢复 | `AVAILABLE` | `scripts/backup_local.bat`、`restore_local.bat` | 带清单、哈希校验和回滚保留 |
| 本地模型网络检测 | `AVAILABLE` | `scripts/check_hf_model_access.bat` | 检查镜像、CLI 和 curl 回退并生成脱敏报告 |
| 本地模型安装 | `AVAILABLE` | `scripts/install_local_models.bat` | BGE Embedding、Qwen3 Reranker，支持断点续传和哈希校验 |
| 私网模型端点显式启用 | `AVAILABLE` | `scripts/enable_private_model_endpoints.bat` | 主动运行一次后幂等写入本机 Git 忽略的 `.env`，后续启动持续生效且不输出密钥 |

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
| 持久化任务 | `AVAILABLE` | 幂等键、原子领取、lease/heartbeat、超时、指数退避、dead-letter、取消、资源预算和多实例安全领取 |

### 2.3 诊断、RAG 与模型

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 规则诊断 | `AVAILABLE` | 基于事件码产生事实、假设、行动建议和限制 |
| 证据约束 LLM 诊断 | `AVAILABLE` | 只允许引用已提供 evidence ID，非法输出回退到确定性结果 |
| 模型网关 | `AVAILABLE` | 前端管理并切换 Chat、Embedding、Reranker 配置；Chat API 支持逐 Profile 加密代理，空值直连，代理启用时保留证书链/主机名校验并跳过吊销检查 |
| 本地/API Embedding | `AVAILABLE` | 内置 Hashing、本地 Sentence Transformers、兼容 API |
| 本地/API Reranker | `AVAILABLE` | 本地 CrossEncoder、Qwen Rerank API |
| 混合检索 | `AVAILABLE` | 有界 BM25 候选、Dense top-K、加权 RRF、模块均衡和单次 Reranker |
| Qdrant 镜像 | `AVAILABLE` | 数据库向量为权威存储，按 generation 镜像并执行有界 top-K |
| 检索评测 | `AVAILABLE` | 固定 query/预期证据/根因和模块，输出 Recall@K、Precision@K、MRR、NDCG@K、Root Cause Top-K |
| 案例对话 | `AVAILABLE` | 基于当前案例、最新诊断和检索证据回答 |

### 2.4 分层知识库

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 树形分类 | `AVAILABLE` | 诊断规则、历史问题、参考资料，可增加和修改分类 |
| 文档 CRUD | `AVAILABLE` | 新增、后台上传、查看、修改和删除；发布/归档必须走审核状态机 |
| 知识设备适用范围 | `AVAILABLE` | 支持 GW、AP、通用和其他；通用知识可被 GW/AP 检索，旧 `OTHER` 数据保持兼容 |
| Markdown 分块 | `AVAILABLE` | 按标题和段落切分，超长单段继续分片并限制 chunk 大小 |
| 自动向量索引 | `AVAILABLE` | 文档变更后更新活动 generation；全量重建失败保留上一版 |
| 故障案例结构化 Markdown | `AVAILABLE` | 错误形式、日志分析、错误定位、解决方案、验证结果 |
| 大模型文件夹案例提炼 | `AVAILABLE` | 文本、HTML、DOCX 和文本层 PDF 在本地提取；脱敏限长后生成带行号引用的 Markdown，支持多轮对话、人工编辑、版本恢复和确认后入草稿 |
| 错误分析 Skill | `AVAILABLE` | 以 Markdown 保存可复用的错误分析技能 |
| 分析方法提炼 | `AVAILABLE` | 从故障案例或错误分析 Skill 提取输入信号、步骤、决策点和验证方法 |
| 知识版本与审核 | `AVAILABLE` | DRAFT/IN_REVIEW/ACTIVE/REJECTED/ARCHIVED、不可变快照、回滚和数据库乐观锁 |
| 人工反馈闭环 | `AVAILABLE` | 诊断反馈先审核，再生成待二次审核的知识草稿；不会自动写入记忆或线上知识 |
| 领域知识图谱 | `LIMITED` | 从已发布知识确定性提取症状/根因/方案等实体关系，generation 原子切换；同义词与冲突消解仍有限 |

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
| GitHub CI | `AVAILABLE` | Linux/Windows 后端、前端、扩展、依赖审计、外部服务、Docker；每个 job 有硬超时 |
| Golden 质量门禁 | `AVAILABLE` | 日志、提炼、Code/Commit Graph、Memory、RAG、Agentic Search 与有界执行九类固定评测 |
| 浏览器 E2E | `AVAILABLE` | Playwright + Fake OpenAI 服务固化混合文档上传、预览、生成、纠错、确认 DRAFT 和轨迹查看，控制台错误即失败 |
| Agent 运行轨迹 | `AVAILABLE` | 记录摘要哈希、模型/Prompt、tokens、耗时、重试、证据、停止和审批；管理员可查看并安全只读重放 |
| 覆盖率与架构门禁 | `AVAILABLE` | 后端 75% 行覆盖率、文件行数/圈复杂度 ratchet、导入边界、运行时 OpenAPI 合同漂移检查 |

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
  - 领域知识图谱 / GraphRAG；
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
- 有界执行基础：类型化 Tool Registry、角色白名单、写操作审批、步骤/跳数/tokens/成本/
  墙钟预算、重试熔断取消和确定性回退已经可用并进入 Golden 门禁；生产检索仍默认使用
  当前确定性 Planner，不把测试中的循环执行器描述成已默认接管线上查询。

### 3.5 领域知识图谱与 GraphRAG

- 状态：`LIMITED`
- 数据源：只使用 `ACTIVE` 且 `active=true` 的已审核知识；
- 实体：症状、设备、型号、固件、模块、日志模式、事件码、根因、诊断步骤、方案、验证和范围；
- 关系：症状、日志、事件码、根因、定位步骤、解决、验证和适用范围；
- 一致性：新 generation 旁路构建，发布前校验知识输入签名并 CAS 切换；
- 失败语义：构建失败或构建期间知识变化时保留上一活动 generation；
- 当前限制：确定性 Markdown/元数据抽取，不等同于完成实体消歧的企业级知识图谱。

### 3.6 质量治理与反馈

- 状态：`AVAILABLE`
- 知识生命周期：草稿、待审核、已发布、已驳回、已归档；
- 编辑和历史恢复均创建新草稿版本，不覆盖不可变历史；
- `lock_version` 同时由前端预期值和数据库 UPDATE 条件校验；
- 检索评测保存模型/算法快照并强制关闭记忆写入；
- 人工反馈必须经过“反馈审核 → 生成知识草稿 → 知识审核”两道门。

## 4. 依赖与冲突约束

| 约束 | 决策 |
| --- | --- |
| 新检索与现有 RAG 重复 | 复用同一 Embedding/Reranker Profile；现有混合检索作为 Agentic Search 的知识模块 |
| 图谱结果没有 evidence ID | 每个节点、关系、Commit、记忆都必须使用稳定数据库 ID |
| Commit 历史与普通代码压缩包冲突 | 代码图谱可独立建立；Commit 图谱明确显示 `UNAVAILABLE`，不伪造历史 |
| 记忆可能放大错误结论 | 保存置信度、结果、来源和失败类型；召回时只作证据候选 |
| 未审核知识污染检索 | 新增、导入、编辑和回滚均为草稿；查询同时校验 `active` 与 `review_status=ACTIVE` |
| 图谱构建期间知识变化 | 发布前重算输入签名并校验构建标记；变化时丢弃新 generation，保留旧版 |
| 评测污染在线记忆 | 评测固定 `record_memory=false`，不新增、不强化、不增加复用次数 |
| 人工反馈自动学习敏感内容 | 反馈审核通过后也只能生成知识草稿，仍需第二次审核发布 |
| 模型生成案例污染知识库 | 提炼会话与知识文档隔离；章节和来源行号校验通过并人工确认后才创建 `DRAFT`，仍需原有审核发布 |
| 文件夹原文直接外发 | 原始文件只进本地 Storage；DOCX/PDF/HTML 也先在本地提取，管理员明确授权后仅发送脱敏、限长、带行号的文本证据 |
| 多窗口同时纠错覆盖内容 | 每轮携带 `expected_draft_version`，条件更新失败返回冲突；所有人工/模型修改保存不可变版本 |
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
- [x] 知识不可变版本、审核状态机、数据库乐观锁和新草稿回滚；
- [x] 已审核知识实体/关系图谱、构建期输入校验、原子 generation 和 GraphRAG；
- [x] 检索评测数据集、后台运行、配置快照和五项基础指标；
- [x] 人工诊断反馈审核，以及“通过反馈 → 待二次审核知识草稿”的安全闭环；
- [x] 前端知识库入口和独立“认知检索”页面；
- [x] 前端“质量与治理”页面，以及知识审核和版本历史操作；
- [x] Alembic `0009` 知识治理/领域图谱/评测迁移、幂等升级和 SQLite 兼容测试；
- [x] RBAC、案例隔离、删除级联、备份恢复和审计回归；
- [x] 更新 README、架构文档、API 使用说明和本总账；
- [x] 后端、前端、扩展、依赖审计、Win11 启动闭环和真实页面验证。

本轮本地验证基线（2026-07-30）：

- 后端：Ruff、compileall、`107 passed, 1 skipped`；跳过项是需要外部
  PostgreSQL/Qdrant 的服务集成测试；
- 前端：干净 `npm ci` 后通过 `vue-tsc` 和 Vite production build；
- VS Code 扩展：干净 `npm ci` 后通过 TypeScript 编译，Archiver 8 依赖链审计为 0；
- 安全审计：pip-audit、前端 npm audit、扩展 npm audit 均无已知漏洞；
- Windows 运行：隔离 SQLite、独立端口的前后端/API 闭环通过；
- 页面：知识草稿→待审核→发布→版本历史、领域图谱原子重建/检索、评测集、
  人工反馈、认知检索和模型状态均已实测，浏览器控制台无错误。

外部 PostgreSQL/Qdrant 和 Linux/Windows 矩阵由每次 push 的 GitHub Actions
继续验证；本机未安装 Docker，因此不把未执行的外部服务项伪装成本地通过。

本轮依赖可复现修复（2026-08-05）：

- [x] 将存在已知漏洞的 `cryptography 49.0.0` 升级并锁定为 `50.0.0`；
- [x] 增加跨 Python/Windows/Linux 的 `backend/uv.lock`，并导出兼容现有 pip
  启动链的 `backend/constraints.lock`；
- [x] Win11 bootstrap/start/doctor、Linux 启动、本地模型安装、CI 和 Docker
  统一服从锁定约束；
- [x] 增加 `scripts\refresh_python_lock.bat` 的更新与只读检查模式；
- [x] 修复新进入审计库的前端 PostCSS 和扩展 brace-expansion 告警；
- [x] Python 3.14 安装、锁文件一致性、Ruff、compileall、`119 passed, 1 skipped`、
  前端构建、扩展编译、三类依赖审计、Win11 bootstrap/doctor/runtime smoke 均通过。

## 6. 后续候选

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Clang AST/compile_commands 深度图谱 | `PLANNED` | 提升 C/C++ 宏、模板、重载和条件编译解析精度 |
| 增量代码/Commit 索引 | `PLANNED` | 按 commit 增量更新，避免全仓重建 |
| 图数据库后端 | `PLANNED` | 数据规模达到阈值后评估 Neo4j/AGE；当前关系表保持可迁移 |
| 记忆衰减与人工审核 | `PLANNED` | 过期、冲突记忆提示和人工批准 |
| 知识差异与实体合并 UI | `PLANNED` | 可视化版本 diff、同义实体合并、冲突关系审核 |
| 独立知识审核角色 | `PLANNED` | 从 ADMIN 中拆出知识维护者和审核者，支持职责分离 |
| OpenTelemetry 桥接 | `PLANNED` | 将现有结构化 Agent 轨迹导出到可选本地观测栈 |
| Agent 任务控制平面 | `PLANNED` | 跨任务依赖 DAG、人工审批队列和通用停滞检测 |
| Review 反馈沉淀 | `PLANNED` | 把人工 Review 安全分类为规则、测试、文档或评测样本，写入前必须审批 |

## 7. 大模型文件夹案例提炼迭代（2026-08-03）

定位：把已有故障材料转换成“可复核、可纠错、可追溯、不会自动发布”的知识案例，
不是让大模型直接向线上知识库写入自由文本。

- [x] 浏览器选择整个文件夹，保留相对路径并流式保存到本地 Storage；
- [x] 文件数、单文件/总大小、路径穿越、Windows 保留名和目录深度限制；
- [x] 文本及无后缀文本探测，HTML 可见正文、DOCX 段落/表格和 PDF 文本层本地提取；
- [x] 旧式 DOC、扫描 PDF、空文件和其他二进制保留并明确标记原因；
- [x] 长文件头部、关键词行和尾部抽样，全局提示长度限制与敏感信息掩码；
- [x] OpenAI-Compatible Chat 模型选择、模型快照和显式数据出站授权；
- [x] 强制结构化案例章节与 `[SRC-xxxx:Lx-Ly]` 来源/行号校验；
- [x] 模型对话纠错、人工 Markdown 修改、乐观并发冲突和不可变版本恢复；
- [x] 人工确认后只创建 `DRAFT + active=false` 知识文档，继续复用审核发布流程；
- [x] `0010` 会话/来源/版本/消息迁移，来源文件随备份保存；
- [x] 管理员 API、前端“AI 案例提炼”工作台和完整使用文档；
- [x] 完整后端回归、前端构建、Win11 运行冒烟和真实浏览器流程验证。

本功能本地验证基线（2026-08-03）：Ruff、compileall、`pip check`、`119 passed,
1 skipped`、Vue TypeScript/Vite production build、VS Code 扩展编译和隔离 Win11
运行冒烟均通过；真实浏览器已完成 TXT、MD、HTML、DOCX、PDF 混合文件夹上传、三种文档
正文预览、模型初稿、对话修订及“确认后只创建知识草稿”的完整流程，控制台无错误。

当前边界：Word 支持 `.docx`，旧式 `.doc` 需转换；PDF 只支持已有文本层，扫描件和图片
尚无 OCR；HTML 提取结构化可见文本但不执行浏览器脚本或远程资源。案例生成只支持 API
Chat 模型，本地 BGE Embedding 和 Qwen3 Reranker 不具备生成能力，本地 Chat 运行器仍是
后续候选。详见 [大模型文件夹案例提炼与人工校正](docs/llm-knowledge-curation.md)。

## 8. Harness Engineering P0（2026-08-05）

- [x] 根目录 Agent 项目地图、业务/工程总账和专题文档索引；
- [x] `Fast`、`Full`、`External` 统一 Win11 验证入口及可移植 JSON/日志产物；
- [x] 文档链接、总账交叉引用、CI 触发、Dependabot 覆盖和 Workflow API 的自动契约检查；
- [x] 分支/PR CI 去重，同时保留面向 `main` 的 PR、`main` push 和手工触发；
- [x] `.gitattributes`、PR 模板、CODEOWNERS 和 Python/npm/Actions/Docker Dependabot；
- [x] Workflow allowlist 补齐日志上传/解析/报告和 AI 案例提炼 API，并声明证据与人工 DRAFT 门禁；
- [x] GitHub `main` ruleset；本次 PR 的 9 项新 CI 全绿后启用，必须 PR、最新 CI，
  并禁止删除和 force push；单维护者场景不强制他人批准。

本轮本地 `Full` 验证：15 个步骤全部通过，包含 Harness 11 项契约、`122 passed,
1 skipped`、前端生产构建、扩展编译、三类依赖审计、Doctor 和隔离运行冒烟。
本机未安装 Docker，`External` 中的 PostgreSQL/Qdrant 与镜像构建继续由 GitHub CI 验证。

P1 Golden Dataset、Fake Model、Playwright E2E、质量评测、执行轨迹和架构门禁，以及已完成的
P2 有界 Agent、任务隔离、多实例 lease 和文档进程沙箱见
[HARNESS_ENGINEERING.md](HARNESS_ENGINEERING.md)。剩余任务 DAG 和 Review 自动沉淀仍明确标记为
`PLANNED`，不得描述为已可用。

## 9. Harness Engineering P1 与 P2 基础（2026-08-05）

- [x] 合成 TXT/HTML/DOCX/PDF 与无后缀日志 Golden Corpus，固定二进制哈希；
- [x] Fake OpenAI-compatible Chat/Embedding/Reranker，含超时、429、坏 JSON 和中断注入；
- [x] Playwright 完整提炼闭环与浏览器控制台零错误门禁；
- [x] 九类 Golden 评测和耗时/证据/停止原因 CI 阻断；
- [x] 后端 75% 覆盖率门禁；建立门禁时完整回归实测 77%；
- [x] `AgentRun + AgentTraceEvent`、前端轨迹查看器和不写记忆的脱敏只读重放；
- [x] API、知识提炼、Agentic Search 和前端大文件模块化，并用行数/复杂度/导入边界防回退；
- [x] Workflow 最小契约与 FastAPI 运行时 OpenAPI 双重漂移检查；
- [x] 类型化有界 Agent、角色/审批、预算、重试/熔断/取消和确定性回退；
- [x] 独立 task worktree/端口/数据库/Storage/日志；
- [x] 后台任务 lease/heartbeat/dead-letter/幂等/资源预算；
- [x] Win11 Job Object 与 Linux rlimit 约束的不可信文档独立进程抽取。

详细命令、阈值和隐私边界见
[质量评测、Agent 轨迹与有界执行](docs/quality-harness-and-agent-runtime.md)。
