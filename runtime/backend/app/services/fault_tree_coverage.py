from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from typing import Any

from app.services.diagnostic_methods import DiagnosticMethodDocument


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_BOX_ITEM = re.compile(
    r"^(?P<label>(?:步骤\s*\d+|\d+\.\d+|场景\s*\d+))(?:\s*[:：]\s*|\s+)(?P<body>.+)$",
    re.IGNORECASE,
)
_INLINE_CODE = re.compile(r"`([^`\r\n]{2,500})`")
_FAULT_TABLE_HEADERS = ("判断点", "判断方法", "检查项", "排查项", "根因", "场景")
_EVIDENCE_HEADERS = ("日志", "关键字", "关键词", "证据", "特征", "判断", "结论")


@dataclass(frozen=True)
class FaultTreeCoverageItem:
    id: str
    method_document_id: str
    document_title: str
    category: str
    label: str
    description: str
    section: str
    line_start: int
    evidence_hints: list[str]

    def public_snapshot(self) -> dict[str, Any]:
        return asdict(self)


def _clean_box_line(raw_line: str) -> str:
    cleaned = raw_line.strip()
    cleaned = cleaned.strip("│┃┌┐└┘├┤┬┴┼─━ ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _inline_hints(text: str) -> list[str]:
    hints = [item.strip() for item in _INLINE_CODE.findall(text) if item.strip()]
    if hints:
        return list(dict.fromkeys(hints))[:20]
    candidates = re.findall(
        r"[A-Za-z_][A-Za-z0-9_.:/!\[\]-]{2,}|[\u4e00-\u9fff]{2,12}",
        text,
    )
    ignored = {"判断", "结论", "问题", "正常", "异常", "是否", "查看", "确认"}
    return list(dict.fromkeys(
        item for item in candidates if item.casefold() not in ignored
    ))[:20]


def _make_item(
    document: DiagnosticMethodDocument,
    *,
    category: str,
    label: str,
    description: str,
    section: str,
    line_start: int,
) -> FaultTreeCoverageItem:
    normalized = re.sub(r"\s+", " ", f"{category}\0{label}\0{description}").casefold()
    item_id = "FTITEM-" + hashlib.sha256(
        f"{document.id}\0{document.version}\0{normalized}".encode("utf-8")
    ).hexdigest()[:20]
    return FaultTreeCoverageItem(
        id=item_id,
        method_document_id=document.id,
        document_title=document.title,
        category=category,
        label=label[:500],
        description=description[:4000],
        section=section[:500],
        line_start=line_start,
        evidence_hints=_inline_hints(description),
    )


def _table_items(
    document: DiagnosticMethodDocument,
    lines: list[str],
) -> list[FaultTreeCoverageItem]:
    items: list[FaultTreeCoverageItem] = []
    section = ""
    index = 0
    while index < len(lines):
        heading = _HEADING.match(lines[index])
        if heading:
            section = heading.group(2).strip()
            index += 1
            continue
        if index + 1 >= len(lines) or "|" not in lines[index]:
            index += 1
            continue
        headers = [cell.strip() for cell in lines[index].strip().strip("|").split("|")]
        separators = [cell.strip() for cell in lines[index + 1].strip().strip("|").split("|")]
        if (
            len(headers) != len(separators)
            or not headers
            or not all(_TABLE_SEPARATOR.match(cell) for cell in separators)
            or not any(any(token in header for token in _FAULT_TABLE_HEADERS) for header in headers)
            or not any(any(token in header for token in _EVIDENCE_HEADERS) for header in headers)
        ):
            index += 1
            continue
        row_index = index + 2
        while row_index < len(lines) and "|" in lines[row_index]:
            cells = [cell.strip() for cell in lines[row_index].strip().strip("|").split("|")]
            if cells and any(cells):
                label = cells[0] or f"表格检查 {row_index + 1}"
                description = "；".join(
                    f"{headers[position]}: {cell}"
                    for position, cell in enumerate(cells)
                    if position < len(headers) and cell
                )
                items.append(_make_item(
                    document,
                    category="DECISION",
                    label=label,
                    description=description,
                    section=section,
                    line_start=row_index + 1,
                ))
            row_index += 1
        index = row_index
    return items


def _flow_items(
    document: DiagnosticMethodDocument,
    lines: list[str],
) -> list[FaultTreeCoverageItem]:
    items: list[FaultTreeCoverageItem] = []
    section = ""
    in_fence = False
    fenced_lines: list[tuple[int, str, str]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        heading = _HEADING.match(raw_line)
        if heading:
            section = heading.group(2).strip()
            continue
        if raw_line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            continue
        cleaned = _clean_box_line(raw_line)
        if cleaned and cleaned not in {"▼", "▲"}:
            fenced_lines.append((line_number, section, cleaned))

    labelled: list[tuple[int, int, str, str, str]] = []
    for position, (line_number, item_section, cleaned) in enumerate(fenced_lines):
        match = _BOX_ITEM.match(cleaned)
        if match:
            labelled.append((
                position,
                line_number,
                item_section,
                re.sub(r"\s+", "", match.group("label")),
                match.group("body").strip(),
            ))

    for label_index, (position, line_number, item_section, label, body) in enumerate(labelled):
        end_position = len(fenced_lines)
        for next_position, _, _, next_label, _ in labelled[label_index + 1:]:
            is_boundary = (
                label.startswith("步骤")
                and (next_label.startswith("步骤") or next_label.startswith("场景"))
            ) or (
                "." in label
            ) or (
                label.startswith("场景") and next_label.startswith("场景")
            )
            if is_boundary:
                end_position = next_position
                break
        details: list[str] = []
        for _, _, detail in fenced_lines[position:end_position]:
            normalized = re.sub(r"\s+", " ", detail).strip()
            if not normalized or normalized == "根因分析结论":
                continue
            if normalized not in details:
                details.append(normalized)
        description = "；".join(details) or f"{label}: {body}"
        category = "ROOT_CAUSE" if label.startswith("场景") else "FLOW_STEP"
        items.append(_make_item(
            document,
            category=category,
            label=label,
            description=description,
            section=item_section,
            line_start=line_number,
        ))
    return items


def compile_fault_tree_items(
    documents: list[DiagnosticMethodDocument],
) -> list[FaultTreeCoverageItem]:
    """Compile stable, auditable decision items from every applicable fault tree.

    Flow boxes preserve the authored investigation sequence; decision tables preserve
    explicit pass/fail branches. Evidence-collection checklists are deliberately not
    separate conclusions because their keywords are already compiled as log patterns.
    """

    compiled: list[FaultTreeCoverageItem] = []
    seen: set[tuple[str, str, str]] = set()
    for document in documents:
        if document.role != "FAULT_TREE":
            continue
        lines = document.content.splitlines()
        for item in [*_flow_items(document, lines), *_table_items(document, lines)]:
            key = (
                item.method_document_id,
                re.sub(r"\s+", "", item.label).casefold(),
                re.sub(r"\s+", " ", item.description).casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            compiled.append(item)
    compiled.sort(key=lambda item: (
        item.document_title,
        item.line_start,
        item.category,
        item.id,
    ))
    return compiled
