from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Case, KnowledgeDocument
from app.services.diagnostic_scope import knowledge_matches_joint_diagnostic_scope
from app.services.text_files import read_text_file


DIAGNOSTIC_SOURCE_TYPES = frozenset({
    "fault_tree",
    "analysis_method",
    "analysis_skill",
    "log_rule",
    "diagnostic_rule",
    "fault_case",
    "historical_bug",
})
LOG_METHOD_SOURCE_TYPES = frozenset({
    "analysis_method",
    "analysis_skill",
    "log_rule",
    "diagnostic_rule",
    "fault_tree",
})
_INLINE_CODE = re.compile(r"`([^`\r\n]{2,500})`")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_TABLE_SEPARATOR = re.compile(r"^:?-{3,}:?$")
_PLACEHOLDER = re.compile(r"%(?:[-+0-9.*hlLzjt]*[diuoxXfFeEgGaAcsp])")
_LOG_HINTS = (
    "日志",
    "关键字",
    "关键词",
    "格式",
    "特征",
    "必查",
    "pattern",
    "keyword",
    "signature",
    "log",
)
_MEANING_HINTS = (
    "含义",
    "说明",
    "意义",
    "解释",
    "判断",
    "结论",
    "用途",
    "结果",
    "meaning",
    "description",
)
_LOCAL_METHOD_FILES = {
    "故障树.md": ("fault_tree", "FAULT_TREE"),
    "日志分析.md": ("analysis_skill", "LOG_ANALYSIS_METHOD"),
}


@dataclass(frozen=True)
class DiagnosticMethodDocument:
    id: str
    title: str
    source_type: str
    version: int
    device_type: str | None
    module: str | None
    content: str
    content_sha256: str
    role: str

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = asdict(self)
        snapshot.pop("content", None)
        return snapshot


@dataclass(frozen=True)
class DiagnosticPattern:
    id: str
    text: str
    match_kind: str
    regex: str
    document_id: str
    document_title: str
    document_version: int
    source_type: str
    heading: str
    line_start: int
    reason: str
    meaning: str

    def public_snapshot(self) -> dict[str, Any]:
        snapshot = asdict(self)
        snapshot.pop("regex", None)
        return snapshot


def _document_role(source_type: str) -> str:
    if source_type == "fault_tree":
        return "FAULT_TREE"
    if source_type in LOG_METHOD_SOURCE_TYPES:
        return "LOG_ANALYSIS_METHOD"
    return "REFERENCE_CASE"


def load_applicable_diagnostic_methods(
    db: Session,
    case: Case,
) -> list[DiagnosticMethodDocument]:
    rows = list(db.scalars(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.active.is_(True),
            KnowledgeDocument.review_status == "ACTIVE",
            KnowledgeDocument.source_type.in_(DIAGNOSTIC_SOURCE_TYPES),
        )
        .order_by(KnowledgeDocument.source_type, KnowledgeDocument.title, KnowledgeDocument.id)
    ).all())
    result: list[DiagnosticMethodDocument] = []
    for row in rows:
        # A managed WLAN is one diagnostic system: an AP symptom may originate
        # from its primary GW and a GW symptom may originate from an AP. Keep
        # ordinary knowledge search device-scoped, but force diagnostic method
        # coverage across both sides plus shared knowledge.
        if not knowledge_matches_joint_diagnostic_scope(row.device_type):
            continue
        content = row.content.replace("\r\n", "\n").replace("\r", "\n")
        result.append(DiagnosticMethodDocument(
            id=row.id,
            title=row.title,
            source_type=row.source_type,
            version=row.version,
            device_type=row.device_type,
            module=row.module,
            content=content,
            content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
            role=_document_role(row.source_type),
        ))
    known_hashes = {document.content_sha256 for document in result}
    repository_root = Path(__file__).resolve().parents[3]
    for filename, (source_type, role) in _LOCAL_METHOD_FILES.items():
        path = repository_root / filename
        if not path.is_file():
            continue
        decoded = read_text_file(path)
        if decoded is None:
            continue
        content = decoded.replace("\r\n", "\n").replace("\r", "\n")
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_hash in known_hashes:
            continue
        result.append(DiagnosticMethodDocument(
            id=f"LOCALDOC-{content_hash[:20]}",
            title=path.stem,
            source_type=source_type,
            version=1,
            device_type=None,
            module=None,
            content=content,
            content_sha256=content_hash,
            role=role,
        ))
        known_hashes.add(content_hash)
    case_scope = str(case.device_type or "").strip().upper()
    result.sort(key=lambda item: (
        0 if str(item.device_type or "").strip().upper() == case_scope else
        1 if str(item.device_type or "").strip().upper() in {"GENERAL", "OTHER", ""} else 2,
        item.source_type,
        item.title,
        item.id,
    ))
    return result


def _clean_candidate(value: str) -> str:
    candidate = value.strip()
    # Markdown is presentation, not part of the runtime log signature. Peel
    # nested emphasis/code wrappers so table cells such as **`message %u`**
    # compile to the message emitted by the device.
    for _ in range(4):
        previous = candidate
        candidate = re.sub(r"^(?:\*\*|__)(.*?)(?:\*\*|__)$", r"\1", candidate).strip()
        candidate = candidate.strip("`'\"“”‘’")
        if candidate == previous:
            break
    candidate = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", "", candidate)
    candidate = re.sub(r"\s+", " ", candidate).strip()
    if ":" in candidate and len(candidate.split(":", 1)[0]) <= 18:
        label, possible = candidate.split(":", 1)
        if any(hint in label.lower() for hint in _LOG_HINTS):
            candidate = possible.strip()
    return candidate


def _candidate_is_pattern(value: str) -> bool:
    if not (2 <= len(value) <= 500):
        return False
    if value.lower().startswith(("http://", "https://")):
        return False
    if value in {"是", "否", "正常", "异常", "无", "未知"}:
        return False
    # Huawei command output and runtime signatures commonly end in "!".
    # Only discard clear Chinese prose punctuation here; Markdown placement
    # and log-shape checks below still prevent arbitrary sentences becoming
    # scan patterns.
    if value.endswith(("。", "？")) and not _PLACEHOLDER.search(value):
        return False
    has_log_shape = bool(re.search(r"[A-Za-z_][A-Za-z0-9_.:/-]{2,}|%[a-zA-Z]|\[[Xx0-9/]+\]", value))
    has_chinese_keyword = len(value) <= 80 and any(
        token in value for token in ("失败", "超时", "离线", "上线", "下线", "异常", "心跳", "拓扑")
    )
    return has_log_shape or has_chinese_keyword


def _pattern_regex(value: str) -> tuple[str, str]:
    placeholders: list[tuple[str, str]] = []

    def remember(regex: str) -> str:
        token = f"\x00P{len(placeholders)}\x00"
        placeholders.append((token, regex))
        return token

    prepared = _PLACEHOLDER.sub(lambda match: remember(
        r"\S+" if match.group(0)[-1].lower() in {"s", "c", "p"} else r"[-+]?\d+(?:\.\d+)?"
    ), value)
    prepared = re.sub(
        r"\[(?:X{1,8}|Y{1,8}|Z{1,8}|x{1,8}|y{1,8}|z{1,8})\]",
        lambda _: remember(r"\[[^\]\r\n]+\]"),
        prepared,
    )
    prepared = re.sub(
        r"(?<![A-Za-z0-9_])(?:X{1,8}|Y{1,8}|Z{1,8}|x{2,8}|y{2,8}|z{2,8})(?![A-Za-z0-9_])",
        lambda _: remember(r"\S+"),
        prepared,
    )
    rendered = re.escape(prepared).replace(r"\ ", r"\s+")
    for token, regex in placeholders:
        rendered = rendered.replace(re.escape(token), regex)
    return rendered, "template" if placeholders else "literal"


def _clean_meaning(value: str) -> str:
    meaning = re.sub(r"<br\s*/?>", "；", value, flags=re.IGNORECASE)
    meaning = meaning.replace("`", "")
    meaning = re.sub(r"(?:\*\*|__)(.*?)(?:\*\*|__)", r"\1", meaning)
    meaning = meaning.strip("*_")
    meaning = re.sub(r"\s+", " ", meaning).strip(" ：:|-—")
    return meaning[:1000]


def _fallback_meaning(document_title: str, heading: str) -> str:
    section = heading or "日志分析方法"
    return f"用于《{document_title}》中“{section}”的筛查（Skill 未提供单独含义）"


def _line_meaning(
    raw_line: str,
    candidate: str,
    *,
    document_title: str,
    heading: str,
) -> str:
    prose = _INLINE_CODE.sub(" ", raw_line)
    prose = prose.replace(candidate, " ")
    prose = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", "", prose)
    prose = _clean_meaning(prose)
    return prose if len(prose) >= 2 else _fallback_meaning(document_title, heading)


def _table_candidates(lines: list[str]) -> list[tuple[str, int, str, str]]:
    result: list[tuple[str, int, str, str]] = []
    index = 0
    while index + 1 < len(lines):
        header = lines[index]
        separator = lines[index + 1]
        if "|" not in header or "|" not in separator:
            index += 1
            continue
        headers = [cell.strip().lower() for cell in header.strip().strip("|").split("|")]
        separators = [cell.strip() for cell in separator.strip().strip("|").split("|")]
        if len(headers) != len(separators) or not all(_TABLE_SEPARATOR.match(cell) for cell in separators):
            index += 1
            continue
        selected_columns = [
            position
            for position, cell in enumerate(headers)
            if any(hint in cell for hint in _LOG_HINTS)
        ]
        meaning_columns = [
            position
            for position, cell in enumerate(headers)
            if any(hint in cell for hint in _MEANING_HINTS)
        ]
        row_index = index + 2
        while row_index < len(lines) and "|" in lines[row_index]:
            cells = [cell.strip() for cell in lines[row_index].strip().strip("|").split("|")]
            meaning = "；".join(
                cleaned
                for position in meaning_columns
                if position < len(cells)
                and (cleaned := _clean_meaning(cells[position]))
            )
            for position in selected_columns:
                if position < len(cells):
                    result.append((
                        cells[position],
                        row_index + 1,
                        "Markdown table log column",
                        meaning,
                    ))
            row_index += 1
        index = row_index
    return result


def _heading_for_line(lines: list[str], line_number: int) -> str:
    heading = ""
    for raw_line in lines[:max(0, line_number - 1)]:
        match = _HEADING.match(raw_line)
        if match:
            heading = match.group(2).strip()
    return heading


def compile_diagnostic_patterns(
    documents: list[DiagnosticMethodDocument],
) -> list[DiagnosticPattern]:
    patterns: list[DiagnosticPattern] = []
    seen: set[tuple[str, str]] = set()
    for document in documents:
        if document.source_type not in LOG_METHOD_SOURCE_TYPES:
            continue
        lines = document.content.splitlines()
        candidates = _table_candidates(lines)
        heading = ""
        heading_is_log_section = False
        for line_number, raw_line in enumerate(lines, start=1):
            heading_match = _HEADING.match(raw_line)
            if heading_match:
                heading = heading_match.group(2).strip()
                heading_is_log_section = any(hint in heading.lower() for hint in _LOG_HINTS)
                continue
            inline_candidates = _INLINE_CODE.findall(raw_line)
            for inline in inline_candidates:
                candidates.append((
                    inline,
                    line_number,
                    "Inline code log pattern",
                    _line_meaning(
                        raw_line,
                        inline,
                        document_title=document.title,
                        heading=heading,
                    ),
                ))
            # Inline code and table-column extraction are more precise than a
            # complete Markdown row. Avoid compiling pipes, emphasis and prose
            # into a regex that can never occur in the raw device log.
            is_table_row = raw_line.lstrip().startswith("|")
            if _PLACEHOLDER.search(raw_line) and not inline_candidates and not is_table_row:
                candidates.append((
                    raw_line,
                    line_number,
                    "Printf-style log template",
                    _line_meaning(
                        raw_line,
                        raw_line,
                        document_title=document.title,
                        heading=heading,
                    ),
                ))
            if (
                heading_is_log_section
                and not inline_candidates
                and re.match(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", raw_line)
            ):
                candidates.append((
                    raw_line,
                    line_number,
                    f"Log method section: {heading}",
                    _line_meaning(
                        raw_line,
                        raw_line,
                        document_title=document.title,
                        heading=heading,
                    ),
                ))

        for raw_candidate, line_number, reason, meaning in candidates:
            candidate = _clean_candidate(raw_candidate)
            if not _candidate_is_pattern(candidate):
                continue
            normalized = candidate.casefold()
            key = (document.id, normalized)
            if key in seen:
                continue
            seen.add(key)
            regex, match_kind = _pattern_regex(candidate)
            pattern_id = "DPAT-" + hashlib.sha256(
                f"{document.id}\0{document.version}\0{normalized}".encode("utf-8")
            ).hexdigest()[:20]
            patterns.append(DiagnosticPattern(
                id=pattern_id,
                text=candidate,
                match_kind=match_kind,
                regex=regex,
                document_id=document.id,
                document_title=document.title,
                document_version=document.version,
                source_type=document.source_type,
                heading=_heading_for_line(lines, line_number),
                line_start=line_number,
                reason=reason,
                meaning=meaning or _fallback_meaning(
                    document.title,
                    _heading_for_line(lines, line_number),
                ),
            ))
    return patterns


def method_prompt_bundle(documents: list[DiagnosticMethodDocument]) -> list[dict[str, Any]]:
    return [
        {
            **document.public_snapshot(),
            "content": document.content,
            "evidence_id": document.id,
        }
        for document in documents
    ]
