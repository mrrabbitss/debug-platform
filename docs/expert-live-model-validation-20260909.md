# 专家迭代真实 API 与 Codex CLI 验证（2026-09-09）

用户明确授权使用自己的第三方 API 请求 `glm-5.2`，并使用真实 Codex CLI 的
`gpt-5.6-terra` 或 `gpt-5.6-luna` 测试当前源码。本次 CLI 选择 `gpt-5.6-terra`。
测试基线为 `5f11eac`，本记录同时覆盖此次发现问题的定向修复。

**结果：Codex CLI 完整诊断与报告通过；第三方 API 部分通过，综合诊断未通过。**
不能据此将该 API 标记为已经完成诊断与报告验收。

## 最终结果

| 链路 / 功能 | 结果 | 实际证据 |
| --- | --- | --- |
| API 角色、模型共享与私有隔离 | PASS | 原令牌晋升专家、空白名单连接、共享可见、私人模型不可越权使用 |
| API 完整 Skill 文件夹整理与发布 | PASS | 6 次全文阅读、2 次规划；6 份原文完整保留，恢复原审批后发布 |
| API 普通用户案例提炼 | PASS | 1319 字符草稿；确认仍为普通 KNOWLEDGE 草稿，提交后 SUBMITTED |
| API 专家多轮修正与审批 | PASS | 两轮 AI 修正、4 条对话、原稿和版本记录保留；批准后 PUBLISHED |
| API 日志规划 | PASS | 6 行全部扫描，9 个精确命中；planner_status=ACCEPTED，无规划回退 |
| API 综合诊断 | FAIL | 首次在 Skill 阅读时收到上游 InternalServerError；第二次在阅读时返回不符合结构的内容，均明确停止 |
| API 诊断 HTML 报告 | 未执行 | 没有成功的 API 综合诊断，不能用默认规则结果冒充模型完成 |
| API 交互问答 | PASS | 最终实际 assistant 回复 842 字符，关联 8 项检索引用；案例尚无成功综合诊断 |
| API Markdown 归类 | PASS | 专家任务完成，文档仍 inactive / DRAFT，没有跳过审核 |
| API 重启持久化 | PASS | 6 份 Skill 和 1 份已审批 Wiki 的 ID、版本、正文重启前后一致，SQLite quick_check=ok |
| CLI ENGINEER 完整诊断与报告 | PASS | terra 完成 2 轮规划、5 类必需工具回执、5 个本案例引用，HTML 哈希与记录一致 |
| CLI EXPERT 归类 / 普通用户边界 | PASS | 专家完成分类但保留 DRAFT；普通用户同一管理工具被拒绝；后台 Chat 请求为 0 |

最终 API 输出目录为 `artifacts/validation/expert-api-live-20260909-f/`，CLI 为
`artifacts/validation/expert-cli-live-20260909-5f11eac-live2/`。API 的 `c → d → e → f`
是合成数据库副本的接续关系：c 保留真实阅读、提炼和归类，d 验证恢复发布与多轮审核，
e 完成真实日志规划，f 复查尚未完成的综合诊断。已经成功的模型任务没有重新调用。
每次启动的模型连接探测仅用于确认继续测试的必要前提。

e 的失败出站审计为 `diagnostic_skill_read / InternalServerError`，耗时 126947ms；
保留 3 份成功阅读回执。f 的新分析保留 4 份成功回执，下一次响应通过 HTTP/JSON 解析，
但未通过 SkillReading 字段校验。失败模型正文未保留，因此不能进一步断言具体是长度、
字段类型还是额外字段问题。两次都没有继续规划、生成根因或报告，属于未通过的真实结果。

问答的 8 项引用是本次检索和上下文引用，不能等同于 8 条独立原始日志，也不能替代尚未
通过的综合诊断。多轮审核保留了认证、电源和恢复事实，但第二轮未保留首轮测试标记；
这不影响版本记录保存，说明发布前仍需核对完整差异，不应将模型稿当成已经人工核验。

## 环境与证据范围

两条链路使用独立的当前源码进程、SQLite 数据库、端口和临时身份。均采用
`APP_ENV=prod`、`DEPLOYMENT_MODE=standalone`、独立用户 RBAC，直接访问本机 loopback。
所有上传材料为脚本内合成案例、日志和 Markdown，没有使用公司 Skill ZIP、历史业务
日志或正在运行的开发实例。测试没有修改全局 Codex 配置。

API 使用用户提供网关的 `/v1` 兼容路径。请求均明确指定 `glm-5.2`，但两次直接探测的
响应 `model` 字段均为 `glm-5.3`。因此本记录证明的是“该网关接受 `glm-5.2` 请求后的
实际响应”，不能证明网关内部实际使用 GLM-5.2，也不能把两条不同样本的测试当作模型排名。

CLI 两次子进程均显式传入 `--model gpt-5.6-terra`，退出码均为 0。命令参数与持久
`client_model_claim` 一致；CLI 的保留事件不能独立证明上游内部路由。
其完整证据和 5 个非终止错误事件的保留边界见 [CLI 交接](expert-cli-live-handoff-20260909.md)。

## API 首轮发现与修正

首轮隔离输出为 `artifacts/validation/expert-api-live-20260909-c/`。

- 模型共享、私有模型隔离、晋升专家后的原令牌权限更新通过，端点白名单保持空。
- 整理助手进行了 6 次全文阅读和 2 次规划，六文件原文逐字保留，但确认后的发布任务
  长时间停在 RUNNING。原因是首次创建图谱状态行后，会话缓存保留了批量更新之前的值，
  将本任务自己的构建状态误判为租约丢失。修复在核对发布权限时重新读取该状态行。
  新增真实 SQLite 与后台 JobRunner 线程检查通过；复用首轮已审批数据库的实际进程恢复
  也通过，六文件发布且原文逐字不变，没有重复模型阅读或人工审批。
- 审核助手的一次响应未通过结构校验，待审核稿保持原状。独立重试通过，未保留该次
  失败原始模型正文，不能归因到某一个具体字段。修复明确提供 JSON Schema 和字符串
  类型约束，格式校验失败最多重新生成一次；重复失败仍返回错误，禁止未经审核发布。
- 普通用户 AI 案例提炼成功生成 1319 字符草稿，确认产生普通 KNOWLEDGE 草稿，提交后
  为 SUBMITTED。Markdown 归类也完成，后续补充检查其 DRAFT 不变量。
- 首版验证脚本漏调日志 triage，综合诊断被前置条件正确拒绝；已补齐与网页一致的步骤。
  问答脚本最初误选用户问题而非助手回答，已改为按任务 ID 和 assistant 角色检查引用。
  重启检查新增“至少六份 Skill 加一份已审批 Wiki”前提，避免空集合产生错误的通过结论。
- 后续真实 ENGINEER 请求发现日志 triage 已入队，但读取其 `/jobs/{id}` 返回 404。任务只
  保存 `triage_run_id`，原权限解析没有追溯它的所属案例。修复按实际 triage 记录解析案例；
  所有者、编辑者、查看者、取消权限、外部用户以及伪造 case_id 的 7 项新检查通过。

定向修复共 12 项新检查通过：首次发布线程回归 1 项、审核结构与并发保护 4 项、日志任务
权限 7 项。模型输出仍需通过字段和引用验证；一次格式纠正不撤销版本检查或人工审批。

最终清理复查：API 各接续数据库模型密钥字段为零、未撤销测试令牌为零，自己启动的
进程均已停止；CLI 三个临时身份的令牌均已撤销。源码和保留证据的凭据扫描结果为零。
仓库护栏 24/24 通过；变更 Python 文件通过 Ruff 和 AST 检查。扫描 108 个源码及证据文件，
未发现模型密钥或完整测试令牌；清理复核记录位于 f 目录的 `final-cleanup-check.json`。

## 可重复运行

API 验证器：`scripts/verify_expert_api_live.py`。凭据只通过当前进程环境变量
`EXPERT_LIVE_API_KEY` 输入，报告和源码不包含密钥。示例中的 Base URL 由操作者填写：

```powershell
.venv/Scripts/python.exe -X utf8 -u -B scripts/verify_expert_api_live.py `
  --output artifacts/validation/new-api-run `
  --base-url https://example.invalid/v1 --model glm-5.2
```

`--resume-from` 仅复制本验证器在 `artifacts/validation` 下生成的合成数据库；原目录保持
不变。它复用已成功的全文阅读和归类，恢复此前已审批的发布任务，避免重复请求模型。
`--phases` 可选择尚未验收的步骤，但仍需先运行 `setup` 准备隔离身份和模型。

CLI 验证器：`scripts/verify_expert_cli_live.py`，可用 `--codex` 指定已登录的 CLI 可执行文件。
它创建临时 MCP 配置，不配置平台 Chat；验证结束后必须满足平台 Chat 出站次数为零。

## 验收限制

这是合成资料、本机源码和两个指定推理入口的功能检查。没有测试真实公司案例的诊断
准确率、检索质量排名、Embedding/Reranker/GGUF 性能、物理断电、远端数据库、TLS 分机
连接、新安装包或升级安装；既有浏览器场景沿用之前记录，没有重复运行历史套件、Full、
External 或手动 CI。未宣称已达到新的合并或安装包发布门禁。
