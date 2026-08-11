# 质量评测、Agent 轨迹与有界执行

最后更新：2026-08-11

本文说明如何重复验证日志解析、知识提炼、认知检索和 Agent 执行质量。所有仓库样本均为
合成数据，禁止把公司日志或凭据加入 Golden Dataset。

## 1. 一键入口

在项目根目录执行：

```bat
scripts\run_golden_evals.bat
scripts\run_browser_e2e.bat
scripts\validate_all.bat Full
```

- Golden 命令输出每项指标，并以非零退出码阻断不满足阈值的提交；
- 浏览器 E2E 自动启动独立数据库、Storage、Fake Model、FastAPI 和 Vite，执行完成后清理；
- `Full` 同时执行锁文件、Ruff、compileall、Harness、架构门禁、Golden、后端覆盖率、
  前端构建、依赖审计、VS Code 扩展、Doctor、运行冒烟和浏览器 E2E。

需要为并行任务预分配独立 worktree 和运行目录时：

```bat
scripts\new_agent_workspace.bat -TaskName fix-auth-timeout -PlanOnly
scripts\new_agent_workspace.bat -TaskName fix-auth-timeout
```

脚本根据任务名确定性分配分支、后端/前端/Fake Model 端口，以及独立数据库、Storage 和
日志目录。`-PlanOnly` 只打印计划，不创建 worktree。

## 2. Golden Incident Corpus

清单位于 [corpus.json](../sample_data/golden_incident/corpus.json)，固定材料包括：

- [无后缀 Huawei 风格日志](../sample_data/golden_incident/logs/collectDebuginfo_golden)；
- [TXT 错误描述](../sample_data/golden_incident/case/error.txt)；
- [HTML 人工分析](../sample_data/golden_incident/case/analysis.html)；
- [DOCX 解决方案](../sample_data/golden_incident/case/solution.docx)；
- [PDF 验证记录](../sample_data/golden_incident/case/validation.pdf)。

DOCX/PDF 是可重复生成的固定二进制样本；执行
`python scripts/generate_golden_documents.py --check` 会校验内容哈希，不匹配即失败。

Golden 套件当前包含九类阻断检查：

| 检查 | 核心断言 |
| --- | --- |
| 样本完整性 | 合成数据声明、文件存在、DOCX/PDF SHA-256 固定 |
| 日志解析 | Parser ID、事件代码、时间、起止行号、最大耗时 |
| 文档提炼 | 必需章节、事实、行号引用、禁止幻觉、敏感信息掩码 |
| Code Graph | `CALLS/REFERENCES/INHERITS/IMPLEMENTS` 固定边 |
| Commit Graph | `query → commit → file → symbol` 固定路径 |
| Memory | 应复用记忆命中；跨案例秘密和不相关失败记忆不得污染 |
| RAG | Recall@K、MRR、NDCG@K、引用准确率均达到清单阈值 |
| Agentic Search | 模块选择、最大跳数、停止原因和耗时 |
| 有界执行器 | 类型化工具、步骤/tokens 预算、轨迹和显式停止原因 |

指标阈值存放在 corpus 清单中。任一必需证据缺失、禁止结论出现、耗时超限或停止原因变化，
`Golden Dataset Quality` CI 都会失败。

独立 Golden CI 是耗时阈值的权威门禁，并保持严格计时。后端 pytest 覆盖率任务仍执行全部
功能断言、记录实际耗时与预算，但不重复用 coverage 插桩后的墙钟时间判定成败，避免慢速
共享 runner 产生假回归；这不会放宽独立 Golden job 的任何阈值。
所有 CI job 另有 10～30 分钟的墙钟硬超时，超时会直接阻断合并。

## 3. Fake OpenAI-compatible 服务与浏览器 E2E

Fake 服务在 [fake_openai_server.py](../backend/tests/fake_openai_server.py)，只读取仓库内合成
响应，支持 Chat、Embedding 和 Rerank 兼容接口，并覆盖：

- 正常结构化生成与修订；
- 超时；
- HTTP 429 限流；
- 非法 JSON；
- 上游中断。

Playwright 场景位于 [knowledge-curation.spec.ts](../frontend/e2e/knowledge-curation.spec.ts)，固定
覆盖三条完整路径：

- TXT/HTML/DOCX/PDF 文件夹上传、三类文档预览、初稿生成、对话纠错、人工确认只创建 `DRAFT`；
- 加密 Chat 代理配置和清除，前端/API 不回显代理凭据；
- 无后缀 Huawei 合成日志上传与解析、自动 LLM 日志规划、完整原文命中、三层证据 UI、至少两轮综合诊断、停止原因、后台案例问答和每阶段轨迹。

页面异常、请求失败、未捕获异常或浏览器控制台错误都会使测试失败。Fake 服务会根据请求 Schema
返回带动态方法/evidence ID 的合法响应，因此测试同时阻断“模型编造 ID”或“只跑一轮”的退化。

## 4. 后端覆盖率和架构门禁

完整后端测试通过 [run_backend_tests.py](../scripts/run_backend_tests.py) 执行。阈值集中在
[quality_gates.json](../harness/quality_gates.json)，当前最低行覆盖率为 75%；本次引入门禁时的
实测基线为 77%。CI 会上传各操作系统的 `coverage.xml`。

[architecture_limits.json](../harness/architecture_limits.json) 和
[check_architecture.py](../scripts/check_architecture.py) 阻断：

- 高冲突文件重新超过本次收紧后的行数上限；
- 新 Python/Vue 文件超过默认上限；
- 圈复杂度超过阈值；
- API 反向依赖 Service 或模型层反向依赖上层；
- 必需领域模块、类型化 API Client、Composable 或组件被意外删除。

`routes.py` 已拆出系统、知识、代码仓和任务 API；知识提炼已拆出上传、文档沙箱抽取、证据、
序列化及编排；Agentic Search 已拆出 Planner、Tool Registry、Fusion、Executor 和 Trace。

## 5. 统一运行轨迹与隐私

Agent 运行使用 `AgentRun + AgentTraceEvent` 保存：

- `run_id`、`case_id`、资源 ID、操作、执行模式；
- 阶段、工具、模型/配置和 Prompt 版本；
- 输入输出摘要 SHA-256，不保存未处理的公司日志正文；
- tokens、成本、耗时、重试次数和证据 ID；
- 状态、明确停止原因、审批状态和重放来源。

日志规划、综合诊断和案例问答在任务执行期间增量写入轨迹，前端不必等整个任务完成才显示阶段。
同一事务连续写入会先 flush 分配出的 sequence，避免多个方法读取事件复用同一序号。规划元数据只
放行文档 ID、版本、角色、轮次和停止原因；方法标题/正文、原始日志和 Prompt 不进入轨迹。

管理员可在前端“运行轨迹”查看失败步骤和脱敏元数据。重放只对具备内容安全 payload 的
只读 Agentic Search 开放，强制 `record_memory=false`；知识提炼可查看轨迹，但不能从轨迹
直接重放原始文件内容。

## 6. 有界 Agent 与故障回退

类型化 `ToolRegistry` 为每个工具声明 Pydantic 输入/输出、角色白名单、只读/写权限、幂等性、
超时和最大重试。`BoundedAgentExecutor` 同时限制步骤、跳数、tokens、成本和墙钟时间，支持：

- 取消；
- 指数退避；
- 连续失败熔断；
- 写工具显式审批；
- 类型校验失败、预算耗尽和工具失败的明确停止原因；
- 失败后回退当前确定性检索器。

当前通用 Agentic Search 仍以确定性 Planner 为默认安全基线；有界循环执行器已经具备测试和
Golden 门禁，但尚未默认替换通用检索器。综合诊断是一个范围更窄的例外：它在确定性检索基线之上
使用两至三轮 LLM Planner，只能产生有界、去重的只读检索 query，所有方法/evidence ID 都经过
Schema 校验，失败后保留确定性诊断。多轮请求在同一异步事件循环内执行，避免复用 HTTP 客户端时
跨事件循环导致第二轮 `APIConnectionError`。

## 7. 后台任务可靠性与不可信文档隔离

持久化任务支持幂等键、原子领取、lease、heartbeat、超时、指数退避、dead-letter、取消、
输入/CPU/内存预算和多实例安全领取。DOCX/PDF/HTML 抽取在独立进程运行；Linux 使用 rlimit，
Win11 使用 Job Object 限制内存并在父进程退出时终止子进程，同时保留墙钟超时。

尚未完成的 P2 控制面能力是跨任务依赖 DAG、通用停滞协调，以及把人工 Review 自动分类成
规则/测试/文档/评测样本。这些能力涉及新的审批和写入语义，在完成专项数据模型与权限设计前
不会伪装成可用功能。
