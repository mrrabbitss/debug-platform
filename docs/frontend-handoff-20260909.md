# 0.4.0 前端交接（2026-09-09）

状态：本分工的前端实现、生产构建、新增模拟浏览器检查及合成截图已完成。
源码仍在共享工作区，未提交、未推送；既有其他代理改动和无关文件均保留。
本记录不代表后端、真实模型、安装包或 CI 的交付状态。

## 已完成行为

- 主导航仅“故障定位 / 知识库 / 系统设置”；管理页面保留受保护的直接路由和旧路径别名。
  工程师、只读账号直达管理路径时返回个人设置，权限加载失败时关闭管理入口。
- 案例创建保留组网、连接、未知类别；继承个人预设 Chat 模型；新案例授权默认 true。
  历史 false 记录加载和修改问题资料时不会被自动改写。清空案例模型会明确发送 null，恢复系统默认。
- 案例详情 682 行（低于 800 行预算）：概览、日志与筛查、综合诊断、交互问答、补充资料、诊断报告。
  移除事件/时间线 UI 和对应前端列表请求；保留后端数据、日志证据定位和报告时间轨迹。
  成员管理、诊断规划、诊断/问答/筛查技术轨迹默认折叠；保留报告预览、导出和问答修订功能。
- 案例负责人或管理员在已完成诊断后提交案例与报告，绑定 case_id 和 analysis_id。
  EDITOR、SHARED、VIEWER 不显示负责人提交按钮；待审记录可在知识库查看。
- 知识按类别下的 log_analysis / diagnosis / fault_tree / report_template / prior_knowledge 浏览。
  工程师读取已发布 Skill/Markdown、提交已定位案例；助手、案例提炼、知识维护、审核与模板选择仅管理员可见。
- 管理员在抽屉内核对案例及附带报告后审核；确认/退回提交当前记录 version。
  已发布自定义报告格式可通过 PUT `/workbench/templates/{category_id}` 提交 `{document_id, version}`。
  内置模板显示服务端返回的 v2，不把内置模板 ID 当作可写库文档。
- 系统设置保留个人模型偏好；模型与索引、安全与审计、技术管理入口仅向管理员展示。
- 工作台统一侧栏、标题、卡片、表格、错误/空状态、焦点样式与窄屏布局。

## 知识助手契约与交互

已与助手代理核对写入后的 `backend/app/api/knowledge_assistant.py`，前端使用实际接口：

- POST `/workbench/assistant`：multipart `files`、`paths`、`message`、`model_egress_approved`、`mode`。
  `webkitRelativePath` 原样保存在 paths；扩展名集合与当前后端一致。
  客户端检查 64 文件、单文件 1 MiB、目录 8 MiB、重复路径；不静默忽略文件或截断正文。
- GET 会话列表/详情：保存的会话可重开；运行会话轮询；切换会话或离开页面使旧响应失效。
- POST `/{id}/messages`：`version`、`message`、`mode`、`model_egress_approved`。
  允许运行中发送新要求，实际取消与重新规划由后端执行；未发送的输入阻止发布按钮。
- POST `/{id}/pause`、`/{id}/cancel`、`/{id}/retry`：均使用当前会话 `version`。
  发布失败返回 REVIEW 后需要重新核对确认；前端不会调用阅读 retry 代替发布确认。
- PATCH `/{id}/consent`：`version` 与布尔授权。新会话默认 true；显式关闭可保存 PAUSED 会话。
  重新开启仅恢复授权，用户点击继续整理才调用 retry。
- 显示每份来源/目标的完整阅读账本、文件依赖、顺序操作、来源路径和章节范围、分类/用途、理由、
  目标当前版本和锁版本、逐行差异、修改前/后全文。GET `/{id}/source?path=...&cursor=...` 提供分页原文预览。
- POST `/{id}/confirm`：`version` 加服务端返回的可选 `review_digest`。
  一个确认按钮即审批；不弹出第二次确认，不在超时或 409 后自动重放审批。
  来源或目标未读完、没有变更、仍有未发送纠偏、授权关闭时不可确认。
  409 刷新目标版本和方案，并明确提示重新核对；索引成功后显示 PUBLISHED。

## 文件交接

本轮直接新增/补齐：

- `frontend/src/components/WorkbenchShell.vue`
- `frontend/src/style.css`
- `frontend/src/views/CaseDetailView.vue`
- `frontend/src/views/KnowledgeWorkbenchView.vue`
- `frontend/src/components/diagnosis/CaseOptionsPanel.vue`
- `frontend/src/components/diagnosis/CaseChatPanel.vue`
- `frontend/src/components/diagnosis/LogTriagePanel.vue`
- `frontend/src/components/knowledge/LibrarySubmissionDialog.vue`
- `frontend/src/components/knowledge/KnowledgeAssistantPane.vue`
- `frontend/src/components/knowledge/AssistantSourceDialog.vue`
- `frontend/src/composables/useKnowledgeAssistant.ts`
- `frontend/src/types/workbench.ts`
- `frontend/src/components.d.ts`（构建器更新声明）
- `frontend/e2e/workbench.spec.ts`
- `frontend/e2e/workbench.config.spec.ts`
- `docs/frontend-handoff-20260909.md`

保留并纳入本次验证的先前部分实现：`App.vue`、`router/index.ts`、`types/index.ts`、
`views/CasesView.vue`、`views/WorkbenchSettingsView.vue`、`composables/useWorkbench.ts`。
未修改包依赖、锁文件、共享真相文档或后端源码。

## 验证结果

在 `D:\GRXM\debugplatform\frontend` 执行：

```powershell
npm run build
npx playwright test --config e2e/workbench.config.spec.ts
```

- 最终生产构建于 2026-09-09 01:17（Asia/Shanghai）完成：vue-tsc + Vite 成功，1741 modules。
  Vite 构建部分 4.74 秒，完整 npm 命令约 9.70 秒。
  仅有上游 VueUse PURE 注释警告。较早一次 Vite 写 `components.d.ts` 遇 Windows UNKNOWN 文件错误；
  未改变权限或结束其他代理进程，随后重试成功，最终构建未出现该错误。
- 最终新增浏览器检查 **9 passed / 0 failed / 0 skipped，15.7 秒**。
  JUnit：`artifacts/validation/workbench-20260909/frontend-e2e.xml`。
- 九项检查：工程师导航/预设/管理路由拒绝；VIEWER 限制；类别与模型创建、合成日志解析、问答/报告、
  负责人完成报告提交；历史授权及 EDITOR 限制/清空模型；目录相对路径、纠偏、精确单次审批及重开；
  暂停/取消/继续/授权；不完整阅读与版本冲突；管理员报告审核与版本化默认模板；390px 窄屏无横向溢出。
- 首轮测试发现 Element Plus 隐藏输入定位方式与 Playwright service-worker 拦截对 sandbox 报告 iframe
  的干扰，修正测试 harness 后通过；未弱化产品报告 iframe 的 sandbox。
- 浏览器运行真实 `frontend/dist`，所有 `/api/v1/**` 都由测试路由合成响应；未启动业务后端或假模型服务器。
  未命中真实业务数据、模型端点或密钥；每个场景断言无未预期 API 调用及无页面 JS 异常。
- `git diff --check -- frontend` 通过；只出现仓库既定 LF/CRLF 提示。
- 没有运行历史回归、Fast/Full/External、后端回归、手动 CI 或打包步骤。

最终 `frontend/dist/index.html` SHA-256：

```text
7B9AE75A8E633BCEBB01314E6D8FC80FF74942617BE28C6C981EDE2C4981ECA9
```

源码基线 HEAD 仍为 `46c3b1f47529ab17095d5e4e01ef8bb6ea59b671`；本地未提交改动是本次构建来源。
主线可使用该完整 dist 目录打包；不得只复制 index.html 而遗漏当前哈希命名 assets。

## 合成截图与后续边界

已保存并逐张视觉核对，窗口宽度 1440px：

- `artifacts/validation/workbench-20260909/case-home.png`
- `artifacts/validation/workbench-20260909/engineer-settings.png`
- `artifacts/validation/workbench-20260909/admin-assistant.png`
- `artifacts/validation/workbench-20260909/admin-assistant-diff.png`

截图与 JUnit 属运行产物，不纳入源码提交。目录上传截图使用短期合成 fixture，该 fixture 已删除。
模拟检查证明 UI 和请求契约，不能替代后端权限、真实持久化/中断恢复、索引原子性、真实模型或多机验收。
真实后端联调、总账/文档索引同步、仓库 harness、安装包与最终发布门禁由主线及对应代理汇总。
本分工没有运行 `check_repo_harness.py`，避免与主线共享文档/契约收敛并行；主线需将本交接加入文档索引。
