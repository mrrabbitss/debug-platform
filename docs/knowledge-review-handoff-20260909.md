# 知识贡献与审核模块交接（2026-09-09）

状态：本模块实现与新增定向验证完成，0024 表结构定稿，可供主线迁移和集成。
主线负责注册路由、中央权限、模型解析器、案例库桥接及共享文档同步；本代理未操作运行数据库、业务 ZIP、提交或推送。
主线已反馈实际 0016→0024 迁移和 6 文件重置完成，历史案例/分析/附件保持；本代理不再改动迁移。
最终收尾完成；主线仅需把下述 `recover_abandoned_contributions(db)` 接入自有 `workflow_recovery.py`。

## 确定接口契约

以下路径均省略平台统一前缀 `/api/v1`。

- 新路由模块 `app.api.knowledge_contributions.router`，前缀 `/knowledge-contributions`。
- 文档类型采用受控的 `metadata.content_kind = KNOWLEDGE | SKILL`；统一通过
  `app.services.knowledge_access.knowledge_kind(document)` 读取（兼容旧 Skill 来源元数据）。
  文件名本身不赋予 Skill 管理权。新普通文档强制写入 KNOWLEDGE；Skill 贡献只是待审建议。
- 贡献与版本模型放在独立 `app.knowledge_contribution_models`；迁移 `0024`，前驱 `0023`。
- 主线需在模型元数据加载入口导入上述模型模块，并注册新 router。
- 知识管理域门禁 `require_knowledge_admin` 接受 ADMIN / EXPERT。普通用户的草稿及投稿走新 API；
  既有 `/knowledge` 写入、Skill 版本/恢复等接口继续仅管理角色使用。
- `/knowledge-curations` 为 ADMIN / EXPERT / ENGINEER 开放，未提交会话、来源和任务仅所有者可访问。
  管理角色可查看已提交来源，但不能借用他人的私有模型修改原提炼会话；审核 AI 使用审核者自己的模型选择。
  创建时默认 `consent_model_egress=true`、`confidentiality=INTERNAL`，旧会话显式关闭/限制记录不自动修改。

### 新贡献 API

| 方法和路径 | 输入与用途 |
| --- | --- |
| GET `/knowledge-contributions` | `status?`, `content_kind?`, `mine?`, `limit?`；普通用户仅自己的，管理角色可查看审核队列 |
| POST `/knowledge-contributions` | `{operation: CREATE/UPDATE/DELETE, content_kind: KNOWLEDGE/SKILL, target_document_id?, title?, content?, source_type?, category_id?, metadata?, source_curation_id?}`；只创建草稿 |
| GET `/{id}` | 返回完整贡献、原稿、候选、差异、历史和对话 |
| POST `/upload` | multipart `file`, `category_id?`；UTF-8 Markdown，至多 1 MB，始终创建普通 KNOWLEDGE 草稿 |
| POST `/from-library/{record_id}` | 把已提交、待审的案例结论接入统一队列；仅提交人或管理角色可用，重复调用返回原贡献 |
| PATCH `/{id}` | `{expected_version, title?, content?, category_id?, metadata?, confidentiality?, comment?}`；所有者修改未审批草稿 |
| DELETE `/{id}` | `expected_version` 查询参数；所有者删除未提交草稿，已审批记录保留 |
| POST `/{id}/submit` | `{expected_version}`；冻结投稿原稿，转 SUBMITTED |
| PATCH `/{id}/review-draft` | `{expected_version, title?, content?, category_id?, metadata?, confidentiality?, comment?}`；管理角色修正待审稿，保留版本差异 |
| POST `/{id}/review-chat` | `{expected_version, instruction, model_profile_id?, consent_model_egress?}`；管理角色 AI 修正待审稿，保存对话和版本 |
| POST `/{id}/review` | `{expected_version, expected_content_hash, action: APPROVE/REJECT/RETURN, comment?}`；批准绑定当前精确候选并入持久发布队列 |

贡献响应包含 `id, owner_id, operation, content_kind, target_document_id, status, version,
content_hash, candidate, original, diff, publication_job_id, published_document_id, created_at, updated_at`。
详情增加 `revisions, messages`。候选为标准知识快照 `{title,content,source_type,category_id,metadata,...}`。
状态包括 DRAFT / SUBMITTED / RETURNED / REJECTED / APPROVED / PUBLISHING / PUBLISHED / FAILED；
删除草稿保留内部 DELETED 墓碑与审计，不再返回列表。confidentiality 为 PUBLIC / INTERNAL / RESTRICTED。
重复批准同一版本与 hash 返回原任务；修改后必须针对新 hash 审批。CREATE / UPDATE / DELETE 均通过原子发布层生效。

### 现有提炼和案例库

`POST /knowledge-curations/{id}/confirm` 继续返回 `session` 与 `knowledge_document`，额外返回
`contribution`（新普通贡献草稿）；不能把确认提炼当成发布。来源与版本持续关联保留。
案例/报告库负责人提交保持原流程，审核角色扩展为 ADMIN / EXPERT。
主线已在 `/workbench/library` 创建并 flush 记录后调用
`app.services.workbench_library.conclusion_contribution(db, identity, row.id)`，再与提交记录统一 commit，响应包含 contribution_id。
请勿在该调用后修改原 library payload/version，否则已固定的来源版本会冲突。
尚未桥接的旧待审记录可通过 `/from-library/{record_id}` 进入队列。
审核发布生成 `reviewed_conclusion`，保留原 `report_markdown` 和 AnalysisRun；不直接改写历史报告。
已经桥接的案例拒绝旧 boolean review 接口，须针对贡献的精确 version/hash 审批。

模型解析器位于 `app.services.model_access`：`resolve_user_chat_profile(db, principal, profile_id=None)`；提炼与审核 AI 使用该解析器，
后台以会话创建者检查固定模型的访问权；其他用户不能使用所有者的 PRIVATE profile。
提炼与审核 AI 使用 `chat_model_snapshot` / `resolve_chat_model_snapshot` 校验配置和凭据变更；不会偷偷换用另一配置。
审核请求调用前固定快照，响应后重验账号、模型启用状态、访问权及配置指纹，失效结果不修改候选或对话。

### Markdown 归类消费者最终接入

- 已接管并修改 `api/knowledge_routing.py` 身份/快照参数，原专家门禁保留。
- `resolve_routing_model(db, model_profile_id, principal)` 使用个人选择优先、共享默认兜底的统一解析器。
  ADMIN / EXPERT 不能借 `profile_id` 使用其他人的私有模型。返回的模型快照保存在 artifact 元数据 `model_snapshot`。
- 新 import 默认 `consent_model_egress=True`；显式 False 和既存关闭记录保持关闭。归类仍只产生 DRAFT。
- 平台任务只使用入队时的模型和指纹；分页调用前后及写入归类结果前重新校验。
  修改个人偏好不会更换已排队任务的模型；模型配置改变/停用则停止，丢弃未确认结果。
  历史未保存有效快照的待处理平台任务需重新导入，不按当前全局配置重新选择。
- `host_cli` 导入与 MCP 外部模型归类流程不调用服务器 Chat；只提供本地 Markdown 和分类上下文。

### 既有管理 API 与后台任务集成

- `knowledge_access.require_knowledge_admin` / `can_publish` 支持 ADMIN、EXPERT。
- `knowledge_access.authorize_routing_job(db, job_id, principal, method=...)`：主线的中央权限和 `/jobs` API
  已传入真实请求方法并分别经过 HTTP 定向验证；普通用户可查看自己的发布任务，取消/重试发布任务仅管理角色；自己的提炼任务可读写。
  管理任务集合已包含 `publish_knowledge_contribution`、`knowledge_reset`。
- `/knowledge` 全部写操作增加接口域门禁，避免绕开中央中间件直接写 Skill。
- `DELETE /knowledge/{id}` 对已发布文档创建并批准 DELETE 贡献，返回
  `{document_id, publication_pending:true, contribution, job}`（HTTP 200）。前端应显示发布进度，不能提前显示已生效。
  非活动文档归档并保留历史，不再连带物理删除分块和派生文档。批准中的预留文档须通过对应审核任务控制。
- 新 helper：`app.services.knowledge_publication.enqueue_publication(db, document, draft, reviewer)`。
  在原 draft APPROVE / intake attest 接口先设置 draft 的 IN_REVIEW 和审核意见，再调用 helper，最后统一 `db.commit()`。
  helper 不自行 commit 或启动线程；审批摘要、任务引用与 AuditEvent 一起提交，现有 dispatcher 自动发现任务。
  主线已将两个 API 接入 helper，统一 commit 后才调度，保留返回 `JobOut`。
  helper 产生的旧版发布任务可在精确快照和审核者身份仍有效时恢复，不重复审批。
  新旧发布及提炼直接构造的 Job 显式设 `max_attempts=3, timeout_seconds=1800`。
- 新恢复 hook：`app.services.knowledge_contribution_publication.recover_abandoned_contributions(db) -> int`。
  在 dispatcher 将过期任务归并后调用，不自行 commit。仅清理 FAILED / CANCELLED / DEAD_LETTER
  对应 PUBLISHING 投稿的 worker/token 和其自身构建代；投稿恢复 APPROVED，精确审批及旧活动索引不变。
  QUEUED / RUNNING 保留给同审批接管；SQL 条件更新防止误释放其他发布的 generation。重复调用无副作用。
  主线已接入 `workflow_recovery.py`；实际 dispatcher 的新增定向检查通过。

## 发布与保留边界

- 新贡献使用独立持久任务 `publish_knowledge_contribution`，复用现有分块、Embedding、私有图谱构建及任务租约设施。
- 原稿、每次候选、修改差异、对话、审核者与具体审批 hash 保留；普通草稿不进入在线索引。
- CREATE / UPDATE / DELETE 的文档状态、版本、向量代、图谱代、publication manifest 与任务完成在同一次事务中切换。
- 构建中断保留旧代和审批记录。相同审批的重试或过期租约接管可继续；旧工作线程不能发布新工作线程的结果。
- 模型结果需通过结构/引用与并发版本校验，模型不能批准自己的修改；审核过程中账号降权也会阻止持久化新修改。
- 迁移 0024 只创建贡献及历史表，不重写旧知识；清理、实际导入和备份由主线/重置模块负责。
- 未完成的私有构建代继续保留，未新增垃圾回收。真实公司模型生成质量、实机部署及系统级断电验收不在本模块结果内。

## 验证

全部在临时 SQLite 数据库、合成用户和合成材料上运行；API 检查使用真实 FastAPI 路由与受控身份依赖，
中央 Jobs 门禁和 API Jobs 门禁已各自独立使用 HTTP 路由验证；生产认证链及前端集成由主线继续验证。没有发送实际公司资料。

- `tests/test_knowledge_contributions_iteration.py`：24 项通过（25.12s），覆盖所有权、专家门禁、Skill 文件名、
  准确审批及幂等、原子增改删、失败保留旧代、租约接管、审核者降权、提炼隔离/默认授权及模型隔离、
  AI 多轮版本和并发冲突、案例原报告保留。
- `tests/test_knowledge_review_boundaries.py`：13 项新增检查分批通过，覆盖 0023→0024 升级/重复执行、
  非法空值、并发审批事务回滚、旧知识读取入口的私有草稿泄漏、AI 无效引用和中途降权、
  旧发布入口同事务入队/回滚/恢复、管理删除入队、失败状态筛选。
- 前阶段合计 37 项新增检查已分批通过，结果复用；没有再次运行这些整套检查。
- `tests/test_knowledge_review_integration.py`：11 项新增通过，另 2 项审核 AI 受影响复核通过，批次 13/13（17.55s）。
  中央/API 两层分别隔离测试 owner 不能 POST cancel/retry、expert 可以；两个旧审批 API 的提交前异常全部回滚，
  提交后调度中断仍可独立连接读到精确审批和 3 次重试的持久任务；模型配置改变/停用不写入审核候选。
- `tests/test_contribution_terminal_recovery.py`：4 项通过，覆盖 CANCELLED / DEAD_LETTER 的归并、调用方回滚、
  重复恢复、保留旧双索引和审批、QUEUED 后同审批接管、不释放别人的 generation。
- `tests/test_knowledge_routing_model_iteration.py`：10/10 通过（12.04s），覆盖管理员/专家个人模型与排队绑定、
  他人私有模型拒绝、调用前/中途配置变更和停用、默认及历史授权、host_cli 零服务器 Chat。
  初次运行的调度 stub 参数不匹配已修正；无遗留失败。
- 更新旧 `tests/test_knowledge_routing.py` 的单个平台归类用例，为新身份/快照契约补合成账号模型，
  并只复核该受影响用例：1/1 通过（3.79s）。未运行旧归类套件。
  合计 62 项本轮新增检查分批通过；最新收尾新增 25 项，另 3 项受影响复核。未运行 Full 或手动 CI。
- 所有本模块修改 Python 文件 Ruff 通过；已消除 curation 文件长度与 publication 函数复杂度超限。
  提炼服务 865 行，纯提示词独立于 `knowledge_curation_prompts.py`。
- 最后一次 `scripts/check_repo_harness.py`：24/24 PASS，主线已修复文档索引与 diagnosis 复杂度。

执行解释器为仓库 `.venv/Scripts/python.exe`，测试目标限上述新增文件/受影响测试。

## 修改路径

- 新增：`backend/app/knowledge_contribution_models.py`、`knowledge_contribution_schemas.py`、
  `backend/app/api/knowledge_contributions.py`、`backend/app/migrations/versions/0024_knowledge_contributions.py`。
- 新增服务：`knowledge_contributions.py`、`knowledge_contribution_review.py`、
  `knowledge_contribution_publication.py`、`knowledge_curation_prompts.py`。
- 修改：`backend/app/api/knowledge.py`、`knowledge_curation.py`、`knowledge_routing.py`（主线最终转交）；
  `backend/app/services/knowledge_access.py`、`knowledge_curation.py`、`knowledge_curation_serialization.py`、
  `knowledge_drafts.py`、`knowledge_publication.py`、`knowledge_routing.py`、`workbench_library.py`。
- 新增上述五个定向测试文件、更新旧归类用例的合成模型夹具及本交接文档；
  未修改共享 models/schemas、中央权限、路由注册、assistant 文件或前端。
