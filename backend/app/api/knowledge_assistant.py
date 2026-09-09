"""Versioned administrator conversations, full sources and one-click approval."""
import json
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm.exc import StaleDataError

from app.api.workbench import Db, admin
from app.core.utils import json_loads
from app.models import Job
from app.schemas import JobOut
from app.workbench_models import WorkbenchRecord
from app.services import assistant_sessions as transitions
from app.services.assistant_sources import relative_path, references, source_page, reading_page
from app.services.assistant_state import digest, locked_session, cancel_session, require_admin_actor
from app.services.workbench import make_record, record_payload
from app.services.knowledge_assistant import plan_job
from app.services.assistant_publication import publication_job
from app.services.jobs import job_runner

router = APIRouter(prefix="/workbench/assistant", tags=["knowledge-assistant"])
job_runner.register("assistant_plan", plan_job, ("session_id", "request_version"),
                    cancellable=True, max_attempts=1, timeout_seconds=3600)
job_runner.register("assistant_publish", publication_job, ("session_id", "request_version", "reviewer"),
                    cancellable=True, max_attempts=3, timeout_seconds=3600)


def actor(request, db):
    identity = admin(request)
    try:
        require_admin_actor(db, identity.get("id"))
    except ValueError as error:
        raise HTTPException(403, str(error)) from error
    return identity["id"]


def session_record(db, session_id):
    row = db.get(WorkbenchRecord, session_id)
    if not row or row.kind != "assistant":
        raise HTTPException(404, "整理会话不存在")
    return row


def editable(db, session_id, version):
    session_record(db, session_id)
    try:
        row, value = locked_session(db, session_id)
        if row.version != version:
            raise ValueError("会话已变化，请刷新后重试")
        return row, value
    except (ValueError, StaleDataError) as error:
        db.rollback()
        raise HTTPException(409, "会话已变化，请刷新后重试") from error


def finish(db, row, job=None):
    try:
        db.commit()
        db.refresh(row)
    except StaleDataError as error:
        db.rollback()
        raise HTTPException(409, "会话已变化，请刷新后重试") from error
    if job:
        # A failed wakeup cannot lose the already committed outbox job.
        try:
            job_runner._schedule(job.id)
        except RuntimeError:
            pass
    return public_session(row)


def public_session(row):
    value = record_payload(row)
    value.pop("model_snapshot", None)
    return value


@router.post("")
async def create_session(request: Request, db: Db, files: list[UploadFile] = File(default=[]),
                         paths: str = Form(default="[]", max_length=34000),
                         message: str = Form(default="请完整阅读资料，比较已有知识，给出归类和新增、合并或替换方案。", min_length=1, max_length=12000),
                         model_egress_approved: bool = Form(default=True),
                         mode: Literal["auto", "answer", "edit"] = Form(default="auto")):
    identity = actor(request, db)
    try:
        names = json.loads(paths)
    except (ValueError, TypeError) as error:
        raise HTTPException(422, "文件相对路径清单必须是JSON数组") from error
    if not isinstance(names, list) or len(names) != len(files) or len(files) > 64 or not message.strip():
        raise HTTPException(422, "文件清单或问题无效；每次最多64个文件")
    items, seen, total = [], set(), 0
    for file, raw_name in zip(files, names, strict=True):
        try:
            name = relative_path(raw_name)
        except ValueError as error:
            raise HTTPException(422, "文件相对路径无效") from error
        if name.casefold() in seen:
            raise HTTPException(422, "文件相对路径重复")
        if PurePosixPath(name).suffix.lower() not in {".md", ".markdown", ".txt", ".json", ".yaml", ".yml", ".py", ".c", ".h", ".cpp", ".ps1", ".sh", ".toml", ".ini", ".cfg", ".csv"}:
            raise HTTPException(422, "文件夹含不支持的非文本文件；请去除后上传，没有忽略文件")
        data = await file.read(1024 * 1024 + 1)
        total += len(data)
        if len(data) > 1024 * 1024 or total > 8 * 1024 * 1024:
            raise HTTPException(413, "单文件最多1MiB，文件夹最多8MiB；未截断内容")
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise HTTPException(422, "文件必须使用UTF-8编码") from error
        if not content.strip() or "\x00" in content:
            raise HTTPException(422, "文件为空或含二进制内容")
        seen.add(name.casefold())
        items.append({"path": name, "content": content, "sha256": digest(content), "references": references(content)})
    value = {"schema_version": 2, "title": items[0]["path"].split("/")[0] if items else message[:40],
        "files": items, "messages": [{"role": "user", "content": message}], "request_version": 1,
        "model_egress_approved": model_egress_approved, "mode": mode, "coverage": {}, "selected_paths": []}
    row = make_record(db, "assistant", identity, {})
    try:
        job = transitions.start_reading(db, row, value, identity, reset=True)
    except ValueError as error:
        db.rollback()
        raise HTTPException(getattr(error, "status_code", 409), str(error)) from error
    return finish(db, row, job)


@router.get("")
def sessions(request: Request, db: Db):
    actor(request, db)
    result = []
    for row in db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "assistant").order_by(WorkbenchRecord.updated_at.desc()).limit(100)):
        value = record_payload(row)
        result.append({key: value.get(key) for key in ("id", "title", "status", "version", "created_at")})
    return result


@router.get("/{session_id}")
def read_session(session_id: str, request: Request, db: Db):
    actor(request, db)
    value = public_session(session_record(db, session_id))
    if value.get("job_id"):
        job = db.get(Job, value["job_id"])
        value["job"] = JobOut.model_validate(job).model_dump() if job else None
    return value


class VersionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: int = Field(ge=1)


class ConversationInput(VersionInput):
    message: str = Field(min_length=1, max_length=12000, pattern=r"\S")
    mode: Literal["auto", "answer", "edit"] | None = None
    model_egress_approved: bool | None = None


class Confirmation(VersionInput):
    review_digest: str | None = Field(default=None, min_length=64, max_length=64)


class ConsentInput(VersionInput):
    model_egress_approved: bool


def transition(db, session_id, version, function):
    row, value = editable(db, session_id, version)
    try:
        job = function(row, value)
        return finish(db, row, job)
    except ValueError as error:
        db.rollback()
        raise HTTPException(409, str(error)) from error


@router.post("/{session_id}/messages")
def converse(session_id: str, payload: ConversationInput, request: Request, db: Db):
    identity = actor(request, db)
    return transition(db, session_id, payload.version, lambda row, value: transitions.correct(db, row, value, payload, identity))


@router.post("/{session_id}/confirm")
def confirm(session_id: str, payload: Confirmation, request: Request, db: Db):
    identity = actor(request, db)
    current = session_record(db, session_id)
    value = json_loads(current.payload_json, {})
    if (value.get("status") in {"APPROVED", "BUILDING", "PUBLISH_FAILED", "PUBLISHED"}
            and value.get("approval_request_version") == payload.version
            and value.get("approved_by") == identity
            and (payload.review_digest is None or payload.review_digest == value.get("approved_digest"))):
        return public_session(current)
    return transition(db, session_id, payload.version,
        lambda row, value: transitions.approve(db, row, value, identity, payload.review_digest))


@router.post("/{session_id}/cancel")
def cancel(session_id: str, payload: VersionInput, request: Request, db: Db):
    actor(request, db)
    return transition(db, session_id, payload.version, lambda row, value: cancel_session(db, row, value))


@router.post("/{session_id}/pause")
def pause(session_id: str, payload: VersionInput, request: Request, db: Db):
    actor(request, db)
    return transition(db, session_id, payload.version, lambda row, value: cancel_session(db, row, value, pause=True))


@router.post("/{session_id}/retry")
def retry(session_id: str, payload: VersionInput, request: Request, db: Db):
    identity = actor(request, db)
    return transition(db, session_id, payload.version, lambda row, value: transitions.retry_reading(db, row, value, identity))


@router.patch("/{session_id}/consent")
def consent(session_id: str, payload: ConsentInput, request: Request, db: Db):
    actor(request, db)
    return transition(db, session_id, payload.version, lambda row, value: transitions.consent(db, row, value, payload.model_egress_approved))


def source_result(db, session_id, function, path, cursor):
    row = session_record(db, session_id)
    try:
        return function(db, session_id, json_loads(row.payload_json, {}), relative_path(path), cursor)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error


@router.get("/{session_id}/source")
def source(session_id: str, request: Request, db: Db, path: str = Query(max_length=512), cursor: int = Query(default=0, ge=0)):
    actor(request, db)
    return source_result(db, session_id, source_page, path, cursor)


@router.get("/{session_id}/readings")
def readings(session_id: str, request: Request, db: Db, path: str = Query(max_length=512), cursor: int = Query(default=0, ge=0)):
    actor(request, db)
    return source_result(db, session_id, reading_page, path, cursor)
