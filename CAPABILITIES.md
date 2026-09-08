# GW/AP Debug Platform 功能、需求与兼容性总账

> 本文件是项目功能范围的唯一总账（Single Source of Truth）。
> 新需求、在研能力、已交付能力、约束和冲突处理都必须同步更新本文件，防止跨迭代遗忘或重复建设。

最后更新：2026-09-07

工程执行、验证、评测和 Agent 护栏的状态与优先级单独维护在
[Harness Engineering 路线与状态总账](HARNESS_ENGINEERING.md)。本文件只判断业务能力是否
可用；两份总账在每次相关迭代中必须同步。

## 1. 状态说明

### 局域网与知识演进 M0–M5（2026-09-07，试点候选）

- 用户已批准 Win11、i7-14700 / 32 GB 的单服务器先导配置，保留现有单机模式；进度以
  [试点交接](docs/pilot-handoff-20260907.md) 和 [实施记录](docs/lan-knowledge-iteration.md) 为准；公司实机验收仍待完成。
- M0 `AVAILABLE`：464 文件可恢复源码快照和完整基线回归通过（416 后端测试、5 浏览器 E2E）。
- M1/M2 `IN_PROGRESS`：HTTPS 网关 + RBAC + 普通工程师 REST/MCP 握手、GW/AP 上传、网页路由、
  Origin 拒绝、令牌撤销通过真实本机 HTTPS 验证；纯 PowerShell 客户端包无需平台运行时。
  完整 GGUF 服务器组合和本机备份恢复已验证；服务安装、公司网络/真实 CodeAgent、实际升级回退和发布门禁仍待验证。
- M3 `IN_PROGRESS`：候选记忆默认留在来源案例；管理员可审核全局发布、到期、驳回或归档。
  模型假设/引用不再自动记成真实 SUCCESS；实际结果需要观察记录与人工反馈审核。
  复发会将相关已发布经验退回待复核。迁移、检索隔离和状态回归已通过，网页新增面板已构建。
- M3 已增加已发布文档的独立编辑/恢复/方法提炼草稿、乐观锁审核、持久发布任务、版本化分块，
  新向量/图谱构建完成后一起切换；构建失败、取消、并发修改保留旧版。历史引用和发布清单可读取。
  最新 Full 18/18 通过（469 后端测试、7 浏览器 E2E），包含个人修订、LAN 首次一致性发布、原发布者或管理员审批与发布任务中断恢复。
- 知识权限、全文分段与方法编译、人工历史资料 HIGH 入库、完整服务器备份恢复已实现并有回归。
  个人修订先供修订者新发起的综合诊断使用；普通问答的个人覆盖、异机备份实测及公司实机验收仍未完成。
- 本地双 Codex CLI 完整 HTTPS/MCP 诊断、进程中断后原会话恢复、会话权限和停机后数据保留已实测通过；
  [验证范围](docs/local-multiclient-cli-validation.md) 是单机进程级分机模拟，不代表物理多机或 10 路并发推理验收。
- 当前分发方式：服务器提供完整离线 EXE，内置 Python、GGUF E/R 和 HTTPS 网关，安装后从桌面启动，无需安装 Windows 服务；
  已移除现场下载构建入口 `setup_server.bat` / `scripts/setup_lan_server.ps1`。
  程序与业务数据分开，数据默认位于 `%LOCALAPPDATA%\GWAPDebugServer`，也可用 `GWAP_SERVER_DATA_ROOT` 指向已有目录。
  支持复用配置、导出分机证书资料、停止后备份及运行/备份互斥。分机 ZIP 附简版指南，仍需已有 CodeAgent。
  操作步骤见 [服务器指南](docs/服务器使用指南.md) 与 [分机指南](docs/分机使用指南.md)。

### WebSkillMcp 0.2.0 本轮交付范围（2026-09-03）

- CodeAgent 会话 MCP 改为追加模式，移除默认 `--strict-mcp-config`；不编辑 `.cac` 或全局
  CLI 配置。源启动器 12 项黑盒已通过，真实魔改客户端模型 E2E 仍待公司电脑验收。
- Win11 x64 安装器新增 Core 必选、GGUF Embedding/Reranker 可选，默认完整安装；原子
  staging/backup 升级保留，源包哈希验证后生成选装清单。0.2.0 ZIP/Setup 已重新构建，真实
  E/R、安装选装矩阵与包内 CLI 启动/复用验收见 `VALIDATION.md`；魔改客户端仍需实机验收。
- 包内新增 Web/CodeAgent 双入口，使用自包含 Python/前端及已安装 E/R；默认本地模式共享
  Windows CurrentUser 加密 MCP 令牌，不修改用户鉴权/Chat Profile/代理；CLI 入口禁止退回
  源码 pip/.venv 准备。该运行形态不包含本地 Chat 模型。
- 全 GGUF 继续保留 `IN_PROGRESS` 发布状态：未签名、未完成独立干净 Win11/CPU 矩阵与
  上游等价质量 Golden；不得把已实现组件选择或本机真实推理称为所有发布门禁通过。
- 最终包真实小型知识检索已通过：正常审核发布、14 个 768 维向量持久化、Dense + Reranker
  实际参与且相关知识第一。该结果不等于大规模质量评测、上游等价或持久向量 ANN 召回验证。

| 状态 | 含义 |
| --- | --- |
| `AVAILABLE` | 已实现并有自动化测试或运行验证 |
| `IN_PROGRESS` | 正在实现，接口或数据结构仍可能调整 |
| `PLANNED` | 已确认进入路线，但尚未开始编码 |
| `LIMITED` | 可以使用，但存在明确的输入或环境限制 |
| `DEPRECATED` | 仅为兼容保留，不应继续扩展 |

## 2. 已有能力

### 2.1 Windows 11 本地运行与运维

| 能力 | 状态 | 入口 | 说明 |
| --- | --- | --- | --- |
| Win11 自包含 Core 便携运行 | `AVAILABLE` | GitHub `Windows Portable Package` / 解压后 `start.bat` | 目标电脑无需 Python、Node、pip、npm 或 Docker；同一 FastAPI 进程托管编译前端，配置/数据默认外置到 `%LOCALAPPDATA%`；解释器禁止读取全局/用户包，文件清单逐项校验 SHA-256；该稳定 Core 版不含权重，默认 SQLite、Hashing Embedding、Reranker Disabled |
| Win11 全 GGUF E/R 组件化离线安装器 | `IN_PROGRESS` | `scripts/build_windows_gguf_installer.bat` / `Windows Full GGUF Installer` | 已实现 Core + 固定 llama.cpp CPU + BGE F16 GGUF + Qwen3 Reranker Q8_0 的组件锁、离线 ZIP/Inno Setup、动态 loopback sidecar、临时令牌、受管 Profile、逐组件回退/恢复、哈希/provenance 与手工/Release 工作流；本地 sidecar 健康检查和平台 E/R 调用强制绕过公司代理，外部模型 Profile 的代理行为不变；Setup 临时解包后复用 staging+backup 原子发布，安装进程由命名互斥体串行化并可保守恢复唯一完整备份；当前 Win11 已完成 clean source build、真实 E/R、ZIP/Setup 首装与覆盖升级、孤儿备份恢复、卸载边界和 Full 回归；仍须代码签名并完成无开发环境 clean Win11 矩阵、上游等价 Golden 和正式 Release，不得标记为 release-ready |
| 源码双击启动前后端 | `AVAILABLE` | `scripts/start_local.bat` | 面向开发电脑，自动准备 Python/Node 开发依赖并启动 FastAPI、Vite |
| codeagent 源码一键启动 | `LIMITED` | 根目录 `start_codeagent.bat` | Windows 11 默认查找 `codeagent`，支持保存任意安装位置的 `-CliCommand` 与本地/远程 MCP 地址；自动准备受锁后端依赖、令牌和会话级 MCP，使用当前仓库 Skill，不覆盖全局 CLI/旧 Skill，不改变 Web Chat Profile。`-DryRun` 无副作用，`-Check` 不依赖 CLI 且不调用模型；自建后端随会话/自检退出，已有服务只复用。PowerShell 5.1 原生命令 stderr warning 按实际退出码处理，非零失败仍保留。追加模式 12 项黑盒验证已通过，覆盖 warning/退出码 0 与真实失败 17、模拟 Program Files 自动发现、中文/空格 `.ps1`/`.cmd` 路径、二次配置与 DPAPI 复用、真实后端 REST/MCP 握手、端点令牌绑定、仓库迁移、退出清理和既有服务/鉴权保留；客户端为模拟 CLI，当前主机未安装魔改 codeagent，不得宣称其真实模型 E2E 已测或用既有 Codex 结果替代。部署及数据边界见 `docs/agent-skill-mcp-deployment.md`。 |
| 本地环境检测 | `AVAILABLE` | `scripts/doctor_local.bat` | 检测版本、依赖、端口和服务根路径 |
| 隔离运行冒烟 | `AVAILABLE` | `scripts/runtime_smoke.bat` | 使用临时数据库和独立端口验证前后端 |
| 统一仓库验证 | `AVAILABLE` | `scripts/validate_all.bat` | Fast/Full/External 三档，保存 JSON 摘要和逐步日志 |
| 真实 GLM Chat 功能验证 | `AVAILABLE` | `scripts/validate_glm_chat_features.bat` | `--preflight-only` 无需密钥即可检查私有故障树编译/检索入口；真实验证的密钥仅从当前进程环境读取，使用临时数据库和合成日志覆盖模型网关、日志规划、20 轮故障树 Agent、最终合成、问答、修订、知识提炼和补丁建议，安全报告不保存正文、模型回复或凭据 |
| AP 频繁离线一键真实 GLM 演示快照 | `AVAILABLE` | 案例列表 → `导入 AP 离线演示` | 仓库源码内置两份纯合成 GW/AP 日志，以及此前通过 `wawapii.com` 成功完成的真实 GLM-5.2 脱敏运行快照；一键幂等创建持久案例、74 条解析事件、两侧三级智能筛查、66 个命中组/149 个逐行位置、真实两轮工具规划、27/27 故障树结论、321,453 Token/146,082 ms 综合诊断轨迹和带“文件名 - 行号”的报告预览。导入时不再次调用模型；API Key、原始 Prompt、记忆正文及私有 `故障树.md`/`日志分析.md` 正文未入包。两份 SHA-256 固定、仅绑定该合成案例的公开方法已通过空知识库自动化和真实 Codex 当前源码 E2E；Core ZIP 已重新构建并通过样本/方法哈希、导入、前后端和安装器 smoke。0.2.0 全 GGUF 新产物已构建并通过本机演示/E/R smoke；独立干净电脑矩阵仍待验收 |
| AP 频繁离线真实模型回归 | `AVAILABLE` | `scripts/seed_ap_offline_demo.bat` | 使用同一组纯合成 GW/AP 双侧日志创建可浏览案例；必须显式选择允许模型外发或仅本地模式。创建前预检本机私有方法或知识库已发布等价方法，脚本对每个后台任务设置有界超时，并校验两侧日志规划均有有效且非宽泛的 Pattern/精确关键词、两种方法角色均被读取、模型自行判定相关且在 20 轮内发起有命中的日志检索、后端以独立策略轨迹按搜索 ID 水合证据（策略调用不冒充模型成功）、故障树语义、四类跨设备假设，以及报告只显示文件行号而不泄漏内部证据 ID |
| 仓库 Harness 契约 | `AVAILABLE` | `scripts/check_repo_harness.py` | 检查文档、CI、依赖治理和 Agent API 合同漂移 |
| SQLite 备份恢复 | `AVAILABLE` | `scripts/backup_local.bat`、`restore_local.bat` | 带清单、哈希校验和回滚保留 |
| 前端受管模型权重下载 | `AVAILABLE` | 系统设置 → 本地模型权重下载 | 将原 `A.py` 的文件清单、Range 断点续传和进度能力重构为持久后台任务；支持 BGE Base 与 Qwen3 Reranker、可选显式 HTTP/HTTPS 代理和加密任务凭据。镜像返回的不可变 Commit 用于全部文件请求；同模型任务由线程与跨进程文件锁串行化，每个版本先写独立 staging generation，完整大小/SHA-256 校验后再原子切换活动指针，失败或取消继续使用上一版本。只下载到 `DATA_ROOT/models`（可由 `MODEL_DOWNLOAD_ROOT` 覆盖），不安装 Torch 或推理运行时 |
| 旧本地模型一体化安装 | `DEPRECATED` | 已删除 | 下载权重并向平台主 `.venv` 注入 Torch/Sentence Transformers 会制造 WinError 1114 等原生 DLL 风险；标准部署改用独立模型服务/API |
| 私网模型端点显式启用 | `AVAILABLE` | `scripts/enable_private_model_endpoints.bat` | 主动运行一次后幂等写入本机 Git 忽略的 `.env`，后续启动持续生效且不输出密钥 |

### 2.2 日志接入与解析

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 无后缀日志上传 | `AVAILABLE` | 上传时规范化为 `.txt`，保留原始名称 |
| Huawei collectDebuginfo 识别 | `AVAILABLE` | 内容探测并选择 Huawei Parser |
| 压缩包与文本安全检查 | `AVAILABLE` | 路径穿越、文件数、深度、单文件和展开大小限制 |
| 大日志流式解析 | `AVAILABLE` | 已覆盖 110,904 行样本规模，批量写入、稀疏行索引 |
| 日志浏览与任意行读取 | `AVAILABLE` | 支持分页、原文搜索；事件、时间线和三层筛查的每一次实际命中都可跳转，先用制品清单规范化斜杠/大小写/相对路径，再打开正确文件并高亮精确源行 |
| 事件与时间线 | `AVAILABLE` | 事件分页、级别/模块聚合、时间线数据 |
| 原子解析版本 | `AVAILABLE` | 新解析失败时保留最后一次成功结果 |
| 持久化任务 | `AVAILABLE` | 幂等键、原子领取、lease/heartbeat、超时、指数退避、dead-letter、取消、资源预算和多实例安全领取 |
| LLM 日志规划与三层证据 | `AVAILABLE` | 在案例明确授权后，后端只读工具强制列出并读取 GW/AP/通用联合诊断方法，再由模型规划 Pattern/关键词；该有界 JSON 提取阶段固定关闭 Thinking，避免推理内容耗尽输出预算或撞到企业代理超时，综合诊断仍遵循 Profile 设置；兼容 GLM JSON 根对象包裹/字面量关键词形态，过滤 `Start`、`FAILED`、`ERROR`、`offline` 等会把无关日志提升到第一层的宽泛补充词，校验失败时有界纠正一次，并区分显示代理、超时、TLS、鉴权、限流、BadRequest、连接、输出截断和无效 JSON 等安全错误码；本地扫描完整提取文本，按“LLM 相关 / 方法必查 / 其他结构化事件”分页展示。重复结果默认折叠，展开后分页列出全部精确命中并逐条跳转；每条规则同时显示从 Skill 表格“含义/说明”等列提取的语义及文档章节来源 |

### 2.3 诊断、RAG 与模型

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 规则诊断 | `AVAILABLE` | 基于事件码产生事实、假设、行动建议和限制 |
| 证据约束 LLM 诊断 | `AVAILABLE` | 原生类型化只读工具 Agent 至少两轮、最多二十轮；策略先读取全部联合方法，并把故障树流程、判断点和根因分支编译为稳定节点。每个节点带跨方法 Pattern/检索词入口，必须绑定检查和实际只读检索，并得到“证据支持 / 已排除 / 证据不足”终态后规划才通过；模型每轮最多规划四次工具调用，首个有结果的模型日志搜索可追加一次后端策略证据读取，该读取计入总工具预算但不会挤掉模型第 4 个调用。工具参数和 GW/AP 双侧方法、Schema、方法/节点/Pattern/evidence ID 均在执行前受门禁约束，单轮最多纠正两次。结构化工具规划固定关闭 Thinking，最终综合与交互问答仍遵循 Profile 的 Thinking 设置，避免每轮 JSON 控制调用耗尽长思考窗口。模型漏字段或错误引用时只允许保守补齐检查/只读检索并把无案例证据的结论降为证据不足；最终由确定性方法语义与实际日志交叉核验全部节点，无关的当前案例证据也不能维持节点的支持或排除结论。知识、方法和记忆只能指导搜索，不能充当本案例事实证据。本地回退直接使用已解析 GW/AP 事件；心跳超时必须满足 `curTime - lastEventTime > iAdvrTimeOut`，端口恢复或心跳成功不能作为故障证据；UDN 与 AP MAC 的相等性判断只在本机原文中执行，该布尔派生证据不暴露原值，原始日志是否发送模型仍受案例出站授权与端点策略控制。生产循环累计供应商实际 usage、工具输出估算、墙钟时间、只读工具总数和连续无进展轮次；每轮请求前清空供应商观测快照，调用前失败不会重复计入上一轮 Token。达到边界时以 `TOKEN_BUDGET`、`TIME_BUDGET`、`TOOL_CALL_BUDGET` 或 `NO_PROGRESS` 显式停止并回退。Token-aware Context Governor 按模型窗口为方法、日志、故障树、历史和工具观察分区，被压缩正文只进入本次运行内存 Spill Store，可经 `get_evidence` 分段续读；句柄不是事实证据且不持久化日志正文。前端可查看预算、上下文占用、压缩和分区指标。证据 ID 只作内部关联与校验，诊断、问答、轨迹和报告统一显示“文件 - 行号”或文档标题，且“已确认事实”固定放在综合诊断和报告末尾；分析 API 的模型配置快照不返回 Base URL 或代理端点 |
| Claude Code / Codex Skill + MCP 诊断 | `IN_PROGRESS` | 方案 A 保留现有 Web `/api/v1` 路径，新增 Streamable HTTP `/mcp`，由当前 CLI 会话模型拥有规划与综合推理，后端只提供案例权限、解析/检索、证据和故障树门禁、持久 Host 会话及不可变分析回写。当前 17 个 `debug_*` 业务工具及权限/草稿门禁已通过完整回归；真实 Codex CLI 已在当前源码完成 Markdown Host 模型归类和“AP 频繁离线”综合诊断。最新诊断会话 4 轮完成 27/27 终态（20 支持/3 排除/4 证据不足）、119 条证据和 HTML 报告，后端生成式调用为零；此前全新数据库/空知识库/固定公开方法的 6 轮回归也继续保留。整体仍标记 `IN_PROGRESS` 仅因为当前主机未安装 Claude CLI，且真实远程 HTTPS/RBAC、真实 CLI 负路径和独立干净电脑矩阵尚未验收；这不表示 Codex 路径未完成。契约见 `workflow/mcp-tools.yaml`，部署边界见 `docs/agent-skill-mcp-deployment.md`。 |
| OpenCode Skill + MCP 客户端 | `PLANNED` | 保留为 Windows 11 辅助客户端方向；当前安装器和配置流程只覆盖 Claude Code/Codex，且按本轮要求未消耗 OpenCode 额度进行真实测试，因此不得宣称已兼容或已通过 E2E |
| 模型网关 | `AVAILABLE` | 前端管理并切换 Chat、Embedding、Reranker 配置；开发/本地环境兼容 HTTP/HTTPS，HTTP 不再强制要求端点白名单，私网地址可由本机 `MODEL_ALLOW_PRIVATE_ENDPOINTS` 持久开关放行，回环/危险系统地址和生产白名单约束仍保留；Chat API 支持逐 Profile 加密代理，空值直连，代理启用时保留证书链/主机名校验并跳过吊销检查；Thinking 支持“跟随模型默认 / 强制开启 / 强制关闭”，GLM-5.1/5.2 关闭时显式发送 `thinking.type=disabled`；可配置 `max_tokens`、真实上下文窗口及输出预留，新 Profile 默认 300 秒超时 |
| Embedding | `LIMITED` | 内置 Hashing 和兼容 API 为标准能力；包内 `llama_cpp_local` BGE F16 sidecar、查询前缀隔离和有限值/维度/L2 门禁已实现并通过当前 Win11 最小真实语义冒烟，但随包安装交付仍为 `IN_PROGRESS`；进程内 Sentence Transformers 仅为既有高级源码安装保留，不进入标准包 |
| Reranker | `LIMITED` | Disabled 和 Qwen Rerank API 为标准能力；包内 `llama_cpp_local` Qwen3 Q8_0 sidecar 已实现并通过当前 Win11 `/v1/rerank` 最小真实排序冒烟，但随包安装交付仍为 `IN_PROGRESS`；进程内 CrossEncoder 仅为既有高级源码安装保留，不进入标准包 |
| 混合检索 | `AVAILABLE` | 有界 BM25 候选、Dense top-K、加权 RRF、模块均衡和单次 Reranker |
| Qdrant 镜像 | `AVAILABLE` | 数据库向量为权威存储，按 generation 镜像并执行有界 top-K |
| 检索评测 | `AVAILABLE` | 固定 query/预期证据/根因和模块，输出 Recall@K、Precision@K、MRR、NDCG@K、Root Cause Top-K |
| Agent 场景矩阵 | `LIMITED` | 36 个完全合成案例已作为 CI 分布契约覆盖设备、日志规模、证据质量、知识/记忆污染、Planner 故障和停止类别，并校验引用/证据/工具/冗余/上下文指标门槛字段；目前只有核心 Golden 案例为全链路可执行样本，其余场景仍需逐步升级为 Fake/真实模型可执行回归 |
| 案例对话与诊断修订 | `AVAILABLE` | 可异步证据问答，也可要求模型生成综合诊断与报告修订草稿；前端预览后人工确认才创建新版不可变诊断，旧诊断/报告不覆盖；任务可恢复轮询、取消并查看轨迹 |

### 2.4 分层知识库

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 树形分类 | `AVAILABLE` | 诊断规则、历史问题、参考资料，可增加和修改分类 |
| 文档 CRUD | `AVAILABLE` | 新增、后台上传、查看、修改和删除；发布/归档必须走审核状态机 |
| 知识设备适用范围 | `AVAILABLE` | 支持 GW、AP、通用和其他；通用知识可被 GW/AP 检索，旧 `OTHER` 数据保持兼容 |
| Markdown 分块 | `AVAILABLE` | 按标题和段落切分，超长单段继续分片并限制 chunk 大小 |
| 自动向量索引 | `AVAILABLE` | 文档变更后更新活动 generation；全量重建失败保留上一版 |
| 故障案例结构化 Markdown | `AVAILABLE` | 错误形式、日志分析、错误定位、解决方案、验证结果 |
| 大模型文件夹案例提炼 | `AVAILABLE` | 文本、HTML、DOCX 和文本层 PDF 在本地提取；脱敏限长后生成带行号引用的 Markdown，支持多轮对话、人工编辑、版本恢复和确认后入草稿 |
| Markdown 知识智能归类 | `AVAILABLE` | 网页“AI 智能导入 MD”与 Skill 的 CLI 上传助手均支持一次提交 1–20 个 `.md`/`.markdown`；服务按文件建立独立持久任务和独立非活动 `DRAFT`。Web 使用选择或当前激活的平台 Chat Profile 对脱敏、限长片段分类；CLI 先经 REST multipart 上传，再由当前 Claude Code/Codex 会话模型读取 `debug_get_knowledge_routing_context` 并提交 `debug_apply_knowledge_routing`，该 Host 路径后端生成式调用为零。活动叶子分类、lock version、正文哈希、管理员权限和禁止自动发布均受回归覆盖；浏览器多文件 E2E 与真实 Codex batch `KRBATCH-28b4b098d8984ae2` 已分别通过，后者正确归入故障树/协议诊断规则且两文档均保持 DRAFT/inactive。Claude CLI 当前主机未安装，OpenCode 按用户要求未测试。 |
| 错误分析 Skill | `AVAILABLE` | 以 Markdown 保存可复用的错误分析技能 |
| 分析方法提炼 | `AVAILABLE` | 从故障案例或错误分析 Skill 提取输入信号、步骤、决策点和验证方法 |
| 知识版本与审核 | `AVAILABLE` | DRAFT/IN_REVIEW/ACTIVE/REJECTED/ARCHIVED、不可变快照、回滚和数据库乐观锁 |
| 人工反馈闭环 | `AVAILABLE` | 诊断反馈先审核，再生成待二次审核的知识草稿；不会自动写入记忆或线上知识 |
| 领域知识图谱 | `LIMITED` | 从已发布知识确定性提取症状/根因/方案等实体关系，generation 原子切换；同义词与冲突消解仍有限 |

### 2.5 代码与工程协同

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| 代码仓归档上传 | `AVAILABLE` | 上传后返回后台任务，ZIP/TAR/Git Bundle 解压不会阻塞 API 事件循环 |
| C/C++ 符号索引 | `AVAILABLE` | 函数、宏、结构体、签名、源码范围和调用名称 |
| 静态分析 | `AVAILABLE` | cppcheck、clang-tidy，工具不可用时明确报告 |
| 补丁建议 | `AVAILABLE` | 只生成候选 unified diff，不自动覆盖代码 |
| VS Code 扩展 | `AVAILABLE` | 上传日志/工作区、关联案例、询问选中代码 |
| 代码语义图谱 | `LIMITED` | 建模四类关系；generation 旁路构建并原子切换，解析仍是多语言静态启发式 |
| Commit 意图图谱 | `LIMITED` | query → commit → changed file → 当前 HEAD 代码符号；最多索引 2,000 个 Commit |
| Git Bundle 导入 | `AVAILABLE` | 在保留完整历史的情况下安全导入仓库 |

### 2.6 安全、权限与部署

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| RBAC | `AVAILABLE` | ADMIN、ENGINEER、VIEWER 和案例成员权限 |
| 个人令牌 | `AVAILABLE` | 哈希保存、到期、撤销和最后使用时间 |
| API Key 加密 | `AVAILABLE` | 模型密钥加密保存且不通过查询接口回显 |
| 审计 | `AVAILABLE` | 身份、管理操作、模型出站元数据，避免记录正文和密钥 |
| 健康检查 | `AVAILABLE` | liveness、readiness、管理员系统状态 |
| Docker/Compose | `AVAILABLE` | PostgreSQL、Qdrant 和前后端镜像 |
| GitHub CI | `AVAILABLE` | Linux/Windows 后端、前端、扩展、依赖审计、外部服务、Docker；每个 job 有硬超时 |
| Golden 质量门禁 | `AVAILABLE` | 日志、提炼、Code/Commit Graph、Memory、RAG、Agentic Search 与有界执行九类固定评测 |
| 浏览器 E2E | `AVAILABLE` | Playwright + Fake OpenAI 服务固化混合文档上传、预览、生成、纠错、确认 DRAFT 和轨迹查看，控制台错误即失败 |
| Agent 运行轨迹 | `AVAILABLE` | 运行中增量记录摘要哈希、方法证据 ID、规划轮次、模型/Prompt、输入/输出/总 tokens、耗时、重试、停止和审批；供应商未返回 total 时由输入与输出求和，校验失败和最终合成也计入；案例页可实时查看，管理员可安全只读重放，正文不写入轨迹 |
| 覆盖率与架构门禁 | `AVAILABLE` | 后端 75% 行覆盖率、文件行数/圈复杂度 ratchet、导入边界、运行时 OpenAPI 合同漂移检查 |

## 3. 新一代认知检索能力

### 3.1 代码图谱

- 状态：`LIMITED`
- 定位：语义升维，从语法符号跨越到程序关系。
- 必须建模：
  - `CALLS`：函数/方法调用；
  - `REFERENCES`：变量、类型、宏、函数和文件内引用；
  - `INHERITS`：类或结构的继承；
  - `IMPLEMENTS`：接口实现、声明到定义或可确认的覆写实现。
- 输出：
  - 图谱节点、边和统计；
  - 关键词到起始符号的语义定位；
  - 1～3 跳邻居扩展和可解释路径；
  - 与 Commit 文件变更的连接。
- 当前边界：无编译数据库时使用安全的静态语法启发式；后续可接入
  Clang/Language Server 提升跨宏、模板和条件编译精度。

### 3.2 Commit 图谱

- 状态：`LIMITED`
- 定位：意图追溯。
- 核心路径：`用户 query → Commit 意图 → 变更文件 → 代码符号 → 代码关系`。
- 数据：
  - commit hash、父提交、作者时间、主题和正文；
  - 新增/修改/删除/重命名文件；
  - 文件到当前代码符号的关联；
  - 可解释的匹配词和多跳路径。
- 输入限制：
  - 普通 ZIP/TAR 通常不包含 `.git`，只能建立代码图谱；
  - 要建立 Commit 图谱，应上传包含 `.git` 的安全归档或项目支持的 Git Bundle。

### 3.3 记忆系统

- 状态：`AVAILABLE`
- 定位：经验复用。
- 三类记忆：
  - `EPISODIC`：某次案例、上下文、结论和结果；
  - `PROCEDURAL`：可复用分析顺序、工具使用和验证步骤；
  - `FAILURE`：失败尝试、缺失信息、错误原因和避免方式。
- 生命周期：
  1. 每轮诊断任务结束或失败时提炼；
  2. 使用证据和来源 ID 建立可审计记忆；
  3. 指纹去重，重复经验增加出现次数；
  4. 下次检索按相似度、置信度、结果和复用次数召回；
  5. 记录复用，不把“曾经成功”误判为“当前必然正确”。

### 3.4 Agentic Search

- 状态：`AVAILABLE`
- 定位：认知中枢。
- 可调度模块：
  - 分层知识库混合检索；
  - 领域知识图谱 / GraphRAG；
  - 代码图谱；
  - Commit 图谱；
  - 三类记忆。
- 检索组件：
  - BM25；
  - Dense Embedding；
  - Reranker；
  - 图关系多跳扩展；
  - 跨模块结果融合。
- 性能与一致性：
  - 知识模块在 Agentic Search 中只生成原始候选，Dense/Reranker 不重复调用；
  - 不同模块按加权 RRF 和候选配额进入统一语义排序；
  - Qdrant 只取有界 top-K；无 Qdrant 时只扫描紧凑向量行，不加载全部文档正文。
- 编排原则：
  1. 先识别 query 的日志、代码、历史、回归、处理步骤等意图；
  2. 动态选择模块，不对每次请求盲目执行所有昂贵步骤；
  3. 第一跳找候选，后续跳沿代码边或 commit/file/symbol 边扩展；
  4. 使用融合和 Reranker 统一排序；
  5. 返回执行计划、各阶段耗时/候选数、最终证据和可解释路径。
- 有界执行基础：类型化 Tool Registry、角色白名单、写操作审批、步骤/跳数/tokens/成本/
  墙钟预算、重试熔断取消和确定性回退已经可用并进入 Golden 门禁；生产检索仍默认使用
  当前确定性 Planner，不把测试中的循环执行器描述成已默认接管线上查询。

### 3.5 领域知识图谱与 GraphRAG

- 状态：`LIMITED`
- 数据源：只使用 `ACTIVE` 且 `active=true` 的已审核知识；
- 实体：症状、设备、型号、固件、模块、日志模式、事件码、根因、诊断步骤、方案、验证和范围；
- 关系：症状、日志、事件码、根因、定位步骤、解决、验证和适用范围；
- 一致性：新 generation 旁路构建，发布前校验知识输入签名并 CAS 切换；
- 失败语义：构建失败或构建期间知识变化时保留上一活动 generation；
- 当前限制：确定性 Markdown/元数据抽取，不等同于完成实体消歧的企业级知识图谱。

### 3.6 质量治理与反馈

- 状态：`AVAILABLE`
- 知识生命周期：草稿、待审核、已发布、已驳回、已归档；
- 编辑和历史恢复均创建新草稿版本，不覆盖不可变历史；
- `lock_version` 同时由前端预期值和数据库 UPDATE 条件校验；
- 检索评测保存模型/算法快照并强制关闭记忆写入；
- 人工反馈必须经过“反馈审核 → 生成知识草稿 → 知识审核”两道门。

## 4. 依赖与冲突约束

| 约束 | 决策 |
| --- | --- |
| 新检索与现有 RAG 重复 | 复用同一 Embedding/Reranker Profile；现有混合检索作为 Agentic Search 的知识模块 |
| 图谱结果没有 evidence ID | 每个节点、关系、Commit、记忆都必须使用稳定数据库 ID |
| Commit 历史与普通代码压缩包冲突 | 代码图谱可独立建立；Commit 图谱明确显示 `UNAVAILABLE`，不伪造历史 |
| 记忆可能放大错误结论 | 保存置信度、结果、来源和失败类型；召回时只作证据候选 |
| 未审核知识污染检索 | 新增、导入、编辑和回滚均为草稿；查询同时校验 `active` 与 `review_status=ACTIVE` |
| 图谱构建期间知识变化 | 发布前重算输入签名并校验构建标记；变化时丢弃新 generation，保留旧版 |
| 评测污染在线记忆 | 评测固定 `record_memory=false`，不新增、不强化、不增加复用次数 |
| 人工反馈自动学习敏感内容 | 反馈审核通过后也只能生成知识草稿，仍需第二次审核发布 |
| 模型生成案例污染知识库 | 提炼会话与知识文档隔离；章节和来源行号校验通过并人工确认后才创建 `DRAFT`，仍需原有审核发布 |
| 知识归类越权或误发布 | Markdown 路由要求管理员权限，只能选择活动叶子分类；每个文件只创建或更新一个非活动 `DRAFT`，后续仍须人工提交审核并批准，模型决策不能直接变成 `ACTIVE` |
| 文件夹原文直接外发 | 原始文件只进本地 Storage；DOCX/PDF/HTML 也先在本地提取，管理员明确授权后仅发送脱敏、限长、带行号的文本证据 |
| 多窗口同时纠错覆盖内容 | 每轮携带 `expected_draft_version`，条件更新失败返回冲突；所有人工/模型修改保存不可变版本 |
| 不同案例之间的数据隔离 | 代码仓、Commit 和情景记忆继承案例访问控制；全局程序记忆只保存脱敏方法 |
| 外部模型数据出站 | 沿用模型网关、端点校验和内容无关审计；无 API 时必须有本地确定性回退 |
| CLI 模型与后端模型职责冲突 | Web 诊断与 Markdown 归类继续使用平台 Profile；MCP `host_cli` 诊断及知识归类由 Claude Code/Codex 当前会话模型推理，后端该路径生成式模型调用固定为零。Embedding/Reranker 仍属于后端有界检索，不得把 MCP transport session 当作持久诊断状态。 |
| 私有筛查方法被误提交或复制 | 根目录可选 `故障树.md`、`日志分析.md` 被 Git 明确忽略，只在运行时读取；推荐把需长期治理的方法发布到知识库；分析快照仅保存方法 ID、版本和哈希，不复制方法正文 |
| 日志正文外发与关键词遗漏 | LLM 只接收案例描述和经授权的方法文档，不接收完整原始日志；模型规划关键词后由本地逐行扫描，且所有方法关键词无论是否被模型选中都必须检查 |
| SQLite 与 PostgreSQL 差异 | 所有新表通过 Alembic 建立，查询不依赖数据库专有 JSON 运算 |
| 大仓库性能 | 上传与解压分离；图谱使用 generation 分批旁路构建；查询先做数据库候选过滤 |
| 源码安全 | 图谱和补丁只读；不自动修改、执行或编译上传代码 |
| 归档安全 | 保持路径穿越、文件数、深度和大小限制；Git 导入禁用交互凭据和 hooks |
| 功能入口冲突 | “知识库”管理事实和方法；“认知检索”负责跨知识/代码/Commit/记忆查询 |
| 删除一致性 | 案例、仓库、文档删除时派生图谱、Commit、记忆和索引必须级联或解除关联 |

## 5. 本轮验收清单

- [x] 故障案例 Markdown 模板及四个必需章节检查；
- [x] 上传/编辑故障案例后保存结构化字段；
- [x] 从故障案例或错误分析 Skill 一键提炼分析方法；
- [x] 代码关系表、稳定 evidence ID、图谱构建、图谱查询和多跳路径；
- [x] Commit 历史、父子关系、文件变更及 query → commit → code 路径；
- [x] EPISODIC / PROCEDURAL / FAILURE 记忆生成、去重、查询和复用计数；
- [x] Agentic Search 动态计划、BM25、Dense、Reranker、图扩展和 RRF 融合；
- [x] 知识不可变版本、审核状态机、数据库乐观锁和新草稿回滚；
- [x] 已审核知识实体/关系图谱、构建期输入校验、原子 generation 和 GraphRAG；
- [x] 检索评测数据集、后台运行、配置快照和五项基础指标；
- [x] 人工诊断反馈审核，以及“通过反馈 → 待二次审核知识草稿”的安全闭环；
- [x] 前端知识库入口和独立“认知检索”页面；
- [x] 前端“质量与治理”页面，以及知识审核和版本历史操作；
- [x] Alembic `0009` 知识治理/领域图谱/评测迁移、幂等升级和 SQLite 兼容测试；
- [x] RBAC、案例隔离、删除级联、备份恢复和审计回归；
- [x] 更新 README、架构文档、API 使用说明和本总账；
- [x] 后端、前端、扩展、依赖审计、Win11 启动闭环和真实页面验证。

本轮本地验证基线（2026-07-30）：

- 后端：Ruff、compileall、`107 passed, 1 skipped`；跳过项是需要外部
  PostgreSQL/Qdrant 的服务集成测试；
- 前端：干净 `npm ci` 后通过 `vue-tsc` 和 Vite production build；
- VS Code 扩展：干净 `npm ci` 后通过 TypeScript 编译，Archiver 8 依赖链审计为 0；
- 安全审计：pip-audit、前端 npm audit、扩展 npm audit 均无已知漏洞；
- Windows 运行：隔离 SQLite、独立端口的前后端/API 闭环通过；
- 页面：知识草稿→待审核→发布→版本历史、领域图谱原子重建/检索、评测集、
  人工反馈、认知检索和模型状态均已实测，浏览器控制台无错误。

外部 PostgreSQL/Qdrant 和 Linux/Windows 矩阵由每次 push 的 GitHub Actions
继续验证；本机未安装 Docker，因此不把未执行的外部服务项伪装成本地通过。

本轮依赖可复现修复（2026-08-05）：

- [x] 将存在已知漏洞的 `cryptography 49.0.0` 升级并锁定为 `50.0.0`；
- [x] 增加跨 Python/Windows/Linux 的 `backend/uv.lock`，并导出兼容现有 pip
  启动链的 `backend/constraints.lock`；
- [x] Win11 bootstrap/start/doctor、Linux 启动、CI 和 Docker
  统一服从锁定约束；
- [x] 增加 `scripts\refresh_python_lock.bat` 的更新与只读检查模式；
- [x] 修复新进入审计库的前端 PostCSS 和扩展 brace-expansion 告警；
- [x] Python 3.14 安装、锁文件一致性、Ruff、compileall、`119 passed, 1 skipped`、
  前端构建、扩展编译、三类依赖审计、Win11 bootstrap/doctor/runtime smoke 均通过。

本轮 Win11 交付与模型隔离（2026-08-26）：

- [x] FastAPI 可选择同源托管编译后的 Vue，并为客户端路由提供受控 `index.html` 回退；
- [x] 构建自包含 Windows x64 便携包，目标电脑不再安装或配置 pip/npm；
- [x] 便携包固定排除 Torch、Sentence Transformers 和模型权重，并用构建门禁验证；
- [x] 通过 `pythonXY._pth`、`-s` 与包清单隔离目标电脑全局/用户 Python 包；复用旧数据库时停用活动的进程内模型并安全回退；
- [x] 便携启动、自检、SQLite readiness、首页、Vue 深层路由和 API liveness 冒烟；
- [x] 删除失效的一体化模型安装、镜像检测、下载与进程内加载验证脚本；
- [x] 将本机 `A.py` 的安全子集重构为前端可操作的受管权重下载任务；支持显式代理但不复制硬编码路径、全局 `verify=False` 或运行时安装；
- [x] `A.py` 和根目录 RAR 加入 Git 忽略，内网地址与本机制品不会进入仓库；
- [x] GitHub 手工构建 Artifact，`v*` 标签生成带 SHA-256 的 Release。

本轮全 GGUF E/R 离线交付（2026-09-02，`IN_PROGRESS`）：

- [x] 固定 llama.cpp b10729、BGE Base Chinese v1.5 F16 GGUF 和 Qwen3
  Reranker 0.6B Q8_0 的来源 revision、下载大小、SHA-256、许可证与转换配方；
- [x] 模型权重、构建缓存和安装器产物保持 Git 忽略；组件锁必须重新绑定固定资产清单，
  并完整锁定每个 CPU runtime DLL；
- [x] 从微软官方不可变 VSIX 精确提取并逐文件锁定 VC143 x64 release CRT；拒绝
  `debug_nonredist`，构建时校验 Microsoft Authenticode，运行时确认核心 CRT 从包内
  `runtime/llama` 实际加载，目标机无需预装 VC++ Redistributable；微软许可仍由发布者遵守；
- [x] 平台 Python 继续排除 Torch/Sentence Transformers；启动器用两个动态 loopback
  llama.cpp sidecar、临时 key 文件、Win11 Job Object 和逐组件回退提供受管 Profile；
- [x] 本地 sidecar 健康检查使用禁代理 HTTP opener，子进程补齐
  `NO_PROXY/no_proxy=127.0.0.1,localhost`；平台只对受管 GGUF E/R 强制
  `trust_env=False`，不改变外部 Chat/Embedding/Reranker Profile 的代理行为；
- [x] 当前 Win11 真实运行时语义冒烟：BGE 返回 3 个有限、L2 归一化的 768 维向量，
  诊断相关相似度 `0.5609 > 0.1655`；Qwen `/v1/rerank` 返回
  `0.9997 > 0.0001`；
- [x] 增加固定 runner/Action/Python/Node/Inno Setup 的手工或 Release 构建工作流，输出
  ZIP、Setup.exe、哈希和 provenance；Release 事件在质量状态未提升时严格阻断；
- [x] 组装后实验 ZIP 通过 self-check、临时全新 data root、受管 Profile 自动激活和
  真实平台 API；Embedding 返回 `2 x 768`，Reranker 首项为 `index=0`；ZIP 为
  934,065,370 bytes，SHA-256
  `3cbb5fd72f80bfeed429fd05abf2cdb252535abb266ca62df3839075fb5b15f6`；
- [x] 使用固定 Inno Setup 6.7.1 portable compiler 在 213.469 秒内生成单文件安装器；
  未签名候选为 899,941,808 bytes，SHA-256
  `08499a54aabc7bf7e7e4f0e5b0256b1b79d3d60126e70bcfd7341b62c669fb1a`，
  `FileVersion=0.1.0`、`ProductName=GWAP Debug Platform`；用于获取编译器的固定 GitHub
  Release 下载器 Authenticode 状态为 `Valid`，签名者为 `Pyrsys B.V.`；
- [x] 修复 Inno 直接合并 `{app}` 的升级风险：完整 payload 仅解到 Setup 私有临时树，
  `package-manifest.json` 最后触发 `install_local.ps1` 的 staging+backup 原子发布；非零退出
  在 `[Files]` 阶段使安装失败，快捷方式由 Inno 管理，卸载只删除精确受管 app tree；
  Inno 6.7.1 小型合同包已成功编译；
- [x] 原子发布增加 PowerShell 5.1 命名互斥体、120 秒清晰超时和 abandoned mutex 恢复；
  目标缺失时仅恢复唯一且通过 manifest/runtime 基本检查的受管 backup，多个候选不猜测；
- [x] 用此前第三方端点成功运行的真实 GLM-5.2 脱敏快照重建并复验 Core、完整 GGUF staging
  与真实 Setup：固定 2 个制品/74 条事件、66 个命中组/149 个逐行位置、源行跳转、两轮规划、
  27/27 故障树结论、321,453 Token/146,082 ms 轨迹和诊断报告；ZIP 为 937,032,215 bytes，
  SHA-256 `3fd4ab0699ea8778f01816133f98b728391d04e064ceed9bed87e129200f028e`；Setup 为
  902,853,773 bytes，SHA-256
  `d6736ce0d2943b378f253d92e4c75b643cdf2453802664e8dadeb3783d23ddaa`；实际安装树已复验本地
  BGE/Qwen、受管 Profile、前后端 API、演示导入、真实 usage/诊断和源行跳转，并完成精确卸载；
- [x] 当前 Win11 已真实验证 ZIP 首装/覆盖升级、Setup 首装/覆盖升级、目标缺失时恢复唯一
  backup 后继续升级、安装后双 GGUF 平台冒烟，以及卸载仅删除 app tree/卸载器；
- [ ] 完成包内 Profile 的知识全量索引、混合 RAG 质量与逐组件故障注入验证；
- [ ] 对项目 `Setup.exe` 做发布代码签名并验证签名信任；当前候选 Authenticode 状态为
  `NotSigned`；
- [ ] 在无开发环境/无缓存的全新 Win11 上验证安装、冷启动、升级、回退和卸载；
- [ ] 完成 BGE 上游余弦/Recall 与 Qwen 上游排序/NDCG Golden 等价门禁，记录正式 BGE
  构建哈希并提升 `experimental_unverified` 状态；
- [ ] GitHub 手工构建与正式 Release 工作流实际全绿后，才把该交付形态标为
  `AVAILABLE`。详见
  [Windows 11 全 GGUF E/R 离线安装器](docs/windows-offline-gguf-installer.md)。

## 6. 后续候选

| 能力 | 状态 | 说明 |
| --- | --- | --- |
| Clang AST/compile_commands 深度图谱 | `PLANNED` | 提升 C/C++ 宏、模板、重载和条件编译解析精度 |
| 增量代码/Commit 索引 | `PLANNED` | 按 commit 增量更新，避免全仓重建 |
| 图数据库后端 | `PLANNED` | 数据规模达到阈值后评估 Neo4j/AGE；当前关系表保持可迁移 |
| 记忆衰减与人工审核 | `PLANNED` | 过期、冲突记忆提示和人工批准 |
| 知识差异与实体合并 UI | `PLANNED` | 可视化版本 diff、同义实体合并、冲突关系审核 |
| 独立知识审核角色 | `PLANNED` | 从 ADMIN 中拆出知识维护者和审核者，支持职责分离 |
| OpenTelemetry 桥接 | `PLANNED` | 将现有结构化 Agent 轨迹导出到可选本地观测栈 |
| Agent 任务控制平面 | `PLANNED` | 跨任务依赖 DAG、人工审批队列和通用停滞检测 |
| Review 反馈沉淀 | `PLANNED` | 把人工 Review 安全分类为规则、测试、文档或评测样本，写入前必须审批 |

## 7. 大模型文件夹案例提炼迭代（2026-08-03）

定位：把已有故障材料转换成“可复核、可纠错、可追溯、不会自动发布”的知识案例，
不是让大模型直接向线上知识库写入自由文本。

- [x] 浏览器选择整个文件夹，保留相对路径并流式保存到本地 Storage；
- [x] 文件数、单文件/总大小、路径穿越、Windows 保留名和目录深度限制；
- [x] 文本及无后缀文本探测，HTML 可见正文、DOCX 段落/表格和 PDF 文本层本地提取；
- [x] 旧式 DOC、扫描 PDF、空文件和其他二进制保留并明确标记原因；
- [x] 长文件头部、关键词行和尾部抽样，全局提示长度限制与敏感信息掩码；
- [x] OpenAI-Compatible Chat 模型选择、模型快照和显式数据出站授权；
- [x] 强制结构化案例章节与 `[SRC-xxxx:Lx-Ly]` 来源/行号校验；
- [x] 模型对话纠错、人工 Markdown 修改、乐观并发冲突和不可变版本恢复；
- [x] 人工确认后只创建 `DRAFT + active=false` 知识文档，继续复用审核发布流程；
- [x] `0010` 会话/来源/版本/消息迁移，来源文件随备份保存；
- [x] 管理员 API、前端“AI 案例提炼”工作台和完整使用文档；
- [x] 完整后端回归、前端构建、Win11 运行冒烟和真实浏览器流程验证。

本功能本地验证基线（2026-08-03）：Ruff、compileall、`pip check`、`119 passed,
1 skipped`、Vue TypeScript/Vite production build、VS Code 扩展编译和隔离 Win11
运行冒烟均通过；真实浏览器已完成 TXT、MD、HTML、DOCX、PDF 混合文件夹上传、三种文档
正文预览、模型初稿、对话修订及“确认后只创建知识草稿”的完整流程，控制台无错误。

当前边界：Word 支持 `.docx`，旧式 `.doc` 需转换；PDF 只支持已有文本层，扫描件和图片
尚无 OCR；HTML 提取结构化可见文本但不执行浏览器脚本或远程资源。案例生成只支持 API
Chat 模型，本地 BGE Embedding 和 Qwen3 Reranker 不具备生成能力，本地 Chat 运行器仍是
后续候选。详见 [大模型文件夹案例提炼与人工校正](docs/llm-knowledge-curation.md)。

## 8. Harness Engineering P0（2026-08-05）

- [x] 根目录 Agent 项目地图、业务/工程总账和专题文档索引；
- [x] `Fast`、`Full`、`External` 统一 Win11 验证入口及可移植 JSON/日志产物；
- [x] 文档链接、总账交叉引用、CI 触发、Dependabot 覆盖和 Workflow API 的自动契约检查；
- [x] 分支/PR CI 去重，同时保留面向 `main` 的 PR、`main` push 和手工触发；
- [x] `.gitattributes`、PR 模板、CODEOWNERS 和 Python/npm/Actions/Docker Dependabot；
- [x] Workflow allowlist 补齐日志上传/解析/报告和 AI 案例提炼 API，并声明证据与人工 DRAFT 门禁；
- [x] GitHub `main` ruleset；本次 PR 的 9 项新 CI 全绿后启用，必须 PR、最新 CI，
  并禁止删除和 force push；单维护者场景不强制他人批准。

本轮本地 `Full` 验证：15 个步骤全部通过，包含 Harness 11 项契约、`122 passed,
1 skipped`、前端生产构建、扩展编译、三类依赖审计、Doctor 和隔离运行冒烟。
本机未安装 Docker，`External` 中的 PostgreSQL/Qdrant 与镜像构建继续由 GitHub CI 验证。

P1 Golden Dataset、Fake Model、Playwright E2E、质量评测、执行轨迹和架构门禁，以及已完成的
P2 有界 Agent、任务隔离、多实例 lease 和文档进程沙箱见
[HARNESS_ENGINEERING.md](HARNESS_ENGINEERING.md)。剩余任务 DAG 和 Review 自动沉淀仍明确标记为
`PLANNED`，不得描述为已可用。

## 9. Harness Engineering P1 与 P2 基础（2026-08-05）

- [x] 合成 TXT/HTML/DOCX/PDF 与无后缀日志 Golden Corpus，固定二进制哈希；
- [x] Fake OpenAI-compatible Chat/Embedding/Reranker，含超时、429、坏 JSON 和中断注入；
- [x] Playwright 完整提炼闭环与浏览器控制台零错误门禁；
- [x] 九类 Golden 评测和耗时/证据/停止原因 CI 阻断；
- [x] 后端 75% 覆盖率门禁；建立门禁时完整回归实测 77%；
- [x] `AgentRun + AgentTraceEvent`、前端轨迹查看器和不写记忆的脱敏只读重放；
- [x] API、知识提炼、Agentic Search 和前端大文件模块化，并用行数/复杂度/导入边界防回退；
- [x] Workflow 最小契约与 FastAPI 运行时 OpenAPI 双重漂移检查；
- [x] 类型化有界 Agent、角色/审批、预算、重试/熔断/取消和确定性回退；
- [x] 独立 task worktree/端口/数据库/Storage/日志；
- [x] 后台任务 lease/heartbeat/dead-letter/幂等/资源预算；
- [x] Win11 Job Object 与 Linux rlimit 约束的不可信文档独立进程抽取。

详细命令、阈值和隐私边界见
[质量评测、Agent 轨迹与有界执行](docs/quality-harness-and-agent-runtime.md)。

## 10. LLM 日志规划、综合诊断与异步问答（2026-08-11）

- [x] 案例级模型出站授权；未授权时真实 Chat Profile 不发送问题描述或方法文档；
- [x] 自动加载全部适用的已发布故障树、日志规则、分析方法、历史案例，以及本机可选私有方法文件；
- [x] 日志规划先通过类型化只读工具建立方法目录并读取全部方法正文；模型不再用容易误判的自报已读清单作为证明，只接受已编译 Pattern ID 和字面量追加关键词；
- [x] Chat 结构化调用启用 OpenAI-Compatible JSON object 模式；兼容 GLM 将结构名包成根字段、字符串关键词与旧 `plan` 字段，证据门禁失败时携带错误有界纠正一次；
- [x] Markdown 方法规则剥离表格/强调/代码装饰，支持 `%u`、`[X]`、独立 X/Y/Z 等模板变量，避免“已规划但正则永不命中”；
- [x] LLM 相关候选按模型相关度顺序限制为 60 条、补充关键词去重后限制为 30 条；未进入第一层的方法规则仍全部进入第二层扫描；
- [x] 完整提取文本在本机逐行扫描，命中没有结构化事件的命令输出也保留文件与原始行号；
- [x] 模型相关、方法必查、其他结构化事件三层分页展示；重复日志保留聚类摘要，同时持久化全部实际命中，默认折叠并可分页展开、逐条精确跳转；
- [x] 日志 Skill 的表格“含义/说明/判断”等语义随 Pattern 编译并展示；未单独说明的规则明确标为所属文档章节用途，不伪造含义；
- [x] GW/AP 联合诊断不按单一案例设备排除另一侧知识；上传日志可标记 GW/AP 与主从角色，规划与最终证据保留来源；
- [x] 综合诊断使用原生类型化只读工具 Agent，至少两轮、最多二十轮；每轮最多四次模型规划调用，首个非空日志搜索的独立策略证据读取计入总预算但不占模型调用槽位；支持方法目录/全文、混合知识、全部持久化日志筛查证据和 evidence ID 读取，重复调用复用已有结果；
- [x] 故障树流程步骤、子步骤、判断表和根因场景编译为稳定覆盖节点；每个节点必须实际检索并形成“支持 / 排除 / 证据不足”结论，覆盖不完整时规划与最终合成均不得标记为通过；
- [x] 工具输入输出、方法/Pattern/evidence ID 均校验；模型失败或 Schema/证据校验失败时保留确定性基线，并显示稳定错误码、字段路径、重试次数和模型停止原因；
- [x] 规划轮次、方法读取、实际工具调用和最终合成在案例页增量显示；诊断页和日志筛查页可查看本次调用的文档/方法，轨迹只保存哈希和安全元数据；
- [x] 模型 usage 在成功、输出校验失败、纠正重试和最终诊断合成路径均累计；前端同时展示总量与输入/输出明细，并可从阶段事件回算旧轨迹；
- [x] 案例问答改为可恢复后台 Job，支持状态轮询、取消、失败信息和本轮轨迹，不再依赖长 HTTP 请求；
- [x] 对话可切换“修订诊断与报告”，模型只生成 evidence-validated 草稿；人工确认且原诊断仍为最新时创建新版 `AnalysisRun`，报告按新版确定性渲染；
- [x] 内部 evidence ID 继续用于 Schema 门禁和数据库关联，但前端诊断、对话、运行轨迹及 HTML/DOCX/PDF 报告只呈现文件行号或文档标题；已确认事实统一置于末尾；
- [x] GLM-5.1/5.2 Profile 的 Thinking 支持继承、强制开启和强制关闭；关闭时显式发送 `thinking.type=disabled`，并可配置长输出 `max_tokens`；同一多轮任务复用单一事件循环，避免异步 HTTP 客户端跨事件循环导致第二轮连接失败；
- [x] 案例概览将事件卡片明确为“日志记录级别统计”，避免把数千条 CRITICAL/ERROR 记录误读成独立故障；三层筛查、事件和时间线均可跳转规范化后的正确文件并高亮源行；
- [x] Alembic `0014` 保存规划、三层证据索引、问答任务关联和案例授权；`0015` 保存日志设备来源和可审核诊断修订；`0016` 保存逐次日志命中和 Skill 含义；旧库启动时自动升级，升级前筛查结果会提示重新执行以补齐精确命中；
- [x] Fake OpenAI 与 Playwright 使用纯合成日志验证上传、自动规划、完整原文命中、三层 UI、两轮综合诊断、异步问答和控制台零错误。

私有方法文件不是仓库依赖：根目录 `故障树.md` 和 `日志分析.md` 存在时会作为本机只读输入，
不存在时系统仍使用知识库中 `ACTIVE` 的 GW、AP 与通用联合诊断文档。两文件被 Git 忽略，正文不会
写入 Agent 轨迹或分析证据快照。生产环境推荐把需要版本、审核和跨电脑同步的方法作为知识文档
发布；本机私有文件适合暂不能进入仓库或数据库治理的材料。


## 2026-09-07 个人知识修订与试点恢复增量

普通工程师个人修订、管理员或原发布者审批、LAN 首次一致性发布、全文分段与质量预览、人工历史资料 HIGH 入库、完整服务器备份和独立目录升级已实现。
增量行为、边界与尚待公司实机验收项见 [局域网实施记录](docs/lan-knowledge-iteration.md)。
本轮 Full 结果以 VALIDATION.md 的最新记录为准；历史 Full 不能证明后续源码。
