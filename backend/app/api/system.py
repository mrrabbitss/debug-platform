from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (
    AccessToken,
    AgentMemory,
    AuditEvent,
    CodeRelation,
    CodeSymbol,
    CommitRecord,
    KnowledgeDerivation,
    KnowledgeEmbedding,
    ModelProfile,
    Repository,
    UserAccount,
)
from app.schemas import (
    AccessTokenCreate,
    ModelProfileCreate,
    ModelProfileOut,
    ModelProfileUpdate,
    UserCreate,
    UserUpdate,
)
from app.services.access_control import issue_access_token
from app.services.audit import record_audit_event
from app.services.health import readiness_report
from app.services.knowledge_graph import domain_graph_status
from app.services.model_access import (
    ModelAccessError, can_manage_model, change_model_visibility, create_model_access, model_profile_payload,
    require_model_identity, require_model_profile, require_shared_model_default,
    resolve_user_chat_profile, visible_model_clause,
)
from app.services.model_transport import safe_model_connection_error
from app.services.model_profiles import (
    COMPATIBLE_CHAT_PROVIDERS,
    MANAGED_LOCAL_PROVIDER,
    activate_model_profile,
    get_active_model_profile,
    get_profile_proxy_url,
    model_profile_to_dict,
    new_model_profile_id,
    profile_uses_proxy,
    set_profile_api_key,
    set_profile_proxy_url,
    validate_model_profile,
    test_profile_connection,
)
from app.services.retrieval_models import embedding_index_status


router = APIRouter(tags=["system"])
Db = Annotated[Session, Depends(get_db)]


@router.get("/health")
def health() -> dict:
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
        "simple_engineer_login": settings.simple_engineer_login and settings.deployment_mode == "lan_server",
        "token_header": "X-API-Key",
        "legacy_admin_enabled": bool(
            settings.api_key and settings.auth_allow_legacy_admin
        ),
    }


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
        details={
            "username": user.username,
            "role": user.role,
            "token_issued": bool(raw_token),
        },
    )
    return {
        "user": _user_payload(user),
        "token": token_payload,
        "raw_token": raw_token,
    }


@router.patch("/system/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request, db: Db) -> dict:
    user = db.get(UserAccount, user_id)
    if not user:
        raise HTTPException(404, "User not found")
    changes = payload.model_dump(exclude_unset=True)
    removing_admin = user.role == "ADMIN" and (
        changes.get("role", user.role) != "ADMIN"
        or changes.get("active", user.active) is False
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
        select(AccessToken)
        .where(AccessToken.user_id == user_id)
        .order_by(AccessToken.created_at.desc())
    ).all()
    return [_token_payload(token) for token in tokens]


@router.post("/system/users/{user_id}/tokens")
def create_user_token(
    user_id: str,
    payload: AccessTokenCreate,
    request: Request,
    db: Db,
) -> dict:
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
        details={
            "user_id": user.id,
            "name": token.name,
            "expires_at": token.expires_at,
        },
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


@router.get("/system/model")
def model_config(request: Request, db: Db) -> dict:
    profile = _user_chat_profile(request, db)
    info = model_profile_payload(db, request.state.principal, profile)
    proxy_enabled = profile_uses_proxy(profile)
    return {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "provider": info["provider"],
        "base_url_configured": bool(profile and profile.base_url),
        "api_key_configured": bool(profile and profile.api_key_ciphertext),
        "proxy_url_configured": proxy_enabled,
        "certificate_revocation_check_skipped": proxy_enabled,
        "model": profile.model_name,
        "owner_id": info["owner_id"],
        "visibility": info["visibility"],
        "can_manage": info["can_manage"],
        "compatible_providers": list(COMPATIBLE_CHAT_PROVIDERS),
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
    rows = db.scalars(
        query.order_by(AuditEvent.created_at.desc()).limit(limit)
    ).all()
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
async def test_active_model(request: Request, db: Db) -> dict:
    profile = _user_chat_profile(request, db)
    return await test_model_profile(profile.id, request, db)


def _user_chat_profile(request: Request, db: Session) -> ModelProfile:
    try:
        return resolve_user_chat_profile(db, getattr(request.state, "principal", {}))
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _visible_profile(request: Request, db: Session, profile_id: str, *, manage=False) -> ModelProfile:
    try:
        return require_model_profile(db, getattr(request.state, "principal", {}), profile_id, manage=manage)
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@router.get("/system/models", response_model=list[ModelProfileOut])
def list_model_profiles(
    request: Request,
    db: Db,
    task_type: str | None = Query(
        default=None,
        pattern="^(chat|embedding|reranker)$",
    ),
) -> list[dict]:
    try:
        query = select(ModelProfile).where(visible_model_clause(getattr(request.state, "principal", {})))
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if task_type:
        query = query.where(ModelProfile.task_type == task_type)
    profiles = list(db.scalars(
        query.order_by(
            ModelProfile.task_type,
            ModelProfile.is_active.desc(),
            ModelProfile.name,
        )
    ).all())
    return [model_profile_payload(db, request.state.principal, profile) for profile in profiles]


@router.get("/system/models/{profile_id}", response_model=ModelProfileOut)
def read_model_profile(profile_id: str, request: Request, db: Db) -> dict:
    return model_profile_payload(db, request.state.principal, _visible_profile(request, db, profile_id))


@router.post("/system/models", response_model=ModelProfileOut)
def create_model_profile(payload: ModelProfileCreate, request: Request, db: Db) -> dict:
    try:
        identity = require_model_identity(getattr(request.state, "principal", {}))
        validate_model_profile(
            payload.task_type,
            payload.mode,
            payload.provider,
            payload.model_name,
            payload.base_url,
            payload.proxy_url,
        )
    except ValueError as exc:
        raise HTTPException(getattr(exc, "status_code", 400), str(exc)) from exc
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
    try:
        create_model_access(db, identity, profile, payload.visibility)
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    set_profile_api_key(profile, payload.api_key)
    try:
        set_profile_proxy_url(profile, payload.proxy_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.add(profile)
    db.flush([profile])
    db.commit()
    db.refresh(profile)
    return model_profile_payload(db, identity, profile)


@router.patch("/system/models/{profile_id}", response_model=ModelProfileOut)
def update_model_profile(
    profile_id: str,
    payload: ModelProfileUpdate,
    request: Request,
    db: Db,
) -> dict:
    profile = _visible_profile(request, db, profile_id, manage=True)
    values = payload.model_dump(exclude_unset=True)
    try:
        change_model_visibility(db, request.state.principal, profile, values.pop("visibility", None))
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    if any(values.get(key, "present") is None for key in ("name", "mode", "provider", "model_name", "enabled")):
        raise HTTPException(422, "Model name, mode, provider and enabled state cannot be null")
    if profile.task_type == "chat" and request.state.principal["role"] not in {"ADMIN", "EXPERT"}:
        if values.get("mode", profile.mode) != "api" or values.get("provider", profile.provider) != "openai_compatible":
            raise HTTPException(422, "Personal Chat profiles must use an OpenAI-compatible API")
    if profile.provider == MANAGED_LOCAL_PROVIDER or values.get("provider") == MANAGED_LOCAL_PROVIDER:
        raise HTTPException(409, "Bundled llama.cpp profiles are managed by the launcher")
    api_key = values.pop("api_key", None)
    clear_api_key = bool(values.pop("clear_api_key", False))
    proxy_url_supplied = "proxy_url" in values
    proxy_url = values.pop("proxy_url", None)
    clear_proxy_url = bool(values.pop("clear_proxy_url", False))
    config = values.pop("config", None)
    if clear_proxy_url and proxy_url:
        raise HTTPException(400, "Cannot set and clear the model proxy in one request")
    if profile.is_active and values.get("enabled") is False:
        raise HTTPException(409, "Activate another profile before disabling this one")
    before_signature = (
        profile.mode,
        profile.provider,
        profile.model_name,
        profile.base_url,
        profile.config_json,
    )
    for key, value in values.items():
        setattr(profile, key, value.strip() if isinstance(value, str) else value)
    if config is not None:
        profile.config_json = json_dumps(config)
    if clear_api_key:
        set_profile_api_key(profile, "")
    elif api_key is not None:
        set_profile_api_key(profile, api_key)
    try:
        if clear_proxy_url:
            set_profile_proxy_url(profile, "")
        elif proxy_url_supplied:
            set_profile_proxy_url(profile, proxy_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if profile.is_active and profile.mode == "api" and not profile.api_key_ciphertext:
        raise HTTPException(409, "The active API profile must keep a configured API key")
    try:
        validate_model_profile(
            profile.task_type,
            profile.mode,
            profile.provider,
            profile.model_name,
            profile.base_url,
            get_profile_proxy_url(profile) if profile.proxy_url_ciphertext else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    after_signature = (
        profile.mode,
        profile.provider,
        profile.model_name,
        profile.base_url,
        profile.config_json,
    )
    if profile.task_type == "embedding" and before_signature != after_signature:
        db.execute(delete(KnowledgeEmbedding).where(
            KnowledgeEmbedding.profile_id == profile.id
        ))
        profile.active_embedding_generation_id = None
    db.commit()
    db.refresh(profile)
    return model_profile_payload(db, request.state.principal, profile)


@router.delete("/system/models/{profile_id}")
def delete_model_profile(profile_id: str, request: Request, db: Db) -> dict:
    profile = _visible_profile(request, db, profile_id, manage=True)
    if profile.is_active:
        raise HTTPException(409, "Activate another profile before deleting this one")
    if profile.provider == MANAGED_LOCAL_PROVIDER or json_loads(profile.config_json, {}).get("builtin"):
        raise HTTPException(409, "Built-in model profiles cannot be deleted")
    db.execute(delete(KnowledgeEmbedding).where(
        KnowledgeEmbedding.profile_id == profile.id
    ))
    db.delete(profile)
    db.commit()
    return {"deleted": profile_id}


@router.post("/system/models/{profile_id}/activate")
def activate_selected_model(profile_id: str, request: Request, db: Db) -> dict:
    profile = _visible_profile(request, db, profile_id, manage=True)
    try:
        require_shared_model_default(db, request.state.principal, profile)
        activate_model_profile(db, profile)
    except ValueError as exc:
        raise HTTPException(getattr(exc, "status_code", 400), str(exc)) from exc
    db.refresh(profile)
    return {
        "profile": model_profile_payload(db, request.state.principal, profile),
        "requires_reindex": profile.task_type == "embedding",
    }


@router.post("/system/models/{profile_id}/test")
async def test_model_profile(profile_id: str, request: Request, db: Db) -> dict:
    profile = _visible_profile(request, db, profile_id)
    if not profile.enabled and not can_manage_model(db, request.state.principal, profile):
        raise HTTPException(409, "This shared model is disabled")
    if profile.task_type != "chat" and request.state.principal["role"] != "ADMIN":
        raise HTTPException(403, "Only administrators may test global retrieval configuration")
    try:
        return await test_profile_connection(profile)
    except Exception as exc:
        raise HTTPException(502, safe_model_connection_error(exc, proxy_configured=profile_uses_proxy(profile))) from exc


@router.get("/system/retrieval")
def retrieval_config(db: Db) -> dict:
    reranker = get_active_model_profile("reranker", db)
    domain_graph = domain_graph_status(db)
    return {
        "embedding": embedding_index_status(db),
        "reranker": model_profile_to_dict(reranker) if reranker else None,
        "knowledge_storage": (
            f"{db.get_bind().dialect.name} documents/chunks + "
            "generation-scoped vector cache"
        ),
        "knowledge_graph": {
            "enabled": True,
            "kind": "derivation_lineage_and_domain_entities",
            "domain_entity_graph_enabled": True,
            "domain_graph": domain_graph,
            "derivations": int(db.scalar(
                select(func.count(KnowledgeDerivation.id))
            ) or 0),
        },
        "code_graph": {
            "symbols": int(db.scalar(
                select(func.count(CodeSymbol.id))
                .join(Repository, CodeSymbol.repository_id == Repository.id)
                .where(CodeSymbol.generation_id == Repository.active_graph_generation_id)
            ) or 0),
            "relations": int(db.scalar(
                select(func.count(CodeRelation.id))
                .join(Repository, CodeRelation.repository_id == Repository.id)
                .where(CodeRelation.generation_id == Repository.active_graph_generation_id)
            ) or 0),
        },
        "commit_graph": {
            "commits": int(db.scalar(select(func.count(CommitRecord.id))) or 0),
        },
        "memory": {
            "items": int(db.scalar(select(func.count(AgentMemory.id))) or 0),
            "types": ["EPISODIC", "PROCEDURAL", "FAILURE"],
        },
        "agentic_search": {
            "enabled": True,
            "modules": ["knowledge", "domain_graph", "code", "commit", "memory"],
            "algorithms": [
                "BM25",
                "dense_embedding",
                "reciprocal_rank_fusion",
                "reranker",
                "graph_multi_hop",
                "graphrag",
            ],
        },
    }
