from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.utils import json_loads, new_id
from app.diagnostic_models import (
    AnalysisRevision,
    LogEvidenceMatch,
    LogEvidenceOccurrence,
    LogTriageRun,
)
from app.models import AgentRun, AnalysisRun, Artifact, Case, ConversationMessage, LogEvent
from app.schemas import (
    AnalysisOut,
    AnalysisRevisionOut,
    AnalysisRevisionReview,
    ChatRequest,
    ChatSubmission,
    ConversationMessageOut,
    LogEvidencePage,
    LogTriageOut,
    LogTriageSubmission,
)
from app.services.analysis_revision import (
    analysis_revision_job,
    analysis_revision_to_dict,
    apply_analysis_revision,
    reject_analysis_revision,
)
from app.services.audit import record_audit_event
from app.services.agent_trace import agent_run_to_dict
from app.services.agent_trace_runtime import create_live_agent_run
from app.services.case_chat import case_chat_job, conversation_message_to_dict
from app.services.jobs import job_runner
from app.services.llm import get_active_chat_model_info
from app.services.log_triage import (
    LLM_BUCKET,
    METHOD_BUCKET,
    OTHER_BUCKET,
    log_triage_job,
    submit_log_triage,
)


router = APIRouter(tags=["diagnostics"])
Db = Annotated[Session, Depends(get_db)]

job_runner.register(
    "case_chat",
    case_chat_job,
    ("case_id", "message_id", "agent_run_id"),
    cancellable=True,
    max_attempts=1,
    timeout_seconds=15 * 60,
)
job_runner.register(
    "analysis_revision",
    analysis_revision_job,
    ("revision_id", "message_id", "agent_run_id"),
    cancellable=True,
    max_attempts=1,
    timeout_seconds=20 * 60,
    resource_limits={"max_input_bytes": 32 * 1024},
)
job_runner.register(
    "log_triage",
    log_triage_job,
    ("triage_run_id",),
    cancellable=True,
    max_attempts=1,
    timeout_seconds=20 * 60,
    resource_limits={"max_input_bytes": 16 * 1024},
)


def _triage_to_dict(triage: LogTriageRun) -> dict[str, Any]:
    return {
        "id": triage.id,
        "case_id": triage.case_id,
        "artifact_id": triage.artifact_id,
        "parse_run_id": triage.parse_run_id,
        "agent_run_id": triage.agent_run_id,
        "status": triage.status,
        "issue_snapshot": triage.issue_snapshot,
        "model_profile_id": triage.model_profile_id,
        "model_name": triage.model_name,
        "method_coverage": json_loads(triage.method_coverage_json, {}),
        "plan": json_loads(triage.plan_json, {}),
        "summary": json_loads(triage.summary_json, {}),
        "error_message": triage.error_message,
        "created_at": triage.created_at,
        "completed_at": triage.completed_at,
    }


@router.post(
    "/cases/{case_id}/chat",
    response_model=ChatSubmission,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_case_chat(
    case_id: str,
    payload: ChatRequest,
    request: Request,
    db: Db,
) -> dict:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    model_info = get_active_chat_model_info()
    if not model_info.get("is_mock") and not case.model_egress_approved:
        raise HTTPException(
            409,
            "This case has not approved redacted evidence egress to the active Chat model",
        )
    source_analysis: AnalysisRun | None = None
    if payload.intent == "REVISE_DIAGNOSIS":
        source_analysis = (
            db.get(AnalysisRun, payload.source_analysis_id)
            if payload.source_analysis_id else
            db.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.case_id == case_id, AnalysisRun.status == "COMPLETED")
                .order_by(AnalysisRun.created_at.desc()).limit(1)
            ).first()
        )
        if not source_analysis or source_analysis.case_id != case_id:
            raise HTTPException(409, "Complete a diagnosis before requesting a revision")
    principal = getattr(request.state, "principal", {}) or {}
    message = ConversationMessage(
        id=new_id("MSG"),
        case_id=case_id,
        role="user",
        content=payload.question,
        status="QUEUED",
    )
    db.add(message)
    operation = "diagnosis_revision" if source_analysis else "case_chat"
    run = create_live_agent_run(
        db,
        operation=operation,
        case_id=case_id,
        resource_type="conversation_message",
        resource_id=message.id,
        input_summary={
            "case_id": case_id,
            "question": payload.question,
            "intent": payload.intent,
            "source_analysis_id": source_analysis.id if source_analysis else None,
        },
        model_profile_id=str(model_info.get("profile_id") or "") or None,
        model_name=str(model_info.get("model") or "") or None,
        model_config={
            "profile_name": model_info.get("profile_name"),
            "mode": model_info.get("mode"),
            "base_url": model_info.get("base_url"),
            "config": model_info.get("config", {}),
            "proxy_url_configured": model_info.get("proxy_url_configured", False),
        },
        prompt_version=(
            "diagnosis-revision-v1-evidence-validated"
            if source_analysis else "case-chat-v4-joint-evidence"
        ),
        created_by=str(principal.get("id") or "local-user"),
    )
    message.agent_run_id = run.id
    db.flush()
    if source_analysis:
        revision = AnalysisRevision(
            id=new_id("AREV"),
            case_id=case_id,
            source_analysis_id=source_analysis.id,
            source_message_id=message.id,
            agent_run_id=run.id,
            status="QUEUED",
            instruction=payload.question,
            model_profile_id=str(model_info.get("profile_id") or "") or None,
            model_name=str(model_info.get("model") or "") or None,
            created_by=str(principal.get("id") or "local-user"),
        )
        db.add(revision)
        db.flush()
        job = job_runner.submit(
            db,
            "analysis_revision",
            analysis_revision_job,
            revision.id,
            message.id,
            run.id,
            input_data={
                "case_id": case_id,
                "revision_id": revision.id,
                "message_id": message.id,
                "agent_run_id": run.id,
            },
            deduplicate=False,
            max_attempts=1,
            timeout_seconds=20 * 60,
            resource_limits={"max_input_bytes": 32 * 1024},
        )
        revision.job_id = job.id
        message.job_id = job.id
        db.commit()
        return {
            "message_id": message.id,
            "agent_run_id": run.id,
            "revision_id": revision.id,
            "job": job,
        }
    job = job_runner.submit(
        db,
        "case_chat",
        case_chat_job,
        case_id,
        message.id,
        run.id,
        input_data={
            "case_id": case_id,
            "message_id": message.id,
            "agent_run_id": run.id,
        },
        deduplicate=False,
        max_attempts=1,
        timeout_seconds=15 * 60,
        resource_limits={"max_input_bytes": 64 * 1024},
    )
    message.job_id = job.id
    db.commit()
    return {
        "message_id": message.id,
        "agent_run_id": run.id,
        "revision_id": None,
        "job": job,
    }


@router.get(
    "/cases/{case_id}/analysis-revisions",
    response_model=list[AnalysisRevisionOut],
)
def list_analysis_revisions(case_id: str, db: Db) -> list[dict[str, Any]]:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    rows = list(db.scalars(
        select(AnalysisRevision)
        .where(AnalysisRevision.case_id == case_id)
        .order_by(AnalysisRevision.created_at.desc())
        .limit(100)
    ).all())
    return [analysis_revision_to_dict(row) for row in rows]


@router.get(
    "/cases/{case_id}/analysis-revisions/{revision_id}",
    response_model=AnalysisRevisionOut,
)
def get_analysis_revision(
    case_id: str,
    revision_id: str,
    db: Db,
) -> dict[str, Any]:
    revision = db.get(AnalysisRevision, revision_id)
    if not revision or revision.case_id != case_id:
        raise HTTPException(404, "Diagnosis revision not found")
    return analysis_revision_to_dict(revision)


@router.post(
    "/cases/{case_id}/analysis-revisions/{revision_id}/apply",
    response_model=AnalysisOut,
)
def approve_analysis_revision(
    case_id: str,
    revision_id: str,
    payload: AnalysisRevisionReview,
    request: Request,
    db: Db,
) -> AnalysisRun:
    revision = db.get(AnalysisRevision, revision_id)
    if not revision or revision.case_id != case_id:
        raise HTTPException(404, "Diagnosis revision not found")
    principal = getattr(request.state, "principal", {}) or {}
    reviewer = str(principal.get("id") or "local-user")
    try:
        analysis = apply_analysis_revision(
            db, revision, reviewed_by=reviewer, review_comment=payload.comment,
        )
        db.commit()
        db.refresh(analysis)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    record_audit_event(
        "diagnosis.revision.apply", actor_id=reviewer,
        actor_type=str(principal.get("type") or "system"),
        resource_type="analysis_revision", resource_id=revision.id,
        case_id=case_id,
        details={
            "source_analysis_id": revision.source_analysis_id,
            "applied_analysis_id": analysis.id,
        },
    )
    return analysis


@router.post(
    "/cases/{case_id}/analysis-revisions/{revision_id}/reject",
    response_model=AnalysisRevisionOut,
)
def reject_analysis_revision_endpoint(
    case_id: str,
    revision_id: str,
    payload: AnalysisRevisionReview,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    revision = db.get(AnalysisRevision, revision_id)
    if not revision or revision.case_id != case_id:
        raise HTTPException(404, "Diagnosis revision not found")
    principal = getattr(request.state, "principal", {}) or {}
    reviewer = str(principal.get("id") or "local-user")
    try:
        reject_analysis_revision(
            revision, reviewed_by=reviewer, review_comment=payload.comment,
        )
        db.commit()
        db.refresh(revision)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    record_audit_event(
        "diagnosis.revision.reject", actor_id=reviewer,
        actor_type=str(principal.get("type") or "system"),
        resource_type="analysis_revision", resource_id=revision.id,
        case_id=case_id,
    )
    return analysis_revision_to_dict(revision)


@router.get(
    "/cases/{case_id}/conversations",
    response_model=list[ConversationMessageOut],
)
def list_case_conversation(
    case_id: str,
    db: Db,
    limit: int = 200,
) -> list[dict]:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    rows = list(db.scalars(
        select(ConversationMessage)
        .where(ConversationMessage.case_id == case_id)
        .order_by(ConversationMessage.created_at.asc())
        .limit(max(1, min(limit, 500)))
    ).all())
    return [conversation_message_to_dict(row) for row in rows]


@router.post(
    "/cases/{case_id}/artifacts/{artifact_id}/triage",
    response_model=LogTriageSubmission,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_artifact_triage(
    case_id: str,
    artifact_id: str,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    case = db.get(Case, case_id)
    artifact = db.get(Artifact, artifact_id)
    if not case or not artifact or artifact.case_id != case_id:
        raise HTTPException(404, "Case or artifact not found")
    if not artifact.active_parse_run_id:
        raise HTTPException(409, "Parse the log artifact before starting LLM planning")
    principal = getattr(request.state, "principal", {}) or {}
    try:
        triage, run, job = submit_log_triage(
            db,
            case=case,
            artifact=artifact,
            created_by=str(principal.get("id") or "local-user"),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"triage_run_id": triage.id, "agent_run_id": run.id, "job": job}


@router.get(
    "/cases/{case_id}/log-triage",
    response_model=LogTriageOut | None,
)
def get_latest_log_triage(
    case_id: str,
    db: Db,
    artifact_id: str | None = Query(default=None),
) -> dict[str, Any] | None:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    query = select(LogTriageRun).where(LogTriageRun.case_id == case_id)
    if artifact_id:
        query = query.where(LogTriageRun.artifact_id == artifact_id)
    triage = db.scalars(
        query.order_by(LogTriageRun.created_at.desc()).limit(1)
    ).first()
    return _triage_to_dict(triage) if triage else None


@router.get(
    "/cases/{case_id}/log-triage/{triage_run_id}",
    response_model=LogTriageOut,
)
def get_log_triage(
    case_id: str,
    triage_run_id: str,
    db: Db,
) -> dict[str, Any]:
    triage = db.get(LogTriageRun, triage_run_id)
    if not triage or triage.case_id != case_id:
        raise HTTPException(404, "Log triage run not found")
    return _triage_to_dict(triage)


@router.get(
    "/cases/{case_id}/log-triage/{triage_run_id}/evidence",
    response_model=LogEvidencePage,
)
def list_log_triage_evidence(
    case_id: str,
    triage_run_id: str,
    db: Db,
    bucket: str = Query(pattern="^(LLM_RELEVANT|METHOD_REQUIRED|OTHER)$"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    triage = db.get(LogTriageRun, triage_run_id)
    if not triage or triage.case_id != case_id:
        raise HTTPException(404, "Log triage run not found")
    items: list[dict[str, Any]] = []
    if bucket in {LLM_BUCKET, METHOD_BUCKET}:
        filters = (
            LogEvidenceMatch.triage_run_id == triage.id,
            LogEvidenceMatch.bucket == bucket,
        )
        total = int(db.scalar(
            select(func.count(LogEvidenceMatch.id)).where(*filters)
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
            .offset(offset)
            .limit(limit)
        ).all())
        items = [
            {
                "id": row.id,
                "evidence_id": row.id,
                "bucket": row.bucket,
                "relevance_score": row.relevance_score,
                "source_file": row.source_file,
                "line_start": row.line_start,
                "line_end": row.line_end,
                "timestamp": row.first_timestamp,
                "message": row.message,
                "occurrence_count": row.occurrence_count,
                "pattern_id": row.pattern_id,
                "pattern_text": row.pattern_text,
                "match_kind": row.match_kind,
                "reason": row.reason,
                "method_document_id": row.method_document_id,
                "method_version": row.method_version,
                "metadata": json_loads(row.metadata_json, {}),
            }
            for row in rows
        ]
    else:
        matched_event = select(LogEvidenceOccurrence.id).where(
            LogEvidenceOccurrence.triage_run_id == triage.id,
            LogEvidenceOccurrence.event_id == LogEvent.id,
        ).exists()
        base = select(LogEvent).where(
            LogEvent.case_id == case_id,
            LogEvent.artifact_id == triage.artifact_id,
            LogEvent.parse_run_id == triage.parse_run_id,
            ~matched_event,
        )
        total = int(db.scalar(select(func.count()).select_from(base.subquery())) or 0)
        rows = list(db.scalars(
            base.order_by(LogEvent.source_file, LogEvent.line_start)
            .offset(offset)
            .limit(limit)
        ).all())
        items = [
            {
                "id": row.id,
                "evidence_id": row.id,
                "bucket": OTHER_BUCKET,
                "relevance_score": 0.0,
                "source_file": row.source_file,
                "line_start": row.line_start,
                "line_end": row.line_end,
                "timestamp": row.timestamp_normalized or row.timestamp_raw,
                "level": row.level,
                "module": row.module,
                "event_code": row.event_code,
                "message": row.raw_text or row.message,
                "occurrence_count": 1,
                "metadata": {"component": row.component},
            }
            for row in rows
        ]
    return {
        "triage_run_id": triage.id,
        "bucket": bucket,
        "total": total,
        "offset": offset,
        "limit": limit,
        "items": items,
    }


@router.get("/cases/{case_id}/agent-runs")
def list_case_agent_runs(
    case_id: str,
    db: Db,
    operation: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    query = select(AgentRun).where(AgentRun.case_id == case_id)
    if operation:
        query = query.where(AgentRun.operation == operation)
    rows = list(db.scalars(
        query.order_by(AgentRun.created_at.desc()).limit(limit)
    ).all())
    return [agent_run_to_dict(db, row) for row in rows]


@router.get("/cases/{case_id}/agent-runs/{run_id}")
def get_case_agent_run(
    case_id: str,
    run_id: str,
    db: Db,
) -> dict[str, Any]:
    run = db.get(AgentRun, run_id)
    if not run or run.case_id != case_id:
        raise HTTPException(404, "Agent run not found")
    return agent_run_to_dict(db, run, include_events=True)
