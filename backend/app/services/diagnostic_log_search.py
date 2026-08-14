from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select

from app.core.utils import json_loads, mask_sensitive
from app.diagnostic_models import LogEvidenceMatch
from app.services.diagnostic_tools import SearchLogInput


def search_persisted_log_evidence(
    *,
    triage_run_ids: list[str],
    artifact_sources: list[dict[str, Any]],
    payload: SearchLogInput,
    session_factory: Any,
) -> dict[str, Any]:
    """Search every persisted triage match, not only the prompt's top candidates."""

    if not triage_run_ids:
        return {"results": [], "total_candidates": 0}
    filters: list[Any] = [LogEvidenceMatch.triage_run_id.in_(triage_run_ids)]
    if payload.artifact_ids:
        filters.append(LogEvidenceMatch.artifact_id.in_(payload.artifact_ids))
    match_filters: list[Any] = []
    if payload.pattern_ids:
        match_filters.append(LogEvidenceMatch.pattern_id.in_(payload.pattern_ids))
    for keyword in payload.keywords:
        term = keyword.strip().casefold()
        if len(term) < 2:
            continue
        match_filters.append(or_(
            func.lower(LogEvidenceMatch.message).contains(term, autoescape=True),
            func.lower(LogEvidenceMatch.pattern_text).contains(term, autoescape=True),
            func.lower(LogEvidenceMatch.reason).contains(term, autoescape=True),
        ))
    if match_filters:
        filters.append(or_(*match_filters))
    source_by_artifact = {
        str(item.get("artifact_id")): item
        for item in artifact_sources
        if item.get("artifact_id")
    }
    with session_factory() as db:
        total = int(db.scalar(
            select(func.count())
            .select_from(LogEvidenceMatch)
            .where(*filters)
        ) or 0)
        rows = list(db.scalars(
            select(LogEvidenceMatch)
            .where(*filters)
            .order_by(
                LogEvidenceMatch.relevance_score.desc(),
                LogEvidenceMatch.occurrence_count.desc(),
                LogEvidenceMatch.source_file,
                LogEvidenceMatch.line_start,
            )
            .limit(payload.top_k)
        ).all())
    results: list[dict[str, Any]] = []
    for row in rows:
        metadata = json_loads(row.metadata_json, {})
        method_source = metadata.get("method_source", {})
        results.append({
            "evidence_id": row.id,
            "source_type": "log_triage_match",
            "artifact_id": row.artifact_id,
            "artifact_source": source_by_artifact.get(row.artifact_id)
            or metadata.get("artifact_source", {}),
            "source_file": row.source_file,
            "line_start": row.line_start,
            "line_end": row.line_end,
            "pattern_id": row.pattern_id,
            "pattern_text": row.pattern_text,
            "content": mask_sensitive(row.message)[:2000],
            "score": round(float(row.relevance_score or 0.0), 6),
            "occurrence_count": row.occurrence_count,
            "method_document_id": row.method_document_id
            or method_source.get("document_id"),
            "metadata": metadata,
        })
    return {"results": results, "total_candidates": total}

