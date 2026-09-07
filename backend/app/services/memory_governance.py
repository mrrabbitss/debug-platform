"""Human publication and independently observed outcome of candidate memories."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, json_loads, utcnow
from app.models import AgentMemory, DiagnosisFeedback


def review_memory(db: Session, memory: AgentMemory, *, action: str, expected_version: int,
                  reviewer: str, comment: str, expiry_days: int = 180) -> AgentMemory:
    if expected_version != memory.review_version:
        raise ValueError("Memory changed; refresh before review")
    if action not in {"PUBLISH", "REJECT", "ARCHIVE"}:
        raise ValueError("Unsupported memory review action")
    if memory.review_status == "ARCHIVED":
        raise ValueError("Archived memories cannot be republished; submit a new candidate")
    if action == "PUBLISH":
        if memory.memory_type != "PROCEDURAL" or not memory.case_id or not json_loads(memory.evidence_json, []):
            raise ValueError("Global publication requires a procedural candidate with a source case and evidence")
        if memory.review_status not in {"CANDIDATE", "REJECTED", "PUBLISHED"}:
            raise ValueError("Memory is not reviewable")
        memory.review_status = "PUBLISHED"
        memory.scope = "GLOBAL"
        memory.expires_at = utcnow() + timedelta(days=max(1, min(expiry_days, 730)))
    else:
        memory.review_status = "REJECTED" if action == "REJECT" else "ARCHIVED"
        memory.scope = "CASE"
    memory.reviewed_by = reviewer
    memory.reviewed_at = utcnow()
    memory.review_comment = comment
    db.flush()
    return memory


def apply_reviewed_resolution(db: Session, feedback: DiagnosisFeedback) -> int:
    db.flush()
    latest = db.scalar(select(DiagnosisFeedback).where(
        DiagnosisFeedback.analysis_run_id == feedback.analysis_run_id,
        DiagnosisFeedback.status.in_(["APPROVED", "INCORPORATED"]),
        DiagnosisFeedback.resolution_status != "UNKNOWN",
    ).order_by(DiagnosisFeedback.resolution_observed_at.desc(), DiagnosisFeedback.created_at.desc()).limit(1))
    rows = list(db.scalars(select(AgentMemory).where(
        AgentMemory.case_id == feedback.case_id, AgentMemory.source_kind == "analysis_run",
        AgentMemory.source_id == feedback.analysis_run_id,
    )).all())
    count = 0
    for memory in rows:
        context = json_loads(memory.context_json, {})
        if not latest:
            if not context.get("resolution_feedback_id"):
                continue
            memory.outcome = "UNKNOWN"
            context.pop("resolution_feedback_id", None)
        else:
            memory.outcome = "SUCCESS" if latest.resolution_status == "RESOLVED" else "FAILED"
            context["resolution_feedback_id"] = latest.id
            if memory.outcome == "FAILED" and memory.review_status == "PUBLISHED":
                memory.review_status = "CANDIDATE"
                memory.scope = "CASE"
        memory.context_json = json_dumps(context)
        count += 1
    return count
