# 项目文档索引

业务能力总账见 [CAPABILITIES.md](../CAPABILITIES.md)，Harness Engineering 状态与有序路线见
[HARNESS_ENGINEERING.md](../HARNESS_ENGINEERING.md)，最近一次完整验证证据见
[VALIDATION.md](../VALIDATION.md)。

## 架构与演进

- [从 release 分支准备服务器](仓库部署准备.md)
- [2026-09-07 分机交付文件](../deploy/releases/20260907/README.md)
- [服务器使用指南（脚本启动）](服务器使用指南.md)
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
