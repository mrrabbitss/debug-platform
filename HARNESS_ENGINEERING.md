# Harness Engineering 路线与状态总账

最后更新：2026-08-12

本文维护“让人和 Agent 能稳定理解、执行、验证和追溯本项目”的工程能力。
业务功能总账仍以 `CAPABILITIES.md` 为准；两份文件必须互相链接，避免把工程护栏
和业务能力混为一谈。

## 1. 目标闭环

```text
需求/故障 → 项目地图 → 隔离执行 → 有界工具 → 自动测试与评测
          → 轨迹/证据 → 人工确认 → PR/CI → 安全合并
```

Harness 的目标不是让模型自由度无限增大，而是让每一步都有明确输入、权限、预算、
停止条件、验证结果和可追溯证据。

## 2. 当前基线

### 已完成

- [x] Python 跨平台 `uv.lock` 与 pip 兼容 constraints，Win11/CI/Docker 共用；
- [x] Ubuntu/Windows 后端和前端、扩展、依赖审计、外部服务、Docker、Win11 冒烟 CI；
- [x] 隔离临时数据库、存储和端口的 `runtime_smoke`；
- [x] 数据库持久化后台任务、重启恢复、取消、重试和去重；
- [x] 模型端点策略、内容外发同意、用量审计、证据 ID 校验和 DRAFT 门禁；
- [x] Win11 私网模型端点显式启用脚本，幂等更新本机 `.env` 且不回显密钥；
- [x] Agentic Search 的计划、阶段状态、候选数、耗时与确定性回退；
- [x] GW/AP 联合日志规划、两至八轮综合诊断、异步案例问答和人工确认诊断修订的持久化任务与案例级实时轨迹；
- [x] `CAPABILITIES.md` 业务总账和 `VALIDATION.md` 验证记录。

### 本轮 P0

- [x] 根目录 `AGENTS.md` 项目地图、规则和完成标准；
- [x] `scripts\validate_all.bat` 的 Fast/Full/External 模式和 JSON/日志产物；
- [x] 自动检查文档链接、能力总账、CI 触发器、依赖治理和 Agent API 合同；
- [x] CI 只在 `main` push 或面向 `main` 的 PR 运行，消除开发分支重复任务；
- [x] `.gitattributes` 固定 Win11/Linux 行尾，降低跨电脑无意义冲突；
- [x] PR 模板、CODEOWNERS 和 Dependabot 配置；
- [x] GitHub `main` ruleset：必须 PR、必须最新 CI、禁止删除和 force push；
  `Protect main`（Ruleset `20426708`）在本轮 9 项远端 CI 全绿后启用，无绕过角色。

## 3. P1：质量、可观测性和架构约束

### 3.1 可重复的端到端评测

- [x] 提交脱敏的 TXT/MD/HTML/DOCX/PDF Golden Incident Corpus；
- [x] 提交 Fake OpenAI-compatible 服务，覆盖生成、超时、限流、坏 JSON 和中断；
- [x] 使用 Playwright 固化上传、预览、提炼、对话纠错、确认 DRAFT 的浏览器 E2E；
- [x] 使用 Playwright 固化无后缀日志、完整原文三层筛查、两轮诊断规划和可恢复问答 E2E；
- [x] 日志解析评测：事件代码、时间、行号和上下文准确率；
- [x] 知识提炼评测：章节完整性、证据引用、幻觉、敏感信息泄漏；
- [x] RAG 评测：Recall@K、MRR、NDCG、引用准确率；
- [x] Code Graph 固定调用/引用/继承/实现边；
- [x] Commit Graph 固定 query → commit → file → symbol 路径；
- [x] Memory 固定应复用、不得复用和污染隔离案例；
- [x] Agent trajectory 固定工具选择、最大跳数、停止原因、成本和耗时阈值；
- [x] 后端完整回归 75% 行覆盖率门禁；建立门禁时实测为 77%。
- [x] 每个 GitHub CI job 设置硬墙钟超时，Golden 耗时阈值由独立无插桩任务严格阻断；

### 3.2 运行轨迹

- [x] 统一 `run_id`、`case_id`、阶段/工具、模型和 Prompt 版本；
- [x] 记录输入输出摘要哈希、tokens、成本、耗时、重试和停止原因；
- [x] 只保存证据 ID 和脱敏元数据，不复制公司日志正文；
- [x] 前端运行检查器、失败步骤定位和内容安全的只读重放；
- [x] 运行中增量显示方法读取、规划轮次、检索、三层排序和模型回答，安全保留轮次/停止元数据；
- [ ] 可选增强：将现有结构化轨迹桥接到 OpenTelemetry 本地观测栈。

### 3.3 架构护栏

- [x] `routes.py` 拆出 system、knowledge、repositories 和 jobs 领域路由，聚合器从约 1,972 行降至约 622 行；
- [x] 知识提炼拆出上传、沙箱抽取、证据、序列化和受状态机约束的生成/修订/确认编排；
- [x] Agentic Search 拆为 planner、tools、fusion、executor、trace；
- [x] 前端大型 View 拆出 composable、来源预览领域组件和类型化 API client；
- [x] 增加 API → service → persistence 导入边界检查；
- [x] 增加文件大小、圈复杂度、Vue/TypeScript 类型检查和后端覆盖率下降门禁；
- [x] 将 Workflow 白名单同时与手写最小 OpenAPI 和 FastAPI 运行时 OpenAPI 比对。

## 4. P2：有界 Agent 与多任务运行

- [x] 类型化 Tool Registry、输入输出 Schema 和只读默认权限；
- [x] 每个角色/案例的工具白名单及写操作审批；
- [x] 最大步骤、跳数、tokens、成本、墙钟时间和并发预算；
- [x] 重试退避、熔断、幂等、取消和显式停止原因；
- [x] 失败时回退当前确定性 Planner，而不是无限循环；
- [x] 每个任务独立 worktree、端口、数据库、存储和日志目录；
- [x] 后台任务 lease、heartbeat、dead-letter 和多实例安全领取；
- [x] 不可信 DOCX/PDF/HTML 解析迁移到受 CPU/内存/时间限制的独立进程；
- [ ] 任务控制平面维护依赖 DAG、人工审批和停滞检测；
- [ ] 将人工 Review 反馈沉淀为测试、规则、文档或评测样本。

## 5. 优先顺序与完成条件

1. P0 仓库护栏必须先稳定，确保每台 Win11 和 CI 使用同一执行路径；
2. P1 Golden Dataset、Fake Model 和浏览器 E2E 完成后，才能提高 Agent 自主度；
3. P1 轨迹和预算可观测后，才能把确定性 Planner 升级为循环 Tool Agent；
4. P2 多任务/多实例能力必须经过故障注入和恢复测试；
5. 每完成一项，同步更新本文、`CAPABILITIES.md` 和 `VALIDATION.md`。

一项 Harness 能力只有在仓库中存在可执行入口、自动测试和失败说明时才算完成；
只有文档描述或一次人工验证不算完成。
