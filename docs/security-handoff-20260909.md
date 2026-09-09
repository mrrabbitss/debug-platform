# 0.4.0 权限交接（2026-09-09）

本分工实现及新增定向检查已完成。工作区直接编辑，没有提交、推送、重置或运行历史回归。
仓库总账、完整护栏与最终交付结论由主任务汇总；本文不表示整个 0.4.0 已可发布。

## 实现范围

- `backend/app/services/access_control.py`：按配置的 API 前缀和完整资源段判权，关闭新系统管理路径及工作台管理路径的默认放行；工程师只可写自己的模型预设与提交案例库条目。管理员保留完整权限，VIEWER 只读。
- 系统写操作全部要求 ADMIN；保留既有工程师 GET 模型信息、模型列表、下载目录、检索概况和成员选择目录以兼容客户端。用户管理、审计、全局运行/治理等管理视图仍只对管理员开放；工作台 bootstrap 只返回可选预设的 ID、名称和活动标记。
- `backend/app/services/knowledge_access.py`：统一 `require_knowledge_admin`，REST、MCP 与领域权限入口复用；文档所有者、原发布者、个人草稿不授予写入或审批权限。知识导入、编辑、提炼、建议、修订、分类、审核、发布、索引与相关任务均受管理员边界保护。
- 普通用户知识读取限定为已发布且 PUBLIC/INTERNAL 的文档、分段、可见发布历史与历史证据；管理质量视图及未声明的新知识子路径默认拒绝。历史版本必须属于同一文档且具有可见快照，非当前版本还必须有发布记录。
- `backend/app/services/knowledge_personal.py`：保留前序已完成的禁用新个人覆盖行为并明确用途。任何新运行的 `personal_view` 都为空，已固定的工作修订仍按原 ID 读取，不改写或删除历史快照。原有撤回文档停止新检索的规则保留。
- `backend/app/mcp/debugplatform_registry.py`：路由上下文和路由应用先执行 ADMIN 领域校验，再访问或修改文档；移除 ENGINEER 死分支。分段读取继续允许普通用户访问已发布可见知识，ADMIN 可读草稿。工具说明与权限同步，工具总数保持 18。
- `workflow/skill.yaml`、`workflow/openapi.yaml`：现有 38 个允许的 REST 操作全部补齐角色元数据；覆盖当前 19 个工作台操作和对应请求 schema。新增 5 个普通工作台入口，另将 14 个管理员操作列为 `rest_only_endpoints` / `x-agent-callable: false`，不扩展 MCP 写工具。包含助手 cancel/pause/retry/consent/source/readings 六个新路径。

## 主任务协作修改（本分工只读并验证）

- `core/security.py`：公共 health/auth-info 豁免改为配置 API 前缀下的精确 GET/HEAD 路径。新增测试验证名为 `health` 的知识/案例及其嵌套路径不能跳过鉴权。
- `api/knowledge_routing.py`、`api/knowledge_drafts.py`：管理员检查及所有草稿操作的 `require_publisher` 已收紧，旧工程师分支移除。新增直接函数调用测试验证无法跳过 REST 依赖恢复写权限。
- `services/workbench_library.py`：存在案例、负责人/管理员、匹配且 COMPLETED 的诊断、服务端报告生成、ADMIN 共享确认和并发版本锁；新增 REST 与领域测试覆盖。
- 主任务已同步 `workflow/mcp-tools.yaml`、canonical Skill、两个镜像及 workflow README，本文没有编辑这些文件。
- `backend/tests/test_personal_knowledge.py` 中前序代理的 0.4.0 期望调整原样保留，本轮没有运行其历史整套测试。

## 已执行的定向验证

以下命令的工作目录均为 `D:\GRXM\debugplatform\backend`，解释器均为指定 `.venv`。共 **25 个不同的新增用例通过**；最新助手路径加入后仅重复其受影响的端点覆盖用例，本地配置夹具调整后仅重跑该用例。

1. REST/领域初组：

   ```powershell
   & 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_access.py -q --tb=short --maxfail=2
   ```

   **12 passed in 12.63s**。覆盖知识、系统、任务、历史读取、个人预设、本地管理员及 health 路径。早期夹具曾因 LAN 配置不完整和缺少 chunk_index 阻止执行，均已修正；此记录为修正后的通过结果。

2. MCP HTTP 与案例库：

   ```powershell
   & 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_access_mcp.py tests/test_workbench_access_library.py -q --tb=short
   ```

   **8 passed in 7.53s**。MCP 使用实际 Bearer 令牌解析器、注册表及 Streamable HTTP ASGI 传输，验证已暴露工具的角色绕过、伪造 role 参数与发布/草稿读隔离；案例库验证负责人提交、未完成/跨案例分析拒绝、普通用户不能确认、确认前后可见性、领域入口与并发审批。

3. 最新助手路径、管理员草稿和契约：

   ```powershell
   & 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_access_contract.py tests/test_workbench_access.py::test_admin_can_edit_and_submit_a_draft_while_publication_stays_readable tests/test_workbench_access.py::test_every_registered_knowledge_mutation_rejects_direct_engineer_and_viewer tests/test_workbench_access.py::test_every_assistant_read_route_is_admin_only tests/test_workbench_access.py::test_legacy_proposal_and_routing_handlers_cannot_reenable_engineer_writes -q --tb=short
   ```

   **6 passed in 5.26s**。当前覆盖 33 个知识/助手写操作（逐一验证 ENGINEER 与 VIEWER 403）、12 个系统写操作、4 个管理员助手读取操作；契约检查验证 YAML 唯一键、角色一致性、19 个工作台操作与运行时请求/参数及 schema 完全一致，MCP 工具没有扩张。

4. 本地模式夹具使用 dev + standalone + local 后：

   ```powershell
   & 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_access.py::test_local_development_keeps_admin_bootstrap_and_management -q --tb=short
   ```

   **1 passed in 2.87s**。bootstrap、管理员分类、个人预设与受限知识读取均可用。

5. 指定生产文件及工作流契约的 `git diff --check` 通过。仅有已有 CRLF→LF 提示，没有空白错误。没有执行仓库完整护栏；父任务明确负责最终汇总。

## 证据范围与限制

- 数据均为临时 SQLite 和合成文档/案例；无公司资料，无真实模型调用，无模型密钥，没有启动业务数据库或持久后台任务。
- 这些测试使用真实 API 路由及鉴权依赖，但不启动完整应用生命周期、监听端口、TLS 网关或浏览器。MCP HTTP 为进程内 ASGI 测试，不能替代真实多机/TLS 验收。
- 权限测试隔离了磁盘容量检查。案例库报告权限测试替换了纯渲染函数，用于证明诊断 ID 来自服务器校验、报告正文不信任客户端；四章报告内容和多格式布局由报告分工验证。
- 新增历史快照检查证明固定内容不随草稿或当前正文变更，未重跑历史检索/发布完整套件。低层可信 worker 持久化函数的全面审计、外部服务、PostgreSQL、安装包、Full 和 CI 不在这些证据范围内。
- 所有新增测试文件均为 `backend/tests/test_workbench_access*.py`。本轮没有修改共享总账或无关文件；请主任务将本文加入 `docs/README.md` 并汇总最终护栏结果。
