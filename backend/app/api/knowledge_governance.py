from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (
    AnalysisRun,
    DiagnosisFeedback,
    KnowledgeDocument,
    KnowledgeRevision,
)
from app.schemas import (
    DiagnosisFeedbackCreate,
    DiagnosisFeedbackReview,
    KnowledgeReviewAction,
    KnowledgeRevisionOut,
    KnowledgeRollbackRequest,
)
from app.services.audit import record_audit_event
from app.services.knowledge import index_document
from app.services.knowledge_governance import (
    actor_id,
    feedback_to_dict,
    incorporate_feedback_as_draft,
    rollback_document,
    transition_document_review,
)


router = APIRouter(tags=["knowledge-governance"])
Db = Annotated[Session, Depends(get_db)]


def _principal(request: Request) -> dict[str, Any]:
    return getattr(request.state, "principal", {}) or {}


def _require_admin(request: Request) -> dict[str, Any]:
    principal = _principal(request)
    if principal.get("role") not in {"ADMIN", "EXPERT"}:
        raise HTTPException(403, "Administrator role required")
    return principal


def _review_result(document: KnowledgeDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "review_status": document.review_status,
        "active": document.active,
        "version": document.version,
        "lock_version": document.lock_version,
        "reviewed_by": document.reviewed_by,
        "reviewed_at": document.reviewed_at,
        "review_comment": document.review_comment,
        "published_at": document.published_at,
    }


@router.get(
    "/knowledge/{document_id}/revisions",
    response_model=list[KnowledgeRevisionOut],
)
def list_knowledge_revisions(document_id: str, request: Request, db: Db) -> list[dict[str, Any]]:
    from app.services.knowledge_access import can_read_revision, require_knowledge_access
    principal = _principal(request)
    require_knowledge_access(db, document_id, principal)
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    revisions = list(db.scalars(
        select(KnowledgeRevision)
        .where(KnowledgeRevision.document_id == document_id)
        .order_by(KnowledgeRevision.version.desc())
    ).all())
    return [
        {
            "id": revision.id,
            "document_id": revision.document_id,
            "version": revision.version,
            "content_hash": revision.content_hash,
            "change_summary": revision.change_summary,
            "created_by": revision.created_by,
            "created_at": revision.created_at,
            "snapshot": json_loads(revision.snapshot_json, {}),
        }
        for revision in revisions
        if can_read_revision(db, document, revision, principal)
    ]


def _transition(
    document_id: str,
    payload: KnowledgeReviewAction,
    request: Request,
    db: Session,
    action: str,
) -> dict[str, Any]:
    principal = _principal(request)
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    from app.services.knowledge_access import require_knowledge_access, require_publisher
    if action == "SUBMIT":
        require_knowledge_access(db, document_id, principal, write=True)
    else:
        require_publisher(db, document, principal)
    from app.core.config import get_settings
    if action == "APPROVE" and get_settings().deployment_mode == "lan_server":
        from app.services.knowledge_drafts import draft_for_document
        from app.services.knowledge_governance import document_snapshot, require_lock_version
        from app.models import KnowledgeDraft, KnowledgeAccess
        from app.services.jobs import job_runner
        from app.services.knowledge_publication import publication_job
        try:
            require_lock_version(document, payload.expected_lock_version)
            if document.review_status != "IN_REVIEW":
                raise ValueError("Knowledge is not in review")
            access = db.get(KnowledgeAccess, document_id)
            author = (access.owner_id if access else None) or actor_id(principal)
            draft = draft_for_document(db, document_id, author=author or "")
            if draft and draft.status == "BUILDING":
                raise ValueError("Publication already building")
            if not draft:
                draft = KnowledgeDraft(id=new_id("KDRAFT"), document_id=document_id, base_version=document.version,
                    created_by=author, owner_key=author or "", snapshot_json="{}")
                db.add(draft)
            snapshot = document_snapshot(db, document)
            snapshot.pop("active", None)
            snapshot.pop("review_status", None)
            draft.snapshot_json = json_dumps(snapshot)
            draft.status = "IN_REVIEW"
            draft.review_comment = payload.comment
            db.commit()
            args = {"document_id": document_id, "draft_id": draft.id,
                    "draft_version": draft.version, "reviewer": actor_id(principal)}
            job = job_runner.submit(db, "publish_knowledge_revision", publication_job, *args.values(), input_data=args)
            return {**_review_result(document), "job": {"id": job.id, "status": job.status}, "publication_pending": True}
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
    try:
        transition_document_review(
            db,
            document,
            action=action,
            expected_lock_version=payload.expected_lock_version,
            reviewer=actor_id(principal),
            comment=payload.comment,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    record_audit_event(
        f"knowledge.review.{action.lower()}",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge",
        resource_id=document.id,
        details={
            "review_status": document.review_status,
            "version": document.version,
            "lock_version": document.lock_version,
        },
    )
    return _review_result(document)


@router.post("/knowledge/{document_id}/review/submit")
def submit_knowledge_review(
    document_id: str,
    payload: KnowledgeReviewAction,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    return _transition(document_id, payload, request, db, "SUBMIT")


@router.post("/knowledge/{document_id}/review/approve")
def approve_knowledge_review(
    document_id: str,
    payload: KnowledgeReviewAction,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    return _transition(document_id, payload, request, db, "APPROVE")


@router.post("/knowledge/{document_id}/review/reject")
def reject_knowledge_review(
    document_id: str,
    payload: KnowledgeReviewAction,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    return _transition(document_id, payload, request, db, "REJECT")


@router.post("/knowledge/{document_id}/review/archive")
def archive_knowledge_document(
    document_id: str,
    payload: KnowledgeReviewAction,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    return _transition(document_id, payload, request, db, "ARCHIVE")


@router.post("/knowledge/{document_id}/revisions/{version}/rollback")
def rollback_knowledge_revision(
    document_id: str,
    version: int,
    payload: KnowledgeRollbackRequest,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    revision = db.scalar(
        select(KnowledgeRevision).where(
            KnowledgeRevision.document_id == document_id,
            KnowledgeRevision.version == version,
        )
    )
    if not revision:
        raise HTTPException(404, "Knowledge revision not found")
    try:
        rollback_document(
            db,
            document,
            revision,
            expected_lock_version=payload.expected_lock_version,
            created_by=actor_id(principal),
            change_summary=payload.change_summary,
            expected_draft_version=payload.expected_draft_version,
        )
        if not document.active:
            index_document(db, document)
        db.commit()
        db.refresh(document)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    record_audit_event(
        "knowledge.revision.rollback",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge",
        resource_id=document.id,
        details={
            "source_version": version,
            "new_version": document.version,
            "lock_version": document.lock_version,
        },
    )
    return _review_result(document)


@router.get("/cases/{case_id}/diagnosis-feedback")
def list_diagnosis_feedback(case_id: str, db: Db) -> list[dict[str, Any]]:
    rows = list(db.scalars(
        select(DiagnosisFeedback)
        .where(DiagnosisFeedback.case_id == case_id)
        .order_by(DiagnosisFeedback.created_at.desc())
    ).all())
    return [feedback_to_dict(row) for row in rows]


@router.post("/cases/{case_id}/diagnosis-feedback")
def create_diagnosis_feedback(
    case_id: str,
    payload: DiagnosisFeedbackCreate,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    analysis = db.get(AnalysisRun, payload.analysis_run_id)
    if not analysis or analysis.case_id != case_id:
        raise HTTPException(404, "Analysis run not found in this case")
    principal = _principal(request)
    feedback = DiagnosisFeedback(
        id=new_id("FDB"),
        case_id=case_id,
        analysis_run_id=analysis.id,
        verdict=payload.verdict,
        root_cause_correct=payload.root_cause_correct,
        evidence_correct=payload.evidence_correct,
        comment=payload.comment,
        corrections_json=json_dumps(payload.corrections),
        resolution_status=payload.resolution_status,
        resolution_notes=payload.resolution_notes,
        resolution_observed_at=payload.resolution_observed_at,
        status="SUBMITTED",
        submitted_by=actor_id(principal),
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback_to_dict(feedback)


@router.post("/cases/{case_id}/diagnosis-feedback/{feedback_id}/review")
def review_diagnosis_feedback(
    case_id: str,
    feedback_id: str,
    payload: DiagnosisFeedbackReview,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    feedback = db.get(DiagnosisFeedback, feedback_id)
    if not feedback or feedback.case_id != case_id:
        raise HTTPException(404, "Diagnosis feedback not found")
    if feedback.status not in {"SUBMITTED", "APPROVED", "REJECTED"}:
        raise HTTPException(409, f"Feedback is already {feedback.status.lower()}")
    feedback.status = "APPROVED" if payload.action == "APPROVE" else "REJECTED"
    feedback.reviewed_by = actor_id(principal)
    feedback.reviewed_at = utcnow()
    feedback.review_comment = payload.comment
    from app.services.memory_governance import apply_reviewed_resolution

    apply_reviewed_resolution(db, feedback)
    db.commit()
    db.refresh(feedback)
    return feedback_to_dict(feedback)


@router.post(
    "/cases/{case_id}/diagnosis-feedback/{feedback_id}/incorporate"
)
def incorporate_diagnosis_feedback(
    case_id: str,
    feedback_id: str,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    feedback = db.get(DiagnosisFeedback, feedback_id)
    if not feedback or feedback.case_id != case_id:
        raise HTTPException(404, "Diagnosis feedback not found")
    try:
        document = incorporate_feedback_as_draft(
            db,
            feedback,
            created_by=actor_id(principal),
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "feedback": feedback_to_dict(feedback),
        "document": {
            "id": document.id,
            "title": document.title,
            "review_status": document.review_status,
            "version": document.version,
            "lock_version": document.lock_version,
        },
    }
