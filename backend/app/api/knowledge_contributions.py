"""Ordinary-knowledge drafts and the expert review queue."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import and_, case, exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.timeouts import AI_JOB_TIMEOUT_SECONDS
from app.core.db import get_db
from app.knowledge_contribution_models import KnowledgeContribution
from app.models import Job
from app.schemas import JobOut
from app.knowledge_contribution_schemas import (ContributionChat, ContributionCreate, ContributionReview,
    ContributionUpdate, ContributionVersion)
from app.services.jobs import job_runner
from app.services.interactive_model_jobs import contribution_review_job
from app.services.knowledge_access import is_knowledge_manager, require_contributor, require_knowledge_admin
from app.services.knowledge_contribution_publication import publication_job
from app.services.knowledge_contribution_review import refine_contribution
from app.services.knowledge_contributions import (contribution_payload, create_contribution, delete_contribution,
    require_contribution, review_contribution, submit_contribution, update_contribution)

router = APIRouter(prefix="/knowledge-contributions", tags=["knowledge-contributions"])


def conflict_checked_db(db: Annotated[Session, Depends(get_db)]):
    try:
        yield db
    except StaleDataError as error:
        db.rollback()
        raise HTTPException(409, "Contribution changed concurrently; refresh before continuing") from error


Db = Annotated[Session, Depends(conflict_checked_db)]
job_runner.register("publish_knowledge_contribution", publication_job,
                    ("contribution_id", "approved_version", "approved_hash", "reviewer"),
                    cancellable=True, max_attempts=3, timeout_seconds=1800)
job_runner.register("refine_knowledge_contribution", contribution_review_job,
                    ("contribution_id", "expected_version", "instruction", "owner_id", "model_snapshot",
                     "consent_model_egress"),
                    cancellable=True, max_attempts=1, timeout_seconds=AI_JOB_TIMEOUT_SECONDS)


def principal(request):
    identity = getattr(request.state, "principal", {}) or {}
    require_contributor(identity)
    return identity


def committed(db, row):
    try:
        db.commit()
    except StaleDataError as error:
        db.rollback()
        raise HTTPException(409, "Contribution changed concurrently; refresh before continuing") from error
    db.refresh(row)
    return contribution_payload(db, row)


@router.get("")
def list_contributions(request: Request, db: Db, status: str | None = None,
                       content_kind: Literal["KNOWLEDGE", "SKILL"] | None = None,
                       mine: bool = False, limit: int = Query(default=100, ge=1, le=500)):
    identity = principal(request)
    query = select(KnowledgeContribution).where(KnowledgeContribution.status != "DELETED")
    if mine or not is_knowledge_manager(identity):
        query = query.where(KnowledgeContribution.owner_id == identity["id"])
    else:
        query = query.where(or_(KnowledgeContribution.owner_id == identity["id"], KnowledgeContribution.status != "DRAFT"))
    if status:
        terminal_job = exists(select(Job.id).where(Job.id == KnowledgeContribution.publication_job_id,
            Job.status.in_(["FAILED", "CANCELLED", "DEAD_LETTER"])))
        displayed_status = case((and_(KnowledgeContribution.status.in_(["APPROVED", "PUBLISHING"]), terminal_job), "FAILED"),
                                else_=KnowledgeContribution.status)
        query = query.where(displayed_status == status)
    if content_kind:
        query = query.where(KnowledgeContribution.content_kind == content_kind)
    return [contribution_payload(db, row, detail=False) for row in db.scalars(
        query.order_by(KnowledgeContribution.updated_at.desc(), KnowledgeContribution.id).limit(limit))]


@router.post("", status_code=201)
def create(payload: ContributionCreate, request: Request, db: Db):
    return committed(db, create_contribution(db, principal(request), payload.model_dump(exclude_unset=True)))


@router.post("/upload", status_code=201)
async def upload(request: Request, db: Db, file: UploadFile = File(...), category_id: str | None = Form(default=None)):
    identity = principal(request)
    content = await file.read(1_000_001)
    if len(content) > 1_000_000:
        raise HTTPException(413, "Markdown upload exceeds 1 MB")
    try:
        markdown = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(422, "Upload Markdown encoded as UTF-8") from error
    from pathlib import PurePosixPath
    title = PurePosixPath((file.filename or "knowledge.md").replace("\\", "/")).name[:512]
    # Even a file called SKILL.md is ordinary unpublished knowledge in this upload path.
    return committed(db, create_contribution(db, identity, {"title": title, "content": markdown,
        "content_kind": "KNOWLEDGE", "source_type": "document", "category_id": category_id}))


@router.post("/from-library/{record_id}", status_code=201)
def from_library(record_id: str, request: Request, db: Db):
    from app.services.workbench_library import conclusion_contribution
    return committed(db, conclusion_contribution(db, principal(request), record_id))


@router.get("/{contribution_id}")
def get(contribution_id: str, request: Request, db: Db):
    return contribution_payload(db, require_contribution(db, contribution_id, principal(request)))


@router.patch("/{contribution_id}")
def edit(contribution_id: str, payload: ContributionUpdate, request: Request, db: Db):
    identity = principal(request)
    row = require_contribution(db, contribution_id, identity, owner=True)
    return committed(db, update_contribution(db, row, identity, payload.model_dump(exclude_unset=True)))


@router.delete("/{contribution_id}")
def delete(contribution_id: str, request: Request, db: Db, expected_version: int = Query(ge=1)):
    identity = principal(request)
    row = require_contribution(db, contribution_id, identity, owner=True)
    delete_contribution(db, row, identity, expected_version)
    committed(db, row)
    return {"deleted": row.id}


@router.post("/{contribution_id}/submit")
def submit(contribution_id: str, payload: ContributionVersion, request: Request, db: Db):
    identity = principal(request)
    row = require_contribution(db, contribution_id, identity, owner=True)
    return committed(db, submit_contribution(db, row, identity, payload.expected_version))


@router.patch("/{contribution_id}/review-draft")
def reviewer_edit(contribution_id: str, payload: ContributionUpdate, request: Request, db: Db):
    identity = principal(request)
    require_knowledge_admin(identity)
    row = require_contribution(db, contribution_id, identity)
    return committed(db, update_contribution(db, row, identity, payload.model_dump(exclude_unset=True), review=True))


@router.post("/{contribution_id}/review-chat")
async def reviewer_chat(contribution_id: str, payload: ContributionChat, request: Request, db: Db):
    identity = principal(request)
    require_knowledge_admin(identity)
    row = require_contribution(db, contribution_id, identity)
    return committed(db, await refine_contribution(db, row, identity, payload.model_dump()))


@router.post("/{contribution_id}/review-chat-jobs", response_model=JobOut)
def reviewer_chat_job(contribution_id: str, payload: ContributionChat, request: Request, db: Db):
    """Queue one reviewer correction; the synchronous endpoint remains available."""
    from app.services.model_access import ModelAccessError, chat_model_snapshot, resolve_user_chat_profile

    identity = principal(request)
    require_knowledge_admin(identity)
    row = require_contribution(db, contribution_id, identity)
    from app.services.knowledge_contributions import require_version
    require_version(row, payload.expected_version)
    if row.status != "SUBMITTED" or row.operation == "DELETE":
        raise HTTPException(409, "AI correction requires a submitted content change")
    try:
        profile = resolve_user_chat_profile(db, identity, payload.model_profile_id)
        model_snapshot = chat_model_snapshot(db, identity, profile)
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if profile.provider == "mock":
        raise HTTPException(409, "Select a diagnostic Chat API for AI review")
    if profile.mode == "api" and payload.consent_model_egress is not True:
        raise HTTPException(409, "Model egress consent is disabled")
    data = {
        "contribution_id": row.id,
        "expected_version": payload.expected_version,
        "instruction": payload.instruction,
        "owner_id": identity["id"],
        "model_snapshot": model_snapshot,
        "consent_model_egress": payload.consent_model_egress,
    }
    try:
        return job_runner.submit(db, "refine_knowledge_contribution", contribution_review_job,
                                 input_data=data, max_attempts=1, timeout_seconds=AI_JOB_TIMEOUT_SECONDS)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/{contribution_id}/review")
def review(contribution_id: str, payload: ContributionReview, request: Request, db: Db):
    identity = principal(request)
    require_knowledge_admin(identity)
    row = require_contribution(db, contribution_id, identity)
    return committed(db, review_contribution(db, row, identity, payload.model_dump()))
