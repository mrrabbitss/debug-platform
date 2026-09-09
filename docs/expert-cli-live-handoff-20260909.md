# 真实 Codex CLI 专家流程验证交接（2026-09-09）

本记录只说明当前源码 `5f11eacb406f03e1062a589e1762e15a43f6b41c` 的一次本机、合成资料验证，不改变产品能力结论，也不替代 CI、Full、远端模型或现场验收。

## 范围与隔离

- 验证脚本为 `scripts/verify_expert_cli_live.py`，实际调用已登录的 `C:\Users\23173\AppData\Local\OpenAI\Codex\bin\fd4c151a749f3ab4\codex.exe`（`codex-cli 0.153.4`），两次子进程均显式传入 `--model gpt-5.6-terra`。
- 后端使用新的、Git 忽略的 `artifacts/validation/expert-cli-live-20260909-5f11eac-live2` 数据目录和独立 loopback 端口 `2347`；不读取 `backend/data`、公司 ZIP 或父进程的 `127.0.0.1:15245` 实例。结束后该端口已可重新绑定。
- 后端以 `APP_ENV=prod`、`DEPLOYMENT_MODE=standalone`、`AUTH_MODE=rbac`、`AUTH_ALLOW_LEGACY_ADMIN=false` 运行。脚本先通过 `scripts/manage_users.py` 创建一次性 ADMIN，再创建独立 EXPERT 与 ENGINEER 令牌；不使用共享 legacy admin。三枚令牌在停机后均已撤销，数据库复查 `active_tokens=0`。
- `LLM_PROVIDER=mock`，且模型出站审计中的 Chat 数为 `0`。CLI 是唯一推理者；未配置或调用平台 Chat。

## 通过的真实 CLI 流程

ENGINEER 子进程完成当前案例的 host-MCP 诊断：读取固定方法，进行知识搜索、日志搜索和精确证据读取，提交两轮规划，并持久化分析和 HTML 报告。最终会话为 `COMPLETED`，有 2 轮规划、5 类必需工具收据和 5 个当前案例引用；报告文件哈希与数据库记录一致。

EXPERT 子进程完成一份合成 Markdown 的 host-model 路由。ENGINEER 直接调用同一 expert-only MCP 路由被拒绝；EXPERT 成功读取完整分段并应用分类。最终文档 `active=false`、`review_status=DRAFT`、`lock_version=2`，未发布、未审核。该结果验证了 DRAFT/review 边界，而不表示内容已经成为共享知识。

两个 CLI 子进程均以退出码 `0` 完成。脚本在命令行、模型最终声明和持久 host-run 的 `client_model_claim` 三处要求 `gpt-5.6-terra`。Codex CLI 0.153.4 的 JSONL 事件没有提供可独立校验的、服务端签名的最终模型配置字段，因此这里的模型确认范围是：显式参数被 CLI 接受、两个运行成功，且 host-run 保留同一模型声明；不能从保留的 JSONL 证明上游服务的内部路由细节。

## EXPERT JSONL 的 `error` 事件

保留的脱敏事件序列包含 5 个 `error` 标记。每个标记之后仍有 `item.completed` 或后续工具活动，最后出现 `turn.completed`；EXPERT 退出码为 `0`，MCP 路由结果已写入 DRAFT。数据库还记录了 33 次成功 POST，且没有失败的 MCP/HTTP 审计结果。

因此，这些事件在本次运行中都是**非终止且已恢复**的：它们没有阻止后续工具调用、最终分类或进程成功退出。但不能据此断言每一条都属于“可重试错误”。验证器只保存事件类型、计数和最终状态，刻意丢弃原始 JSONL 错误正文、CLI stdout/stderr、提示词及模型回答，故没有可用的真实错误码、MCP 方法名或异常文本来区分网络重试、工具参数纠正或客户端显示事件。此次不应把它们报告成具体后端缺陷；若未来需要归因，应在新的受控运行中额外保存经字段级脱敏的 `error` 类型/代码，而不是保留正文。

## 断言、清理与保留边界

脚本使用实际断言，而非仅检查 CLI 退出码：验证诊断会话状态、两轮规划、所有必需读取工具收据、引用只能来自当前会话允许的当前案例证据、持久分析、报告 SHA-256、ENGINEER 的 expert-routing 拒绝、EXPERT 的 DRAFT 不变量以及 Chat egress 为零。

CLI stdout/stderr 仅在内存中解析为脱敏事件类型摘要；最终回答文件在摘要后删除。没有保存提示词、Bearer 值、`raw_token` 或完整模型回复。保留的 SQLite 中只含访问令牌哈希/提示和已撤销状态；合成日志、合成方法、脱敏结果摘要及报告均不含公司资料。脚本在 `finally` 中终止自己启动的 FastAPI 进程、关闭日志句柄、撤销隔离令牌并以只读方式重新打开数据库确认持久化。

第一次运行曾因验证器把服务器保存的 `debug_*` 工具收据名误写成未加前缀名称而失败；真实 ENGINEER 会话当时已完成。该断言已更正后，在新的独立目录完成上述 PASS。该问题是验证器后处理错误，不是产品缺陷。
