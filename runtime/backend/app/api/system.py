from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

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
from app.services.health import readiness_report, system_status_report
from app.services.knowledge_graph import domain_graph_status
from app.services.llm import LLMError, get_active_chat_model_info, get_llm_provider
from app.services.model_profiles import (
    COMPATIBLE_CHAT_PROVIDERS,
    activate_model_profile,
    get_active_model_profile,
    get_profile_proxy_url,
    model_profile_to_dict,
    new_model_profile_id,
    profile_uses_proxy,
    set_profile_api_key,
    set_profile_proxy_url,
    validate_model_profile,
)
from app.services.retrieval_models import (
    RetrievalModelError,
    embed_texts,
    embedding_index_status,
    rerank_documents,
)


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
        "token_header": "X-API-Key",
        "legacy_admin_enabled": bool(
            settings.api_key and settings.auth_allow_legacy_admin
        ),
    }


@router.get("/system/diagnostic-method-runtime")
def diagnostic_method_runtime() -> dict:
    configured = os.environ.get("DIAGNOSTIC_METHODS_ROOT")
    return {
        "schema": "gw-ap-debug-diagnostic-method-runtime/v1",
        "case_binding_supported": True,
        "control_root": str(Path(configured).expanduser().resolve()) if configured else None,
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
def model_config(db: Db) -> dict:
    profile = get_active_model_profile("chat", db)
    info = get_active_chat_model_info()
    proxy_enabled = profile_uses_proxy(profile)
    return {
        "profile_id": info["profile_id"],
        "profile_name": info["profile_name"],
        "provider": info["provider"],
        "base_url_configured": bool(profile and profile.base_url),
        "api_key_configured": bool(profile and profile.api_key_ciphertext),
        "proxy_url_configured": proxy_enabled,
        "certificate_revocation_check_skipped": proxy_enabled,
        "model": info["model"],
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
async def test_active_model(db: Db) -> dict:
    profile = get_active_model_profile("chat", db)
    if not profile:
        raise HTTPException(409, "No active chat model")
    return await test_model_profile(profile.id, db)


@router.get("/system/models", response_model=list[ModelProfileOut])
def list_model_profiles(
    db: Db,
    task_type: str | None = Query(
        default=None,
        pattern="^(chat|embedding|reranker)$",
    ),
) -> list[dict]:
    query = select(ModelProfile)
    if task_type:
        query = query.where(ModelProfile.task_type == task_type)
    profiles = list(db.scalars(
        query.order_by(
            ModelProfile.task_type,
            ModelProfile.is_active.desc(),
            ModelProfile.name,
        )
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
            payload.proxy_url,
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
    try:
        set_profile_proxy_url(profile, payload.proxy_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return model_profile_to_dict(profile)


@router.patch("/system/models/{profile_id}", response_model=ModelProfileOut)
def update_model_profile(
    profile_id: str,
    payload: ModelProfileUpdate,
    db: Db,
) -> dict:
    profile = db.get(ModelProfile, profile_id)
    if not profile:
        raise HTTPException(404, "Model profile not found")
    values = payload.model_dump(exclude_unset=True)
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
    db.execute(delete(KnowledgeEmbedding).where(
        KnowledgeEmbedding.profile_id == profile.id
    ))
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
                "proxy_url_configured": getattr(provider, "proxy_configured", False),
            }
        if profile.task_type == "embedding":
            vectors = await run_in_threadpool(
                lambda: embed_texts(
                    profile,
                    ["GW 无法上线", "AP 认证失败"],
                    purpose="connection_test",
                )
            )
            dimension = len(vectors[0]) if vectors else 0
            return {
                "ok": bool(dimension),
                "dimension": dimension,
                "vectors": len(vectors),
            }
        ranking = await run_in_threadpool(
            lambda: rerank_documents(
                "AP 认证失败如何排查",
                ["检查 EAP 和四次握手日志", "查询设备外壳颜色"],
                2,
                profile,
                purpose="connection_test",
            )
        )
        return {
            "ok": ranking is None or bool(ranking),
            "ranking": ranking or [],
            "disabled": ranking is None,
        }
    except (LLMError, RetrievalModelError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Model connection failed: {exc}") from exc


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
