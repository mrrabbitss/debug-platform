# 项目文档索引

业务能力总账见 [CAPABILITIES.md](../CAPABILITIES.md)，Harness Engineering 状态与有序路线见
[HARNESS_ENGINEERING.md](../HARNESS_ENGINEERING.md)，最新定向及历史完整验证证据见
[VALIDATION.md](../VALIDATION.md)。

## 架构与演进

- [公司安装日志：新目录发布被拒绝](installer-publish-access-20260910.md)：0.5.5 原子改名有限重试、原始错误摘要与验证边界。

- [旧库升级后的内置 Skill 导入](bundled-skill-upgrade-import-20260910.md)：PRESERVED 原因、六文件预览与保留旧知识的原子导入。

- [0.5.3 安装失败与目录切换修复](installer-upgrade-fix-20260910.md)
- [最近需求核对与模型任务进度](requirements-audit-20260910.md)
- [0.5.1 内置组网 Skill 与管理员恢复修复](installer-skill-admin-fix-20260909.md)
- [管理员登录与本机恢复](admin-recovery-20260909.md)
- [0.5.0 慢模型等待与交付记录](slow-model-timeouts-20260909.md)
- [专家迭代真实 API 与 Codex CLI 验证](expert-live-model-validation-20260909.md)
- [真实 Codex CLI 诊断与专家归类证据](expert-cli-live-handoff-20260909.md)
- [专家角色、模型共享与知识协作迭代](expert-knowledge-iteration-20260909.md)
- [个人及共享 Chat 模型实现与验证](model-sharing-handoff-20260909.md)
- [知识贡献与审核实现](knowledge-review-handoff-20260909.md)
- [专家迭代前端实现与验证](frontend-expert-handoff-20260909.md)
- [知识重置、六文件导入及恢复](knowledge-reset-handoff-20260909.md)

- [三栏工作台改版与接续记录](workbench-iteration-20260908.md)
- [0.4.0 权限实现与定向验证](security-handoff-20260909.md)
- [0.4.0 前端实现与浏览器验证](frontend-handoff-20260909.md)
- [0.4.0 报告模板、导出与定向验证](report-handoff-20260909.md)
- [0.4.0 知识助手、原子发布与恢复验证](assistant-handoff-20260909.md)
- [0.4.0 服务器与分机交付](../deploy/releases/20260909/0.4.0/README.md)

- [服务器离线安装准备](仓库部署准备.md)
- [0.3.3 内网识别码登录配套包](../deploy/releases/20260908/0.3.3/README.md)
- [2026-09-08 服务器离线安装包](../deploy/releases/20260908/README.md)
- [2026-09-07 分机交付文件](../deploy/releases/20260907/README.md)
- [服务器使用指南（离线安装）](服务器使用指南.md)
- [分机使用指南](分机使用指南.md)
- [本地多客户端 CLI 推理、中断恢复与持久化实测](local-multiclient-cli-validation.md)
- [2026-09-07 试点交接与验收范围](pilot-handoff-20260907.md)
- [局域网与知识演进 M0–M5 实施记录](lan-knowledge-iteration.md)
- [Win11 局域网服务器先导运行说明](../deploy/windows-server/README.md)
- [Win11 无后端轻客户端安装与使用](../deploy/windows-client/README.md)
- [项目结构、技术栈、优缺点和迭代历程](project-architecture-and-evolution.md)
- [Windows 11 Claude Code / Codex Skill + MCP 部署](agent-skill-mcp-deployment.md)
- [Windows 11 便携部署与本地模型隔离](windows-portable-deployment.md)
- [Windows 11 全 GGUF E/R 离线安装器（实验中）](windows-offline-gguf-installer.md)
- [模型网关、Embedding/Reranker、分层知识库与 Markdown 智能归类](model-and-knowledge-configuration.md)
- [质量评测、Agent 轨迹与有界执行](quality-harness-and-agent-runtime.md)

## 诊断与认知检索

- [故障案例、代码/Commit 图谱、记忆与 Agentic Search](cognitive-retrieval.md)
- [知识治理、领域图谱、GraphRAG 与检索评测](quality-governance-and-evaluation.md)
- [AP 频繁离线 GW/AP 联合诊断演示](demo-ap-frequent-offline.md)

## 大模型知识提炼

- [文件夹案例提炼、Word/PDF/HTML 抽取和人工校正](llm-knowledge-curation.md)

新增、移动或删除 `docs/*.md` 时必须同步更新本索引。CI 中的
`scripts/check_repo_harness.py` 会验证索引完整性和所有本地 Markdown 链接。
