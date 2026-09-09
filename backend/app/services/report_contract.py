"""Pure report snapshot and output contract shared by web, host and chat callers.

Resolve workbench references before calling these functions. They never query the
current category/template or write historical analyses.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import re
from typing import Any


CHAPTERS = ("一、组网总览", "二、异常设备时间轨迹", "三、异常AP分析推理", "四、结论与后续方向")
LAYERS = ("第一层：物理链路检查", "第二层：Hilink报文(UDM)检查", "第三层：WiFi业务层检查")
REFERENCE = re.compile(r"\[\[([A-Za-z0-9][A-Za-z0-9_.:/-]{0,127})\]\]")
PENDING = re.compile(r"待确认|证据不足|未采集|未继续排查|未识别|暂无|未知|不适用")
TOPOLOGY_COLUMNS = ("设备", "型号", "MAC", "IP", "上行方式", "级联关系", "SmartLink版本", "ApOnlineFlag", "SyncStatus", "组网状态")
TIMELINE_COLUMNS = ("时间", "事件", "关键日志/来源", "证据引用")
ROOT_COLUMNS = ("AP", "故障现象", "根因", "推理结论", "置信度", "后续方向")


def builtin_report_template() -> dict[str, Any]:
    content = (Path(__file__).resolve().parents[1] / "templates/network-report.md").read_text(encoding="utf-8")
    return {"id": "builtin-network-report", "version": 2, "category": "network",
            "content": content, "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}


def resolve_report_snapshot(config: dict[str, Any]) -> dict[str, Any]:
    """Validate the *resolved* run config. Missing templates use the builtin.

    An incomplete/mismatched snapshot is an error, never silently replaced by a
    newer published template. Historical records without a snapshot bypass this
    resolver at the rendering boundary and retain their legacy format.
    """
    category = config.get("problem_category") or "unknown"
    template = config.get("report_template")
    if config.get("workbench_snapshot_id") and not template:
        raise ValueError("Resolve workbench configuration before reading its report template")
    if not template:
        template = builtin_report_template()
    if not isinstance(template, dict):
        raise ValueError("Report template snapshot must be an object")
    if any(not isinstance(template.get(key), str) or not template[key].strip()
           for key in ("id", "category", "content", "sha256")):
        raise ValueError("Report template snapshot is incomplete")
    if type(template.get("version")) is not int or template["version"] < 1:
        raise ValueError("Report template version must be a positive integer")
    digest = hashlib.sha256(template["content"].encode("utf-8")).hexdigest()
    if digest != template["sha256"]:
        raise ValueError("Report template snapshot content hash mismatch")
    return {"problem_category": category, "report_template": deepcopy(template)}


def report_instructions(config: dict[str, Any]) -> dict[str, Any]:
    """Pass this object in every synthesis/revision/host prompt, after resolve_configuration.

    Validation must receive the same report_template, plus the case-only evidence
    allowlist; knowledge and method IDs must not enter that allowlist.
    """
    snapshot = resolve_report_snapshot(config)
    return {**snapshot, "report_instructions": (
        "输出 report_markdown，遵循固定 report_template.content 的要求。规范型模板中的目的/格式/示例标题是指引，不必复制成报告章节。"
        "内置组网报告严格四章，逐设备时间线、逐AP三层检查及因果链、根因/置信度/后续/覆盖/缺失证据。"
        "每条事实/时间线行/结论引用本次案例证据 [[evidence_id]]；自由文件名、行号、链接、代码中的引用不能替代。"
        "组网四章模板中，每AP用独立三级标题；三层用四级标题，根因总览/故障树覆盖/缺失证据用三级标题。"
        "不混用AP；无法确认归属或下层状态则写待确认，不能靠IP缺失、时间相邻、未命中关键词推定故障或因果。"
        "正文不能复制模板实例；无模型报告可留空交由服务端从可核对的结构化证据生成四章。"
        "未知类别可填写 suggested_problem_category 和 category_reason，理由引用本案例证据；无依据时两项留空。"
        "跨类参考说明类别和原因。置信度只用 P1（确定）至 P4（猜测）或待确认，与处理优先级分开。"
    )}


def _visible_lines(markdown: str) -> list[str]:
    lines, fence = [], None
    for line in markdown.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            if fence is None:
                fence = match[1]
            elif fence[0] == match[1][0] and len(match[1]) >= len(fence) and not line[match.end():].strip():
                fence = None
            continue
        if fence is None:
            lines.append(line)
    if fence:
        raise ValueError("Report contains an unclosed code fence")
    return lines


def structured_template(template: dict[str, Any] | None) -> bool:
    """The v1 builtin and runs without a template predate the four-chapter gate."""
    return bool(template) and not (template.get("id") == "builtin-network-report" and template.get("version", 1) < 2)


def table_cells(line: str) -> list[str]:
    """Split Markdown tables without turning escaped literal pipes into columns."""
    text = line.strip()
    cells, buffer, escaped = [], [], False
    for character in text:
        if character == "|" and not escaped:
            cells.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(character)
        escaped = character == "\\" and not escaped
    cells.append("".join(buffer).strip())
    if text.startswith("|"):
        cells = cells[1:]
    if text.endswith("|") and not cells[-1]:
        cells = cells[:-1]
    return cells


def report_tables(text: str) -> list[tuple[list[str], list[list[str]]]]:
    lines = _visible_lines(text)
    tables, index = [], 0
    while index + 1 < len(lines):
        header, separator = table_cells(lines[index]), table_cells(lines[index + 1])
        if "|" not in lines[index] or not separator or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
            index += 1
            continue
        if len(header) != len(separator):
            raise ValueError("Report table header and separator widths differ")
        index += 2
        rows = []
        while index < len(lines) and lines[index].strip().startswith("|"):
            row = table_cells(lines[index])
            if len(row) != len(header):
                raise ValueError("Report table has inconsistent column counts")
            rows.append(row)
            index += 1
        tables.append((header, rows))
    return tables


def report_references(markdown: str) -> set[str]:
    # Inspect every token for malformed/unknown spellings; only prose citations
    # count as grounding. Escaped citations, link labels and code are not receipts.
    remaining = REFERENCE.sub("", markdown)
    if "[[" in remaining or "]]" in remaining:
        raise ValueError("Report contains malformed evidence references")
    visible = "\n".join(_visible_lines(markdown))
    visible = re.sub(r"(`+).*?\1", "", visible)
    visible = re.sub(r"!?\[[^\n]*?\]\([^\n]*?\)", "", visible)
    visible = re.sub(r"\\\[\[[^\n]*?\]\]", "", visible)
    return set(REFERENCE.findall(visible))


def _sections(markdown: str, level: int) -> list[tuple[str, str]]:
    text = "\n".join(_visible_lines(markdown))
    headings = list(re.finditer(rf"^{'#' * level} (.+?)[ \t]*$", text, re.M))
    return [(match[1], text[match.end():headings[i + 1].start() if i + 1 < len(headings) else len(text)].strip())
            for i, match in enumerate(headings)]


def network_report_template(template: dict[str, Any]) -> bool:
    # Published templates can be instructions or examples, not a literal report
    # scaffold. Only the builtin/explicit four-chapter scaffold fixes headings.
    return template.get("id") == "builtin-network-report" or tuple(name for name, _ in _sections(template["content"], 2)) == CHAPTERS


def _required_table(body, columns):
    tables = [entry for entry in report_tables(body) if set(columns).issubset(entry[0])]
    if not tables or not tables[0][1]:
        raise ValueError("Report requires a populated table: " + "/".join(columns))
    return tables[0]


def _validate_table_receipts(markdown):
    for header, rows in report_tables(markdown):
        if {"缺失项", "影响", "获取方式"}.issubset(header):
            continue  # Collection requests are not observations.
        for row in rows:
            if not report_references(" | ".join(row)) and not all(PENDING.search(cell) or cell in {"", "-"} for cell in row):
                raise ValueError("Report factual table rows require evidence references or pending status")


def _validate_layers(body):
    layers = _sections(body, 4)
    if tuple(name for name, _ in layers) != LAYERS:
        raise ValueError("Each AP requires the physical/UDM/WiFi layers in order")
    chains = re.findall(r"因果链[^\n:：]*[:：]([^\n]+)", body)
    if not chains:
        raise ValueError("Each AP requires an evidence-based or pending causal chain")
    if any(not PENDING.search(chain) and not report_references(chain) for chain in chains):
        raise ValueError("A concluded causal chain requires case evidence references")
    blocked = False
    for index, (_, content) in enumerate(layers):
        conclusions = re.findall(r"(?:\*\*)?结论(?:\*\*)?\s*[:：](.*)", content)
        conclusion = conclusions[-1] if conclusions else ""
        if not re.search(r"正常|异常|证据不足|待确认|未继续排查", conclusion):
            raise ValueError("Each layer requires an explicit conclusion or pending status")
        if blocked and "未继续排查" not in conclusion:
            raise ValueError("Report must stop upper-layer diagnosis when a lower layer is abnormal or pending")
        if not PENDING.search(conclusion) and not report_references(content):
            raise ValueError("Each layer conclusion requires case evidence")
        blocked = blocked or "异常" in conclusion or bool(PENDING.search(conclusion))
        if index == 2 and "正常" in conclusion and not PENDING.search(conclusion):
            raise ValueError("An abnormal AP cannot be concluded entirely normal at the WiFi layer")


def _validate_devices(chapters):
    device_sections = [_sections(chapters[index][1], 3) for index in (1, 2)]
    for index in (1, 2):
        if not _sections(chapters[index][1], 3) and not PENDING.search(chapters[index][1]):
            raise ValueError("Report requires separate device sections")
    for title, body in device_sections[0]:
        if len(_ap_ids(title)) > 1:
            raise ValueError("Report timeline must not combine different APs")
        _required_table(body, TIMELINE_COLUMNS)
    for title, body in device_sections[1]:
        if len(_ap_ids(title)) > 1:
            raise ValueError("Report analysis must not combine different APs")
        _validate_layers(body)
    timelines = set().union(*(_ap_ids(title) for title, _ in device_sections[0]))
    analyses = set().union(*(_ap_ids(title) for title, _ in device_sections[1]))
    if timelines != analyses:
        raise ValueError("Every identified abnormal AP requires its own timeline and analysis")
    final = dict(_sections(chapters[3][1], 3))
    if timelines and "根因总览" in final:
        header, rows = _required_table(final["根因总览"], ROOT_COLUMNS)
        roots = set().union(*(_ap_ids(row[header.index("AP")]) for row in rows))
        if not timelines <= roots:
            raise ValueError("Every abnormal AP requires a root-cause overview row")


def _ap_ids(title):
    return {value.upper() for value in re.findall(r"(?<![A-Za-z0-9])AP\d+(?![A-Za-z0-9])", title, re.I)}


def _validate_conclusion(final):
    if not {"根因总览", "故障树覆盖", "缺失证据"}.issubset({name for name, _ in _sections(final, 3)}):
        raise ValueError("Report conclusion requires root causes, coverage and missing evidence")
    if "置信度" not in final or "后续方向" not in final:
        raise ValueError("Report conclusion requires confidence and next steps")
    header, rows = _required_table(dict(_sections(final, 3))["根因总览"], ROOT_COLUMNS)
    for row in rows:
        confidence = row[header.index("置信度")].strip().strip("*")
        if PENDING.search(confidence):
            continue
        match = re.fullmatch(r"P([1-4])(?:\s*[（(](确定|较确定|可能|猜测)[）)])?", confidence)
        if not match or (match[2] and match[2] != {"1": "确定", "2": "较确定", "3": "可能", "4": "猜测"}[match[1]]):
            raise ValueError("Report confidence must use P1-P4 or pending, separately from action priority")
    _required_table(dict(_sections(final, 3))["缺失证据"], ("缺失项", "影响", "获取方式"))


def validate_report_markdown(markdown: str, valid_evidence_ids: set[str] | None = None,
                             template: dict[str, Any] | None = None) -> None:
    if not markdown:
        return  # Local structured diagnoses use the conservative renderer.
    references = report_references(markdown)
    if valid_evidence_ids is not None and set(REFERENCE.findall(markdown)) - valid_evidence_ids:
        raise ValueError("Report contains unknown evidence references")
    if not structured_template(template):
        return  # Historical free-form Markdown predates the new structure gate.
    resolve_report_snapshot({"report_template": template})
    if valid_evidence_ids and not references:
        raise ValueError("Report must cite verified evidence using [[evidence_id]] outside code/links")
    if not references and not PENDING.search(markdown):
        raise ValueError("A report without evidence must mark its conclusions pending")
    _validate_table_receipts(markdown)
    if not network_report_template(template):
        return  # Category-specific prose specifications guide the model freely.
    chapters = _sections(markdown, 2)
    if tuple(name for name, _ in chapters) != CHAPTERS or any(not body for _, body in chapters):
        raise ValueError("Report chapter structure does not match the fixed template")
    if not references and not all(PENDING.search(body) for _, body in chapters):
        raise ValueError("A report without evidence must mark every chapter pending")
    _required_table(chapters[0][1], TOPOLOGY_COLUMNS)
    _validate_devices(chapters)
    _validate_conclusion(chapters[3][1])
