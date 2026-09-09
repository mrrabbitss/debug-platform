# 0.5.0 慢模型等待与交付记录

用户要求在上一轮真实 API / Codex CLI 验证后提高网页和 CLI 超时，构建新版服务器及分机包，
发布 GitHub Release；不重复已经通过的回归，也不重新调用真实模型消耗额度。

## 等待策略

| 层次 | 0.5.0 行为 |
| --- | --- |
| 后台 OpenAI-Compatible Chat | 单次请求默认从 300 秒提高到 900 秒，SDK 与 HTTP 传输使用相同值；重试次数不变 |
| 历史默认配置 | `.env` 中 300 秒解释为 900 秒；旧模型配置的 300 秒跟随服务器当前默认；其他显式值保留 |
| 网页同步 AI | 等待 2 小时，容纳慢请求及原有重试；上传等待不降低 |
| 新建 AI 持久任务 | 问答、报告修订、日志规划、知识归类/提炼、整理及助手发布采用 2 小时上限；追踪预算同步 |
| 综合诊断 | 原有推理总预算 3 小时、作业上限 4 小时，保持不变 |

分机 CodeAgent 启动器为本次会话配置平台 MCP `timeout=7200000` 毫秒；模型请求、首字节及
流空闲等待通过对应环境变量提高到至少 900000 毫秒，用户已有更长值保留，退出恢复原进程环境。
仅平台 MCP 配置增加等待，不改模型地址、密钥、账号或其他 MCP 条目。参数依据
[Claude Code MCP/环境变量文档](https://code.claude.com/docs/en/env-vars)；魔改 CodeAgent 对新版本
环境变量的支持仍取决于其实现，本机没有该客户端，未声称真实 CodeAgent 模型验收。

原生 Codex 的安装脚本在 `codex mcp add` 生成的 `gw-ap-debug` 表中写入 `tool_timeout_sec=7200`，
只改变该表并保留配置备份；字段依据 [OpenAI MCP 文档](https://learn.chatgpt.com/docs/extend/mcp)。
Codex 自身模型流等待由其 provider 配置控制，可在实际使用的 `[model_providers.<id>]` 中配置
`stream_idle_timeout_ms = 900000`；安装器不覆盖用户的模型 provider。参见
[OpenAI 配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。
两个 Skill 上传脚本及仓库安装镜像的 HTTP 等待/知识任务轮询统一提高到 2 小时。

新上限只影响新建作业，旧排队任务保留持久化的原上限；不重写业务数据库、用户密钥、模型选择、
出站同意或审批记录。租约心跳、取消、并发限制、原子发布机制不变。解析文件和静态代码检查
不属于慢 Chat 等待，本轮不延长其资源保护时间。

端点服务或其代理主动返回 500、限流、关闭连接，以及模型未满足 JSON 结构，仍会明确失败。
本轮增加等待不构成对这些问题的修复或对真实公司模型语义质量的验收。

## 验证范围

新增 `backend/tests/test_slow_model_timeouts.py` 的 7 项检查已通过，验证旧环境文件默认值升级、
其他自定义值保留，以及模型级超时实际传入 OpenAI SDK 与底层 HTTP 客户端；没有发送模型请求。
前端生产构建通过；原生 Codex 使用隔离配置执行 MCP 安装及 `mcp get --json`，实际解析到 7200 秒，
其他 MCP 与模型选择保留。CodeAgent 会话环境覆盖 4 种输入、4 个变量以及退出恢复，8 个 PowerShell 脚本语法检查通过。
新配置验证修复了 Windows PowerShell 5.1 将首次安装的“条目不存在” stderr 当异常的问题，及原子配置替换的空备份路径兼容问题。
仓库护栏 24/24 通过（197 Python / 36 Vue），API 契约仍为 38 项、MCP 工具 18 项。
新增 Python 无 Ruff 问题；3 个被调整的旧模块各有一条既有 E402，确认本轮未增加。
完整包已构建：服务器 10,200 项清单由构建器验证，276 个应用/前端文件及 22 个配套源码文件逐字节匹配；
分机 26 项清单、源码及 ZIP CRC 核对通过。随包 Python 在新建的隔离目录启动成功，网页入口可读，
空白验证数据库迁移到 0024；未启动模型推理，也未运行以前通过的功能测试。

服务器 EXE：`GWAP-Debug-Server-Setup-0.5.0-x64.exe`，921,020,150 字节，SHA-256
`1129ff90a7947341ae8eda0b611d32696a149d5a478c584ed04f18753a957511`。
分机发布名 `GWAP-Client-0.5.0.zip`，49,691 字节，SHA-256
`8ed2a944a331780a059142758b948d8579f9bbe5520454d1f6d1f287ba9ae4eb`。EXE 的产品版本已核对为 0.5.0。
完整交付证据保存在 Git 外 `artifacts/lan/expert-0.5.0/`，源码提交与远端附件校验信息见
[GitHub Release](https://github.com/mrrabbitss/debug-platform/releases/tag/v0.5.0) 的 delivery-manifest.json。
构建时基线为 7f02fa9，未跟踪的用户文章保留且未打包；最终标签额外含分机打包清单与交付文档，
应用及已打包脚本均与最终源码核对一致。

上一轮真实 API / Codex CLI 结果沿用[已保留证据](expert-live-model-validation-20260909.md)：
CLI 使用 gpt-5.6-terra 完成诊断、报告和专家归类；API 知识整理、提炼、审核、日志规划、问答与重启保留通过，
综合诊断仍被上游 500 / Skill 阅读结构校验阻断，未生成 API 诊断报告。
不重复这批已通过项目，不运行 Full / External，不手动触发 CI。

## 安装与升级

版本号采用 0.5.0，包含专家与知识协作迭代、真实测试后的三项修复及本轮等待优化。
服务器完整 EXE 沿用固定的 Python 运行时、BGE Embedding、Qwen3 Reranker、llama.cpp 和 HTTPS 网关；
分机重新构建，包含专家登录及 CLI 等待更新。公开产物不包含用户的业务知识源包、数据库或 API Key。

0.3.3 / 0.4.0 服务器无需卸载：停止、备份，以原 Windows 账号覆盖安装，再等待数据库迁移至 0024 和 READY。
升级沿用数据目录、身份、证书、端口和模型配置；不自动清空共享知识。
分机解压新版后运行 Install.bat，用原识别码更新。详见[服务器指南](服务器使用指南.md)与[分机指南](分机使用指南.md)。

## GitHub 发布完成

[0.5.0 Release](https://github.com/mrrabbitss/debug-platform/releases/tag/v0.5.0) 已公开发布并设置为最新版本，
7 个附件的名称、大小、SHA-256、发布正文及标签提交均与本地交付清单一致；标签为
`bfaef6bc367f82058cb97640a457f4655b45e760`。校验记录在 Git 外
`artifacts/lan/expert-0.5.0/github-release/published-verification.json`。

发布自动触发[完整 GGUF 工作流](https://github.com/mrrabbitss/debug-platform/actions/runs/34341280002)，
按用户不重复验证的要求已取消。该运行在 Python 选择步骤报错，后续应用/模型验证及产物上传均未执行；
不将取消或已有本地证据表述为新 CI 通过。未修改工作流、未手动重新运行，未合并主分支。
