from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (
    AccessToken, AnalysisRun, Artifact, AuditEvent, Case, CaseMember, CodeSymbol, Job,
    KnowledgeCategory, KnowledgeChunk, KnowledgeDocument, KnowledgeDocumentCategory,
    KnowledgeEmbedding, LogEvent, ModelProfile, Repository, Report, UserAccount,
)
from app.schemas import (
    AccessTokenCreate, AnalysisOut, ArtifactOut, CaseCreate, CaseMemberUpdate, CaseOut,
    CaseUpdate, ChatRequest, ChatResponse, JobOut, KnowledgeCategoryCreate,
    KnowledgeCategoryOut, KnowledgeCategoryUpdate, KnowledgeCreate, KnowledgeDetailOut,
    KnowledgeOut, KnowledgeUpdate, ModelProfileCreate, ModelProfileOut, ModelProfileUpdate,
    PatchRequest, StaticAnalysisRequest, UserCreate, UserUpdate,
)
from app.services.archive import UnsafeArchiveError, extract_archive
from app.services.access_control import accessible_case_clause, case_permission, issue_access_token
from app.services.audit import record_audit_event
from app.services.code_index import index_repository_job
from app.services.diagnosis import analyze_case_job, chat_about_case
from app.services.events import active_log_event_clause
from app.services.health import readiness_report, system_status_report
from app.services.jobs import job_runner
from app.services.knowledge import index_document, reindex_knowledge_job
from app.services.knowledge_taxonomy import (
    category_document_counts, descendant_category_ids, get_default_category_id,
    new_category_code, new_category_id, set_document_category, validate_category_parent,
)
from app.services.llm import LLMError, get_active_chat_model_info, get_llm_provider
from app.services.model_profiles import (
    activate_model_profile, get_active_model_profile, model_profile_to_dict,
    new_model_profile_id, set_profile_api_key, validate_model_profile,
)
from app.services.parse_service import parse_artifact_job
from app.services.report import generate_docx, generate_html_file, generate_pdf, render_html
from app.services.retrieval_models import (
    RetrievalModelError, embed_texts, embedding_index_status, rerank_documents,
)
from app.services.static_tools import static_analysis_job
from app.services.storage import normalize_debug_log_filename, storage
from app.services.text_files import read_text_range, search_text_lines

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]

job_runner.register("parse_artifact", parse_artifact_job, ("case_id", "artifact_id"), cancellable=True)
job_runner.register("analyze_case", analyze_case_job, ("case_id",), cancellable=True)
job_runner.register("reindex_knowledge", reindex_knowledge_job, ("profile_id",), cancellable=True)
job_runner.register("index_repository", index_repository_job, ("repository_id",))
job_runner.register("static_analysis", static_analysis_job, ("repository_id", "tools"), cancellable=True)


@router.get("/health")
def health() -> dict:
    # Keep the legacy health route intentionally minimal because probes are
    # unauthenticated. Detailed environment/model information is available to
    # administrators through /system/status and /system/model.
    return {"status": "ok"}


@router.get("/health/live")
def health_live() -> dict:
    return {"status": "alive"}


@router.get("/health/ready")
def health_ready(db: Db):
    report = readiness_report(db, get_settings().storage_root)
    public_report = {
        "status": report["status"],
        "ready": report["ready"],
        "checked_at": report["checked_at"],
        "checks": {
            name: {
                "ok": check["ok"],
                **({"error_type": check["error_type"]} if not check["ok"] else {}),
            }
            for name, check in report["checks"].items()
        },
    }
    return JSONResponse(public_report, status_code=200 if report["ready"] else 503)


@router.get("/system/auth-info")
def auth_info() -> dict:
    settings = get_settings()
    return {
        "mode": settings.auth_mode,
        "token_header": "X-API-Key",
        "legacy_admin_enabled": bool(settings.api_key and settings.auth_allow_legacy_admin),
    }


@router.get("/system/status")
def system_status(db: Db) -> dict:
    settings = get_settings()
    report = system_status_report(db, settings.storage_root, settings.job_workers)
    report["app"] = settings.app_name
    report["environment"] = settings.app_env
    return report


@router.get("/system/me")
def current_identity(request: Request) -> dict:
    return getattr(request.state, "principal", {})


@router.get("/system/user-directory")
def user_directory(db: Db) -> list[dict]:
    users = db.scalars(
        select(UserAccount)
        .where(UserAccount.active.is_(True))
        .order_by(UserAccount.username)
    ).all()
    return [
        {
            "id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "role": user.role,
        }
        for user in users
    ]


def _user_payload(user: UserAccount) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
        "active": user.active,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
    }


def _token_payload(token: AccessToken) -> dict:
    return {
        "id": token.id,
        "user_id": token.user_id,
        "name": token.name,
        "token_hint": token.token_hint,
        "expires_at": token.expires_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
        "created_at": token.created_at,
    }


@router.get("/system/users")
def list_users(db: Db) -> list[dict]:
    users = db.scalars(select(UserAccount).order_by(UserAccount.username)).all()
    return [_user_payload(user) for user in users]


@router.post("/system/users")
def create_user(payload: UserCreate, request: Request, db: Db) -> dict:
    username = payload.username.lower()
    if db.scalar(select(UserAccount.id).where(UserAccount.username == username)):
        raise HTTPException(409, "Username already exists")
    user = UserAccount(
        id=new_id("USR"),
        username=username,
        display_name=payload.display_name,
        role=payload.role,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    raw_token = None
    token_payload = None
    if payload.issue_token:
        token, raw_token = issue_access_token(
            db,
            user,
            name=payload.token_name,
            expires_days=payload.token_expires_days,
        )
        token_payload = _token_payload(token)
    principal = getattr(request.state, "principal", {})
    record_audit_event(
        "auth.user_created",
        actor_id=principal.get("id"),
        actor_type=principal.get("type", "system"),
        resource_type="user",
        resource_id=user.id,
        details={"username": user.username, "role": user.role, "token_issued": bool(raw_token)},
    )
    return {"user": _user_payload(user), "token": token_payload, "raw_token": raw_token}


@router.patch("/system/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request, db: Db) -> dict:
    user = db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    changes = payload.model_dump(exclude_unset=True)
    removing_admin = user.role == "ADMIN" and (
        changes.get("role", user.role) != "ADMIN" or changes.get("active", user.active) is False
    )
    if removing_admin:
        other_admins = int(db.scalar(select(func.count(UserAccount.id)).where(
            UserAccount.id != user.id,
            UserAccount.role == "ADMIN",
            UserAccount.active.is_(True),
        )) or 0)
        if other_admins == 0:
            raise HTTPException(409, "At least one active administrator must remain")
    for key, value in changes.items():
        setattr(user, key, value)
    if changes.get("active") is False:
        tokens = db.scalars(select(AccessToken).where(
            AccessToken.user_id == user.id,
            AccessToken.revoked_at.is_(None),
        )).all()
        for token in tokens:
            token.revoked_at = utcnow()
    db.commit()
    db.refresh(user)
    principal = getattr(request.state, "principal", {})
    record_audit_event(
        "auth.user_updated",
        actor_id=principal.get("id"),
        actor_type=principal.get("type", "system"),
        resource_type="user",
        resource_id=user.id,
        details={"changed_fields": sorted(changes)},
    )
    return _user_payload(user)


@router.get("/system/users/{user_id}/tokens")
def list_user_tokens(user_id: str, db: Db) -> list[dict]:
    if not db.get(UserAccount, user_id):
        raise HTTPException(404, "User not found")
    tokens = db.scalars(
        select(AccessToken).where(AccessToken.user_id == user_id).order_by(AccessToken.created_at.desc())
    ).all()
    return [_token_payload(token) for token in tokens]


@router.post("/system/users/{user_id}/tokens")
def create_user_token(user_id: str, payload: AccessTokenCreate, request: Request, db: Db) -> dict:
    user = db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    try:
        token, raw_token = issue_access_token(
            db,
            user,
            name=payload.name,
            expires_days=payload.expires_days,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    principal = getattr(request.state, "principal", {})
    record_audit_event(
        "auth.token_issued",
        actor_id=principal.get("id"),
        actor_type=principal.get("type", "system"),
        resource_type="access_token",
        resource_id=token.id,
        details={"user_id": user.id, "name": token.name, "expires_at": token.expires_at},
    )
    return {"token": _token_payload(token), "raw_token": raw_token}


@router.delete("/system/users/{user_id}/tokens/{token_id}")
def revoke_user_token(user_id: str, token_id: str, request: Request, db: Db) -> dict:
    token = db.get(AccessToken, token_id)
    if not token or token.user_id != user_id:
        raise HTTPException(404, "Token not found")
    if token.revoked_at is None:
        token.revoked_at = utcnow()
        db.commit()
    principal = getattr(request.state, "principal", {})
    record_audit_event(
        "auth.token_revoked",
        actor_id=principal.get("id"),
        actor_type=principal.get("type", "system"),
        resource_type="access_token",
        resource_id=token.id,
        details={"user_id": user_id},
    )
    return {"revoked": token.id}


@router.post("/cases", response_model=CaseOut)
def create_case(payload: CaseCreate, request: Request, db: Db) -> Case:
    principal = getattr(request.state, "principal", {})
    owner_id = principal.get("id") if principal.get("type") == "user_token" else None
    case = Case(id=new_id("CASE"), owner_id=owner_id, **payload.model_dump())
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def _require_case_owner(request: Request, db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    principal = getattr(request.state, "principal", {})
    if principal.get("role") != "ADMIN" and case_permission(db, case_id, principal) != "OWNER":
        raise HTTPException(403, "Only an administrator or case owner may manage members")
    return case


@router.get("/cases/{case_id}/members")
def list_case_members(case_id: str, request: Request, db: Db) -> list[dict]:
    _require_case_owner(request, db, case_id)
    rows = db.execute(
        select(CaseMember, UserAccount)
        .join(UserAccount, UserAccount.id == CaseMember.user_id)
        .where(CaseMember.case_id == case_id)
        .order_by(UserAccount.username)
    ).all()
    return [
        {
            "id": membership.id,
            "case_id": membership.case_id,
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "permission": membership.permission,
        }
        for membership, user in rows
    ]


@router.put("/cases/{case_id}/members/{user_id}")
def set_case_member(
    case_id: str,
    user_id: str,
    payload: CaseMemberUpdate,
    request: Request,
    db: Db,
) -> dict:
    case = _require_case_owner(request, db, case_id)
    user = db.get(UserAccount, user_id)
    if not user or not user.active:
        raise HTTPException(404, "Active user not found")
    if case.owner_id == user_id:
        raise HTTPException(409, "The case owner already has owner permission")
    membership = db.scalar(select(CaseMember).where(
        CaseMember.case_id == case_id,
        CaseMember.user_id == user_id,
    ))
    if membership:
        membership.permission = payload.permission
    else:
        membership = CaseMember(
            id=new_id("MEM"),
            case_id=case_id,
            user_id=user_id,
            permission=payload.permission,
        )
        db.add(membership)
    db.commit()
    return {
        "id": membership.id,
        "case_id": case_id,
        "user_id": user_id,
        "permission": membership.permission,
    }


@router.delete("/cases/{case_id}/members/{user_id}")
def remove_case_member(case_id: str, user_id: str, request: Request, db: Db) -> dict:
    _require_case_owner(request, db, case_id)
    membership = db.scalar(select(CaseMember).where(
        CaseMember.case_id == case_id,
        CaseMember.user_id == user_id,
    ))
    if not membership:
        raise HTTPException(404, "Case member not found")
    db.delete(membership)
    db.commit()
    return {"deleted": membership.id}


@router.get("/cases", response_model=list[CaseOut])
def list_cases(request: Request, db: Db, limit: int = Query(default=100, ge=1, le=500)) -> list[Case]:
    query = select(Case)
    principal = getattr(request.state, "principal", {})
    if principal.get("type") == "user_token" and principal.get("role") != "ADMIN":
        query = query.where(accessible_case_clause(str(principal["id"])))
    return list(db.scalars(query.order_by(Case.created_at.desc()).limit(limit)).all())


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str, db: Db) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@router.get("/cases/{case_id}/access")
def get_case_access(case_id: str, request: Request, db: Db) -> dict:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    principal = getattr(request.state, "principal", {})
    return {
        "case_id": case_id,
        "role": principal.get("role", "VIEWER"),
        "permission": case_permission(db, case_id, principal),
    }


@router.patch("/cases/{case_id}", response_model=CaseOut)
def update_case(case_id: str, payload: CaseUpdate, db: Db) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(case, key, value)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, db: Db) -> dict:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    artifact_ids = list(db.scalars(select(Artifact.id).where(Artifact.case_id == case_id)).all())
    repository_ids = list(db.scalars(select(Repository.id).where(Repository.case_id == case_id)).all())
    db.delete(case)
    db.commit()
    cleanup_errors = storage.cleanup_case(artifact_ids, repository_ids, case_id)
    return {"deleted": case_id, "storage_cleanup_errors": cleanup_errors}


@router.post("/cases/{case_id}/artifacts", response_model=ArtifactOut)
async def upload_artifact(
    case_id: str,
    db: Db,
    file: UploadFile = File(...),
    kind: str = Form(default="debug_log"),
) -> Artifact:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    artifact_id = new_id("ART")
    raw_uploaded_name = file.filename or "collectDebuginfo"
    if kind == "debug_log":
        uploaded_name, stored_name = normalize_debug_log_filename(raw_uploaded_name)
    else:
        uploaded_name = Path(raw_uploaded_name.replace("\\", "/")).name
        stored_name = uploaded_name
    try:
        path, size, digest = await storage.save_upload(file, artifact_id, target_name=stored_name)
    except ValueError as exc:
        storage.remove_artifact(artifact_id)
        raise HTTPException(413, str(exc)) from exc
    artifact = Artifact(
        id=artifact_id, case_id=case_id, kind=kind, original_name=stored_name,
        stored_path=storage.storage_key(path), sha256=digest, size_bytes=size, status="UPLOADED",
        metadata_json=json_dumps({
            "uploaded_original_name": uploaded_name,
            "filename_normalized": uploaded_name != stored_name,
        }),
    )
    db.add(artifact)
    case.status = "UPLOADED"
    db.commit()
    db.refresh(artifact)
    return artifact


@router.get("/cases/{case_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(case_id: str, db: Db) -> list[Artifact]:
    return list(db.scalars(select(Artifact).where(Artifact.case_id == case_id).order_by(Artifact.created_at.desc())).all())


@router.delete("/cases/{case_id}/artifacts/{artifact_id}")
def delete_artifact(case_id: str, artifact_id: str, db: Db) -> dict:
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.case_id != case_id:
        raise HTTPException(404, "Artifact not found")
    repository_ids = list(db.scalars(
        select(Repository.id).where(Repository.artifact_id == artifact_id)
    ).all())
    db.delete(artifact)
    db.commit()
    cleanup_errors: list[str] = []
    try:
        storage.remove_artifact(artifact_id)
    except (OSError, ValueError) as exc:
        cleanup_errors.append(str(exc))
    for repository_id in repository_ids:
        try:
            storage.remove_repository(repository_id)
        except (OSError, ValueError) as exc:
            cleanup_errors.append(str(exc))
    return {"deleted": artifact_id, "storage_cleanup_errors": cleanup_errors}


@router.post("/cases/{case_id}/artifacts/{artifact_id}/parse", response_model=JobOut)
def parse_artifact(case_id: str, artifact_id: str, db: Db) -> Job:
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.case_id != case_id:
        raise HTTPException(404, "Artifact not found")
    return job_runner.submit(db, "parse_artifact", parse_artifact_job, case_id, artifact_id, input_data={"case_id": case_id, "artifact_id": artifact_id})


@router.get("/cases/{case_id}/events", response_model=list[dict])
def list_events(
    case_id: str,
    db: Db,
    level: str | None = None,
    module: str | None = None,
    component: str | None = None,
    search: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    query = _filtered_event_query(case_id, level, module, component, search)
    rows = db.scalars(query.order_by(LogEvent.timestamp_normalized.asc().nullslast(), LogEvent.line_start.asc()).offset(offset).limit(limit)).all()
    return [
        {
            "id": row.id, "artifact_id": row.artifact_id,
            "source_file": row.source_file, "line_start": row.line_start, "line_end": row.line_end,
            "timestamp_raw": row.timestamp_raw, "timestamp_normalized": row.timestamp_normalized,
            "level": row.level, "module": row.module, "component": row.component,
            "event_code": row.event_code, "message": row.message, "raw_text": row.raw_text,
            "entities": json_loads(row.entities_json, {}), "confidence": row.confidence,
        }
        for row in rows
    ]


def _filtered_event_query(
    case_id: str,
    level: str | None = None,
    module: str | None = None,
    component: str | None = None,
    search: str | None = None,
):
    query = (
        select(LogEvent)
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
    )
    if level:
        query = query.where(LogEvent.level == level.upper())
    if module:
        query = query.where(LogEvent.module == module.upper())
    if component:
        query = query.where(LogEvent.component.ilike(f"%{component}%"))
    if search:
        query = query.where((LogEvent.message.ilike(f"%{search}%")) | (LogEvent.event_code.ilike(f"%{search}%")))
    return query


@router.get("/cases/{case_id}/events/stats")
def event_stats(
    case_id: str,
    db: Db,
    level: str | None = None,
    module: str | None = None,
    component: str | None = None,
    search: str | None = None,
) -> dict:
    filtered = _filtered_event_query(case_id, level, module, component, search).subquery()
    filtered_total = int(db.scalar(select(func.count()).select_from(filtered)) or 0)
    total = int(db.scalar(
        select(func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
    ) or 0)
    level_counts = dict(db.execute(
        select(LogEvent.level, func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .group_by(LogEvent.level)
    ).all())
    module_counts = dict(db.execute(
        select(LogEvent.module, func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .group_by(LogEvent.module)
    ).all())
    return {
        "total": total,
        "filtered_total": filtered_total,
        "level_counts": level_counts,
        "module_counts": module_counts,
    }


@router.get("/cases/{case_id}/timeline")
def timeline(case_id: str, db: Db, limit: int = Query(default=1000, ge=1, le=5000)) -> dict:
    rows = db.scalars(
        select(LogEvent)
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .order_by(LogEvent.timestamp_normalized.asc().nullslast(), LogEvent.source_file.asc(), LogEvent.line_start.asc())
        .limit(limit)
    ).all()
    module_counts = dict(db.execute(
        select(LogEvent.module, func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .group_by(LogEvent.module)
    ).all())
    return {
        "items": [
            {
                "id": row.id, "artifact_id": row.artifact_id,
                "time": row.timestamp_normalized or row.timestamp_raw,
                "module": row.module, "component": row.component, "level": row.level,
                "event_code": row.event_code, "message": row.message,
                "source_file": row.source_file, "line_start": row.line_start,
            }
            for row in rows
        ],
        "module_counts": module_counts,
    }


@router.get("/artifacts/{artifact_id}/files")
def artifact_files(artifact_id: str, db: Db) -> dict:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    return json_loads(artifact.metadata_json, {})


@router.get("/artifacts/{artifact_id}/content", response_class=PlainTextResponse)
def artifact_content(
    artifact_id: str,
    db: Db,
    path: str = Query(...),
    start_line: int = Query(default=1, ge=1),
    line_count: int = Query(default=500, ge=1, le=5000),
) -> PlainTextResponse:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    meta = json_loads(artifact.metadata_json, {})
    root_text = meta.get("extract_root")
    if not root_text:
        raise HTTPException(409, "Artifact has not been parsed")
    try:
        root = storage.resolve_path(root_text)
    except ValueError as exc:
        raise HTTPException(400, "Unsafe stored path") from exc
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "Unsafe path")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    manifest_item = next(
        (item for item in meta.get("manifest", []) if item.get("path", "").replace("\\", "/") == path.replace("\\", "/")),
        {},
    )
    selected = read_text_range(
        target,
        start_line,
        line_count,
        line_index=manifest_item.get("line_index"),
        encoding_hint=manifest_item.get("encoding"),
    )
    if selected is None:
        raise HTTPException(415, "File is binary or exceeds the text parsing limit")
    total_lines = int(manifest_item.get("line_count") or 0)
    has_more = selected.has_more or bool(total_lines and start_line - 1 + selected.returned_lines < total_lines)
    return PlainTextResponse(
        selected.text,
        headers={
            "X-Start-Line": str(start_line),
            "X-Returned-Lines": str(selected.returned_lines),
            "X-Total-Lines": str(total_lines),
            "X-Has-More": "true" if has_more else "false",
            "X-Text-Encoding": selected.encoding,
        },
    )


@router.get("/artifacts/{artifact_id}/search")
def search_artifact_content(
    artifact_id: str,
    db: Db,
    path: str = Query(...),
    query: str = Query(..., min_length=1, max_length=256),
    start_line: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    meta = json_loads(artifact.metadata_json, {})
    root_text = meta.get("extract_root")
    if not root_text:
        raise HTTPException(409, "Artifact has not been parsed")
    try:
        root = storage.resolve_path(root_text)
    except ValueError as exc:
        raise HTTPException(400, "Unsafe stored path") from exc
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "Unsafe path")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    manifest_item = next(
        (item for item in meta.get("manifest", []) if item.get("path", "").replace("\\", "/") == path.replace("\\", "/")),
        {},
    )
    result = search_text_lines(
        target,
        query,
        start_line=start_line,
        max_matches=limit,
        max_scan_lines=get_settings().text_search_max_scan_lines,
        line_index=manifest_item.get("line_index"),
        encoding_hint=manifest_item.get("encoding"),
    )
    if result is None:
        raise HTTPException(415, "File is binary or exceeds the text parsing limit")
    return {
        "query": query,
        "path": path,
        "encoding": result.encoding,
        "scanned_from_line": result.scanned_from_line,
        "scanned_to_line": result.scanned_to_line,
        "has_more": result.has_more,
        "next_start_line": result.scanned_to_line + 1 if result.has_more else None,
        "matches": [
            {"line_number": match.line_number, "text": match.text}
            for match in result.matches
        ],
    }


@router.post("/cases/{case_id}/analyses", response_model=JobOut)
def analyze_case(case_id: str, db: Db) -> Job:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    return job_runner.submit(db, "analyze_case", analyze_case_job, case_id, input_data={"case_id": case_id})


@router.get("/cases/{case_id}/analyses", response_model=list[AnalysisOut])
def list_analyses(case_id: str, db: Db) -> list[AnalysisRun]:
    return list(db.scalars(select(AnalysisRun).where(AnalysisRun.case_id == case_id).order_by(AnalysisRun.created_at.desc())).all())


@router.get("/analyses/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: str, db: Db) -> AnalysisRun:
    run = db.get(AnalysisRun, analysis_id)
    if not run:
        raise HTTPException(404, "Analysis not found")
    return run


@router.post("/cases/{case_id}/chat", response_model=ChatResponse)
async def case_chat(case_id: str, payload: ChatRequest, db: Db) -> ChatResponse:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    answer, citations = await chat_about_case(case_id, payload.question)
    return ChatResponse(answer=answer, citations=citations)


@router.get("/cases/{case_id}/analyses/{analysis_id}/report/preview", response_class=HTMLResponse)
def preview_report(case_id: str, analysis_id: str) -> str:
    try:
        return render_html(case_id, analysis_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/cases/{case_id}/analyses/{analysis_id}/reports/{fmt}")
def generate_report(case_id: str, analysis_id: str, fmt: str, db: Db) -> dict:
    run = db.get(AnalysisRun, analysis_id)
    if not run or run.case_id != case_id:
        raise HTTPException(404, "Analysis not found")
    generators = {"html": generate_html_file, "pdf": generate_pdf, "docx": generate_docx}
    if fmt not in generators:
        raise HTTPException(400, "Supported formats: html, pdf, docx")
    report = generators[fmt](case_id, analysis_id)
    return {"report_id": report.id, "format": report.format, "version": report.version, "sha256": report.sha256}


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, db: Db) -> FileResponse:
    report = db.get(Report, report_id)
    report_path = storage.resolve_path(report.stored_path) if report else None
    if not report or not report_path or not report_path.is_file():
        raise HTTPException(404, "Report not found")
    media = {"html": "text/html", "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    return FileResponse(report_path, media_type=media.get(report.format, "application/octet-stream"), filename=report_path.name)


def _knowledge_to_dict(
    document: KnowledgeDocument,
    category_map: dict[str, tuple[str, str]],
    chunk_counts: dict[str, int],
    *,
    include_content: bool = False,
) -> dict:
    category = category_map.get(document.id)
    result = {
        "id": document.id,
        "title": document.title,
        "source_type": document.source_type,
        "device_type": document.device_type,
        "device_model": document.device_model,
        "firmware_range": document.firmware_range,
        "module": document.module,
        "trust_level": document.trust_level,
        "confidentiality": document.confidentiality,
        "active": document.active,
        "category_id": category[0] if category else None,
        "category_name": category[1] if category else None,
        "chunk_count": chunk_counts.get(document.id, 0),
        "metadata": json_loads(document.metadata_json, {}),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if include_content:
        result["content"] = document.content
    return result


def _knowledge_response_maps(
    db: Session,
    document_ids: list[str],
) -> tuple[dict[str, tuple[str, str]], dict[str, int]]:
    if not document_ids:
        return {}, {}
    category_rows = db.execute(
        select(KnowledgeDocumentCategory.document_id, KnowledgeCategory.id, KnowledgeCategory.name)
        .join(KnowledgeCategory, KnowledgeDocumentCategory.category_id == KnowledgeCategory.id)
        .where(KnowledgeDocumentCategory.document_id.in_(document_ids))
    ).all()
    chunk_rows = db.execute(
        select(KnowledgeChunk.document_id, func.count(KnowledgeChunk.id))
        .where(KnowledgeChunk.document_id.in_(document_ids))
        .group_by(KnowledgeChunk.document_id)
    ).all()
    return (
        {document_id: (category_id, name) for document_id, category_id, name in category_rows},
        {document_id: int(count) for document_id, count in chunk_rows},
    )


def _single_knowledge_response(db: Session, document: KnowledgeDocument, *, detail: bool = False) -> dict:
    categories, chunks = _knowledge_response_maps(db, [document.id])
    return _knowledge_to_dict(document, categories, chunks, include_content=detail)


@router.post("/knowledge", response_model=KnowledgeDetailOut)
def create_knowledge(payload: KnowledgeCreate, db: Db) -> dict:
    document = KnowledgeDocument(
        id=new_id("DOC"), title=payload.title, source_type=payload.source_type,
        device_type=payload.device_type, device_model=payload.device_model,
        firmware_range=payload.firmware_range, module=payload.module,
        trust_level=payload.trust_level, confidentiality=payload.confidentiality,
        content=payload.content, metadata_json=json_dumps(payload.metadata),
    )
    db.add(document)
    db.flush()
    category_id = payload.category_id or get_default_category_id(db, payload.source_type)
    set_document_category(db, document.id, category_id)
    index_document(db, document)
    db.refresh(document)
    return _single_knowledge_response(db, document, detail=True)


@router.post("/knowledge/upload", response_model=KnowledgeDetailOut)
async def upload_knowledge(
    db: Db,
    file: UploadFile = File(...),
    source_type: str = Form(default="document"),
    device_type: str | None = Form(default=None),
    module: str | None = Form(default=None),
    trust_level: str = Form(default="MEDIUM"),
    category_id: str | None = Form(default=None),
) -> dict:
    raw = await file.read(get_settings().max_single_file_bytes + 1)
    if len(raw) > get_settings().max_single_file_bytes:
        raise HTTPException(413, "Knowledge file is too large")
    content = raw.decode("utf-8", errors="replace")
    document = KnowledgeDocument(
        id=new_id("DOC"), title=file.filename or "Knowledge document", source_type=source_type,
        device_type=device_type, module=module, trust_level=trust_level,
        content=content, metadata_json=json_dumps({"original_name": file.filename}),
    )
    db.add(document)
    db.flush()
    set_document_category(db, document.id, category_id or get_default_category_id(db, source_type))
    index_document(db, document)
    db.refresh(document)
    return _single_knowledge_response(db, document, detail=True)


@router.get("/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(
    db: Db,
    limit: int = Query(default=200, ge=1, le=1000),
    category_id: str | None = None,
    include_descendants: bool = True,
    source_type: str | None = None,
    search: str | None = None,
) -> list[dict]:
    query = select(KnowledgeDocument)
    if category_id:
        category_ids = (
            descendant_category_ids(db, category_id) if include_descendants else {category_id}
        )
        query = query.join(
            KnowledgeDocumentCategory,
            KnowledgeDocumentCategory.document_id == KnowledgeDocument.id,
        ).where(KnowledgeDocumentCategory.category_id.in_(category_ids))
    if source_type:
        query = query.where(KnowledgeDocument.source_type == source_type)
    if search:
        query = query.where(
            KnowledgeDocument.title.ilike(f"%{search}%")
            | KnowledgeDocument.content.ilike(f"%{search}%")
        )
    documents = list(db.scalars(
        query.order_by(KnowledgeDocument.updated_at.desc()).limit(limit)
    ).all())
    categories, chunks = _knowledge_response_maps(db, [document.id for document in documents])
    return [_knowledge_to_dict(document, categories, chunks) for document in documents]


@router.get("/knowledge/categories", response_model=list[KnowledgeCategoryOut])
def list_knowledge_categories(db: Db) -> list[dict]:
    counts = category_document_counts(db)
    categories = list(db.scalars(
        select(KnowledgeCategory).order_by(KnowledgeCategory.sort_order, KnowledgeCategory.name)
    ).all())
    return [
        {
            "id": category.id,
            "name": category.name,
            "code": category.code,
            "parent_id": category.parent_id,
            "description": category.description,
            "sort_order": category.sort_order,
            "system": category.system,
            "active": category.active,
            "document_count": counts.get(category.id, 0),
            "created_at": category.created_at,
            "updated_at": category.updated_at,
        }
        for category in categories
    ]


@router.post("/knowledge/categories", response_model=KnowledgeCategoryOut)
def create_knowledge_category(payload: KnowledgeCategoryCreate, db: Db) -> dict:
    category_id = new_category_id()
    category = KnowledgeCategory(
        id=category_id,
        name=payload.name,
        code=new_category_code(category_id),
        parent_id=payload.parent_id,
        description=payload.description,
        sort_order=payload.sort_order,
        system=False,
    )
    db.add(category)
    db.flush()
    try:
        validate_category_parent(db, category, payload.parent_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    db.refresh(category)
    return {**KnowledgeCategoryOut.model_validate(category).model_dump(), "document_count": 0}


@router.patch("/knowledge/categories/{category_id}", response_model=KnowledgeCategoryOut)
def update_knowledge_category(
    category_id: str,
    payload: KnowledgeCategoryUpdate,
    db: Db,
) -> dict:
    category = db.get(KnowledgeCategory, category_id)
    if not category:
        raise HTTPException(404, "Knowledge category not found")
    values = payload.model_dump(exclude_unset=True)
    if "parent_id" in values:
        try:
            validate_category_parent(db, category, values["parent_id"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    for key, value in values.items():
        setattr(category, key, value)
    db.commit()
    db.refresh(category)
    count = category_document_counts(db).get(category.id, 0)
    return {**KnowledgeCategoryOut.model_validate(category).model_dump(), "document_count": count}


@router.delete("/knowledge/categories/{category_id}")
def delete_knowledge_category(category_id: str, db: Db) -> dict:
    category = db.get(KnowledgeCategory, category_id)
    if not category:
        raise HTTPException(404, "Knowledge category not found")
    if category.system:
        raise HTTPException(409, "Built-in categories cannot be deleted")
    has_children = db.scalar(
        select(KnowledgeCategory.id).where(KnowledgeCategory.parent_id == category_id).limit(1)
    )
    has_documents = db.scalar(
        select(KnowledgeDocumentCategory.document_id)
        .where(KnowledgeDocumentCategory.category_id == category_id)
        .limit(1)
    )
    if has_children or has_documents:
        raise HTTPException(409, "Move child categories and documents before deleting this category")
    db.delete(category)
    db.commit()
    return {"deleted": category_id}


@router.post("/knowledge/reindex", response_model=JobOut)
def reindex_knowledge(db: Db) -> Job:
    profile = get_active_model_profile("embedding", db)
    if not profile:
        raise HTTPException(409, "No active embedding model")
    return job_runner.submit(
        db,
        "reindex_knowledge",
        reindex_knowledge_job,
        profile.id,
        input_data={"profile_id": profile.id},
    )


@router.get("/knowledge/{document_id}", response_model=KnowledgeDetailOut)
def get_knowledge(document_id: str, db: Db) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    return _single_knowledge_response(db, document, detail=True)


@router.patch("/knowledge/{document_id}", response_model=KnowledgeDetailOut)
def update_knowledge(document_id: str, payload: KnowledgeUpdate, db: Db) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    values = payload.model_dump(exclude_unset=True)
    category_was_set = "category_id" in values
    category_id = values.pop("category_id", None)
    metadata = values.pop("metadata", None)
    for key, value in values.items():
        setattr(document, key, value)
    if metadata is not None:
        document.metadata_json = json_dumps(metadata)
    if category_was_set:
        try:
            set_document_category(db, document.id, category_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    index_document(db, document)
    db.refresh(document)
    return _single_knowledge_response(db, document, detail=True)


@router.delete("/knowledge/{document_id}")
def delete_knowledge(document_id: str, db: Db) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    chunk_ids = select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document_id)
    db.execute(delete(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id.in_(chunk_ids)))
    link = db.get(KnowledgeDocumentCategory, document_id)
    if link:
        db.delete(link)
    db.delete(document)
    db.commit()
    return {"deleted": document_id}


@router.post("/cases/{case_id}/repositories", response_model=dict)
async def upload_repository(case_id: str, db: Db, file: UploadFile = File(...)) -> dict:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    artifact_id = new_id("ART")
    repository_id = new_id("REPO")
    uploaded_name = Path((file.filename or "repository.zip").replace("\\", "/")).name
    try:
        path, size, digest = await storage.save_upload(file, artifact_id, target_name=uploaded_name)
        destination = storage.repository_dir(repository_id)
        manifest = extract_archive(path, destination)
    except (ValueError, UnsafeArchiveError) as exc:
        storage.remove_artifact(artifact_id)
        storage.remove_repository(repository_id)
        raise HTTPException(400, str(exc)) from exc
    artifact = Artifact(
        id=artifact_id, case_id=case_id, kind="source_repository", original_name=uploaded_name[:512],
        stored_path=storage.storage_key(path), sha256=digest, size_bytes=size, status="EXTRACTED",
        metadata_json=json_dumps({"manifest_file_count": len(manifest.files), "extracted_bytes": manifest.total_bytes}),
    )
    repository = Repository(
        id=repository_id, case_id=case_id, artifact_id=artifact_id,
        name=Path(uploaded_name).stem[:255] or "repository",
        root_path=storage.storage_key(destination),
        status="UPLOADED",
    )
    db.add(artifact)
    db.flush()
    db.add(repository)
    db.commit()
    return {"repository_id": repository_id, "artifact_id": artifact_id, "files": len(manifest.files)}


@router.get("/cases/{case_id}/repositories")
def list_repositories(case_id: str, db: Db) -> list[dict]:
    rows = db.scalars(select(Repository).where(Repository.case_id == case_id).order_by(Repository.created_at.desc())).all()
    return [{"id": row.id, "name": row.name, "status": row.status, "branch": row.branch, "commit_hash": row.commit_hash, "created_at": row.created_at} for row in rows]


@router.post("/repositories/{repository_id}/index", response_model=JobOut)
def index_repository(repository_id: str, db: Db) -> Job:
    if not db.get(Repository, repository_id):
        raise HTTPException(404, "Repository not found")
    return job_runner.submit(db, "index_repository", index_repository_job, repository_id, input_data={"repository_id": repository_id})


@router.get("/repositories/{repository_id}/symbols")
def list_symbols(
    repository_id: str,
    db: Db,
    search: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=300, ge=1, le=2000),
) -> list[dict]:
    query = select(CodeSymbol).where(CodeSymbol.repository_id == repository_id)
    if search:
        query = query.where((CodeSymbol.name.ilike(f"%{search}%")) | (CodeSymbol.file_path.ilike(f"%{search}%")))
    if kind:
        query = query.where(CodeSymbol.kind == kind)
    rows = db.scalars(query.order_by(CodeSymbol.file_path, CodeSymbol.line_start).limit(limit)).all()
    return [
        {
            "id": row.id, "kind": row.kind, "name": row.name, "file_path": row.file_path,
            "line_start": row.line_start, "line_end": row.line_end, "signature": row.signature,
            "module": row.module, "calls": json_loads(row.calls_json, []), "code": row.code,
        }
        for row in rows
    ]


@router.post("/repositories/{repository_id}/static-analysis", response_model=JobOut)
def run_static_analysis(repository_id: str, payload: StaticAnalysisRequest, db: Db) -> Job:
    if not db.get(Repository, repository_id):
        raise HTTPException(404, "Repository not found")
    return job_runner.submit(
        db, "static_analysis", static_analysis_job, repository_id, payload.tools,
        input_data={"repository_id": repository_id, "tools": payload.tools},
    )


@router.post("/cases/{case_id}/patch-suggestions")
async def patch_suggestion(case_id: str, payload: PatchRequest, db: Db) -> dict:
    case = db.get(Case, case_id)
    symbol = db.get(CodeSymbol, payload.symbol_id)
    repository = db.get(Repository, symbol.repository_id) if symbol else None
    if not case or not symbol or not repository or repository.case_id != case_id:
        raise HTTPException(404, "Case or symbol not found")
    latest = db.scalars(
        select(AnalysisRun).where(AnalysisRun.case_id == case_id, AnalysisRun.status == "COMPLETED")
        .order_by(AnalysisRun.created_at.desc()).limit(1)
    ).first()
    diagnosis = json_loads(latest.result_json, {}) if latest else {}
    provider = get_llm_provider()
    if provider.is_mock:
        return {
            "status": "NEED_LLM_CONFIGURATION",
            "message": "配置 Qwen/GLM API 后可生成候选 unified diff。当前仅返回人工审查模板。",
            "symbol": {"file": symbol.file_path, "name": symbol.name, "line_start": symbol.line_start},
            "review_checklist": ["确认日志证据与该函数存在数据流或调用关系", "采用最小修改", "重新编译并运行相关测试", "不得直接覆盖原文件"],
        }
    prompt = {
        "instruction": payload.instruction,
        "case": {"title": case.title, "description": case.description, "device": case.device_type},
        "diagnosis": diagnosis,
        "symbol": {"file_path": symbol.file_path, "line_start": symbol.line_start, "line_end": symbol.line_end, "code": symbol.code},
        "output": "只输出 unified diff；不得修改无关文件；不得调用不存在的 API；无法安全修复时说明 NEED_HUMAN_REVIEW。",
    }
    text = await provider.generate_text(
        "你是 C/C++ 网络设备代码审查工程师，生成最小、可审查、未自动应用的候选补丁。",
        json_dumps(prompt),
        purpose="patch_suggestion",
    )
    return {"status": "SUGGESTED", "patch": text, "auto_applied": False}


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Db) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, db: Db) -> Job:
    try:
        return job_runner.request_cancel(db, job_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Job not found" else 409
        raise HTTPException(status_code, str(exc)) from exc


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, db: Db) -> Job:
    try:
        return job_runner.retry(db, job_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Job not found" else 409
        raise HTTPException(status_code, str(exc)) from exc


@router.get("/system/model")
def model_config(db: Db) -> dict:
    profile = get_active_model_profile("chat", db)
    info = get_active_chat_model_info()
    return {
        "profile_id": info["profile_id"],
        "profile_name": info["profile_name"],
        "provider": info["provider"],
        "base_url_configured": bool(profile and profile.base_url),
        "api_key_configured": bool(profile and profile.api_key_ciphertext),
        "model": info["model"],
        "compatible_providers": ["Qwen Model Studio OpenAI-compatible API", "GLM OpenAI-compatible API", "internal OpenAI-compatible gateway"],
    }


@router.get("/system/audit")
def list_audit_events(
    db: Db,
    action: str | None = None,
    outcome: str | None = None,
    case_id: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[dict]:
    query = select(AuditEvent)
    if action:
        query = query.where(AuditEvent.action == action)
    if outcome:
        query = query.where(AuditEvent.outcome == outcome.upper())
    if case_id:
        query = query.where(AuditEvent.case_id == case_id)
    rows = db.scalars(query.order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [
        {
            "id": row.id,
            "actor_id": row.actor_id,
            "actor_type": row.actor_type,
            "action": row.action,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "case_id": row.case_id,
            "outcome": row.outcome,
            "ip_address": row.ip_address,
            "details": json_loads(row.details_json, {}),
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/system/model/test")
async def test_model(db: Db) -> dict:
    profile = get_active_model_profile("chat", db)
    if not profile:
        raise HTTPException(409, "No active chat model")
    return await test_model_profile(profile.id, db)


@router.get("/system/models", response_model=list[ModelProfileOut])
def list_model_profiles(
    db: Db,
    task_type: str | None = Query(default=None, pattern="^(chat|embedding|reranker)$"),
) -> list[dict]:
    query = select(ModelProfile)
    if task_type:
        query = query.where(ModelProfile.task_type == task_type)
    profiles = list(db.scalars(
        query.order_by(ModelProfile.task_type, ModelProfile.is_active.desc(), ModelProfile.name)
    ).all())
    return [model_profile_to_dict(profile) for profile in profiles]


@router.post("/system/models", response_model=ModelProfileOut)
def create_model_profile(payload: ModelProfileCreate, db: Db) -> dict:
    try:
        validate_model_profile(
            payload.task_type,
            payload.mode,
            payload.provider,
            payload.model_name,
            payload.base_url,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    profile = ModelProfile(
        id=new_model_profile_id(),
        name=payload.name,
        task_type=payload.task_type,
        mode=payload.mode,
        provider=payload.provider,
        model_name=payload.model_name.strip(),
        base_url=(payload.base_url or "").strip() or None,
        config_json=json_dumps(payload.config),
        enabled=payload.enabled,
        is_active=False,
    )
    set_profile_api_key(profile, payload.api_key)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return model_profile_to_dict(profile)


@router.patch("/system/models/{profile_id}", response_model=ModelProfileOut)
def update_model_profile(profile_id: str, payload: ModelProfileUpdate, db: Db) -> dict:
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Model profile not found")
    values = payload.model_dump(exclude_unset=True)
    api_key = values.pop("api_key", None)
    clear_api_key = bool(values.pop("clear_api_key", False))
    config = values.pop("config", None)
    if profile.is_active and values.get("enabled") is False:
        raise HTTPException(409, "Activate another profile before disabling this one")
    before_signature = (profile.mode, profile.provider, profile.model_name, profile.base_url, profile.config_json)
    for key, value in values.items():
        setattr(profile, key, value.strip() if isinstance(value, str) else value)
    if config is not None:
        profile.config_json = json_dumps(config)
    if clear_api_key:
        set_profile_api_key(profile, "")
    elif api_key is not None:
        set_profile_api_key(profile, api_key)
    if profile.is_active and profile.mode == "api" and not profile.api_key_ciphertext:
        raise HTTPException(409, "The active API profile must keep a configured API key")
    try:
        validate_model_profile(
            profile.task_type,
            profile.mode,
            profile.provider,
            profile.model_name,
            profile.base_url,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    after_signature = (profile.mode, profile.provider, profile.model_name, profile.base_url, profile.config_json)
    if profile.task_type == "embedding" and before_signature != after_signature:
        db.execute(delete(KnowledgeEmbedding).where(KnowledgeEmbedding.profile_id == profile.id))
    db.commit()
    db.refresh(profile)
    return model_profile_to_dict(profile)


@router.delete("/system/models/{profile_id}")
def delete_model_profile(profile_id: str, db: Db) -> dict:
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Model profile not found")
    if profile.is_active:
        raise HTTPException(409, "Activate another profile before deleting this one")
    if json_loads(profile.config_json, {}).get("builtin"):
        raise HTTPException(409, "Built-in model profiles cannot be deleted")
    db.execute(delete(KnowledgeEmbedding).where(KnowledgeEmbedding.profile_id == profile.id))
    db.delete(profile)
    db.commit()
    return {"deleted": profile_id}


@router.post("/system/models/{profile_id}/activate")
def activate_selected_model(profile_id: str, db: Db) -> dict:
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Model profile not found")
    try:
        activate_model_profile(db, profile)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.refresh(profile)
    return {
        "profile": model_profile_to_dict(profile),
        "requires_reindex": profile.task_type == "embedding",
    }


@router.post("/system/models/{profile_id}/test")
async def test_model_profile(profile_id: str, db: Db) -> dict:
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Model profile not found")
    try:
        if profile.task_type == "chat":
            provider = get_llm_provider(profile)
            text = await provider.generate_text(
                "你是连接测试助手。",
                "仅回复 MODEL_CONNECTION_OK",
                purpose="connection_test",
            )
            return {
                "ok": provider.is_mock or "MODEL_CONNECTION_OK" in text,
                "response": text[:500],
                "model": provider.model_name,
            }
        if profile.task_type == "embedding":
            vectors = embed_texts(
                profile,
                ["GW 无法上线", "AP 认证失败"],
                purpose="connection_test",
            )
            dimension = len(vectors[0]) if vectors else 0
            return {"ok": bool(dimension), "dimension": dimension, "vectors": len(vectors)}
        ranking = rerank_documents(
            "AP 认证失败如何排查",
            ["检查 EAP 和四次握手日志", "查询设备外壳颜色"],
            2,
            profile,
            purpose="connection_test",
        )
        return {"ok": ranking is None or bool(ranking), "ranking": ranking or [], "disabled": ranking is None}
    except (LLMError, RetrievalModelError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Model connection failed: {exc}") from exc


@router.get("/system/retrieval")
def retrieval_config(db: Db) -> dict:
    reranker = get_active_model_profile("reranker", db)
    return {
        "embedding": embedding_index_status(db),
        "reranker": model_profile_to_dict(reranker) if reranker else None,
        "knowledge_storage": "SQLite documents/chunks + profile-specific SQLite vector cache",
        "knowledge_graph": False,
    }
