# 大模型文件夹案例提炼与人工校正

本功能把一个包含日志、错误现象、人工分析和解决方案的文件夹，转换为可追溯的
Markdown 故障案例草稿。模型输出不会直接进入在线知识检索：工程师必须先在工作台中
逐条核对、与模型多轮纠错并人工确认，随后还要经过知识库原有的“提交审核 → 发布”流程。

## 1. 使用前准备

1. 运行 `scripts\start_local.bat`。
2. 进入“系统设置 → 诊断大模型”，新增一个公司批准的 OpenAI-Compatible Chat 模型。
3. 测试并启用该模型。内网模型地址需要按 README 配置
   `MODEL_ENDPOINT_ALLOWLIST`。
4. 准备一个单案例文件夹。建议用文件名表达用途，例如：

```text
AP-auth-timeout/
├─ 01-error-description.md
├─ 02-device.log
├─ 03-analysis.txt
└─ 04-solution-and-validation.md
```

当前支持：

- 文本和无后缀文本：按内容识别 UTF-8、UTF-16、GBK/GB18030 等常见编码；
- HTML/HTM/XHTML：提取标题、段落、列表和表格等正文，忽略脚本、样式、模板、显式
  `hidden`/`aria-hidden` 和内联隐藏内容，不加载远程资源；
- Word `.docx`：提取正文段落和表格，不执行宏或外部链接；
- PDF：逐页提取已有文本层，并插入 `[PDF page N]` 页码标记。

旧式二进制 `.doc` 需要先转换为 `.docx`；扫描 PDF 和图片当前没有 OCR；Excel、PCAP、
ZIP/TAR 等其他二进制会保留在本地来源目录中，并在来源列表标记为未纳入模型证据。

## 2. 页面操作

1. 打开顶部“AI 案例提炼”，或从“分层知识库”点击“AI 文件夹提炼”。
2. 点击“选择文件夹并提炼”，选择整个案例文件夹。
3. 选择 Chat 模型、知识分类、可信级别和保密级别；设备、型号、固件和模块可预填。
4. 确认“模型数据出站”。只有这一步明确授权后，API 模型才会收到脱敏证据。
5. 上传后任务在后台生成 v1 初稿。关闭页面或刷新不会丢失会话。
6. 在“来源文件”中核对纯文本原文或文档的本地提取正文，在 Markdown 中核对
   `[SRC-xxxx:Lx-Ly]` 引用。
7. 可以直接修改 Markdown 并保存，也可以在右侧告诉模型哪里错误、如何修正。每次保存或
   对话都会创建新的不可变版本，旧版本可恢复。
8. 当结构和引用校验通过后，点击“确认无误并加入知识库草稿”。
9. 返回“分层知识库”，提交审核并发布。只有 `ACTIVE` 的已发布知识才参与检索、RAG 和
   领域图谱构建。

建议每次只上传一个故障案例。若一个目录混有多个无关问题，先拆成多个文件夹，避免模型
把不同设备、时间段或根因错误合并。

## 3. 生成与纠错规则

初稿必须至少包含：

- 错误形式；
- 日志分析；
- 错误定位；
- 解决方案；
- 验证结果；
- 适用范围与限制；
- 来源证据。

关键事实必须引用来源，例如 `[SRC-0002:L37-L52]`。后端会验证来源编号、起止行号和
章节完整性。来源不存在、行号越界、没有带行号的引用或缺少必需章节时，页面会显示
警告并禁用最终确认。证据不足的内容应写“待确认”，而不是让模型补造结论。

模型对话不是只返回一段答复：它必须同时返回完整的新 Markdown。后端采用
`expected_draft_version` 做并发检查；如果另一窗口已保存新版本，旧窗口的修改会被拒绝，
需要刷新后重新操作。恢复旧版本也会创建一个新版本，不会覆盖审计历史。

## 4. 数据和模型边界

数据流如下：

```text
浏览器文件夹
  → 本地流式保存、路径/数量/大小校验、SHA-256
  → 本地文本探测；HTML/DOCX/PDF 转为 UTF-8 可核对正文
  → 以稳定提取行号执行长文抽样
  → 密码/Token/API Key、IP、MAC、序列号脱敏
  → 限长、带行号的 evidence_for_model.md
  → 经批准的 Chat 模型 API
  → 结构化 JSON + Markdown 草稿
  → 后端章节/来源/行号校验
  → 人机多轮校正与不可变版本
  → 人工确认
  → 不可检索的 KnowledgeDocument DRAFT
  → 审核发布后参与检索
```

原始文件不会直接发送给模型。模型只收到：

- 每个可读来源的编号、相对路径、用途和原文件 SHA-256；
- HTML/DOCX/PDF 的本地提取方式、页数和是否因安全上限截断；
- 短文件的完整限长内容，或长文件的开头、错误/告警/根因/解决关键词附近行和结尾；
- 本地掩码后的文本，以及页面填写的脱敏元数据；
- 继续纠错时的当前草稿、最近对话和相同证据包。

默认限制可在 `.env` 调整：

| 配置 | 默认值 | 作用 |
| --- | ---: | --- |
| `CURATION_MAX_FILES` | 500 | 单次文件数 |
| `CURATION_MAX_TOTAL_BYTES` | 512 MiB | 文件夹总大小 |
| `CURATION_MAX_FILE_BYTES` | 128 MiB | 单文件大小 |
| `CURATION_MAX_PROMPT_CHARS` | 120,000 | 发送给模型的证据字符上限 |
| `CURATION_MAX_DRAFT_CHARS` | 500,000 | Markdown 草稿字符上限 |
| `CURATION_MAX_EXTRACTED_TEXT_CHARS` | 4,000,000 | 每个 HTML/DOCX/PDF 的本地提取正文上限 |
| `CURATION_MAX_DOCUMENT_UNCOMPRESSED_BYTES` | 256 MiB | DOCX ZIP 包解压后安全上限 |
| `CURATION_PDF_MAX_PAGES` | 500 | 单个 PDF 最多提取页数 |
| `CURATION_PDF_MAX_CONTENT_STREAM_BYTES` | 64 MiB | 单 PDF 内容流处理上限 |

文件名和文件内容都按不可信输入处理。系统提示明确禁止执行来源中的指令，以降低日志中
提示注入的风险；但规则脱敏不等同于完整 DLP，上传公司材料前仍要遵守内部数据制度并
选择受批准的模型网关。

## 5. 持久化与审计

数据库迁移 `0010` 新增四类记录：

- `knowledge_curation_sessions`：状态、元数据、模型快照、当前草稿和知识文档关联；
- `knowledge_curation_source_files`：来源编号、相对路径、原文/提取正文哈希、提取方式、
  页数、编码、行数、截断和纳入状态；
- `knowledge_curation_revisions`：每版 Markdown、内容哈希、校验结果和变更说明；
- `knowledge_curation_messages`：工程师、模型和系统消息。

来源副本、HTML/DOCX/PDF 的 UTF-8 提取 sidecar 与模型证据包位于 Storage 的
`curations/<session-id>` 下，路径以相对 storage key 保存，可随项目备份迁移到另一台电脑。未入库会话可以在页面删除；已经确认的来源会保留，
用于知识 provenance。模型快照不包含 API Key，审计日志不记录正文和提示词。

状态流转为：

```text
QUEUED → EXTRACTING → REVIEWING → CONFIRMING → CONFIRMED
                    ↘ FAILED / CANCELLED → retry
```

`CONFIRMED` 只表示人工确认并创建了知识草稿，不等于知识已经发布。

## 6. 常见问题

### 页面没有可选模型

内置 Mock 模型不能用于文件夹提炼。请在系统设置中创建并启用 Chat 类型的
OpenAI-Compatible API Profile。本项目当前尚未提供本地 Chat 推理运行器；本地 BGE 和
Qwen3 Reranker 不负责生成 Markdown。

### 文件显示“已跳过”

文件可能是空文件、损坏/加密文档、不受支持的文本编码、旧式 `.doc`，或没有文本层的
扫描 PDF。只要平台成功得到可读正文，“查看”按钮就可核对模型发送前使用的本地内容；
无法提取正文的文件会禁用查看并显示具体原因。来源表会显示纯文本、HTML 正文、Word
DOCX 或 PDF 文本层，以及是否截断。若整个文件夹没有任何可提取正文，任务会明确失败，
不会生成空草稿。

### 草稿无法确认

先查看页面的校验警告。最常见原因是缺章节、引用不存在、引用没有行号或行号超出来源
范围。修正并保存后，后端会重新校验。

### 模型提炼失败

检查模型 Profile 的连接测试、Base URL 白名单、API Key、超时和模型 JSON 输出能力。
初次提炼失败且还没有草稿时，可以在页面重新提交；原始来源不会因一次模型失败而丢失。

### 换电脑后会话是否保留

Git 只同步代码，不同步数据库和日志。要迁移提炼会话、来源证据和知识审核状态，请在旧
电脑停止平台后运行 `scripts\backup_local.bat`，在新电脑使用
`scripts\restore_local.bat` 恢复。只执行 `git pull` 不会携带这些运行数据。

## 7. 相关接口

管理员接口前缀为 `/api/v1/knowledge-curations`：

- `POST /`：multipart 文件夹上传并创建后台提炼任务；
- `GET /`、`GET /{id}`：会话列表与详情；
- `GET /{id}/sources/{source_id}/preview`：分页查看本地来源；
- `POST /{id}/chat`：与模型讨论并生成下一版；
- `PATCH /{id}/draft`：保存人工修改；
- `POST /{id}/revisions/{version}/restore`：从历史内容创建新版本；
- `POST /{id}/confirm`：人工确认并创建知识库 `DRAFT`；
- `POST /{id}/retry`、`DELETE /{id}`：重试或删除未入库会话。

交互式请求结构可以在启动后的 `/docs` 查看。所有接口要求管理员权限；这样可以避免普通
用户把未经审核的数据发往模型，或绕过知识发布治理。
