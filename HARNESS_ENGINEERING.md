# Harness Engineering 路线与状态总账

最后更新：2026-09-10

公司提供的 0.5.4 日志确认 staging 发布改名被拒绝，旧程序已恢复，具体占用来源尚不确定。
0.5.5 保留原子重命名并增加受范围/错误码限制的有限重试，补原始异常摘要；三个新增故障检查通过。
0.5.5 已发布：工程检查 24/24、10,220 项文件清单和七份远端附件及源码标签核验通过。
自动 portable CI 34433440625、GGUF CI 34433441099 已取消，无 CI 全绿或公司成功声明。
交付边界见 [安装访问故障记录](docs/installer-publish-access-20260910.md)，不重复已成功回归。

0.5.4 修补旧库 PRESERVED 状态缺少随包 Skill 显式导入入口的问题，新增仅管理员/专家的
服务端选包预览/确认/查询 API；复用持久审批和原子发布作业，增量导入保留旧知识及索引内容。
新增八项后端、三项浏览器场景、前端构建、原六文件实际导入与工程检查 24/24 通过，
0.5.4 已发布，10,220 项包清单、包内 Python/新 API 实际导入及七份远端附件和源码标签核验通过。
自动 GGUF CI 34431531771 在 Python 选择阶段失败，portable CI 34431531628 已取消，无 CI 全绿声明。
交付边界见 [旧库导入记录](docs/bundled-skill-upgrade-import-20260910.md)。

0.5.3 安装修复已发布：本机复现旧文件锁定时 Move-Item 部分移动，修复为同父目录重命名；
发布后清理失败独立处理，回滚失败保留路径并报错。六项新增故障/日志检查、中文错误往返、
Inno 编译与工程检查 24/24 通过。原版 0.5.2 实际 EXE 首装及 0.5.3 实际 EXE 覆盖升级通过，
安装后 10,220 项清单核验，合成 SQLite/配置和卸载数据保留通过；七个远端附件及标签核验通过。
发布自动 GGUF CI 34429130022、portable CI 34429131670 均已取消；未手动运行 CI，
不声明 CI 全绿或合并就绪。安装日志、新包与交付状态见
[安装故障记录](docs/installer-upgrade-fix-20260910.md) 和 VALIDATION.md；无应用/API/MCP 或数据库变更。

当前追加任务：核对最近已确认要求，补齐全部网页 Chat 入口的阶段/等待/计数进度和可查询任务。
任务状态与进度同数据库保存（迁移 0025），新增交互任务一次尝试、原子保存修订结果；
不以时间伪造模型完成度，不扩大 MCP 写权限。核对表和验收边界见
[本轮需求记录](docs/requirements-audit-20260910.md)。仅运行新增检查，旧成功回归继续沿用原证据。
0.5.2 已完成并发布：新增后端 20 场景、前端进度/时间断言与 3 个浏览器场景通过，工程检查 24/24。
进度保存、诊断里程碑和页面任务控制已抽为独立模块，文件/复杂度阈值保持不变。
10,217 项包清单、284 个应用文件、六文件 Skill 及七个远端附件均核验；分机沿用 0.5.0。
自动 GGUF CI 34388170059 在 Python 选择阶段失败；自动 portable CI 34388169458 已取消。
没有手动重跑 CI，不声明新 CI 通过或可合并主分支；真实 API 综合诊断仍沿用 PARTIAL 结论。

0.5.1 增加此前遗漏的安装验收边界：指定 Skill 必须真正进入安装载荷，空数据库初始化必须可见，
管理员初始/恢复令牌必须在安装后的 RBAC 登录路径上可用。新增检查及本地交付见
[安装修复记录](docs/installer-skill-admin-fix-20260909.md)。保留既有模型验证证据，不重复成功过的套件。
18 项新增定向检查及真实包内 prod/RBAC 首次登录、六文件初始化、停服恢复和重启保留通过；10,210 项清单、277 个应用文件匹配。
v0.5.1 已发布，七份附件哈希和标签校验通过。自动 CI 34375760371 在 Python 选择阶段失败、应用/模型步骤未执行；未手动重跑，也未合并主分支。

本轮按用户要求提高慢模型等待并完成 0.5.0 本地交付核验，见[等待与交付记录](docs/slow-model-timeouts-20260909.md)。
新增 7 项离线配置检查、前端构建、Codex 配置与 CodeAgent 环境检查通过；复用原真实模型和既有回归证据。
10,200 项服务器清单、276+22 个源码文件、26 项分机清单及随包 Python 独立启动/迁移通过；未重复功能回归。
0.5.0 已公开发布，7 项附件和源码标签远端校验通过；发布自动触发的重复构建已取消，未手动重跑或声称新 CI 通过。

追加[真实 API / Codex CLI 功能验证](docs/expert-live-model-validation-20260909.md)，使用全合成
资料、独立生产 RBAC 源码进程和数据库。Codex CLI 明确选择 gpt-5.6-terra，完成 ENGINEER
诊断和 EXPERT 知识归类，平台 Chat 出站为零。发现的发布缓存、审核 JSON 结构和日志任务
权限问题增加 12 项定向检查（1 + 4 + 7），全部通过；没有重复已通过的历史套件或手动 CI。
增加可选真实模型验证脚本，API 恢复模式复用已完成的阅读与审批，只重试尚未通过的流程。
API 最终为部分通过：知识整理/提炼/审核、日志规划、问答和重启保留通过；综合诊断两次
分别被上游 InternalServerError 和 Skill 阅读结构校验阻断，未生成 API 诊断报告。
请求 glm-5.2 的探测响应标识为 glm-5.3，不将网关模型映射或真实业务质量视为已验收。

2026-09-09 专家与知识协作迭代源码和本机集成完成，见[实施记录](docs/expert-knowledge-iteration-20260909.md)与[验证记录](VALIDATION.md)。
新增定向检查覆盖模型所有权/配置绑定、知识贡献精确审批、完整 Skill 分段阅读、案例提交同事务入队和终态恢复；
重置使用真实子进程异常退出验证同审批恢复。前端26项新场景和生产构建通过，实际页面与重启检查通过。
当前项目数据库已在 Git 外一致备份，完成0016→0024迁移和六文件导入，旧案例/分析/附件/报告列哈希保持不变。
API声明与导出OpenAPI同步，agent入口38项、MCP工具18项保持不变。未重复历史通过的套件、Full或手动CI；
本轮没有构建/发布新安装包，也不以假模型检查代替真实公司模型及物理多机验收。
最终仓库护栏24/24通过（196 Python / 36 Vue边界）；新增源码无 Ruff 问题，两个旧助手测试文件的28条既有诊断未增加。

2026-09-09 三栏工作台源码、新增模块检查及离线服务器/分机交付已完成，详见[接续记录](docs/workbench-iteration-20260908.md)。
新权限替代旧工程师个人修订与原发布者审批；保留历史不可变结果。新增内容寻址的运行知识快照和Skill依赖读取。
只检查新增或改变行为，不重复历史回归或Full，不手动触发CI。源码恢复快照已保存，交付状态以本轮新增证据为准。
新增全文阅读回执、审批摘要绑定、任务租约/请求版本隔离、文档/向量/图谱原子切代与配置损坏终止检查。
前端9项、权限25项、报告41项、助手56项新增独立检查已通过；主线快照10项、流程11项、任务收尾5项通过。
工作流角色、管理员REST专用操作和18项MCP契约已同步；24项仓库护栏、架构限制与包内启动/导出/重启检查通过。
完整EXE由10,181项已校验的文件构建，257个后端/前端文件与源码一致；新CI、真实公司模型/实机和Word分页仍是未覆盖范围。

2026-09-08 Embedding 定点修复：1800 字符的知识块可能超过随包 BGE 的 512-token 上限；受管本地 Embedding 新增 tokenizer 预检、无截断分窗和加权归一化，外部 API 路径不变。只验证此次新增边界、真实 541-token 故障复现与修复，以及小补丁安装/幂等/回滚；未重跑历史回归或 Full，未手动触发 CI。重建索引仍使用原有 generation 原子发布流程。

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

### 2026-09-07 M0–M5 实施中

用户批准的资源、里程碑和证据见 [局域网与知识演进实施记录](docs/lan-knowledge-iteration.md)。
M0 Full 基线 PASS；真实隔离 HTTPS 的普通工程师 REST/MCP、上传和令牌撤销 PASS。
已新增客户端打包/安装校验、服务器资源配置/并发队列、候选记忆/真实结果反馈回归。
已增加发布并存/旧分块引用/失败与取消/并发冲突回归、0019 迁移及网页发布 E2E；
新增只读客户端发现、发布清单和历史分块工作流契约（0.8.0）。
本轮 Full `20260907-115000-full` 18/18 PASS，452 后端测试、6 浏览器 E2E；包内真实 GGUF 检索 PASS。
M1–M5 尚未全部完成，公司网络、知识权限/全文演进、Windows 服务恢复、完整升级备份与最终同步发行包仍是门禁。

### 已完成

- [x] Python 跨平台 `uv.lock` 与 pip 兼容 constraints，Win11/CI/Docker 共用；
- [x] Win11 自包含便携构建：目标电脑无需 Python/Node/pip/npm，构建时真实启动并验证前端、API、SQLite 和 Vue 路由；
- [x] 平台运行时与本地模型原生运行时隔离；稳定 Core 便携包门禁禁止打入 Torch、Sentence Transformers 和权重，并拒绝继承目标电脑全局/用户 Python 包；实验中的全 GGUF 版只把权重交给独立 llama.cpp sidecar，仍禁止原生模型依赖进入平台 Python；
- [x] 前端模型权重下载使用持久任务、密文代理、受管目录、路径穿越/文件数/大小门禁、不可变 Commit、Range 续传和完整 SHA-256 校验；同模型线程/跨进程锁保护 staging generation，活动指针只在全量成功后原子切换，失败与取消保留上一代；模型运行时仍保持隔离；
- [x] Ubuntu/Windows 后端和前端、扩展、依赖审计、外部服务、Docker、Win11 冒烟 CI；
- [x] 隔离临时数据库、存储和端口的 `runtime_smoke`；
- [x] 数据库持久化后台任务、重启恢复、取消、重试和去重；
- [x] 模型端点策略、内容外发同意、用量审计、证据 ID 校验和 DRAFT 门禁；
- [x] Win11 私网模型端点配置脚本保留兼容；专家迭代已移除外部模型白名单和私网显式启用前置要求；
- [x] Agentic Search 的计划、阶段状态、候选数、耗时与确定性回退；
- [x] GW/AP 联合日志规划、原生两至二十轮只读工具诊断 Agent、GLM Thinking 三态/富对象归一化/纠错重试、方法及故障树逐节点检查/检索/结论门禁、异步案例问答和人工确认诊断修订的持久化任务与案例级实时轨迹；
- [x] 综合诊断将方法/记忆与案例日志证据分型门禁，模型不能用知识正文支撑本案例结论；确定性回退直接读取解析事件，并以不暴露原值的本地派生证据完成敏感标识相等性检查；
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

### 全 GGUF E/R 发布 Harness（实验中）

- [x] 固定 llama.cpp、BGE/Qwen 来源 commit、文件大小、SHA-256、许可证、转换命令和
  CPU runtime DLL 集合；模型权重与安装器产物不得进入 Git；
- [x] 标准准备脚本支持固定 Hugging Face origin 或显式 HTTPS mirror、断点续传、完整
  校验、BGE 转换和组件锁生成；本地安装器构建不能只信任自报锁，必须重新绑定仓库资产
  清单；
- [x] 启动器契约覆盖安全组件路径、动态 loopback 端口、命令行参数门禁、临时 key 文件、
  Win11 kill-on-close Job Object、health-before-FastAPI、逐组件回退和内容安全状态文件；
- [x] 本地回环通信不继承公司代理：启动器健康检查使用无代理 opener，sidecar 环境补齐
  `NO_PROXY/no_proxy`，受管 E/R 客户端强制 `trust_env=False`；外部 Profile 保留原代理策略；
- [x] 真实 GGUF 语义冒烟入口验证 BGE 的 768 维、有限值、L2 归一化和最小相关性排序，
  以及 Qwen `/v1/rerank` 的最小相关性排序；2026-09-01 当前 Win11 结果为
  `0.5609 > 0.1655` 与 `0.9997 > 0.0001`；
- [x] clean-system 原生依赖合同锁定微软官方 VC143 x64 app-local release CRT 的来源、
  实际下载大小、SHA、逐 DLL 哈希和 Authenticode；拒绝 `debug_nonredist`，真实 sidecar
  模块枚举要求核心 CRT 从包内目录加载，避免开发机 System32 依赖掩盖缺包；
- [x] 单独的 Windows 工作流仅允许手工/Release 触发，固定 runner、Action commit、
  Python、Node 和 Inno Setup；产物集合必须同时包含 ZIP、Setup.exe、SHA-256 与
  provenance；
- [x] 正式 Release 路径强制严格清单状态；当前 `experimental_unverified` 会按设计失败，
  防止把一次最小语义冒烟误当成发布质量证明；
- [x] 组装后实验 ZIP 的完整 self-check、临时全新 data root、受管 Profile 自动激活和
  真实平台 API 已通过：Embedding `2 x 768`、Reranker 首项 `index=0`；
- [x] 固定 Inno Setup 6.7.1 portable compiler 已在当前 Win11 完成单文件编译（213.469
  秒），校验安装器大小、SHA-256、`FileVersion=0.1.0` 和
  `ProductName=GWAP Debug Platform`；编译器下载器来自固定 GitHub Release，
  Authenticode 为 `Valid / Pyrsys B.V.`；
- [x] Inno 不再把文件逐项覆盖进 `{app}`：payload 先完整释放到 `{tmp}`，以最后释放的
  `package-manifest.json` 回调 `install_local.ps1 -NoLaunch -NoShortcuts`；合同强制等待进程、
  非零 `RaiseException`、Restart Manager 资源登记、受管目录检查和只删 app tree 的卸载
  边界；固定 6.7.1 小型编译合同已通过；
- [x] Setup/ZIP 发布由 PowerShell 5.1 命名互斥体跨进程串行化，超时清晰失败且崩溃自动
  释放；目标缺失时只允许恢复唯一且通过 manifest/runtime 基本完整性检查的受管 backup，
  多个候选拒绝猜测；
- [x] 用当前原子发布源码完整重建 ZIP/Setup 并记录大小/哈希；本机真实验证 ZIP 与 Setup
  首装/覆盖升级、唯一孤儿 backup 恢复、安装后 E/R/API 冒烟、无受管临时目录残留，以及
  卸载只删除 app tree/卸载器；
- [x] 0.2.0 包内真实 Profile 小型检索闭环：合成知识审核发布、14 x 768 持久向量、五候选
  Dense/Reranker 参与及相关项第一；仅最小健全性验收，不冒充存储向量 ANN 召回；
- [ ] 大规模知识全量索引、混合 RAG 质量、存储向量召回与逐组件故障注入；
- [ ] 对当前 `NotSigned` 的项目 `Setup.exe` 做发布代码签名并校验签名信任；
- [ ] clean Win11 x64 CPU-only 冷启动、安装、升级、回退、卸载和数据保留矩阵；
- [ ] BGE 与固定上游模型的余弦/Recall 对照、Qwen 与固定上游模型的排序/NDCG 对照；
- [ ] 手工工作流和正式 Release 工作流实际全绿，并将安装器哈希、最终大小和验证记录
  写回 `VALIDATION.md`。

### Claude Code / Codex Skill + MCP 方案 A（在研）

- [x] 冻结 `workflow/mcp-tools.yaml` 当前 17 个 `debug_*` 名称（15 个诊断工具与 2 个知识
  路由工具）、`/mcp`
  Streamable HTTP、Bearer、读写分类和案例/运行作用域；`workflow/skill.yaml` 只引用该契约，
  不复制第二套后端运行时；
- [x] 标准 MCP transport 的 `initialize`、`tools/list`、`tools/call`、Bearer 拒绝和安全参数错误
  已有聚焦测试；transport 不依赖模型网关，并支持 FastAPI 精确 `/mcp` 路由；
- [x] 持久 `HostAgentSession` 已具备案例/解析/方法快照哈希、乐观版本、lease、预算、覆盖、
  evidence allowlist、tool receipt 和终态；finalization 聚焦测试证明可写回原生不可变
  `AnalysisRun` 且后端 Chat provider 调用为零；
- [x] 共享薄 Skill、Claude Code/Codex 非符号链接镜像、Win11 同步/安装 dry-run、REST
  大文件上传助手及独立 Skill 契约测试已完成；
- [x] Windows Core 便携构建已打包标准 `agent-skills/gw-ap-debug` 和 PowerShell/BAT
  安装器，包完整性与安装器 dry-run 纳入 smoke，启动器按实际 `--port` 注入
  `MCP_PUBLIC_BASE_URL`；当前重建 ZIP 的 10,040 文件清单、完整 smoke 与 SHA-256 已通过；
- [x] 当前 17 个业务工具已注册到 typed provider 并接入主 FastAPI 生命周期；回归覆盖真实
  Streamable HTTP `initialize` → 17 项 `tools/list` → `debug_status`、共享 Bearer/API Key/
  数据库 Personal Token 解析、失效令牌拒绝、案例作用域，以及知识路由的管理员权限、并发/
  内容哈希、活动叶子分类和 DRAFT 门禁；
- [x] 本机 Codex CLI 以 `--ephemeral --ignore-user-config` 加载仓库 `$gw-ap-debug` Skill，
  通过临时 SQLite、临时 Bearer 和真实 `/mcp` 仅调用一次 `debug_status`，返回
  `inference_owner=host_cli`、`backend_chat_allowed=false`、`backend_chat_calls=0`；同轮
  `scripts\validate_all.bat Full` 的 18 个步骤和浏览器 E2E 全部通过；
- [x] 本机 Codex CLI 使用当前会话模型完成纯合成“AP 频繁离线”案例 E2E；网络重连后沿用
  同一 `HostAgentSession`，最终 5 轮规划、27/27 节点终态、126 条 GW/AP 双侧证据、
  `host_cli_mcp` 不可变分析、HTML 报告及 Web 链接均通过只读复核，报告哈希和文件行号正确、
  无内部 ID，隔离库 `backend_chat_calls=0` 且 `model.egress=0`；
- [x] 演示重新诊断路径已接入两份 SHA-256 固定、仅匹配保留案例 ID/两份日志哈希/设备角色/
  dataset 标记的公开合成方法；自动化验证编译保持 59 个 Pattern、27 个节点和三个根因场景，
  空知识库且无私有根目录方法时仍可初始化，普通案例继续使用原知识加载链；
- [x] Codex CLI 0.152.1 以 `gpt-5.6-luna` 在全新数据库、空知识库和上述公开方法上完成当前
  源码真实 E2E：固定方法 ID/hash、6 轮、27/27 终态、115 条双侧分析证据、报告和网页链接
  均通过独立复核，后端 Token 0/0/0、`backend_chat_calls=0`、`model.egress=0`；
- [x] 增加 1–20 个 Markdown 的 REST multipart 路由入口，按文件建立持久任务与独立非活动
  `DRAFT`；Web 使用选择或激活的平台 Chat Profile，CLI 只在 REST 上传后通过
  `debug_get_knowledge_routing_context` / `debug_apply_knowledge_routing` 让 Host 当前模型分类；
  两条路径都只允许活动叶子分类，Host 写回校验 lock version 与正文 SHA-256，且禁止自动发布；
- [x] 最终源码 Full 回归 `20260903-014606-full` 全部 18 阶段通过：后端 `330 passed, 1 skipped`、
  前端生产构建、runtime smoke 与 5 个浏览器 E2E 均通过；多 Markdown Web 场景分别归入不同
  受管分类且每文件保持一个 `DRAFT/active=false`；
- [x] 真实 Codex CLI 完成 Host 模型知识分类：batch `KRBATCH-28b4b098d8984ae2` 的两文档
  分别归入 `history.fault_trees` 与 `diagnosis.protocol_rules`，均为 DRAFT/inactive，后端 Chat/
  model egress 为零；同次当前源码综合诊断会话 `HASESS-6bbdc379f6a04e7f` 在 4 轮完成
  27/27 节点、119 条证据和报告 `RPT-b6fa95b301634af7`，后端模型调用为零；
- [ ] 在实际 HTTPS 反向代理和签发的 Personal Token 上验证远程连接、角色与案例权限组合；
- [ ] 在独立干净 Win11 电脑从重建 Core ZIP 启动服务并重复真实 Codex 全案例 E2E；本机包
  smoke 与同源码/同方法哈希的真实 Codex E2E 已分别通过，但未重复消耗一次包内全诊断；
- [ ] 当前验证主机未安装 Claude CLI；安装后执行真实 Claude Code 合成案例 E2E，并在真实
  CLI 层补齐证据拒绝、快照漂移和取消
  验收（这些边界已有服务/MCP 自动化测试），证明 `backend_chat_calls=0` 和最终 Web 可见；
- [ ] 完成远程 HTTPS/RBAC、真实 Claude Code、最终便携包 Codex 重跑和真实 CLI 负路径验收后，
  再把能力总账提升为 `AVAILABLE`；不得用 transport 单测或配置 dry-run 替代全链路验收；
- [ ] OpenCode 保持辅助客户端规划状态；当前未实现安装器/配置适配，也按本轮要求未做真实
  MCP E2E，不得宣称兼容。

### codeagent 一键启动编排（LIMITED）

- [x] 2026-09-03 改为额外加载 MCP：默认移除 strict 参数，12 项源码启动器黑盒覆盖
  用户合成 MCP 配置不变、代理/模型环境继承、包内 Skill fallback 和 `-ConnectOnly`；
- [x] Win11 组件选装与共享令牌已增加定向测试：Core/E/R 组合、源清单投影、原子升级与
  失败回退、真实 DPAPI 和并发首次初始化，不更改用户 `.cac`、`.env` 或认证模式；
- [x] WebSkillMcp 0.2.0 新包构建与真实双 GGUF 已通过；Core/GGUF 包分别完成 CodeAgent
  自建后端及 Web 先启动复用的 2/2 编排检查，非阻塞父进程管道修复 Win11 原生导入卡住；
  使用模拟 CLI、Chat 调用为零，不替代魔改客户端推理验收。精确产物与回归见 `VALIDATION.md`。
- [x] 最终源码 Full `20260903-155255-full`：18/18 阶段、后端 416 passed / 1 skipped、
  80.44% 覆盖率、5 项浏览器 E2E 全部通过；SQLite ResourceWarning 如实保留在验证记录中。

- [x] 根目录 `start_codeagent.bat` 与 PowerShell 入口覆盖 Windows 11 默认/自定义 CLI 路径、
  首次受锁后端依赖准备、保存连接设置、CurrentUser DPAPI 令牌、会话级 MCP 与当前仓库
  Skill；不修改用户全局 CLI/MCP、旧 Skill、Web Chat Profile 或仓库 `.env`；PowerShell 5.1
  原生命令重定向的 stderr warning 按实际退出码判断，不把 warning 误判失败或吞掉非零错误；
- [x] 2026-09-03 启动器黑盒 `10 passed`（68.40 秒）：无副作用 `-DryRun`、无需 CLI 且不调用
  模型的 `-Check`、真实后端 REST/MCP 握手、模拟 Program Files 自动发现、显式 `.ps1`/`.cmd`
  中文/空格/方括号路径、二次配置与 DPAPI 复用、换端点不复用旧令牌、项目迁移后 cwd/Skill
  路径、CLI 退出码 23 传播与自建后端回收、外来端口和现有 API Key 后端保留/错误令牌拒绝，
  以及原生 stderr warning 配合退出码 0 继续、真实失败退出码 17 保留及 `.cmd --help` warning；
  客户端使用 fake CLI，不冒充真实模型运行；
- [x] 修复后源码 Full `20260903-105852-full` 全部 18/18 阶段通过，后端 `340 passed,
  1 skipped`、覆盖率 80.44%，5 项网页 E2E 通过；包括 Web 综合诊断与多 Markdown 归类。
- [ ] 在安装了用户魔改 `codeagent` 的 Windows 11 上验收真实模型 Skill 读取、多轮诊断、
  报告写回与 Web 可见性；当前主机没有该客户端，fake CLI 加真实后端握手不能冒充模型 E2E，
  也不能用此前已通过的原生 Codex CLI 结果替代。

## 3. P1：质量、可观测性和架构约束

### 3.1 可重复的端到端评测

- [x] 提交脱敏的 TXT/MD/HTML/DOCX/PDF Golden Incident Corpus；
- [x] 提交 Fake OpenAI-compatible 服务，覆盖生成、超时、限流、坏 JSON 和中断；
- [x] 使用 Playwright 固化上传、预览、提炼、对话纠错、确认 DRAFT 的浏览器 E2E；
- [x] 使用 Playwright 固化无后缀日志、完整原文三层筛查、方法/工具可视化、正确文件与精确行高亮、两轮工具诊断规划和可恢复问答 E2E；
- [x] 提交 AP 频繁离线纯合成 GW/AP 演示日志、显式模型外发同意的种子脚本，以及 UDM 崩溃 → Advertise 失败 → GW 心跳超时 → 拓扑离线的解析/故障树回归；样本测试固定 DEMO/SYNTHETIC 标识、RFC 5737 地址和本地管理 MAC，防止后续误换成公司数据；
- [x] 把同一组合成样本与此前真实成功的 GLM-5.2 运行固化为 Core/全 GGUF 一键演示：快照导出器采用字段白名单，把数据库主键重定位到当前解析事件/筛查命中，保留真实两侧日志计划、66 个命中组/149 个位置、两轮工具诊断、27/27 故障树结论、Token/耗时和 16 阶段轨迹；同时剔除凭据、原始 Prompt、私有方法/记忆正文，并以源结果/证据/日志 SHA-256 和快照自身哈希防漂移。API、服务、Playwright 与便携包门禁区分“历史真实模型运行”和“导入时零模型出站”；
- [x] 演示脚本阻断仅“27/27 完成”但根因语义错误的结果：固定校验物理链路排除、UDM 支持、网络证据不足、UDN/MAC 本地派生证据、四类跨设备假设、方法角色/LLM 工具规划、报告可读证据及知识不得冒充案例证据；每个后台任务都有显式超时；
- [x] AP 离线因果链负向门禁：端口恢复、心跳发送成功和未超过阈值的计时字段不得提升为故障证据；`curTime - lastEventTime > iAdvrTimeOut` 由解析器和确定性故障树共同执行；`TestLinkOK failed` 只作为待分流信号，明确物理故障与 UDP/SSDP 传输故障分别归因；样本三轮 AP→GW 时序均受回归约束；
- [x] 日志解析评测：事件代码、时间、行号和上下文准确率；
- [x] 知识提炼评测：章节完整性、证据引用、幻觉、敏感信息泄漏；
- [x] RAG 评测：Recall@K、MRR、NDCG、引用准确率；
- [x] Code Graph 固定调用/引用/继承/实现边；
- [x] Commit Graph 固定 query → commit → file → symbol 路径；
- [x] Memory 固定应复用、不得复用和污染隔离案例；
- [x] Agent trajectory 固定工具选择、最大跳数、停止原因、成本和耗时阈值；
- [x] 增加 36 个纯合成 Agent 场景矩阵分布契约，CI 阻断设备/规模/证据/知识/记忆/Planner 故障/停止类别覆盖缺失和质量指标门槛缺项；
- [x] 综合诊断生产循环累计 Token、墙钟时间和只读工具预算；连续重复且没有覆盖、查询或证据推进时以稳定停止原因安全回退；
- [x] 每轮模型请求前清空供应商观测快照，调用前失败不重复累计上一轮 Token；策略证据水合计入总工具预算但不挤占四个模型规划调用槽位；
- [x] 后端完整回归 75% 行覆盖率门禁；建立门禁时实测为 77%。
- [x] 每个 GitHub CI job 设置硬墙钟超时，Golden 耗时阈值由独立无插桩任务严格阻断；

### 3.2 运行轨迹

- [x] 统一 `run_id`、`case_id`、阶段/工具、模型和 Prompt 版本；
- [x] 记录输入输出摘要哈希、tokens、成本、耗时、重试和停止原因；
- [x] Chat JSON 模式、Thinking 开/关显式请求、供应商 usage/finish reason 映射、校验失败用量保留和前端阶段用量回算回归；
- [x] 日志规划强制使用无 Thinking 的有界 JSON 请求，并将代理、超时、TLS、鉴权、限流、HTTP 状态、连接、截断和无效 JSON 分类为可审计且不含响应正文的稳定错误码；
- [x] 只保存证据 ID 和脱敏元数据，不复制公司日志正文；
- [x] 内部证据 ID 与人类可读标签分层：校验/关联继续使用稳定 ID，前端与报告仅显示文件行号或文档标题，避免把实现主键暴露给操作人员；
- [x] 分析 API 对模型配置快照移除 Base URL、代理和端点字段；历史行同样在响应模型边界脱敏；
- [x] 前端运行检查器、失败步骤定位和内容安全的只读重放；
- [x] 运行中增量显示方法读取、规划轮次、实际工具调用、三层排序和模型回答，安全保留校验错误码/字段、轮次和停止元数据；
- [x] 前端故障树规划面板显示轮次、输入/输出/总 Token、工具调用、耗时、连续无进展轮次和对应硬上限；
- [x] 每轮记录上下文窗口、输入预算、估算/实际占用、分区压缩和 Spill Handle 数量；前端可查看且不保存溢出正文；
- [ ] 可选增强：将现有结构化轨迹桥接到 OpenTelemetry 本地观测栈。

### 3.3 架构护栏

- [x] `routes.py` 拆出 system、knowledge、repositories 和 jobs 领域路由，聚合器从约 1,972 行降至约 622 行；
- [x] 知识提炼拆出上传、沙箱抽取、证据、序列化和受状态机约束的生成/修订/确认编排；
- [x] Agentic Search 拆为 planner、tools、fusion、executor、trace；
- [x] 日志筛查拆为任务/扫描发布与独立 LLM Planning 模块，避免真实模型兼容逻辑重新制造高冲突大文件；
- [x] 日志筛查的聚类摘要与逐次命中分表持久化并在同一事务发布，既保持排序性能，也支持完整展开和逐行跳转；
- [x] 综合诊断 Planner 将 GLM 输出 Schema、归一化、方法/故障树覆盖门禁、Prompt 压缩、类型化工具和二十轮执行器拆为独立模块，降低多轮执行器的上下文和改动冲突；
- [x] 抽出共享 `agent_runtime` 上下文治理/Spill/预算账本与通用执行器 Contracts，避免业务循环复制硬限制语义；
- [x] 案例详情拆出独立日志浏览组件，集中处理制品清单路径解析、分页搜索和精确行高亮；
- [x] 前端大型 View 拆出 composable、来源预览领域组件和类型化 API client；
- [x] 增加 API → service → persistence 导入边界检查；
- [x] 增加文件大小、圈复杂度、Vue/TypeScript 类型检查和后端覆盖率下降门禁；
- [x] 将 Workflow 白名单同时与手写最小 OpenAPI 和 FastAPI 运行时 OpenAPI 比对。
- [x] 可选静态前端托管与源码 Vite 模式共用同一 API；便携数据根、环境文件和前端根均使用显式可测试路径，不依赖启动工作目录。

## 4. P2：有界 Agent 与多任务运行

- [x] 类型化 Tool Registry、输入输出 Schema 和只读默认权限；
- [x] 每个角色/案例的工具白名单及写操作审批；
- [x] 最大步骤、跳数、tokens、成本、墙钟时间和并发预算；通用执行器按真实依赖链计算因果深度，优先采用供应商 usage/cost 并计入工具输出，Planner 的 hop/估算值只作兼容提示或无 usage 时的保守回退；综合诊断的独立生产循环复用同一实际用量账本；
- [x] 重试退避、熔断、幂等、取消和显式停止原因；
- [x] 失败时回退当前确定性 Planner，而不是无限循环；
- [x] 综合诊断生产路径接入方法目录/全文、混合知识、全部持久化日志筛查证据和证据读取五类只读工具；每轮最多四次模型规划调用、总计最多二十轮，重复调用复用已有结果；
- [x] 故障树节点覆盖账本和最终合成双门禁：稳定节点 ID、实际检索状态、终态结论与 evidence ID 均可审计，未覆盖节点会阻止 LLM 规划被接受；
- [x] 故障树确定性交叉核验要求证据与当前节点的 Pattern/提示实际相关；同案例但无关的证据 ID 不能保留模型的支持或排除结论；
- [x] 真实 GLM Chat 隔离验证脚本覆盖全部 Chat 调用入口；支持无密钥故障树预检和按 probe 定向复测，凭据只读当前环境变量，报告内容安全且临时数据库在正常退出时自动销毁；
- [x] 当前私有 `故障树.md` 实测编译为 27 个覆盖节点，27/27 有确定检索入口；真实 GLM-5.2 在 65,536 输出预算下完成 3 至 5 轮、27/27 检索与结论闭环，下游综合、问答和修订均通过；
- [x] 每个任务独立 worktree、端口、数据库、存储和日志目录；
- [x] 后台任务 lease、heartbeat、dead-letter 和多实例安全领取；
- [x] 不可信 DOCX/PDF/HTML 解析迁移到受 CPU/内存/时间限制的独立进程；
- [ ] 任务控制平面维护依赖 DAG、人工审批和停滞检测；
- [ ] 将人工 Review 反馈沉淀为测试、规则、文档或评测样本。
- [ ] 将 36 个场景分布契约中的高风险行逐步升级为可执行 Fake Model/私有脱敏 Golden，不把“有矩阵定义”误报为“模型质量已全量验证”。

## 5. 优先顺序与完成条件

1. P0 仓库护栏必须先稳定，确保每台 Win11 和 CI 使用同一执行路径；
2. P1 Golden Dataset、Fake Model 和浏览器 E2E 完成后，才能提高 Agent 自主度；
3. P1 轨迹和预算可观测后，才能把确定性 Planner 升级为循环 Tool Agent；
4. P2 多任务/多实例能力必须经过故障注入和恢复测试；
5. 每完成一项，同步更新本文、`CAPABILITIES.md` 和 `VALIDATION.md`。

一项 Harness 能力只有在仓库中存在可执行入口、自动测试和失败说明时才算完成；
只有文档描述或一次人工验证不算完成。


## 2026-09-07 个人知识修订与试点恢复增量

普通工程师个人修订、管理员或原发布者审批、LAN 首次一致性发布、全文分段与质量预览、人工历史资料 HIGH 入库、完整服务器备份和独立目录升级已实现。
增量行为、边界与尚待公司实机验收项见 [局域网实施记录](docs/lan-knowledge-iteration.md)。
本轮 Full 结果以 VALIDATION.md 的最新记录为准；历史 Full 不能证明后续源码。

## 2026-09-07 本地多客户端 CLI 验证增量

`scripts/verify_multiclient_cli.py` 已用实际 Codex CLI 和完整 HTTPS 服务器包完成双客户端推理、
中断后同会话恢复、方法与证据规划、跨账户会话拒绝及报告保存。
验证器拒绝跨案例引用、仅凭方法作事实、未知反证和错误主假设；停机后重新核对持久数据与报告哈希。
结果与物理分机边界见 [实测记录](docs/local-multiclient-cli-validation.md)。

## 2026-09-07 脚本服务器与分机快速包

新增服务器前台脚本，复用完整服务器程序目录，不安装服务或定时任务。
独占锁阻止重复启动及运行中备份；已有地址不被新参数覆盖，日志、证书资料和备份保存在独立数据目录。
首次启动、复用数据重启、真实 HTTPS、管理员鉴权、证书导出和停机完整备份已实测。
分机包构建时纳入简版 Markdown 指南；安装、包哈希、证书校验及不启动分机后端的检查保留。

2026-09-08 按用户要求移除新加的现场构建入口及对应测试，改用完整离线服务器 EXE。
服务器安装器沿用 `install_local.ps1` 的临时目录校验与原子替换，固定安装完整 E/R；专用程序目录与业务目录分离。
服务器启动、停机备份继续调用已有 `run_lan_server.py`；包内入口显式指定程序和数据路径。
此次只做产物构建和完整性检查，不重复历史回归或手动触发 CI。具体产物见 VALIDATION.md。

## 2026-09-08 内网识别码登录

专用部署配置开启 `SIMPLE_ENGINEER_LOGIN`，自助入口只签发 ENGINEER 身份；一位小写字母加八位数字映射到稳定 user_accounts.id。
管理员与停用账号不允许从该入口登录。既有 RBAC、个人修订、发布者审批和案例归属继续使用同一主体。
分机通过预置 HTTPS 地址首次取得公共 CA，仅此无凭据请求允许未知签发链；导入 CurrentUser 后全部身份请求正常校验证书。
已保存的证书不自动替换，个人凭据仍用 DPAPI 保存；内网地址直连设置只作用于连接器启动的进程。
网页使用短时一次性兑换接续分机身份，凭据不作为普通 REST/MCP 令牌接受。新增入口不加入模型可调用的工作流工具清单。
本次检查限于新增身份流程、前端构建、客户端语法与产物构建；没有重跑历史回归或启动 CI。
