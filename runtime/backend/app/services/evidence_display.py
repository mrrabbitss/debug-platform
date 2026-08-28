"""Human-readable evidence locations for UI and exported reports.

Stable evidence IDs remain internal primary keys for validation and joins.  This
module prevents those implementation identifiers from leaking into operator-
facing text while preserving their internal use.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.diagnostic_models import LogEvidenceHit, LogEvidenceMatch
from app.models import (
    AgentMemory,
    AnalysisRun,
    CodeSymbol,
    CommitRecord,
    KnowledgeChunk,
    KnowledgeDocument,
    LogEvent,
)


_INTERNAL_EVIDENCE_ID = re.compile(
    r"\b(?:EVT|LEM|LEH|DOC|KCHUNK|LOCALDOC|SYM|COMMIT|MEM|ANL|AREV)-"
    r"[A-Za-z0-9_.:-]+\b"
)


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _location_label(source_file: str, line_start: Any, line_end: Any) -> str:
    source = str(source_file).replace("\\", "/").strip()
    start = _positive_int(line_start)
    end = _positive_int(line_end)
    if start and end and end != start:
        return f"{source} - 第 {start}-{end} 行"
    if start:
        return f"{source} - 第 {start} 行"
    return source


def evidence_display_label(item: dict[str, Any]) -> str:
    explicit = str(item.get("display_label") or "").strip()
    if explicit:
        return explicit
    metadata = item.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    source_file = (
        item.get("source_file")
        or item.get("file_path")
        or metadata.get("source_file")
        or metadata.get("file_path")
    )
    if source_file:
        return _location_label(
            str(source_file),
            item.get("line_start") or metadata.get("line_start") or metadata.get("line_number"),
            item.get("line_end") or metadata.get("line_end"),
        )
    title = str(item.get("title") or metadata.get("title") or "").strip()
    heading = str(item.get("heading") or metadata.get("heading") or "").strip()
    if title and heading and heading.casefold() != title.casefold():
        return f"《{title}》 - {heading}"
    if title:
        return f"《{title}》"
    source_type = str(item.get("source_type") or "").casefold()
    type_labels = {
        "analysis": "最新综合诊断",
        "log_triage_match": "日志筛查结果（位置未记录）",
        "log_event": "日志事件（位置未记录）",
        "knowledge": "知识库文档",
        "knowledge_chunk": "知识库章节",
        "fault_tree": "故障树方法",
        "analysis_method": "日志分析方法",
        "analysis_skill": "日志分析 Skill",
        "code": "代码位置",
        "code_symbol": "代码位置",
        "commit": "代码提交",
        "memory": "诊断记忆",
    }
    return type_labels.get(source_type, "证据位置未记录")


def build_evidence_label_map(
    evidence: Iterable[dict[str, Any]],
) -> dict[str, str]:
    return {
        str(item["evidence_id"]): evidence_display_label(item)
        for item in evidence
        if isinstance(item, dict) and item.get("evidence_id")
    }


def labels_for_evidence_ids(
    evidence_ids: Iterable[Any],
    labels: dict[str, str],
) -> list[str]:
    result: list[str] = []
    for evidence_id in evidence_ids:
        label = labels.get(str(evidence_id), "证据位置未记录")
        if label not in result:
            result.append(label)
    return result


def replace_evidence_ids(text: str, labels: dict[str, str]) -> str:
    rendered = str(text)
    for evidence_id in sorted(labels, key=len, reverse=True):
        if evidence_id:
            rendered = rendered.replace(evidence_id, labels[evidence_id])
    return _INTERNAL_EVIDENCE_ID.sub("证据位置未记录", rendered)


def resolve_evidence_labels(
    db: Session,
    evidence_ids: Iterable[Any],
) -> dict[str, str]:
    """Resolve trace IDs in bulk without returning evidence body content."""

    requested = list(dict.fromkeys(
        str(item) for item in evidence_ids if item is not None and str(item).strip()
    ))
    if not requested:
        return {}
    requested_set = set(requested)
    labels: dict[str, str] = {}

    for row in db.scalars(select(LogEvent).where(LogEvent.id.in_(requested_set))):
        labels[row.id] = _location_label(row.source_file, row.line_start, row.line_end)
    for model in (LogEvidenceMatch, LogEvidenceHit):
        for row in db.scalars(select(model).where(model.id.in_(requested_set))):
            labels[row.id] = _location_label(row.source_file, row.line_start, row.line_end)
    for row, document in db.execute(
        select(KnowledgeChunk, KnowledgeDocument)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .where(KnowledgeChunk.id.in_(requested_set))
    ):
        title = document.title
        labels[row.id] = f"《{title}》 - {row.heading or f'第 {row.chunk_index + 1} 段'}"
    for row in db.scalars(
        select(KnowledgeDocument).where(KnowledgeDocument.id.in_(requested_set))
    ):
        labels[row.id] = f"《{row.title}》"
    for row in db.scalars(select(CodeSymbol).where(CodeSymbol.id.in_(requested_set))):
        labels[row.id] = _location_label(row.file_path, row.line_start, row.line_end)
    for row in db.scalars(select(CommitRecord).where(CommitRecord.id.in_(requested_set))):
        labels[row.id] = f"Commit {row.commit_hash[:12]} - {row.subject or '无标题'}"
    for row in db.scalars(select(AgentMemory).where(AgentMemory.id.in_(requested_set))):
        labels[row.id] = f"诊断记忆 - {row.title}"
    for row in db.scalars(select(AnalysisRun).where(AnalysisRun.id.in_(requested_set))):
        labels[row.id] = "综合诊断结果"

    for evidence_id in requested:
        labels.setdefault(evidence_id, evidence_display_label({
            "evidence_id": evidence_id,
            "source_type": (
                "log_event" if evidence_id.startswith("EVT-") else
                "log_triage_match" if evidence_id.startswith("LEM-") else
                "knowledge" if evidence_id.startswith(("DOC-", "KCHUNK-", "LOCALDOC-")) else
                "analysis" if evidence_id.startswith("ANL-") else ""
            ),
        }))
    return labels
