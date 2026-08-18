"""Exact-hit construction and atomic batch publication for log triage."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import insert

from app.core.utils import new_id, utcnow
from app.diagnostic_models import (
    LogEvidenceHit,
    LogEvidenceMatch,
    LogEvidenceOccurrence,
    LogTriageRun,
)


_VARIABLE_NUMBER = re.compile(
    r"(?<![A-Za-z])(?:0x[0-9a-f]+|\d{1,4}(?:[.:/-]\d{1,4}){1,5}|\d+)(?![A-Za-z])",
    re.IGNORECASE,
)
_MAC = re.compile(r"\b(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", re.IGNORECASE)


def normalize_log_message(value: str) -> str:
    normalized = _MAC.sub("<MAC>", value)
    normalized = _VARIABLE_NUMBER.sub("<N>", normalized)
    return re.sub(r"\s+", " ", normalized).strip()[:2000]


def build_exact_hit(
    triage: LogTriageRun,
    match_id: str,
    source_file: str,
    line_number: int,
    text: str,
    event_info: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "id": new_id("LEH"),
        "triage_run_id": triage.id,
        "match_id": match_id,
        "artifact_id": triage.artifact_id,
        "source_file": source_file,
        "line_start": line_number,
        "line_end": line_number,
        "timestamp": event_info.get("timestamp") if event_info else None,
        "message": text[:4000],
        "created_at": utcnow(),
    }


def _insert_batches(db: Any, model: Any, rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), 1000):
        db.execute(insert(model), rows[start:start + 1000])


def publish_triage_evidence(
    db: Any,
    matches: list[dict[str, Any]],
    hits: list[dict[str, Any]],
    occurrences: list[dict[str, Any]],
) -> None:
    _insert_batches(db, LogEvidenceMatch, matches)
    _insert_batches(db, LogEvidenceHit, hits)
    _insert_batches(db, LogEvidenceOccurrence, occurrences)
