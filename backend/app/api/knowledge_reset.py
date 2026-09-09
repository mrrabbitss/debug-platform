"""Privileged, explicit preview/confirm API for a server-selected Skill ZIP."""
import os
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import JobOut
from app.services import knowledge_reset as service
from app.services.jobs import job_runner
from app.services.knowledge_access import require_knowledge_admin

router = APIRouter(prefix="/workbench/knowledge-reset", tags=["knowledge-reset"])
Db = Annotated[Session, Depends(get_db)]
job_runner.register(service.KIND, service.reset_job, ("operation_id",), cancellable=True,
    max_attempts=3, timeout_seconds=3600)


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    data_root: str = Field(min_length=1, max_length=4096)


class ConfirmInput(PreviewInput):
    expected_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]
    model_egress_approved: bool = False

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_boolean_confirmation(cls, value):
        if value is not True:
            raise ValueError("confirmed must be the literal JSON boolean true")
        return value


def actor(request, db):
    principal = getattr(request.state, "principal", {})
    require_knowledge_admin(principal)
    identity = principal.get("id")
    try:
        service.require_manager(db, identity)
    except service.ResetError as error:
        raise HTTPException(403, str(error)) from None
    return identity


def source_zip():
    raw = os.environ.get("KNOWLEDGE_RESET_SOURCE_ZIP", "")
    if not raw or not Path(raw).is_absolute():
        raise HTTPException(409, "Server operator must set the absolute KNOWLEDGE_RESET_SOURCE_ZIP first")
    return Path(raw)


@router.post("/preview")
def preview(payload: PreviewInput, request: Request, db: Db):
    identity = actor(request, db)
    try:
        return service.preview_reset(db, **payload.model_dump(), source_zip=source_zip(), actor=identity)
    except service.ResetError as error:
        raise HTTPException(409, str(error)) from None
    except OSError:
        raise HTTPException(409, "The configured source or data root is unavailable") from None


@router.post("/confirm")
def confirm(payload: ConfirmInput, request: Request, db: Db):
    identity = actor(request, db)
    try:
        row, job = service.confirm_reset(db, **payload.model_dump(), source_zip=source_zip(), actor=identity)
    except service.ResetError as error:
        raise HTTPException(409, str(error)) from None
    except OSError:
        db.rollback()
        raise HTTPException(409, "Backup or archive could not be retained; reset was not queued") from None
    if job.status == "QUEUED":
        try:
            job_runner._schedule(job.id)
        except RuntimeError:
            pass  # A later dispatcher sees the already committed outbox job.
    return {"operation": service.operation_payload(row), "job": JobOut.model_validate(job).model_dump()}


@router.get("/{operation_id}")
def status(operation_id: str, request: Request, db: Db):
    actor(request, db)
    try:
        row, value = service.read_operation(db, operation_id)
    except service.ResetError as error:
        raise HTTPException(404, str(error)) from None
    from app.models import Job
    job = db.get(Job, value["job_id"])
    return {"operation": service.operation_payload(row),
        "job": JobOut.model_validate(job).model_dump() if job else None}
