# 知识助手交接（2026-09-09）

助手分工已完成，源码冻结，可供父代理集成和封装。未提交、未推送，未修改共享功能总账或其他代理负责的源码。

## 实现范围

- `backend/app/api/knowledge_assistant.py`：保留原创建、列表、详情、对话、确认入口，补充授权、暂停、取消、继续和来源分页。
- `backend/app/services/knowledge_assistant.py`：持久任务编排和中断处理。
- `backend/app/services/assistant_runtime.py`、`assistant_controller.py`：完整分段阅读、阅读凭据、分页只读工具、严格模型结构和有界草稿规划。
- `backend/app/services/assistant_sources.py`、`assistant_plan.py`：来源快照、依赖映射、区间覆盖、唯一修改锚点、具体全文和差异、目标版本及审批摘要。
- `backend/app/services/assistant_sessions.py`、`assistant_state.py`：管理员状态转换、同事务任务出箱、请求/任务/执行轮次/租约围栏和恢复钩子。
- `backend/app/services/assistant_publication.py`：整组知识、向量和图谱的原子发布。
- 新检查仅位于 `backend/tests/test_workbench_assistant.py` 和 `backend/tests/test_workbench_assistant_concurrency.py`。

## 最终端点合同

前缀：`/api/v1/workbench/assistant`。所有入口仅管理员；实际身份同时支持 `local-development` 本地模式和启用的 LAN ADMIN 账号。工作中重新核对管理员状态。

| 方法与相对路径 | 输入 | 行为 |
| --- | --- | --- |
| POST 空路径 | multipart `files`、JSON数组字符串 `paths`、`message`、`model_egress_approved=true`、`mode=auto` | 不上传文件也可查询或修改已有知识；显式授权 false 保存为 PAUSED，不创建模型任务 |
| GET 空路径 | 无 | 最近100个会话摘要 |
| GET `/{id}` | 无 | 持久会话、全文草稿、覆盖、依赖、回答及当前任务 |
| POST `/{id}/messages` | `version,message`，可选 `mode,model_egress_approved` | 可在运行中纠偏；取消旧任务、递增请求版本并清除旧草稿及审批；阅读凭据保留 |
| POST `/{id}/confirm` | `version`，可选 `review_digest` | 对具体清单的一次确认即审批；同事务保存 APPROVED 与发布任务 |
| POST `/{id}/pause` | `version` | 阅读变为 PAUSED；发布变为 REVIEW 并撤销审批 |
| POST `/{id}/cancel` | `version` | 阅读变为 CANCELLED；发布变为 REVIEW 并撤销审批 |
| POST `/{id}/retry` | `version` | 仅 FAILED/CANCELLED/PAUSED 的阅读可继续，须授权 true；发布须重新 confirm |
| PATCH `/{id}/consent` | `version,model_egress_approved` | false 停止后续模型调用；true 仅开启授权，仍需明确 retry；不自动继续或发布 |
| GET `/{id}/source` | `path,cursor=0` | 原始正文分页；cursor 是字符偏移 |
| GET `/{id}/readings` | `path,cursor=0` | 持久阅读凭据分页；cursor 是零起始段索引 |

`mode` 为 `auto|answer|edit`。answer 不允许知识变更；auto 结合最新用户请求规划，所有修改仍须具体清单审批。JSON 输入禁止多余字段和宽松类型转换。

请求使用响应中的 **session.version**，不要使用 `request_version` 或任务版本。旧版本返回409。恢复、纠偏、取消和授权变更均不能沿用旧发布审批。

详情保留 `plan[].before/after/diff/source_paths/categories/role/reason/expected_version`，并提供 `expected_lock/expected_sha256/expected_fingerprint/sources`、`bundle_manifest`、`review_digest`、`answer_evidence`。
来源分页返回 `path,content,start,end,total_characters,sha256,next_cursor`；阅读分页返回 `items,total,next_cursor`。

前端代理已直接收到合同，并回复已接入暂停、取消、继续、授权开关、目录路径、版本冲突和一次确认。前端检查由前端代理单独记录。

## 阅读、依赖与审批约束

所有已接收文件先完整读取，再规划。每4000字符一段，凭据绑定路径、全文SHA-256、区间和段落SHA-256；已完成段在暂停、进程中断和继续后复用。脱敏先匹配完整正文再切段，保持字符偏移和换行，避免跨段秘密泄漏。管理员看到原始全文及确切差异。

一个文件允许用重叠或拆分区间服务多个用途；多个合并可顺序作用于同一目标。每个上传字符必须被变更或显式 skip 区间覆盖。模型不能提供任意目标版本、未知来源、越界区间或非唯一修改锚点。读取目标同时固定正文、版本、乐观锁及相关元数据指纹。

根 SKILL、相对文件、目录依赖、带空格的尖括号链接以及章节锚点均形成映射。`bundle_manifest` 每个路径有 `document_id` 和 `document_ids`；`references` 是归一化对象，包含引用、解析路径、目标编号、外部标记/目录标记等；同时保存 `original_references` 原始字符串和 `resolved_references` 对象。父代理更新后的诊断依赖解析器支持此合同。依赖文件的 skip 必须关联已经共享发布的目标，或关联同一清单将发布的目标。

创建、替换、合并、关联和跳过都校验已读凭据。问答必须引用已读来源的具体区间；没有定位到知识时可返回补充信息的回答。审批摘要绑定清单、覆盖、依赖、模式、请求版本、授权和回答证据；任何纠偏清除旧审批。

输入限制明确报错，不静默忽略或截断：最多64个UTF-8文本文件，单文件1MiB、整组8MiB；已定位知识最多64篇且每篇不超过100万字符；草稿最多128项，累计原文/新文/差异1600万字符，单个候选正文100万字符。每次运行最多256次Chat请求、96步规划和3600秒任务时限；阅读/规划预算耗尽保存为 PAUSED，继续入口恢复进度。上下文为分页工具结果和可重新读取的持久记录，超预算显式停止。

## 发布和恢复

会话与关联 Job 在同一事务提交，提交之后才唤醒调度器，没有先入队后补 job_id 的窗口。API 模块导入仍注册 `assistant_plan` 与 `assistant_publish`。

工作者核对请求版本、session.job_id、Job状态、执行轮次、worker token、lease owner和到期时间；会话及任务行获写入围栏。旧工作者无法覆盖纠偏、取消或新接管者。

发布采用私有向量/图谱代。待发布分块持久化为 version 0，向量及图谱输入使用对应的最终文档版本。最终事务校验审批、完整知识/分块快照、目标及Embedding配置，再一次性更新全部文档/修订/发布清单、分块版本、向量活动指针、图谱活动指针、会话和Job完成标记。多项同目标修改只产生一个最终新版本。构建或最终事务任意失败，原已发布正文及两类活动索引均保留。

`recover_abandoned_assistant_sessions(db)` 保持可导入，父代理在 jobs 回收过期租约后、提交前调用。阅读恢复复用凭据；发布中断释放且仅释放自己的图谱构建标记，清除审批并回到 REVIEW。通用任务重试不能直接重用旧发布审批。

父代理负责共享 jobs.py 的事务完成后检查、lease 丢失 no-op 和错误/取消更新围栏，已反馈相应定向检查；本助手没有修改该文件。

## 本次检查证据

所有命令在 `D:\GRXM\debugplatform\backend` 下使用指定 `.venv` Python，数据为临时SQLite和纯合成文本，Chat、向量、图谱均使用Fake实现，无公司数据、模型密钥读取或外部模型调用。

```powershell
& 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_assistant.py tests/test_workbench_assistant_concurrency.py -q --tb=short
```

结果：**55 passed，181.08秒**。覆盖全文、所有操作映射、单文件多用途、连续合并、严格结构/证据、无上传问答和修改、阅读预算与恢复、授权关闭、并发纠偏/确认、失效目标、租约接管、取消、通用错误重试、整组回滚、构建竞争及事务任务完成。

其后最终归一化manifest兼容调整及新增“已有SKILL自动读依赖”检查使用以下定向命令核对：

```powershell
& 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_assistant_concurrency.py::test_angle_links_and_directory_dependencies_are_complete tests/test_workbench_assistant_concurrency.py::test_no_upload_reads_published_skill_and_normalized_dependencies tests/test_workbench_assistant.py::test_full_folder_all_characters_and_durable_receipts tests/test_workbench_assistant.py::test_success_publishes_bundle_once_and_keeps_old_chunks -q --tb=short
```

结果：**4 passed，12.37秒**，其中3项为受调整影响的重查、1项为新增检查；合计 **56个不同的新检查** 已通过。此前首轮的LAN fixture字段错误及独立测试模块导入错误已修正，并被上述通过结果覆盖。

最终源码AST/行数检查复用 `scripts/check_architecture.py` 的复杂度函数，仅扫描助手范围：API 221行，服务最大318行，最高函数复杂度47（限制分别650/900/50）。助手OpenAPI生成成功：10个路径、11个操作。

## 边界及父代理后续

- 未运行历史回归、Full、手动CI、真实模型质量评测、真实远程向量库、PostgreSQL并发或公司LAN实机；不能将Fake流程检查描述成上述验收。
- 失败构建的私有代、version 0分块和DRAFT预留行保留，未新增后台垃圾回收；它们不进入活动检索，后续重新确认不会把旧分块带入新版本。
- 旧的无可校验凭据/审批摘要的半成品会话不能直接发布，需要重新创建安全会话。
- 按分工没有编辑共享 `docs/README.md`、CAPABILITIES、HARNESS、VALIDATION或workflow合同。父代理需加入本交接链接、汇总范围，并完成仓库护栏及封装检查。
- 此交接写入后停止助手源码修改；父代理可继续集成封装。
