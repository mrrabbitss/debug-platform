"""Review and retrieve publication-preserving knowledge proposals."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.db import get_db
from app.core.utils import json_loads
from app.models import KnowledgeChunk, KnowledgeDocument, KnowledgeDraft, KnowledgePublication, KnowledgeRevision
from app.schemas import JobOut
from app.services.knowledge_drafts import draft_for_document, draft_payload, review_draft
from app.services.knowledge_governance import actor_id
from app.services.knowledge_publication import publication_job, enqueue_publication
from app.services.jobs import job_runner

router = APIRouter(tags=["knowledge-publications"])
Db = Annotated[Session, Depends(get_db)]
job_runner.register("publish_knowledge_revision", publication_job,
                    ("document_id", "draft_id", "draft_version", "reviewer"),
                    cancellable=True, max_attempts=3, timeout_seconds=1800)


class DraftReview(BaseModel):
    draft_id: str | None = Field(default=None, max_length=40)
    action: Literal["SUBMIT", "APPROVE", "REJECT", "ARCHIVE"]
    expected_version: int = Field(ge=1)
    comment: str = Field(default="", max_length=4000)


@router.post("/knowledge/{document_id}/draft/review")
def review_proposal(document_id: str, payload: DraftReview, request: Request, db: Db) -> dict:
    principal = getattr(request.state, "principal", {})
    from app.services.knowledge_access import require_knowledge_access, require_publisher
    document = require_knowledge_access(db, document_id, principal)
    require_publisher(db, document, principal)
    draft = db.get(KnowledgeDraft, payload.draft_id) if payload.draft_id else draft_for_document(db, document_id)
    if not draft or draft.document_id != document_id:
        raise HTTPException(404, "Knowledge proposal not found")
    try:
        if payload.action == "APPROVE":
            if draft.version != payload.expected_version or draft.status != "IN_REVIEW":
                raise ValueError("Proposal changed or is not in review")
            draft.review_comment = payload.comment
            job = enqueue_publication(db, document, draft, actor_id(principal))
            db.commit()
            try:
                job_runner._schedule(job.id)
            except RuntimeError:
                pass
            return {"job": JobOut.model_validate(job).model_dump(), "previous_publication_active": True}
        review_draft(db, draft, action=payload.action, expected_version=payload.expected_version,
                     reviewer=actor_id(principal), comment=payload.comment)
        db.commit()
        return {"draft": draft_payload(draft)}
    except (ValueError, StaleDataError) as error:
        db.rollback()
        raise HTTPException(409, "Draft review conflict; refresh and retry") from error


@router.get("/knowledge/{document_id}/publications")
def list_publications(document_id: str, request: Request, db: Db) -> list[dict]:
    from app.services.knowledge_access import require_knowledge_access
    principal = getattr(request.state, "principal", {})
    require_knowledge_access(db, document_id, principal)
    rows = [json_loads(row.manifest_json, {}) for row in db.scalars(
        select(KnowledgePublication).where(KnowledgePublication.document_id == document_id)
        .order_by(KnowledgePublication.created_at.desc()).limit(100))]
    if principal.get("role") not in {"ADMIN", "EXPERT"}:
        rows = [{**row, "document_versions": {document_id: row["document_version"]},
                 "chunk_ids": {document_id: row.get("chunk_ids", {}).get(document_id, [])}} for row in rows]
    return rows


@router.get("/knowledge/{document_id}/sections")
def knowledge_sections(document_id: str, request: Request, db: Db, content_sha256: str,
                       offset: int = Query(default=0, ge=0, le=512), limit: int = Query(default=2, ge=1, le=2)) -> dict:
    from app.services.knowledge_compiler import read_sections
    from app.services.knowledge_access import require_knowledge_access
    require_knowledge_access(db, document_id, getattr(request.state, "principal", {}))
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    try:
        return read_sections(document.content, content_sha256=content_sha256, offset=offset, limit=limit)
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


@router.get("/knowledge/{document_id}/quality")
def knowledge_quality(document_id: str, request: Request, db: Db) -> dict:
    from app.services.knowledge_quality import quality_report
    from app.services.knowledge_access import require_knowledge_access
    principal = getattr(request.state, "principal", {})
    require_knowledge_access(db, document_id, principal)
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    return quality_report(db, document, principal)


@router.get("/knowledge/{document_id}/versions/{version}/chunks/{chunk_id}")
def published_chunk(document_id: str, version: int, chunk_id: str, request: Request, db: Db) -> dict:
    from app.services.knowledge_access import require_knowledge_access, can_read_revision
    principal = getattr(request.state, "principal", {})
    require_knowledge_access(db, document_id, principal)
    document = db.get(KnowledgeDocument, document_id)
    revision = db.scalar(select(KnowledgeRevision).where(
        KnowledgeRevision.document_id == document_id, KnowledgeRevision.version == version))
    chunk = db.get(KnowledgeChunk, chunk_id)
    if not document or not revision or not chunk or chunk.document_id != document_id or chunk.document_version != version:
        raise HTTPException(404, "Committed knowledge evidence not found")
    if not can_read_revision(db, document, revision, principal):
        raise HTTPException(403, "No access to this historical knowledge scope")
    return {"document_id": document_id, "document_version": version, "chunk_id": chunk.id,
            "revision_id": revision.id, "heading": chunk.heading, "content": chunk.content,
            "current": document.version == version}
