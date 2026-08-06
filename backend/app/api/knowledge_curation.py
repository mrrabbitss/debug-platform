from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (
    KnowledgeCategory,
    KnowledgeCurationRevision,
    KnowledgeCurationSession,
    KnowledgeCurationSourceFile,
)
from app.schemas import (
    JobOut,
    KnowledgeCurationChatRequest,
    KnowledgeCurationConfirmRequest,
    KnowledgeCurationDraftUpdate,
    KnowledgeCurationRetryRequest,
    KnowledgeCurationRestoreRequest,
)
from app.services.audit import record_audit_event
from app.services.jobs import job_runner
from app.services.knowledge_curation import (
    CurationConflict,
    CurationError,
    confirm_curation_session,
    curate_knowledge_folder_job,
    persist_curation_uploads,
    preview_curation_source,
    refine_curation_session,
    resolve_curation_model,
    save_manual_curation_draft,
    session_to_dict,
)
from app.services.knowledge_governance import actor_id
from app.services.knowledge_taxonomy import get_default_category_id
from app.services.llm import LLMError
from app.services.storage import storage


router = APIRouter(prefix="/knowledge-curations", tags=["knowledge-curation"])
Db = Annotated[Session, Depends(get_db)]

job_runner.register(
    "curate_knowledge_folder",
    curate_knowledge_folder_job,
    ("session_id",),
    cancellable=True,
)


def _principal(request: Request) -> dict[str, Any]:
    return getattr(request.state, "principal", {}) or {}


def _require_admin(request: Request) -> dict[str, Any]:
    principal = _principal(request)
    if principal.get("role") != "ADMIN":
        raise HTTPException(403, "Administrator role required")
    return principal


def _get_session(db: Session, session_id: str) -> KnowledgeCurationSession:
    session = db.get(KnowledgeCurationSession, session_id)
    if not session:
        raise HTTPException(404, "Knowledge curation session not found")
    return session


def _raise_curation_error(exc: CurationError) -> None:
    status_code = 409 if isinstance(exc, CurationConflict) else 400
    raise HTTPException(status_code, str(exc)) from exc


@router.get("")
def list_curation_sessions(
    request: Request,
    db: Db,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    _require_admin(request)
    sessions = list(db.scalars(
        select(KnowledgeCurationSession)
        .order_by(KnowledgeCurationSession.updated_at.desc())
        .limit(limit)
    ).all())
    return [session_to_dict(db, session, detail=False) for session in sessions]


@router.post("", status_code=202)
async def create_curation_session(
    request: Request,
    db: Db,
    files: list[UploadFile] = File(...),
    relative_paths_json: str = Form(default="[]"),
    title_hint: str = Form(default=""),
    category_id: str | None = Form(default=None),
    device_type: str | None = Form(default=None),
    device_model: str | None = Form(default=None),
    firmware_range: str | None = Form(default=None),
    module: str | None = Form(default=None),
    trust_level: str = Form(default="MEDIUM"),
    confidentiality: str = Form(default="RESTRICTED"),
    model_profile_id: str | None = Form(default=None),
    consent_model_egress: bool = Form(default=False),
) -> dict[str, Any]:
    principal = _require_admin(request)
    if len(title_hint) > 512:
        raise HTTPException(400, "Title hint is too long")
    if trust_level not in {"LOW", "MEDIUM", "HIGH"}:
        raise HTTPException(400, "Unsupported trust level")
    if confidentiality not in {"PUBLIC", "INTERNAL", "RESTRICTED"}:
        raise HTTPException(400, "Unsupported confidentiality level")
    paths = json_loads(relative_paths_json, None)
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise HTTPException(400, "relative_paths_json must be a JSON string array")
    try:
        profile, model_snapshot = resolve_curation_model(db, model_profile_id)
    except CurationError as exc:
        _raise_curation_error(exc)
    if profile.mode == "api" and not consent_model_egress:
        raise HTTPException(
            409,
            "Confirm that masked source excerpts may be sent to the selected model API",
        )
    selected_category_id = category_id or get_default_category_id(db, "fault_case")
    if selected_category_id:
        category = db.get(KnowledgeCategory, selected_category_id)
        if not category or not category.active:
            raise HTTPException(400, "Knowledge category not found or inactive")

    session_id = new_id("KCUR")
    try:
        uploaded = await persist_curation_uploads(session_id, files, paths)
    except CurationError as exc:
        _raise_curation_error(exc)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc

    manifest = {
        "file_count": len(uploaded),
        "total_bytes": sum(int(item["size_bytes"]) for item in uploaded),
        "files": [
            {
                key: item[key]
                for key in (
                    "source_ref",
                    "relative_path",
                    "sha256",
                    "size_bytes",
                    "source_role",
                )
            }
            for item in uploaded
        ],
        "raw_sources_local_only": True,
        "model_egress_consent": consent_model_egress,
        "model_egress_consent_at": utcnow().isoformat(),
        "model_egress_masked_excerpts_only": True,
    }
    session = KnowledgeCurationSession(
        id=session_id,
        status="QUEUED",
        title_hint=title_hint.strip(),
        category_id=selected_category_id,
        device_type=device_type or None,
        device_model=device_model or None,
        firmware_range=firmware_range or None,
        module=module or None,
        trust_level=trust_level,
        confidentiality=confidentiality,
        model_profile_id=profile.id,
        model_snapshot_json=json_dumps(model_snapshot),
        source_manifest_json=json_dumps(manifest),
        created_by=actor_id(principal),
    )
    session_committed = False
    try:
        db.add(session)
        for item in uploaded:
            db.add(KnowledgeCurationSourceFile(
                id=item["id"],
                session_id=session.id,
                source_ref=item["source_ref"],
                relative_path=item["relative_path"],
                stored_path=item["stored_path"],
                sha256=item["sha256"],
                size_bytes=item["size_bytes"],
                media_type=item["media_type"],
                source_role=item["source_role"],
            ))
        db.commit()
        session_committed = True
        job = job_runner.submit(
            db,
            "curate_knowledge_folder",
            curate_knowledge_folder_job,
            session.id,
            input_data={"session_id": session.id},
        )
        session.job_id = job.id
        db.commit()
        db.refresh(session)
    except Exception:
        db.rollback()
        if session_committed:
            persisted = db.get(KnowledgeCurationSession, session_id)
            if persisted and persisted.status == "QUEUED":
                persisted.status = "FAILED"
                persisted.error_message = "Unable to submit the background curation job"
                db.commit()
        else:
            try:
                storage.remove_curation(session_id)
            except (OSError, ValueError):
                pass
        raise
    record_audit_event(
        "knowledge.curation.create",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge_curation",
        resource_id=session.id,
        details={
            "file_count": len(uploaded),
            "total_bytes": manifest["total_bytes"],
            "model_profile_id": profile.id,
        },
    )
    return {
        "session": session_to_dict(db, session, detail=True),
        "job": JobOut.model_validate(job).model_dump(),
    }


@router.get("/{session_id}")
def get_curation_session(
    session_id: str,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    _require_admin(request)
    return session_to_dict(db, _get_session(db, session_id), detail=True)


@router.post("/{session_id}/retry", status_code=202)
def retry_curation_session(
    session_id: str,
    payload: KnowledgeCurationRetryRequest,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    session = _get_session(db, session_id)
    if session.status not in {"FAILED", "CANCELLED"} or session.draft_version > 0:
        raise HTTPException(409, "Only a failed initial extraction can be retried")
    selected_profile_id = payload.model_profile_id or session.model_profile_id
    try:
        profile, snapshot = resolve_curation_model(db, selected_profile_id)
    except CurationError as exc:
        _raise_curation_error(exc)
    manifest = json_loads(session.source_manifest_json, {})
    has_consent = bool(manifest.get("model_egress_consent"))
    if profile.mode == "api" and not (payload.consent_model_egress or has_consent):
        raise HTTPException(409, "Model API egress consent is required")
    if payload.consent_model_egress:
        manifest["model_egress_consent"] = True
        manifest["model_egress_consent_at"] = utcnow().isoformat()
    session.status = "QUEUED"
    session.error_message = None
    session.model_profile_id = profile.id
    session.model_snapshot_json = json_dumps(snapshot)
    session.source_manifest_json = json_dumps(manifest)
    db.commit()
    job = job_runner.submit(
        db,
        "curate_knowledge_folder",
        curate_knowledge_folder_job,
        session.id,
        input_data={"session_id": session.id, "retry_at": utcnow().isoformat()},
        deduplicate=False,
    )
    session.job_id = job.id
    db.commit()
    db.refresh(session)
    record_audit_event(
        "knowledge.curation.retry",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge_curation",
        resource_id=session.id,
        details={"job_id": job.id, "model_profile_id": profile.id},
    )
    return {
        "session": session_to_dict(db, session, detail=True),
        "job": JobOut.model_validate(job).model_dump(),
    }


@router.get("/{session_id}/sources/{source_id}/preview")
def preview_source(
    session_id: str,
    source_id: str,
    request: Request,
    db: Db,
    start_line: int = Query(default=1, ge=1),
    line_count: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    _require_admin(request)
    source = db.get(KnowledgeCurationSourceFile, source_id)
    if not source or source.session_id != session_id:
        raise HTTPException(404, "Knowledge curation source file not found")
    try:
        return preview_curation_source(
            source,
            start_line=start_line,
            line_count=line_count,
        )
    except CurationError as exc:
        _raise_curation_error(exc)


@router.post("/{session_id}/chat")
async def chat_with_curation_session(
    session_id: str,
    payload: KnowledgeCurationChatRequest,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    session = _get_session(db, session_id)
    try:
        session = await refine_curation_session(
            db,
            session,
            instruction=payload.instruction.strip(),
            expected_draft_version=payload.expected_draft_version,
            actor=actor_id(principal),
        )
    except (CurationError, LLMError) as exc:
        if isinstance(exc, CurationError):
            _raise_curation_error(exc)
        raise HTTPException(502, str(exc)) from exc
    record_audit_event(
        "knowledge.curation.refine",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge_curation",
        resource_id=session.id,
        details={"draft_version": session.draft_version},
    )
    return session_to_dict(db, session, detail=True)


@router.patch("/{session_id}/draft")
def update_curation_draft(
    session_id: str,
    payload: KnowledgeCurationDraftUpdate,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    session = _get_session(db, session_id)
    try:
        session = save_manual_curation_draft(
            db,
            session,
            markdown=payload.markdown,
            title=payload.title,
            expected_draft_version=payload.expected_draft_version,
            change_summary=payload.change_summary,
            actor=actor_id(principal),
        )
    except CurationError as exc:
        _raise_curation_error(exc)
    record_audit_event(
        "knowledge.curation.manual_edit",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge_curation",
        resource_id=session.id,
        details={"draft_version": session.draft_version},
    )
    return session_to_dict(db, session, detail=True)


@router.post("/{session_id}/revisions/{version}/restore")
def restore_curation_revision(
    session_id: str,
    version: int,
    payload: KnowledgeCurationRestoreRequest,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    session = _get_session(db, session_id)
    revision = db.scalar(
        select(KnowledgeCurationRevision).where(
            KnowledgeCurationRevision.session_id == session.id,
            KnowledgeCurationRevision.version == version,
        )
    )
    if not revision:
        raise HTTPException(404, "Knowledge curation revision not found")
    try:
        session = save_manual_curation_draft(
            db,
            session,
            markdown=revision.markdown,
            title=session.draft_title,
            expected_draft_version=payload.expected_draft_version,
            change_summary=f"Restored curation revision v{version}",
            actor=actor_id(principal),
        )
    except CurationError as exc:
        _raise_curation_error(exc)
    record_audit_event(
        "knowledge.curation.restore_revision",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge_curation",
        resource_id=session.id,
        details={
            "restored_version": version,
            "new_draft_version": session.draft_version,
        },
    )
    return session_to_dict(db, session, detail=True)


@router.post("/{session_id}/confirm")
def confirm_curation(
    session_id: str,
    payload: KnowledgeCurationConfirmRequest,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    session = _get_session(db, session_id)
    try:
        document = confirm_curation_session(
            db,
            session,
            expected_draft_version=payload.expected_draft_version,
            actor=actor_id(principal),
        )
    except CurationError as exc:
        _raise_curation_error(exc)
    record_audit_event(
        "knowledge.curation.confirm",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge_curation",
        resource_id=session.id,
        details={
            "draft_version": session.draft_version,
            "knowledge_document_id": document.id,
        },
    )
    return {
        "session": session_to_dict(db, _get_session(db, session_id), detail=True),
        "knowledge_document": {
            "id": document.id,
            "title": document.title,
            "review_status": document.review_status,
            "version": document.version,
            "lock_version": document.lock_version,
        },
    }


@router.delete("/{session_id}")
def delete_curation_session(
    session_id: str,
    request: Request,
    db: Db,
) -> dict[str, Any]:
    principal = _require_admin(request)
    session = _get_session(db, session_id)
    if session.status in {"QUEUED", "EXTRACTING", "CONFIRMING"}:
        raise HTTPException(409, "Wait for the active operation to finish before deletion")
    if session.knowledge_document_id:
        raise HTTPException(
            409,
            "Confirmed curation evidence is retained for knowledge provenance",
        )
    db.delete(session)
    db.commit()
    try:
        storage.remove_curation(session_id)
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as exc:
        raise HTTPException(
            500,
            f"Session metadata was deleted but source cleanup failed: {exc}",
        ) from exc
    record_audit_event(
        "knowledge.curation.delete",
        actor_id=actor_id(principal),
        actor_type=str(principal.get("type") or "system"),
        resource_type="knowledge_curation",
        resource_id=session_id,
        details={},
    )
    return {"deleted": session_id}
