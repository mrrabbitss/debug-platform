"""One report and a passive Markdown parser shared by HTML, Word and PDF.

Raw HTML, images and links stay text. No renderer fetches remote resources.
"""
from __future__ import annotations

import html
import re

from app.services.evidence_display import replace_evidence_ids
from app.services.report_contract import (
    CHAPTERS, LAYERS, ROOT_COLUMNS, TIMELINE_COLUMNS, TOPOLOGY_COLUMNS,
    REFERENCE, structured_template, table_cells, validate_report_markdown,
)


def literal(value) -> str:
    """Put untrusted structured values into one Markdown line without markup."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "待确认")).strip()
    return re.sub(r"([\\`*\[\]_<>#|])", r"\\\1", text)


def _table(columns, rows):
    return "\n".join(["| " + " | ".join(columns) + " |",
                      "| " + " | ".join("---" for _ in columns) + " |"] +
                     ["| " + " | ".join(row) + " |" for row in rows]) + "\n\n"


def _cited_literal(value):
    parts = REFERENCE.split(str(value))
    return " ".join("[[" + part + "]]" if index % 2 else literal(part) for index, part in enumerate(parts)).strip()


def _cross_category_notes(context):
    notes = []
    for item in context["evidence"].values():
        metadata = item.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        reason = metadata.get("cross_category_reason")
        if reason:
            categories = metadata.get("problem_categories") or []
            if isinstance(categories, str):
                categories = [categories]
            note = "- 跨类别参考：" + literal(item.get("title") or "来源名称待确认")
            note += "；来源类别：" + literal("、".join(str(value) for value in categories) or "待确认") + "；原因：" + literal(reason)
            if note not in notes:
                notes.append(note)
    return "\n\n跨类别参考说明（方法指引，不能作为当前案例事实）：\n\n" + "\n".join(notes) if notes else ""


def _citations(ids, allowed):
    refs = list(dict.fromkeys(str(key) for key in ids or [] if str(key) in allowed))
    return "、".join("[[" + key + "]]" for key in refs) or "待确认：缺少本案例证据"


def _facts(result, allowed):
    return "\n\n".join("- " + literal(fact.get("statement", "待确认")) + "\n\n> 证据：" +
                         _citations(fact.get("evidence_ids"), allowed)
                         for fact in result.get("confirmed_facts", []) if isinstance(fact, dict)) or "待确认：暂无已确认事实。"


def _coverage(result, allowed):
    coverage = (result.get("diagnostic_planning") or {}).get("fault_tree_coverage") or {}
    groups = {}
    for item in coverage.get("items", []):
        if not isinstance(item, dict):
            continue
        identity = (item.get("method_document_id"), item.get("id") or item.get("item_id"))
        if all(identity):
            groups.setdefault(identity, []).append(item)
    if not groups:
        return "待确认：未提供完整适用的故障树账本，已排查数、已定位数及总数未知。\n\n"
    supported = excluded = attempted = 0
    rows = []
    for (method, node), duplicates in groups.items():
        item = duplicates[0]
        statuses = {entry.get("status") for entry in duplicates}
        ids = list(dict.fromkeys(key for entry in duplicates for key in entry.get("evidence_ids", [])))
        verified = bool(ids) and set(ids) <= allowed
        status = item.get("status") if len(statuses) == 1 else "CONFLICT"
        if status in {"SUPPORTED", "EXCLUDED"} and not verified:
            status = "UNVERIFIED"
        supported += status == "SUPPORTED"
        excluded += status == "EXCLUDED"
        attempted += any(entry.get("attempted") for entry in duplicates) or status in {"SUPPORTED", "EXCLUDED"}
        label = {"SUPPORTED": "已定位根因", "EXCLUDED": "已排查并排除", "INSUFFICIENT_EVIDENCE": "证据不足",
                 "CONFLICT": "待确认：账本状态冲突", "UNVERIFIED": "待确认：账本缺少有效证据"}.get(status, "未覆盖/待确认")
        rows.append([literal(item.get("title") or item.get("label") or node), literal(method), label,
                     _citations(ids, allowed), literal(item.get("next_action") or "待确认：补充节点所需证据")])
    total = len(groups)
    text = f"本次账本已排查：{attempted}/{total}；已定位根因：{supported}/{total}；已排除：{excluded}/{total}。\n\n"
    text += "覆盖范围仅限本次账本；未列入的适用节点总数待确认。排除节点不计为根因。\n\n"
    return text + _table(["节点", "方法", "状态", "证据", "后续方向"], rows)


def _fallback_markdown(context):
    """Untyped prose cannot establish device identities or causal links.

    Preserve existing observations and candidates while explicitly marking the
    absent topology/per-AP structure. Never copy template example data.
    """
    result, allowed = context["result"], context["case_evidence_ids"]
    text = "# " + literal(context["title"]) + "\n\n## " + CHAPTERS[0] + "\n\n"
    text += _table([*TOPOLOGY_COLUMNS, "证据"], [["待确认"] * 11])
    text += "待确认：现有结果未提供可核对的设备拓扑字段；采集设备清单、上行及父子关系后逐字段核对。\n\n"
    text += "## " + CHAPTERS[1] + "\n\n### 设备归属待确认\n\n" + _table(TIMELINE_COLUMNS, [["待确认"] * 4])
    text += "待确认：缺少已核对的逐 AP 身份、时间校准及事件映射；不能把不同设备的观察混入一条轨迹。\n\n"
    text += "## " + CHAPTERS[2] + "\n\n### AP归属待确认\n\n"
    for index, layer in enumerate(LAYERS):
        text += "#### " + layer + "\n\n结论：" + ("证据不足/待确认：设备关联及本层观察尚未核验。" if not index else
                                                 "未继续排查：下层待确认；现有上层观察不能证明下层正常。") + "\n\n"
    text += "因果链待确认：设备关联、分层判断及因果机制尚未形成可核验链条。\n\n"
    text += "## " + CHAPTERS[3] + "\n\n### 根因总览\n\n" + _table(ROOT_COLUMNS, [["待确认"] * 6])
    text += "设备归属与 P1–P4 置信度待确认；以下保留已有候选解释，不将处理优先级换算为置信度。\n\n"
    for item in result.get("hypotheses", []):
        text += "- 候选解释：" + literal(item.get("title")) + "。" + literal(item.get("description")) + "\n\n"
        text += "> 支持证据：" + _citations(item.get("supporting_evidence"), allowed) + "\n\n"
        if item.get("contradicting_evidence"):
            text += "> 反证：" + _citations(item.get("contradicting_evidence"), allowed) + "\n\n"
    text += "#### 后续方向与处理优先级\n\n"
    for number, action in enumerate(result.get("recommended_actions", []), 1):
        text += f"{number}. 处理优先级：" + literal(action.get("priority", "UNKNOWN")) + "；" + literal(action.get("action"))
        text += "。原因：" + literal(action.get("reason")) + "；预期结果：" + literal(action.get("expected_result")) + "\n"
    if not result.get("recommended_actions"):
        text += "待确认：先补充逐设备身份与双侧证据，再确定处理动作及优先级。\n"
    text += "\n### 故障树覆盖\n\n" + _coverage(result, allowed) + "### 缺失证据\n\n"
    rows = [[literal(value), "待确认：相关结论仍缺少核验", "待确认：补充对应设备资料并复核"] for value in result.get("missing_information", [])]
    text += _table(["缺失项", "影响", "获取方式"], rows or [["待确认：设备身份和分层观察", "待确认：无法建立逐 AP 因果链", "采集设备清单与对应 GW/AP 日志并核对时间"]])
    text += "#### 已有摘要与已确认事实\n\n" + literal(result.get("summary") or "待确认") + "\n\n" + _facts(result, allowed) + "\n\n"
    return text


def _legacy_markdown(context):
    result, case, allowed = context["result"], context["case"], set(context["evidence"])
    text = "# " + literal(context["title"]) + "\n\n## 一、问题现象\n\n" + literal(case.description or "未填写") + "\n\n"
    text += "## 二、综合摘要\n\n" + literal(result.get("summary", "暂无诊断摘要")) + "\n\n## 三、根因候选\n\n"
    for item in result.get("hypotheses", []):
        text += "### " + literal(item.get("title")) + "\n\n" + literal(item.get("description")) + "\n\n"
        text += "可信等级：" + literal(item.get("confidence_level")) + "；处理优先级：" + literal(item.get("priority")) + "\n\n"
        text += "> 支持证据：" + _citations(item.get("supporting_evidence"), allowed) + "\n\n"
        if item.get("contradicting_evidence"):
            text += "> 反证：" + _citations(item.get("contradicting_evidence"), allowed) + "\n\n"
    text += "## 四、建议排查步骤\n\n"
    for index, action in enumerate(result.get("recommended_actions", []), 1):
        text += f"{index}. 处理优先级：" + literal(action.get("priority")) + "；" + literal(action.get("action")) + "。" + literal(action.get("reason")) + "；预期结果：" + literal(action.get("expected_result")) + "\n"
    text += "\n## 五、相关代码\n\n"
    for symbol in result.get("related_code", []):
        text += "- " + literal(symbol.get("name")) + " " + literal(symbol.get("file_path")) + ":" + literal(symbol.get("line_start")) + "\n"
    text += "\n## 六、缺失信息与限制\n\n" + "\n".join("- " + literal(value) for value in result.get("missing_information", []) + result.get("limitations", []))
    text += "\n\n## 七、关键证据附录\n\n"
    cited = list(dict.fromkeys(key for fact in result.get("confirmed_facts", []) for key in fact.get("evidence_ids", [])))
    for key in cited:
        item = context["evidence"].get(key)
        if item:
            text += "> " + literal(item.get("content") or item.get("raw_text") or "正文未保存") + " " + _citations([key], allowed) + "\n\n"
    return text + "## 八、已确认事实\n\n" + _facts(result, allowed) + "\n\n"


def report_markdown(context):
    result, template = context["result"], context.get("report_template")
    text = result.get("report_markdown")
    if text:
        if structured_template(template):
            validate_report_markdown(text, context["case_evidence_ids"], template)
    elif structured_template(template):
        text = _fallback_markdown(context)
    else:
        text = _legacy_markdown(context)
    if structured_template(template) and result.get("limitations"):
        text += "\n\n分析限制：\n\n" + "\n".join("- " + literal(item) for item in result["limitations"])
    if result.get("suggested_problem_category"):
        text += "\n\n建议问题类别：" + literal(result["suggested_problem_category"]) + "；依据：" + _cited_literal(result.get("category_reason") or "待确认")
    text += _cross_category_notes(context)
    config = context.get("configuration", {})
    text += "\n\n---\n\n案例：" + literal(context["case"].title) + "\n\n固定问题类别：" + literal(config.get("problem_category") or "未知（历史记录未保存）")
    if template:
        text += "\n\n模板：" + literal(template["id"]) + f" / v{template['version']}；类别：" + literal(template["category"])
        text += "\n\n模板内容 SHA-256：" + template["sha256"]
        if config.get("problem_category") != template["category"]:
            text += "\n\n模板跨类别原因：当前类别未配置专用模板，采用运行开始时固定的组网回退模板。"
    else:
        text += "\n\n模板：历史记录未保存模板快照，沿用历史格式。"
    labels = {key: literal(value) for key, value in context["evidence_labels"].items()}
    text = REFERENCE.sub(lambda match: labels.get(match[1], "证据待核对"), text)
    return replace_evidence_ids(text, labels) + "\n"


_INLINE = re.compile(r"\\([\\`*\[\]_<>#|])|(`+)(.+?)\2|\*\*(.+?)\*\*|(?<!\*)\*([^*]+)\*(?!\*)")


def inline_runs(text):
    """Yield (text, bold, italic, code), escape only at the format boundary."""
    cursor = 0
    for match in _INLINE.finditer(text):
        if match.start() > cursor:
            yield text[cursor:match.start()], False, False, False
        if match[1]:
            yield match[1], False, False, False
        elif match[2]:
            yield match[3], False, False, True
        else:
            for value, bold, italic, code in inline_runs(match[4] or match[5]):
                yield value, bool(match[4]) or bold, bool(match[5]) or italic, code
        cursor = match.end()
    if cursor < len(text):
        yield text[cursor:], False, False, False


def markdown_blocks(text):
    """Parse our passive Markdown subset once for all output formats."""
    lines, blocks, index = text.splitlines(), [], 0
    while index < len(lines):
        line = lines[index]
        index += 1
        if not line.strip():
            continue
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            content = []
            while index < len(lines):
                end = re.fullmatch(r"\s*" + re.escape(fence[1][0]) + "{" + str(len(fence[1])) + r",}\s*", lines[index])
                index += 1
                if end:
                    break
                content.append(lines[index - 1])
            blocks.append({"kind": "code", "text": "\n".join(content)})
        elif index < len(lines) and "|" in line and table_cells(line) and (separator := table_cells(lines[index])) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            header, rows = table_cells(line), []
            index += 1
            while index < len(lines) and lines[index].strip().startswith("|"):
                row = table_cells(lines[index])
                if len(row) > len(header):
                    row = row[:len(header)-1] + [" | ".join(row[len(header)-1:])]
                rows.append(row + [""] * (len(header) - len(row)))
                index += 1
            blocks.append({"kind": "table", "header": header, "rows": rows})
        elif match := re.match(r"^(#{1,6})\s+(.*)", line):
            blocks.append({"kind": "heading", "level": len(match[1]), "text": match[2]})
        elif re.fullmatch(r"\s*(?:-{3,}|\*{3,}|_{3,})\s*", line):
            blocks.append({"kind": "rule"})
        elif match := re.match(r"^\s*(?:([-+*])|(\d+)[.)])\s+(.*)", line):
            block = {"kind": "list", "ordered": bool(match[2]), "start": int(match[2] or 1), "items": [match[3]]}
            while index < len(lines):
                following = re.match(r"^\s*(?:([-+*])|(\d+)[.)])\s+(.*)", lines[index])
                if following and bool(following[2]) == block["ordered"]:
                    block["items"].append(following[3])
                elif lines[index].startswith("  ") and lines[index].strip() and not lines[index].lstrip().startswith(">"):
                    block["items"][-1] += " " + lines[index].strip()
                else:
                    break
                index += 1
            blocks.append(block)
        elif line.lstrip().startswith(">"):
            blocks.append({"kind": "quote", "text": line.lstrip()[1:].lstrip()})
        else:
            blocks.append({"kind": "paragraph", "text": line})
    return blocks


def inline_html(value):
    chunks = []
    for text, bold, italic, code in inline_runs(value):
        text = html.escape(text)
        if code:
            text = "<code>" + text + "</code>"
        if bold:
            text = "<strong>" + text + "</strong>"
        if italic:
            text = "<em>" + text + "</em>"
        chunks.append(text)
    return "".join(chunks)


def markdown_html(text):
    result = []
    for block in markdown_blocks(text):
        kind = block["kind"]
        if kind == "table":
            result.append('<div class="table-wrap"><table><thead><tr>' + "".join('<th scope="col">' + inline_html(cell) + '</th>' for cell in block["header"]) + '</tr></thead><tbody>')
            result.extend('<tr>' + ''.join('<td>' + inline_html(cell) + '</td>' for cell in row) + '</tr>' for row in block["rows"])
            result.append('</tbody></table></div>')
        elif kind == "list":
            tag = "ol" if block["ordered"] else "ul"
            start = f' start="{block["start"]}"' if block["ordered"] else ""
            result.append(f"<{tag}{start}>" + "".join("<li>" + inline_html(item) + "</li>" for item in block["items"]) + f"</{tag}>")
        elif kind == "rule":
            result.append("<hr>")
        elif kind == "code":
            result.append("<pre><code>" + html.escape(block["text"]) + "</code></pre>")
        else:
            tag = f'h{block["level"]}' if kind == "heading" else "blockquote" if kind == "quote" else "p"
            result.append(f"<{tag}>" + inline_html(block["text"]) + f"</{tag}>")
    return "\n".join(result)


def html_report(context):
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'">'
            '<title>' + html.escape(context["title"]) + '</title>'
            '<style>body{font:15px/1.8 "Microsoft YaHei",sans-serif;max-width:1200px;margin:32px auto;padding:0 24px;color:#243349}'
            'table{border-collapse:collapse;width:100%;table-layout:fixed}td,th{border:1px solid #d8e0e9;padding:8px;vertical-align:top;overflow-wrap:anywhere}'
            'th{background:#edf2f7;text-align:left}.table-wrap{overflow:auto}blockquote{border-left:3px solid #7a9cbf;padding-left:12px;color:#52647a}'
            'p,li,pre{overflow-wrap:anywhere}pre{white-space:pre-wrap}h2{margin-top:32px;border-bottom:1px solid #d8e0e9}'
            '@page{size:A4 landscape;margin:15mm}@media print{body{font-size:10pt;margin:0;padding:0}.table-wrap{overflow:visible}tr{break-inside:avoid}thead{display:table-header-group}}</style></head><body>'
            + markdown_html(report_markdown(context)) + '</body></html>')
