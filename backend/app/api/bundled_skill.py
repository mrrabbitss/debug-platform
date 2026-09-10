"""Review and add a verified installed bundle without replacing existing knowledge."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.knowledge_reset import Db, actor
from app.models import Job
from app.schemas import JobOut
from app.services import bundled_knowledge as bundled, knowledge_reset as reset
from app.services.knowledge_bundle_import import public_preview
from app.services.jobs import job_runner

router = APIRouter(prefix="/workbench/bundled-skill", tags=["bundled-skill"])


class PreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class ConfirmInput(PreviewInput):
    expected_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirmed: Literal[True]
    model_egress_approved: bool = False

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("confirmed must be the literal JSON boolean true")
        return value


def inputs():
    package = bundled.read_packaged_bundle()
    if package is None:
        raise reset.ResetError("此服务器未附带内置 Skill 包，请安装完整服务器包。")
    return {"source_zip": package[0], "data_root": bundled.get_settings().data_root, "preserve_existing": True}


@router.post("/preview")
def preview(payload: PreviewInput, request: Request, db: Db):
    identity = actor(request, db)
    try:
        return public_preview(reset.preview_reset(db, **payload.model_dump(), **inputs(), actor=identity))
    except (ValueError, OSError) as error:
        raise HTTPException(409, str(error) if isinstance(error, reset.ResetError) else "内置 Skill 包校验失败或目录不可用。") from None


@router.post("/confirm")
def confirm(payload: ConfirmInput, request: Request, db: Db):
    identity = actor(request, db)
    try:
        row, job = reset.confirm_reset(db, **payload.model_dump(), **inputs(), actor=identity)
    except (ValueError, OSError) as error:
        db.rollback()
        raise HTTPException(409, str(error) if isinstance(error, reset.ResetError) else "内置 Skill 校验或备份失败，未提交导入。") from None
    if job.status == "QUEUED":
        try:
            job_runner._schedule(job.id)
        except RuntimeError:
            pass  # The durable dispatcher resumes the committed job.
    return {"operation": reset.operation_payload(row), "job": JobOut.model_validate(job).model_dump()}


@router.get("/{operation_id}")
def status(operation_id: str, request: Request, db: Db):
    actor(request, db)
    try:
        row, value = reset.read_operation(db, operation_id)
        # The original installer initialization also appears in this dialog.
        if not value["approved_plan"].get("preserve_existing") and value.get("approval_origin") != "INSTALLER_DEFAULT":
            raise reset.ResetError("此操作不是内置 Skill 导入。")
    except reset.ResetError as error:
        raise HTTPException(404, str(error)) from None
    job = db.get(Job, value["job_id"])
    return {"operation": reset.operation_payload(row), "job": JobOut.model_validate(job).model_dump() if job else None}
