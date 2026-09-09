# 0.4.0 报告交接记录

报告子范围已完成实现。新增合成测试共 41 项，按变更和失败原因分组执行，累计最新结果全部通过。未运行历史回归、Full 或 CI，未提交或推送。

## 文件范围

- `backend/app/services/report.py`：事务复用、固定配置解析及 Markdown/HTML/Word/PDF 导出。
- `backend/app/services/category_report.py`：共享报告正文、保守回退、被动 Markdown 解析和 HTML 转义。
- `backend/app/services/report_contract.py`：模板快照、提示词助手、四章及证据引用校验。
- `backend/app/services/diagnosis_contract.py`：报告校验入口和可选建议类别字段。
- `backend/app/templates/network-report.md`：按用户原始格式规范整理的内置 v2 模板；无案例示例数据。
- `backend/tests/test_workbench_report.py`：本次新增的隔离检查。
- 本交接文件。其他代理、共享总账与业务数据未改动。

原始格式依据为用户附件 `C:\Users\23173\.codex\attachments\836c1770-8b92-48b3-9e15-c80614371858\pasted-text.txt`。模板保留组网总览、逐设备时间轨迹、逐 AP 三层分析与因果链、结论与后续方向四章；未复制原文示例中的设备、地址、参数、时间或节点数。

## 主线接入契约

```python
configuration = workbench.resolve_configuration(db, stored_model_config)
prompt.update(report_instructions(configuration))

validated = validate_llm_diagnosis(
    payload,
    valid_evidence_ids,
    required_fault_tree_items,
    report_template=configuration.get("report_template"),
    case_evidence_ids={
        str(item["evidence_id"])
        for item in evidence
        if is_case_log_evidence(item)
    },
)

context = get_report_context(case_id, analysis_id, db=db)
submission_markdown = report_markdown(context)
```

- `report_template=` 是主线当前使用的关键字；也保留 `template_snapshot=` 别名。两者同时提供且不同会报错。
- 新模板且 `report_markdown` 非空时必须显式传 `case_evidence_ids`，不能把一般检索白名单中的方法/知识当作当前案例事实。实际检查使用该集合与 `valid_evidence_ids` 的交集。
- `get_report_context(case_id, analysis_id, db=None)` 对传入数据库会话使用 `nullcontext`，不关闭或提交调用方事务；在事务内调用 `workbench.resolve_configuration`，因此支持未提交的 RC 快照及 library submission。
- 上下文含 `configuration`、`report_template` 和经过 `is_case_log_evidence` 筛选的 `case_evidence_ids`。
- `report_instructions` 接收已解析配置；未解析的 RC 指针会报错，避免误用当前内置模板。
- `suggested_problem_category` 与 `category_reason` 均可省略；有建议时必须成对出现，理由引用本案例证据。类别 ID 允许大小写字母、数字、下划线、连字符，首字符字母或数字，最长 80 字符，兼容 `WB-...`。主线负责与本次运行可用类别集合核对，建议不会自动修改案例类别。
- 主线负责生成、修订、应用修订、Host 四入口接入；本子范围未编辑这些调用文件。`workbench.template_snapshot` 当前已转为调用 `builtin_report_template()`，与本实现的 v2 一致。

## 模板与兼容策略

- 类别模板选择及组网回退仍由主线 `workbench.template_snapshot` 决定；报告只消费运行时固定的内容、ID、版本、类别和 SHA-256，不查询当前发布模板。
- 内容哈希不符或 RC 快照损坏会在新导出发布之前失败，不用新模板悄悄替换旧快照。
- 内置 v2，以及明确包含且仅包含四个标准二级章节的发布模板，执行四章结构、拓扑表、独立 AP 轨迹/分析、三层顺序及停止规则、因果链、根因总览、缺失证据表等校验。
- 发布模板可以是规范型 Markdown。对于没有标准四章脚手架的规范型模板，其标题是写作指引，不强制成为报告章节；仍校验本案例引用及事实表格引用。这避免“目的/格式/示例”类模板使所有新诊断失败。
- 没有模板快照的历史记录、内置 v1 保留旧自由格式兼容路径。生成新版本不会修改原 `AnalysisRun` 或已发布报告文件。
- 没有模型 Markdown 的结构化结果会生成完整四章回退，保留已有摘要、事实、候选解释、处理优先级和实际故障树账本。缺少设备映射及逐层证据时明确写待确认，不通过文件来源、IP 缺失或时间邻近推断 AP 身份或根因。
- 置信度使用 P1（确定）、P2（较确定）、P3（可能）、P4（猜测），与 P0–P3 处理优先级分开。回退不把旧 `confidence_score` 或 `priority` 换算成新置信度。
- 故障树账本按方法与节点身份去重；SUPPORTED 算根因，EXCLUDED 算排除；冲突或缺少有效案例证据的状态标待确认。总数只代表实际提供的账本，未列适用范围不冒充全覆盖。
- 保存的跨类参考原因会呈现为方法指引，并与本案例观察证据区分。

## 导出与渲染

- Markdown、HTML、DOCX、PDF 共用正文和块解析，均显示同一章节、表格、列表及证据位置；新报告引用显示 `文件名:L起-L止`，保留数据库/命令证据的实际显示标签。
- HTML 不激活原始 HTML、图片、链接或脚本，不请求外部资源。标题、文件名和类别说明也经过处理；表格使用 `thead/tbody/th/td`，列表使用 `ul/ol/li`。
- DOCX/PDF 使用 A4 横向与 15 mm 页边距，为 11 列拓扑表留出页宽。Word 表格固定列宽并重复表头；PDF 长单元格可以跨页，表头重复，不静默截断事实。
- Windows PDF 嵌入已安装的微软雅黑常规/粗体及 Segoe UI Symbol，解决中文和 ✅/❌ 缺字。非 Windows 或无该字体时保留 STSong CID 回退；无符号字体时以文字状态表达。未增加依赖、下载或打包字体文件。
- `generate_markdown` 新增服务层 `.md` 导出。现有 HTTP 路由仍提供 HTML/DOCX/PDF；新增 Markdown 下载路由若需要，由主线维护 API 契约。

## 已执行检查

所有 pytest 命令的工作目录为 `D:\GRXM\debugplatform\backend`，解释器固定为 `D:\GRXM\debugplatform\.venv\Scripts\python.exe`。仅运行新文件，使用合成证据、临时 SQLite 和独立存储，不接触业务数据库或模型。

1. 首轮新增检查：24 通过、1 失败、7 fixture 错误。失败原因分别为中文相邻 AP 标识的边界判断，以及 pytest basetemp 父目录尚未存在；均已纠正并定点复核。

```powershell
& 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_report.py -q --basetemp=../artifacts/validation/report-20260909/pytest --junitxml=../artifacts/validation/report-20260909/report.xml
```

2. 仅重跑上述受影响检查：8 通过、24 未选择，10.31 秒。

```powershell
& 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_report.py -q -k 'AP1 or caller_transaction or new_snapshot or category_template or conservative_fallback or all_formats or legacy_files or failed_export' --basetemp=../artifacts/validation/report-20260909/retry1 --junitxml=../artifacts/validation/report-20260909/retry1.xml
```

3. 补充逐 AP 对应、置信度定义、文件名注入、RC 损坏、符号字体和长表格检查，并复核相关结构/导出变更：23 通过、15 未选择，14.20 秒。

```powershell
& 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_report.py -q -k 'every_ap or confidence_definition or malicious_filename or tampered_rc or unicode_symbols or large_chinese or valid_four or invalid_structure or all_formats' --basetemp=../artifacts/validation/report-20260909/final-changes --junitxml=../artifacts/validation/report-20260909/final-changes.xml
```

4. 最后补充显式案例白名单、反斜杠/竖线、类别说明注入，复核相关导出与 schema：8 通过、33 未选择，8.99 秒。

```powershell
& 'D:\GRXM\debugplatform\.venv\Scripts\python.exe' -m pytest tests/test_workbench_report.py -q -k 'new_report_needs or escaped_pipe or suggestion_and_cross or unicode_symbols or passive_html or all_formats or valid_four or category_suggestion' --basetemp=../artifacts/validation/report-20260909/final-render --junitxml=../artifacts/validation/report-20260909/final-render.xml
```

证据目录：`artifacts/validation/report-20260909/`。`summary.json` 按各 XML 中每个测试的最新结果汇总，41 个独立新增检查均为 passed。没有把多轮重复的检查累计为更多测试。

另外执行了四个服务文件的 Python 编译检查、限定文件的 `git diff --check`，并使用仓库 `scripts/check_architecture.py` 的 `_complexity` 函数定点计算最大复杂度：report 17、category_report 26、report_contract 19、diagnosis_contract 28，均低于 50。此项不是完整仓库架构或 Harness 验证。

## 视觉核验及限制

最终合成 PDF 为 `artifacts/validation/report-20260909/final-render/test_all_formats_share_unicode0/storage/reports/CASE-synthetic-report/ANL-synthetic-report_v1.pdf`。使用以下命令渲染后，已检查 `final-page-1.png` 至 `final-page-4.png`：中文、❌、加粗、11 列拓扑表、逐 AP 轨迹、列表及结论表可读，没有观察到截断或重叠。

```powershell
pdftoppm -scale-to 1600 -png 'D:\GRXM\debugplatform\artifacts\validation\report-20260909\final-render\test_all_formats_share_unicode0\storage\reports\CASE-synthetic-report\ANL-synthetic-report_v1.pdf' 'D:\GRXM\debugplatform\artifacts\validation\report-20260909\final-page'
```

Poppler 输出了环境字体提示 `No display font for 'Symbol'` / `ArialUnicode`，退出码为 0；最终报告正文使用已嵌入字体，四页人工检查未出现缺字方框。

Word 已通过内容、中文 EastAsia 字体声明、表格列宽、表头重复等检查；实际渲染尝试失败，原因是 `LibreOffice soffice.exe was not found on PATH`。使用的命令如下，未为此安装新依赖。因此不能宣称已经通过 Word/LibreOffice 实际分页视觉验收。

```powershell
& 'C:\Users\23173\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' 'C:\Users\23173\.codex\plugins\cache\openai-primary-runtime\documents\26.905.11957\skills\documents\render_docx.py' 'D:\GRXM\debugplatform\artifacts\validation\report-20260909\retry1\test_all_formats_share_unicode0\storage\reports\CASE-synthetic-report\ANL-synthetic-report_v1.docx' --output_dir 'D:\GRXM\debugplatform\artifacts\validation\report-20260909\docx-render' --emit_pdf --verbose
```

结构及 ID 白名单校验不能替代对日志事实、跨设备身份关联、每一步因果机制和置信度强度的语义审核。规范型自定义模板的自然语言要求由固定提示词传给推理入口，不能从任意规范文字自动编译出完整语义验证器。本次没有真实模型调用或公司材料验收。

主线仍需把本交接加入 `docs/README.md` 并同步共享真值文档；按分工本子范围未编辑这些文件。完整 Harness、CI、安装包及公司实机验收由主线统一记录，不能用本次定点结果替代。
