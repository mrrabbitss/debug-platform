# 一次性知识重置与 Skill ZIP 导入交接（2026-09-09）

状态：sidecar 实现与新增定向验证已完成。仅新增本文件、独立服务、路由、CLI 和合成测试。
没有执行真实预览/确认/清库/导入，没有升级真实数据库。实际 ZIP 只做了只读完整性与引用检查。

## 提前提供的接口约定

- 路由模块：`app.api.knowledge_reset.router`，前缀 `/workbench/knowledge-reset`。
- `POST /preview`：`{operation_id, data_root}`。只读取；返回精确清理数量、六文件 manifest、适配 diff、源 ZIP SHA-256 和 `preview_hash`。`data_root` 必须明确填写并与服务器已配置根目录和 SQLite 实际文件匹配。
- `POST /confirm`：`{operation_id, data_root, expected_source_sha256, expected_preview_hash, confirmed: true, model_egress_approved: true}`。重新核对预览、一致性备份成功后保存人工审批及持久任务，返回 `{operation, job}`。没有第二次 AI 阅读审批。
- `GET /{operation_id}`：读取状态、manifest 和任务；ADMIN / EXPERT 才能访问所有入口，执行时再次核对审核人权限。
- HTTP 请求不接收 ZIP 路径、输出路径、归类策略或任意替换正文。服务器操作者通过 `KNOWLEDGE_RESET_SOURCE_ZIP` 指定实际 ZIP；CLI 通过显式 `--source-zip` 指定。所有落盘路径由服务在验证后的专用目录生成。
- 服务入口为 `preview_reset`、`confirm_reset`、`reset_job`，任务类型 `knowledge_reset`。使用现有 WorkbenchRecord/Job 持久化，不新增表或迁移。
- 内容契约已从知识代理交接核对：`metadata.content_kind = SKILL`，`problem_categories = [network]`；root/methodology 为 diagnosis，fault-tree 为 fault_tree，log-analysis 为 log_analysis，architecture 为 prior_knowledge，report-format 为 report_template。
- 主线负责注册 router、中央访问策略及关闭旧知识自动补种；本模块不改 main、route_registry、workbench、models、迁移或既有发布模块。

## 边界

确认固定 operation id、源 hash、完整 manifest 和旧 corpus 指纹；先备份 SQLite，再排队。
完整新文档和向量/图谱在私有 generation 中构建，最终同一数据库事务退休旧文档、激活新包和切换索引。
保留旧文档正文、版本、分块、发布清单及案例/报告引用；它们不再作为当前知识被检索。
原 ZIP 和适配差异仅保存在本机数据目录的受管归档中，不进入 Git。

## 主线集成点

1. 在正常 API registry 中注册 `app.api.knowledge_reset.router`。导入该路由即注册 `knowledge_reset` 持久任务，参数只有 `operation_id`，允许取消，最多 3 次尝试，每次 3600 秒。
2. 中央 `/workbench/knowledge-reset` REST 权限只允许 ADMIN / EXPERT；路由和服务也分别复核当前角色与数据库账号状态。
3. 将 `knowledge_reset` 加入 `knowledge_access.KNOWLEDGE_MANAGEMENT_JOB_KINDS`，保护 `/jobs/{id}` 的读取、取消、重试，不能让普通用户从通用 jobs 入口访问。
4. 在 dispatcher 完成过期租约及终态归并后、提交之前调用 `recover_abandoned_resets(db)`。它只释放已结束任务独占的构建标记，不创建任务、不提交事务；QUEUED / RUNNING 的原审批继续由 `reset_job` 恢复。
5. 主线关闭旧 seed 的自动重建；本模块不会以启动/升级作为重置触发器。
6. 主线在 `docs/README.md` 索引本交接，并同步能力、接口总账。按分工，本 sidecar 不编辑这些共享文件。
7. 新检查按指定所有权放在仓库根 `tests/test_knowledge_reset_iteration.py`；默认后端 pytest 的 `testpaths` 不收集根目录 tests。主线可纳入显式 CI 收集或移动到 `backend/tests`，测试已按仓库标志定位，可在两处运行。

服务签名：

```python
preview_reset(db, *, operation_id, data_root, source_zip, actor) -> dict
confirm_reset(db, *, operation_id, data_root, source_zip, actor,
              expected_source_sha256, expected_preview_hash,
              confirmed, model_egress_approved, archive_root=None) -> (WorkbenchRecord, Job)
reset_job(ctx, operation_id) -> dict
recover_abandoned_resets(db) -> int
```

`preview_reset` 是只读操作。`confirm_reset` 必须使用干净、专用的 Session；先做备份和归档，
再用 SQLite `BEGIN IMMEDIATE` 复核完整基线，在同一事务保存人工审批、审计和 Job。它不调度任务；
HTTP router 提交后唤醒 dispatcher，CLI 仅排队，由已经集成处理器的服务器执行。

`confirm_reset` 同一 operation id、源 hash 与 preview hash 重复调用返回原记录和任务，
包括已经 PUBLISHED 的记录，不再次备份或清库。不同内容不能复用旧 operation id。
构建期间知识、默认模板、Embedding 配置发生变化时停止切换；需要新的预览与新操作确认。
正常中断、进程接管及无内容变化的重试保留原人工审批，不增加 AI 回执。

`KNOWLEDGE_RESET_SOURCE_ZIP` 和可选 `KNOWLEDGE_RESET_ARCHIVE_ROOT` 是服务器进程环境变量。
本模块不扩展 Settings，不假定 pydantic 会把未知 `server.env` 字段写回 `os.environ`。
若通过网页执行，请由启动环境传入；CLI 则使用显式路径参数。

## 清理与保留的精确范围

- 将本次预览中的全部旧 KnowledgeDocument（含 DRAFT / 当前 Skill）退为 ARCHIVED，保留正文、版本和所有历史分块；记录 `retired_by_reset`，不物理删除旧正文。
- 未发布的 KnowledgeDraft 退为 ARCHIVED，保留原稿；GLOBAL AgentMemory 归档，CASE 记忆保持不变。
- 旧活动向量、图谱由新的私有代替换。旧向量/图谱记录不物理删除，旧发布清单和退休清单固定历史代；不清除用于案例重现的 run_context / knowledge_snapshot。
- 模型加载缓存与模型配置不属于知识内容，不删除。当前检索代码没有独立的知识正文进程缓存；当前知识通过 active/status、分块版本和 generation 指针同时隔离。
- 旧默认报告模板绑定退为历史信息；同一发布事务把 `template-network` 绑定到导入的 report-format 文档。
- 案例、分析/报告、已确认案例库记录、用户、令牌、个人偏好、模型配置和已有审计保留。既有提炼/投稿对话作为历史记录保留；本模块不修改知识贡献代理拥有的新投稿状态逻辑。

六份文档全部采用 `metadata.content_kind=SKILL` 和 `problem_categories=[network]`。
总领及方法论为 diagnosis，故障树为 fault_tree，日志分析为 log_analysis，架构为 prior_knowledge，
报告规范为 report_template。发布后的 bundle manifest 包含稳定 document_id、原路径、源/适配 hash、
引用的目标 document_ids 和 resolved_references；不会伪造 `source_ranges` 或 AI 阅读记录。

根文件的 YAML front matter 保持位置，随后加入明确的平台运行适配说明，原文保持；
预览和本地归档保存零上下文 unified diff。其他五份正文不改。
正式 Markdown 相对链接必须解析到包内文件；原总领中用反引号书写的单独文件名只在唯一匹配时解析为包内别名。

## 文件安全与备份

- HTTP body 禁止指定源/目标文件、输出目录、解压策略、角色映射或替换正文，额外字段直接拒绝。
- 必须显式给出服务器配置中的真实 data_root，连到的 SQLite 实际文件也必须位于该根目录，身份以文件标识固定。
- 只读取 ZIP，不调用 extract / extractall，不执行任何嵌入命令。验证恰好六份 UTF-8 Markdown，拒绝 Zip Slip、绝对路径、反斜杠、盘符/ADS、特殊设备名、符号链接、重复路径、未知文件、缺文件及损坏引用。
- 整个 ZIP 最多 16 MiB，每文件最多 1 MiB；文件数和解码均完整检查，不截断正文。
- 归档采用服务生成的独占随机子目录，不能经过符号链接/junction 或位于任何 Git 工作树内。
  若数据库在开发仓库内，必须把 archive_root 配置到 Git 外。
- 使用 SQLite online backup API 读取已提交 WAL，一致备份通过 quick_check / foreign_key_check、文件同步和 SHA-256 后才允许排队。
  Windows 使用可写句柄执行文件同步。原 ZIP、备份及预览存为本机受管文件。
- 新任务执行前再次校验归档源及备份 hash；失败只留未活动的私有代和本机恢复材料，不激活部分导入。
- 备份是该时点 SQLite 一致快照；本操作不覆盖存储中的案例/报告文件。若要回滚整库，应停服并由操作者按备份时点处理后续写入，不能在线直接覆盖数据库。

## CLI 使用（本模块交接后，主线已完成实际导入）

实际选定的数据库、外置备份及一次性执行结果见[主线记录](expert-knowledge-iteration-20260909.md)。
以下是命令说明，不代表需要再执行一次重置。

默认只读盘点：

```powershell
.\.venv\Scripts\python.exe scripts\reset_knowledge.py inventory
```

确认目标、先由主线完成架构迁移后，使用同一 operation id 预览并核对 JSON：

```powershell
.\.venv\Scripts\python.exe scripts\reset_knowledge.py preview --data-root '<已验证数据根>' --source-zip '<实际ZIP绝对路径>' --actor '<现有管理员或专家ID>' --operation-id '<本次唯一编号>'
```

只有核对后再调用 `confirm`，额外传 `--confirm --expected-source-sha256 '<预览源hash>' --expected-preview-hash '<预览hash>'`；
需要外部 Embedding 时显式传 `--allow-model-egress`。在开发工作树中另传 `--archive-root '<Git外归档根>'`。
`--database` 可指定根目录内实际 SQLite 文件，默认 `gw_ap_debug.db`；不接受缺失数据库，不自动初始化数据库或执行迁移。
`status` 搭配同一 `--data-root`、`--actor` 和 `--operation-id` 读取已排队/已完成状态。

## 只读盘点证据

2026-09-09 运行上述 inventory，仅打印路径、大小、表计数和迁移号，没有打印凭据、端点或正文：

| 候选 | 只读发现 | 处理 |
| --- | --- | --- |
| 仓库 `backend/data/gw_ap_debug.db` | schema 0016；4 文档、12 分块、4 修订、14 案例、16 分析、0 用户、9 模型、566 审计；文件 12,771,328 字节，存在 WAL | 旧库，不能视为已选目标；父任务先确认并迁移 |
| 当前用户默认 GWAPDebugServer 及其 data 子目录 | 不存在 | 未创建 |
| 当前 portable 默认 GWAPDebugPlatform/data | 不存在 | 未创建 |
| `artifacts/lan` 历史验证目录 | 另发现 8 份 server.json/server.env 路径，位于 transport / multi-cli / script-server-smoke 命名目录 | 未选为目标，也未读写其内容 |

原始 `D:/GRXM/hilink-diag.zip` 未修改，仍在 Git 外。只读验证得到 ZIP SHA-256
`c45d0f0be6a4cb0e2b9565c8b88be25437c32c95537aa397366d510009a99d3f`；压缩包 74,836 字节，
六份 UTF-8 文件合计 235,974 字节，所有包内引用可解析；仅根文件需要平台适配 diff。
没有把源正文或完整 ZIP 放入仓库、合成测试或公开文档，也没有将实际源发送到模型。

## 新增验证证据与限制

全部检查使用临时数据根与人工生成的六文件包，SQLite WAL/FULL、实际本地 hashing embedding 和实际确定性图谱构建，
Qdrant 镜像调用被隔离；没有 Chat API、真实公司数据或模型语义评估。

- 首批 40 项：初次 26 PASS，14 因 Windows 只读句柄 fsync 失败；修正后只重跑失败项，14 PASS。
- 接着新增 5 项并重检 3 项与实现修改有关的边界：8 PASS / 37 deselected。
- CLI 全流程、只读盘点、严格布尔确认新增 6 项：6 PASS / 45 deselected。
- 终态中断任务恢复新增 1 项：1 PASS / 51 deselected。
- 合计 52 个不同定向场景有通过证据；没有重跑历史全量回归、Full 或手动 CI。

关键证据包括：一整包六份正文和引用、network 默认模板；管理员/专家与普通用户隔离；显式根目录、
ZIP 路径与归档策略边界；原数据 WAL 一致备份；备用模型出站授权及配置指纹；重复确认不重复发布；
向量/图谱/最终事务失败均保留旧活动知识；取消和过期工作者不能切换；原稿、旧分块、案例报告及配置保留。

真实子进程在最终发布事务 flush 后、commit 前执行 `os._exit(73)`：数据库重开后旧知识和索引仍活动，
现有 dispatcher 将过期租约重新排队，第二个 worker 使用同一审批完成发布，只有一份新包和一个任务。
另覆盖构建阶段异常终止、相同 lease owner 下 attempt 接管、终态任务释放自身构建标记。
这验证进程中断恢复，不等于硬件故障、真实断电或外部 Qdrant/GGUF 现场验收。

对本次四个 Python 文件执行 Ruff 通过。仓库护栏已执行，当前共享工作区暂未全通过：
docs/README 尚未索引本轮四份交接；知识代理的 knowledge_curation.py 超行数，主线 knowledge_publication.publication_job 超复杂度。
新 reset 服务/路由不在架构失败列表；这些共享文件留给各 owner 集成处理，本 sidecar 不越界修改。
主线后续已完成文档索引、长度/复杂度修正及实际导入；最终护栏状态以 VALIDATION.md 为准。
