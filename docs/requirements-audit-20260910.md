# 最近需求核对与模型任务进度（2026-09-10）

核对基线为分支
`codex/expert-knowledge-iteration` 的 `v0.5.1` 交付源码；不把
`CAPABILITIES.md` 的声明当作实现证据，而是逐项读取 HTTP 权限、服务层、前端入口和
持久化路径。

本次没有运行回归、真实第三方 API 或 Codex CLI 模型测试。既有真实模型结论沿用
[`expert-live-model-validation-20260909.md`](expert-live-model-validation-20260909.md)：
CLI terra 的合成完整诊断与报告通过；第三方 API 的整理、提炼、审核、问答和日志规划通过，
但综合诊断两次分别因上游 500 和 Skill 阅读结构不合格中止。因此不能把该第三方 API
标记为“完整诊断已验收”。

## 结论与优先级

| 优先级 | 结论 | 处理建议 |
| --- | --- | --- |
| 本轮补齐 | 各 Chat 模型流程原先缺少一致的当前阶段与估算剩余比例。 | 新增统一阶段/估算进度、持久计数、真实模型等待标记和可查询任务；见“进度专项”。 |
| 本轮补齐 | 常用 Chat API 新增窗口仍显示 `300` 秒默认值。 | 改为显示真实默认 900 秒，编辑旧 300 也显示 900，自定义值保留，网页上限 7200 秒。 |
| 本轮补齐 | 候选代码补丁仍用旧同步入口，未接入发起人的统一模型选择与持续进度。 | 新服务固定个人模型/权限/代码代，增加后台任务；仅展示与复制候选，不修改源码。 |
| 已确认 | 独立“事件与时间线”案例页签已删除。 | 当前默认折叠的 PlanningTracePanel 是工具与证据核验记录，不是被删除的日志事件页；本轮保留它，报告的设备时间轨迹也保留。 |
| 交付边界 | 已安装服务器升级保留已有知识库；空库才自动导入六文件包。 | 管理员可走已有的重置预览与一次确认，清空旧知识和 Skill 并保留案例报告；升级不会自行覆盖后来已审批的修改。 |
| 维护说明 | 旧白名单配置字段、旧知识源文件仍保留兼容，但运行路径不使用。 | 本轮显式标注旧配置不参与端点校验；不把未加载的资源误算为现行知识。 |

## 已确认需求逐项核对

| 已确认需求 | 结论 | 代码证据 | 验收边界或遗漏 |
| --- | --- | --- | --- |
| 左侧普通用户只保留故障定位、知识库、系统设置；专家/管理员增加独立知识库管理。 | 已实现。 | [`WorkbenchShell.vue`](../frontend/src/components/WorkbenchShell.vue#L18) 第 19–22 行固定三栏，并仅在 `canManageKnowledge` 时添加“知识库管理”；[`router/index.ts`](../frontend/src/router/index.ts#L13) 第 13 行对此页要求知识管理角色。 | 需在实际部署浏览器复查视觉层级；已有代码与角色门禁一致。 |
| 专家由管理员在安全与审计中调整角色；专家不能管理其他用户、不能改全局 GGUF/Embedding/Reranker，可看审计、管理知识/共享 Chat、重建索引。 | 已实现。 | [`SecurityView.vue`](../frontend/src/views/SecurityView.vue#L45) 第 45–63 行仅 ADMIN 读取用户管理和系统状态、EXPERT 只读审计；第 307、370 行提供 EXPERT 角色。[`model_access.py`](../backend/app/services/model_access.py#L72) 第 72–80 行规定 E/R 只允许 ADMIN，Chat 共享允许 ADMIN/EXPERT。 | 需保留角色 API 的定向权限测试；不应以 UI 隐藏代替服务端校验。 |
| 任意用户可添加诊断 Chat API；任意 HTTP(S) Base URL 无白名单和私网开关；普通用户私有，专家/管理员默认共享但可私有，密钥不泄露。 | 已实现，协议范围有限。 | [`ChatModelsPane.vue`](../frontend/src/components/ChatModelsPane.vue#L71) 第 71–98 行提供全员新增、管理角色共享/私有选择和 Key 隐藏文案；[`model_access.py`](../backend/app/services/model_access.py#L41) 第 41–55 行按共享或所有者过滤，私有模型对其他人返回不可发现；[`model_profiles.py`](../backend/app/services/model_profiles.py#L42) 第 42–63 行只校验 HTTP(S) URL 语法，不读白名单。 | “任意 Base URL”是任意 HTTP(S) 地址，仍要求 OpenAI-compatible Chat 协议，见 `ChatModelsPane.vue` 第 35 行和第 89 行；非兼容厂商协议未实现适配。`core/config.py` 仍保留未使用的白名单配置字段，属 P2 维护残留。 |
| 每人选择统一模型，个人选择优先，未选时使用共享默认；用于新诊断、问答、AI 提炼、审核/整理，任务启动后固定快照，失效不静默切换。 | 已实现。 | [`WorkbenchSettingsView.vue`](../frontend/src/views/WorkbenchSettingsView.vue#L29) 第 29–30 行保存 `chat_profile_id`；第 42–48 行说明和渲染个人选择/个人 API。[`model_access.py`](../backend/app/services/model_access.py#L127) 第 127–147 行先选个人偏好、后退共享默认；第 190–216 行固定并校验任务快照。[`workbench.py`](../backend/app/services/workbench.py#L155) 第 155–196 行把模型、知识、类别、模板共同固化到运行上下文。 | 第三方真实 API 的综合诊断仍未验收通过，不能将“统一选择”外推为模型结果质量保证。 |
| 普通用户可上传案例/Wiki、管理自己的普通草稿，提出共享知识或 Skill 修改/删除；普通用户 Skill 只读，专家/管理员可直接增删改。 | 已实现。 | [`KnowledgeWorkbenchView.vue`](../frontend/src/views/KnowledgeWorkbenchView.vue#L37) 第 37–46 行给普通用户 Wiki、案例/报告、只读 Skill 和提交入口；[`knowledge_contributions.py`](../backend/app/services/knowledge_contributions.py#L107) 第 107–146 行创建贡献并保存原稿/候选稿；[`knowledge_access.py`](../backend/app/services/knowledge_access.py#L21) 第 21–33 行区分管理者和投稿者。[`SkillManagementPane.vue`](../frontend/src/components/knowledge/SkillManagementPane.vue#L1) 的直接 Skill 编辑组件只由知识管理页面加载。 | 需用 ENGINEER 实机账号复查不能绕过贡献 API 直接写 `/knowledge`；路由和服务端门禁均已存在。 |
| 专家/管理员审批案例结论、普通知识和 Skill 修改；可多轮 AI 修正，保存原稿、差异、审核记录，确认后直接发布。 | 已实现。 | [`knowledge_contribution_review.py`](../backend/app/services/knowledge_contribution_review.py#L16) 第 16–92 行将多轮对话、原稿、候选稿和模型快照一起校验后保存；[`knowledge_contributions.py`](../backend/app/services/knowledge_contributions.py#L70) 第 70–103 行输出原稿、差异、修订与消息；第 199–245 行用精确版本/哈希审批并同事务创建发布 Job。[`knowledge_contribution_publication.py`](../backend/app/services/knowledge_contribution_publication.py#L270) 第 270–298 行保留失败审批以便重试。 | 审核 AI 的真实第三方 API 验证通过；模型修改仍必须由专家/管理员最终批准。 |
| 新增/改名/停用故障类别后，创建案例立即可选；按类别使用专属+通用 Skill，未知跨类并说明原因；没有专属则用通用，没有任何 Skill 则仅日志证据并醒目标识。 | 已实现，真实质量有限。 | [`problem_categories.py`](../backend/app/services/problem_categories.py#L55) 的持久类别实现由 [`workbench.py`](../backend/app/services/workbench.py#L35) 第 35–50 行读取和校验；[`CaseOptionsPanel.vue`](../frontend/src/components/diagnosis/CaseOptionsPanel.vue#L31) 第 31–35 行动态渲染类别与 Skill 状态。[`workbench.py`](../backend/app/services/workbench.py#L236) 第 236–263 行实现 unknown 跨类建议、通用匹配；[`SkillStatusNotice.vue`](../frontend/src/components/diagnosis/SkillStatusNotice.vue) 和 `knowledge_methods.py` 负责状态提示与证据模式。 | 第三方 API 综合诊断在 Skill 阅读阶段失败，说明分类/回退机制的控制流已验收，实际模型对业务 Skill 的语义质量仍待公司环境验收。 |
| 报告按问题大类模板；默认组网模板，模板与知识在任务开始时固定。 | 已实现。 | [`workbench.py`](../backend/app/services/workbench.py#L266) 第 266–284 行先取类别模板、再退回 network、最后使用内置组网模板；[`report_contract.py`](../backend/app/services/report_contract.py#L24) 第 24–65 行固定模板摘要并把要求传给报告生成；[`knowledge_reset.py`](../backend/app/services/knowledge_reset.py#L41) 第 41–45 行把 ZIP 的 `report-format.md` 映射为 `report_template`。 | API 综合诊断/报告未完成真实验收；CLI terra 合成报告通过。 |
| 新案例、知识助手、AI 案例提炼默认开启模型出站；历史关闭记录不自动改写。 | 已实现。 | [`schemas.py`](../backend/app/schemas.py#L30) 第 30–41 行、[`models.py`](../backend/app/models.py#L54) 和 [`CasesView.vue`](../frontend/src/views/CasesView.vue#L13) 第 13–14 行均默认 `true`；[`KnowledgeCurationView.vue`](../frontend/src/views/KnowledgeCurationView.vue#L64) 第 64–75 行和第 156–169 行默认 `consent_model_egress=true`；[`knowledge_curation.py`](../backend/app/api/knowledge_curation.py#L114) 第 114–169 行服务端默认也为 true。 | 旧案例若已关闭仍会在模型调用前被拒绝，符合“历史关闭不自动修改”。 |
| 完整 Skill 文件夹先让 AI 全文识别用途、归位、合并/替换建议和依赖，人工纠偏/一次确认后原子发布。 | 已实现，输入上限明确。 | [`KnowledgeAssistantPane.vue`](../frontend/src/components/knowledge/KnowledgeAssistantPane.vue#L35) 第 35–38 行支持 `webkitdirectory`、64 文件/单文件 1 MiB/总计 8 MiB；第 55–69 行展示全文覆盖、依赖、差异和一次确认。[`assistant_controller.py`](../backend/app/services/assistant_controller.py) 与 [`assistant_publication.py`](../backend/app/services/assistant_publication.py) 负责持久会话和原子发布。 | 超过 64 文件、单文件 1 MiB 或合计 8 MiB 的完整方向不能直接导入；这是已声明的处理上限，不是静默截断。 |
| 已审批修改在服务异常/重启后仍有效；索引、图谱、文档不暴露半代。 | 已实现，有合成恢复证据。 | [`knowledge_contributions.py`](../backend/app/services/knowledge_contributions.py#L237) 第 237–245 行同事务写审批和持久 Job；[`knowledge_contribution_publication.py`](../backend/app/services/knowledge_contribution_publication.py#L176) 第 176–298 行构建私有代、失败保留旧代和审批。[`workflow_recovery.py`](../backend/app/services/workflow_recovery.py) 接入恢复钩子。 | 已有合成进程中断/重启检查；未覆盖真实断电、远端 PostgreSQL/Qdrant。 |
| 清空旧知识与 Skill，保留案例和报告；导入用户提供的 `hilink-diag.zip` 六文件，并作为服务器包默认知识。 | 新库安装已实现；已有库自动升级存在限制。 | [`knowledge_reset.py`](../backend/app/services/knowledge_reset.py#L254) 第 254–271 行的预览明确保留 cases/reports/users/models/audit，六文件角色映射在第 41–45 行。[`bundled_knowledge.py`](../backend/app/services/bundled_knowledge.py#L29) 第 29–45 行校验包内 ZIP，0.5.1 包内验证记录见 [`installer-skill-admin-fix-20260909.md`](installer-skill-admin-fix-20260909.md)。 | `initialize_packaged_knowledge` 第 57–84 行仅对完全空的知识库导入；已有 0.3.x/0.4.x 数据库被标为 `PRESERVED`，不会自动清空。因此升级机器要使用知识管理中的重置预览/确认，而不是期待安装覆盖自动迁移。 |
| 处理中“事件与时间线”删除，保留报告设备轨迹和问答。 | 已实现独立页签删除。 | [`CaseDetailView.vue`](../frontend/src/views/CaseDetailView.vue) 的页签没有事件时间线；[`PlanningTracePanel.vue`](../frontend/src/components/diagnosis/PlanningTracePanel.vue) 只在默认折叠区域保留模型执行依据。 | 报告内设备轨迹属于报告契约；技术核验记录不代替本轮新增的当前进度面板。 |
| Chat 请求和网页/CLI 等待应适配公司慢模型。 | 本轮补齐常用表单默认值。 | [`core/timeouts.py`](../backend/app/core/timeouts.py) 使用 900 秒并兼容遗留 300 秒；[`ChatModelsPane.vue`](../frontend/src/components/ChatModelsPane.vue) 新建及编辑旧默认均显示 900 秒。 | 自定义值保留；不重复已完成的真实 API/CLI 验证。 |

## 进度专项

用户新要求是所有涉及诊断 Chat 模型的页面清楚显示“正在第几步、已完成多少、估算还剩多少”。
以下列出 v0.5.1 的显示基线和本轮对应改进：

| 流程 | 当前展示 | 证据 | 本轮目标 |
| --- | --- | --- | --- |
| 日志解析/综合诊断 | 一个原始 Job 名称、message 和裸百分比。 | [`CaseDetailView.vue`](../frontend/src/views/CaseDetailView.vue#L486) 第 486–493 行。 | 显示例如“读取 Skill 3/6、等待模型规划、验证报告”等阶段；百分比来自可计数里程碑，模型等待明确为估算。 |
| 案例问答 | Job 百分比，无阶段/剩余说明。 | [`CaseChatPanel.vue`](../frontend/src/components/diagnosis/CaseChatPanel.vue) 第 178–180 行。 | 统一使用阶段化组件，避免把未知模型推理伪装成精确进度。 |
| 知识整理助手 | 有覆盖文件数和裸进度，但 `COMPLETED` 可表示一轮阅读完成而非整个会话完成。 | [`KnowledgeAssistantPane.vue`](../frontend/src/components/knowledge/KnowledgeAssistantPane.vue#L51) 第 51–56 行。 | 同时显示会话状态、阅读文件/分段计数、当前阶段和下一步；暂停等待人工确认时不显示 100%。 |
| AI 案例提炼 | 上传有进度条，模型提炼阶段只有笼统提示和自动刷新。 | [`KnowledgeCurationView.vue`](../frontend/src/views/KnowledgeCurationView.vue#L432) 第 432–439 行、628 行。 | 增加提取、证据整理、调用模型、结构验证、草稿保存等阶段。 |
| 审批 AI 多轮修正、模型连通性测试、Markdown 归类 | 同步请求主要靠按钮 loading，无法看出模型等待处于何阶段。 | [`knowledge_contribution_review.py`](../backend/app/services/knowledge_contribution_review.py#L46) 第 46–92 行是同步模型调用；[`ChatModelsPane.vue`](../frontend/src/components/ChatModelsPane.vue#L48) 第 48–65 行为同步测试。 | 用适合短流程的阶段状态（准备、请求模型、校验、保存/完成）；不伪造 token 级百分比。 |

进度百分比必须是阶段权重和已知文件/批次计数的估算，不能根据等待时长不断增长，也不能在模型尚未响应时显示 99%。失败、取消、暂停、等待人工确认和模型配置失效应独立说明，不和“还剩百分之几”混在一起。

## 完成判定与验收边界

1. 以阶段进度组件替换原始 Job 条；独立事件页已删除，默认折叠的证据执行记录继续保留。
2. 统一诊断、问答、日志筛查、知识整理、案例提炼、审批 AI 修正、归类和模型测试的阶段命名与状态语义。
3. 修正 Chat 新建表单显示的默认超时为 900 秒。
4. 在服务器升级指南补充：已有知识库不会自动清空，管理员需审阅重置预览后执行六文件替换；保留案例和报告。
5. 对第三方 API 再次验收综合诊断与报告之前，继续将其标为 PARTIAL，而不是将 CLI 合成成功代替 API 验收。

## 本轮实现契约

统一 [ModelTaskProgress](../frontend/src/components/common/ModelTaskProgress.vue) 覆盖综合诊断、问答/修订、日志规划、
提炼及后续对话、整理助手、Markdown 批次归类、审核 AI 修正、候选补丁和两处 Chat 连通性测试。
综合诊断按收集日志、检索知识、完整阅读 Skill、多轮推理与核验、综合结论、校验保存六步显示。
读取按已确认字符/分段推进，推理按实际轮数和已核对故障树项展示；轮数上限不是预计工作总量。
未知工作量保持当前里程碑，模型等待期间也显示约剩余工作比例，但不预测剩余时间。

[JobContext](../backend/app/services/jobs.py) 将结构化进度保存在新增 `jobs.progress_json`（迁移 0025），
公开 `JobOut.progress_detail`。心跳只说明任务仍存活，不增加百分比；同一尝试进度不倒退，保存结果前不显示 100%。
字段不含正文、提示词、模型输出、端点或密钥。失败、取消、暂停、等待人工核对与已发布各有独立状态。
“本轮整理已完成、待人工核对”不代表知识已发布，暂停的 Job 即使已结束也不等于会话完成。

| 新 REST 提交入口 | Job kind | 完成结果 |
| --- | --- | --- |
| `/knowledge-contributions/{id}/review-chat-jobs` | `refine_knowledge_contribution` | 重新读取投稿、原稿和修订差异 |
| `/knowledge-curations/{id}/chat-jobs` | `refine_knowledge_curation` | 重新读取提炼会话 |
| `/system/models/{id}/test-jobs` | `test_chat_model_connection` | `result_json.test`，仅 `ok: true` 算测试成功 |
| `/cases/{id}/patch-suggestion-jobs` | `patch_suggestion` | `result_json.patch`，仅展示与复制 |

上述任务单次尝试、最长 7200 秒，可取消；不自动重复耗用模型。旧同步接口保留，REST 新入口不增加 MCP/CLI 工具权限。
审核/提炼稿与完成标记同事务保存，取消/失租不能发布半份结果。互动任务仅发起人且仍有资源权限时可读取，案例补丁遵循案例权限。
常用模型表单超时遗漏与候选补丁的个人模型选择已修正。验证数量、浏览器与交付状态见 [VALIDATION.md](../VALIDATION.md) 的本轮条目。
刷新恢复的任务书签仅保存 Job ID，并按当前用户和资源隔离。多轮提炼持续跟踪本轮任务，等待时禁止重复发送；
完成响应携带更新草稿版本时，页面先读取该版本再解除等待，避免保留旧稿。时间显示兼容数据库 UTC 时间戳。
本轮没有重复既有成功回归或调用真实第三方/CLI 模型；历史 API 完整诊断仍为 PARTIAL。
