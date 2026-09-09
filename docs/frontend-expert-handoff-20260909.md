# 专家迭代前端交接（2026-09-09）

状态：本模块实现和新增定向浏览器检查完成，交接主线集成。修改仅在 `frontend/` 和本文件；未提交、未调整依赖或锁文件，未修改后端、共享文档或 `AI_PRACTICE_COMMUNITY_POST.md`。

## 已实现页面

- 普通用户保留故障定位、知识库、系统设置三个入口；ADMIN / EXPERT 直接增加知识库管理。路由与页面均检查角色，越权深链返回设置。专家可查看审计，不显示或请求用户管理、全局 Embedding / Reranker / GGUF 配置。管理员用户角色编辑新增专家。
- 知识库包含知识 Wiki、案例与报告、只读诊断 Skill、我的提交。普通用户可上传 Markdown、编辑/删除自己的草稿，针对共享知识提出修改/删除，针对 Skill 提交修改建议；文件名为 SKILL.md 也不会赋予 Skill 类型或管理权限。
- AI 案例提炼对普通用户开放，默认允许模型处理，默认使用个人统一模型来源。确认提炼只产生普通贡献草稿，页面随后提交审核，跳转到我的提交查看状态。
- 独立知识库管理包含 Skill 与故障类别、AI 整理助手、审批中心、知识维护。现有目录导入、完整阅读/差异确认、Markdown 编辑器、历史版本、质量治理、索引和高级维护保留可用。
- Skill 新增/修改/删除通过精确贡献版本核对，专家/管理员可直接批准发布。编辑保留完整包元数据、类别、用途、相对文件路径。类别新增/改名/停用同步进入创建案例选项；general / unknown 的停用按钮禁用。
- 审批详情包含原稿、候选、差异、完整属性、版本历史、审核意见和 AI 多轮修正；保存或 AI 修改后清除旧勾选，批准绑定当前 version + content_hash。409 不自动重放；未保存内容或未发送纠偏要求阻止批准。
- 新案例提交由服务器同事务保存案例/报告与审核贡献，响应带 contribution_id 时直接完成。兼容旧响应时再调用幂等 `/from-library/{id}`；接入失败保留记录 ID，重试不重复创建。历史未接入记录仍可由管理页处理。案例库优先显示 `reviewed_conclusion`，保留可切换的提交原稿和原始 `report_markdown`。
- 所有人使用个人 Chat 模型配置与分组选择；ENGINEER 创建强制 PRIVATE，ADMIN / EXPERT 默认 SHARED、可选 PRIVATE。共享默认与个人偏好分开保存。只有 `can_manage` 的模型显示编辑/删除，只有管理角色的共享模型显示设为共享默认。API Key 不预填、不显示共享凭据，不要求端点白名单。保留代理、推理和上下文参数。
- 新诊断统一显示个人设置解析出的模型，不再提供会被后端忽略的案例级选择。问题资料保存仅 PATCH 类别和描述，保留旧案例的 model ID 与关闭的出站授权。个人选择失效时显式警告，不显示虚假的共享回退。
- 创建案例及案例资料显示类别 `skill_status` 提示。综合诊断顶部直接显示 `diagnostic_planning.method_coverage.skill_status.warning`，覆盖通用回退、无任何 Skill、未知跨类；不依赖展开技术详情。没有额外暴露 `model_reading` 复杂统计。
- 整理助手支持 PUBLISH_FAILED 中文状态和同审批重试，APPROVED / BUILDING 持续查询，PUBLISHED 为最终状态。清单和差异详情显示 `content_kind` 对应的“诊断 Skill / 普通知识”；历史缺失类型显示待核对，不自行猜测。
- 知识维护的可选重置动作展示清理范围、源 hash、完整文件清单、适配差异；用户确认精确 preview 后才提交备份/重置，页面不接收任意 ZIP 路径。浏览器保留操作编号，可重新打开读取持久状态，不自动再次批准。
- 旧维护页 DELETE 已适配 `publication_pending`：进入审批详情查看发布进度，不提前提示知识已删除。
- Element Plus 弹窗统一中文；窄屏导航换行，管理页保留表格内部滚动。

## 已对齐接口

省略共同前缀 `/api/v1`。无已知待接通的核心前端 API。

| 功能 | 实际使用的接口与约束 |
| --- | --- |
| 配置/角色 | GET `/workbench/bootstrap`；类别 version/active/skill_status、可见 models、preferences、model_selection；既有 `/system/me` / users / audit |
| 模型 | GET/POST `/system/models`；PATCH/DELETE `/{id}`；POST `/{id}/test`、`/{id}/activate`；PUT `/workbench/preferences` `{chat_profile_id:null|string}` |
| 故障类别 | POST `/workbench/categories` `{name}`；PATCH `/{id}` `{name,version}`；DELETE `/{id}` body `{version}`；非空拒绝消息直接呈现 |
| 已发布知识 | GET `/workbench/knowledge`，使用权威 content_kind；单 Skill 编辑前 GET `/knowledge/{id}` 保留元数据 |
| 普通知识提交 | GET/POST `/knowledge-contributions`；GET/PATCH `/{id}`；DELETE `/{id}` query `expected_version`；POST `/{id}/submit` |
| 审核 | PATCH `/{id}/review-draft`；POST `/{id}/review-chat`；POST `/{id}/review` `{expected_version,expected_content_hash,action,comment}`；失败恢复 POST `/jobs/{publication_job_id}/retry` 后 GET 贡献 |
| 案例/提炼 | 既有 `/workbench/library`、`/knowledge-curations`；POST `/knowledge-contributions/from-library/{record_id}`；curation confirm 响应中的 contribution 随后 submit |
| 整理助手 | 保留 `/workbench/assistant` 原有目录/对话/确认流程；PUBLISH_FAILED 使用 `/{id}/retry` `{version}`，不调用 confirm；content_kind 与后台 review_digest 一致展示 |
| 重置 | POST `/workbench/knowledge-reset/preview` `{operation_id,data_root}`；POST `/confirm` `{operation_id,data_root,expected_source_sha256,expected_preview_hash,confirmed:true,model_egress_approved:true}`；GET `/{operation_id}` |
| 旧知识维护删除 | DELETE `/knowledge/{id}` 若返回 publication_pending + contribution，打开管理页 `?tab=review&contribution={id}`，继续同一持久发布任务 |

前端 Markdown 上传使用 UTF-8 解码后 POST 贡献（500 KB 界面限制），不依赖可选 multipart `/knowledge-contributions/upload`。
旧 VIEWER 维持后端约定：可选/测试可见模型，不能创建模型、案例或知识；普通贡献角色为 ENGINEER。

## 新增检查和构建证据

测试文件：`frontend/e2e/expert-iteration.spec.ts`；隔离配置：`frontend/e2e/expert-iteration.config.ts`。
运行真实生产前端，由 Playwright Mock `/api/v1/**`；全部使用合成身份/材料，不连接真实模型，不读写业务数据库。每项断言没有意外 API 请求和页面 JavaScript 错误。

| 批次 | 结果 | 覆盖 |
| --- | --- | --- |
| 初始新定向批次 | 14 passed，19.5s | 三/四栏角色入口、专家审计无用户/GGUF请求、管理员授予专家、私人 HTTP API/共享来源、Wiki/Skill 边界、草稿 CRUD、共享提议、多轮审核/hash/409、动态类别、Skill 包元数据保留、同审批重试、普通 AI 提炼、390px 窄屏 |
| 收尾新增批次 `--grep 'final integration'` | 9 passed，9.5s | 三种诊断顶部 warning、旧案例设置保留、类别改名/停用版本、重置精确确认/重新打开恢复、案例接入失败重试、审核内容/原稿切换、失效模型提示、助手类型标识/未发送纠偏阻止批准 |
| 新维护删除检查 `--grep 'legacy maintenance deletion'` | 1 passed，3.0s | 发布中删除不虚报完成，直接打开持久贡献详情，旧知识保留 |

合计 24 项新增检查均有通过证据。后一批只运行新增目标，没有重跑已通过的历史或完整套件。

`npm run build` 在主要页面与助手类型标识完成后通过（Vite 1759 modules，5.69s，包含 vue-tsc）。最后维护删除修正时，Windows 自动组件声明写入发生一次 UNKNOWN 临时错误，导致声明短暂缺少 Element Plus 条目；通过原生成器 `npx vite build` 恢复。最终源代码分别通过 `npx vite build`（1759 modules，5.50s）和 `npx vue-tsc -b`（退出码 0，5.40s）。没有手改生成声明、升级依赖或修改环境权限。剩余构建提示仅依赖既有 VueUse PURE 注解警告。

本地证据均在前端忽略目录：

- `frontend/node_modules/.cache/expert-iteration-20260909/frontend-e2e.xml`
- `frontend/node_modules/.cache/expert-iteration-20260909/final-integration/frontend-e2e.xml`
- `frontend/node_modules/.cache/expert-iteration-20260909/maintenance-delete/frontend-e2e.xml`
- `frontend/node_modules/.cache/expert-iteration-20260909/expert-mobile.png`（已人工查看，无页面横向溢出）

前端范围 `git diff --check` 通过。未运行历史回归、Full、手动 CI；后端真实 HTTP 集成、启动 smoke、真实 ZIP 导入、运行数据库断电恢复和仓库全局护栏由主线验证，本交接不把模拟浏览器结果宣称为这些环境的验收。

## 主要修改路径

- 页面/路由：`frontend/src/router/index.ts`、`components/WorkbenchShell.vue`、`views/KnowledgeManagementView.vue`、`KnowledgeWorkbenchView.vue`、`WorkbenchSettingsView.vue`、`KnowledgeCurationView.vue`、`KnowledgeView.vue`、`CasesView.vue`、`CaseDetailView.vue`、`SecurityView.vue`、`SettingsView.vue`。
- 新组件：`components/ChatModelsPane.vue`、`ChatModelSelect.vue`、`knowledge/SkillManagementPane.vue`、`KnowledgeContributionsPane.vue`、`ContributionDetail.vue`、`KnowledgeResetPane.vue`、`diagnosis/SkillStatusNotice.vue`。
- 现有组件适配：`knowledge/KnowledgeAssistantPane.vue`、`LibrarySubmissionDialog.vue`、`diagnosis/CaseOptionsPanel.vue`、`KnowledgeDraftActions.vue`。
- 契约/样式：`api/knowledgeContributions.ts`、`api/knowledgeCuration.ts`、`composables/useWorkbench.ts`、`types/index.ts`、`types/workbench.ts`、`style.css`、`App.vue`，及构建生成的 `components.d.ts`。
- 上述两个新 E2E 文件和本交接文档。

主线已接管收尾：新增同事务案例提交的浏览器检查1项通过、旧桥接失败重试的受影响检查1项通过；
再补齐旧候选经验面板的EXPERT角色检查，新增1个浏览器场景通过；合计26个不同新场景。
证据 `frontend/node_modules/.cache/expert-iteration-20260909/atomic-library/frontend-e2e.xml`
及 `expert-memory/frontend-e2e.xml`（同一父目录）。
此次改变后 `npm run build`（含 vue-tsc，1759模块）再次通过。
