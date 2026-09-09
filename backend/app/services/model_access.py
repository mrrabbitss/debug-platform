"""Caller-scoped Chat selection and permissions, shared by HTTP and job consumers."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import and_, exists, or_, select, update
from sqlalchemy.orm import Session

from app.model_access_models import ModelProfileAccess
from app.models import ModelProfile, UserAccount
from app.workbench_models import WorkbenchRecord
from app.core.utils import json_loads

MANAGEMENT_ROLES = {"ADMIN", "EXPERT"}
CHAT_WRITE_ROLES = MANAGEMENT_ROLES | {"ENGINEER"}
AUTHENTICATED_ROLES = CHAT_WRITE_ROLES | {"VIEWER"}


class ModelAccessError(ValueError):
    def __init__(self, message: str, status_code: int = 403):
        super().__init__(message)
        self.status_code = status_code


def require_model_identity(principal: dict) -> dict:
    if (not principal.get("id") or principal.get("role") not in AUTHENTICATED_ROLES
            or principal.get("type") == "anonymous"):
        raise ModelAccessError("Valid authenticated model user required", 401)
    return principal


def principal_for_model_user(db: Session, user_id: str | None) -> dict:
    """Refresh a job initiator's account; saved roles do not grant enduring authority."""
    user = db.get(UserAccount, user_id, populate_existing=True) if user_id else None
    if user:
        if not user.active or user.role not in AUTHENTICATED_ROLES:
            raise ModelAccessError("The model request owner is no longer active")
        return {"id": user.id, "role": user.role, "type": "user_token"}
    # These are the actual identity IDs emitted by local/legacy authentication.
    # A deleted named account must never be replaced by a privileged identity.
    if user_id in {"local-development", "legacy-api-key"}:
        from app.core.config import get_settings
        settings = get_settings()
        if ((user_id == "local-development" and settings.auth_mode == "local") or
                (user_id == "legacy-api-key" and
                 (settings.auth_mode in {"local", "api_key"} or settings.auth_allow_legacy_admin))):
            return {"id": user_id, "role": "ADMIN", "type": "local"}
    raise ModelAccessError("The model request owner no longer exists")


def shared_model_clause():
    return exists().where(ModelProfileAccess.profile_id == ModelProfile.id,
                          ModelProfileAccess.visibility == "SHARED")


def visible_model_clause(principal: dict):
    require_model_identity(principal)
    return or_(ModelProfile.task_type != "chat", exists().where(
        ModelProfileAccess.profile_id == ModelProfile.id,
        or_(ModelProfileAccess.visibility == "SHARED", and_(
            ModelProfileAccess.visibility == "PRIVATE", ModelProfileAccess.owner_id == principal["id"]))),
    )


def model_access_record(db: Session, profile: ModelProfile) -> ModelProfileAccess | None:
    return db.get(ModelProfileAccess, profile.id, populate_existing=True)


def can_manage_model(db: Session, principal: dict, profile: ModelProfile) -> bool:
    if not principal.get("id"):
        return False
    if profile.task_type != "chat":
        return principal.get("role") == "ADMIN"
    access = model_access_record(db, profile)
    if not access:
        return False
    if access.visibility == "PRIVATE":
        return access.owner_id == principal["id"] and principal.get("role") in CHAT_WRITE_ROLES
    return access.visibility == "SHARED" and principal.get("role") in MANAGEMENT_ROLES


def require_model_profile(db: Session, principal: dict, profile_id: str, *,
                          manage: bool = False, require_enabled: bool = False) -> ModelProfile:
    require_model_identity(principal)
    profile = db.scalar(select(ModelProfile).where(ModelProfile.id == profile_id, visible_model_clause(principal))
                        .execution_options(populate_existing=True))
    if profile is None:
        # Unknown IDs and another user's private IDs are deliberately indistinguishable.
        raise ModelAccessError("Model profile not found", 404)
    if manage:
        # Serialize mutations on SQLite as well as PostgreSQL. A sharing change
        # and a concurrent activation must never create a private global default.
        if not can_manage_model(db, principal, profile):
            raise ModelAccessError("You cannot manage this model profile")
        lock_model_configuration(db, profile)
        profile = db.scalar(select(ModelProfile).where(ModelProfile.id == profile_id, visible_model_clause(principal))
                            .execution_options(populate_existing=True))
        if profile is None:
            raise ModelAccessError("Model profile not found", 404)
    if manage and not can_manage_model(db, principal, profile):
        raise ModelAccessError("You cannot manage this model profile")
    if require_enabled and not profile.enabled:
        raise ModelAccessError("Selected model is disabled; choose another model", 409)
    return profile


def lock_model_configuration(db: Session, profile: ModelProfile) -> None:
    locked = db.execute(update(ModelProfile).where(ModelProfile.id == profile.id)
        .values(updated_at=ModelProfile.updated_at).execution_options(synchronize_session=False))
    if locked.rowcount != 1:
        raise ModelAccessError("Model profile not found", 404)
    db.refresh(profile)


def create_model_access(db: Session, principal: dict, profile: ModelProfile,
                        visibility: str | None = None) -> ModelProfileAccess:
    require_model_identity(principal)
    role = principal["role"]
    if profile.task_type != "chat":
        if role != "ADMIN":
            raise ModelAccessError("Only administrators may configure Embedding or Reranker")
        if visibility not in {None, "SHARED"}:
            raise ModelAccessError("Embedding and Reranker are global shared configurations", 422)
        visibility = "SHARED"
    else:
        if role not in CHAT_WRITE_ROLES:
            raise ModelAccessError("This legacy read-only role cannot create model profiles")
        visibility = visibility or ("SHARED" if role in MANAGEMENT_ROLES else "PRIVATE")
        if visibility == "SHARED" and role not in MANAGEMENT_ROLES:
            raise ModelAccessError("Only administrators and experts may share Chat models")
        if role not in MANAGEMENT_ROLES and (profile.mode != "api" or profile.provider != "openai_compatible"):
            raise ModelAccessError("Personal Chat profiles must use an OpenAI-compatible API", 422)
    if visibility not in {"PRIVATE", "SHARED"}:
        raise ModelAccessError("Invalid model visibility", 422)
    access = ModelProfileAccess(profile_id=profile.id, owner_id=principal["id"], visibility=visibility)
    db.add(access)
    return access


def change_model_visibility(db: Session, principal: dict, profile: ModelProfile, visibility: str | None) -> None:
    if visibility is None:
        return
    if not can_manage_model(db, principal, profile):
        raise ModelAccessError("You cannot manage this model profile")
    if profile.task_type != "chat":
        if visibility != "SHARED":
            raise ModelAccessError("Embedding and Reranker are global shared configurations", 422)
        return
    access = model_access_record(db, profile)
    if visibility not in {"PRIVATE", "SHARED"}:
        raise ModelAccessError("Invalid model visibility", 422)
    if visibility == access.visibility:
        return
    if principal["role"] not in MANAGEMENT_ROLES:
        raise ModelAccessError("Only administrators and experts may change sharing")
    if profile.is_active and visibility == "PRIVATE":
        raise ModelAccessError("Choose another shared default before making this model private", 409)
    if visibility == "PRIVATE" and access.owner_id not in {None, principal["id"]}:
        raise ModelAccessError("Only the creator can make a shared model private")
    if access.owner_id is None:
        access.owner_id = principal["id"]
    access.visibility = visibility


def require_shared_model_default(db: Session, principal: dict, profile: ModelProfile) -> None:
    if not can_manage_model(db, principal, profile):
        raise ModelAccessError("You cannot change the shared model default")
    if profile.task_type == "chat":
        access = model_access_record(db, profile)
        if principal["role"] not in MANAGEMENT_ROLES or not access or access.visibility != "SHARED":
            raise ModelAccessError("The shared Chat default must be a shared profile", 409)


def resolve_user_chat_profile(db: Session, principal: dict, profile_id: str | None = None) -> ModelProfile:
    require_model_identity(principal)
    if profile_id is None:
        preferences = db.get(WorkbenchRecord, "pref-" + principal["id"])
        saved = json_loads(preferences.payload_json, {}) if preferences else {}
        if not isinstance(saved, dict):
            raise ModelAccessError("Model preference is invalid; select a model again", 409)
        profile_id = saved.get("chat_profile_id")
    if profile_id:
        profile = require_model_profile(db, principal, profile_id, require_enabled=True)
        if profile.task_type != "chat":
            raise ModelAccessError("Select a Chat profile for diagnosis", 422)
        return profile
    profile = db.scalar(select(ModelProfile).where(ModelProfile.task_type == "chat", shared_model_clause(),
                                                   ModelProfile.enabled.is_(True), ModelProfile.is_active.is_(True)))
    if not profile:
        raise ModelAccessError("No enabled shared Chat default; select or add a model", 409)
    return profile


def _safe_config(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if any(part in str(key).casefold() for part in
                ("api_key", "authorization", "password", "secret", "credential", "ciphertext", "proxy_url"))
                or str(key).casefold() in {"headers", "token", "access_token"} else _safe_config(item))
                for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_config(item) for item in value]
    return value


def model_profile_payload(db: Session, principal: dict, profile: ModelProfile) -> dict:
    from app.services.model_profiles import model_profile_to_dict
    require_model_profile(db, principal, profile.id)
    access = model_access_record(db, profile)
    result = model_profile_to_dict(profile)
    result.update(owner_id=access.owner_id if access else None,
                  visibility=access.visibility if access else "SHARED", can_manage=can_manage_model(db, principal, profile))
    result["config"] = _safe_config(result["config"])
    # Shared consumers may use the stored credential, but cannot see even its tail.
    if not access or access.owner_id != principal["id"]:
        result["api_key_hint"] = None
        result["proxy_url_hint"] = None
    return result


def model_profile_fingerprint(db: Session, profile: ModelProfile) -> str:
    access = model_access_record(db, profile)
    value = {"id": profile.id, "task": profile.task_type, "mode": profile.mode, "provider": profile.provider,
             "model": profile.model_name, "base_url": profile.base_url, "config": json_loads(profile.config_json, {}),
             "key": profile.api_key_ciphertext, "proxy": profile.proxy_url_ciphertext,
             "owner": access.owner_id if access else None, "visibility": access.visibility if access else None}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()


def chat_model_snapshot(db: Session, principal: dict, profile: ModelProfile) -> dict:
    require_model_profile(db, principal, profile.id, require_enabled=True)
    if profile.task_type != "chat":
        raise ModelAccessError("Select a Chat profile for diagnosis", 422)
    return {"selected_chat_profile_id": profile.id, "model_actor_id": principal["id"],
            "model_profile_fingerprint": model_profile_fingerprint(db, profile)}


def resolve_chat_model_snapshot(db: Session, snapshot: dict) -> ModelProfile:
    profile_id = snapshot.get("selected_chat_profile_id")
    if not profile_id:
        raise ModelAccessError("The saved Chat selection is missing; start a fresh request", 409)
    actor_id = snapshot.get("model_actor_id")
    if actor_id:
        principal = principal_for_model_user(db, actor_id)
        profile = require_model_profile(db, principal, profile_id, require_enabled=True)
    else:
        # A pre-0023 task has no authenticated model owner. It can only retain a
        # migrated shared selection, never acquire access to a new private API.
        profile = db.scalar(select(ModelProfile).where(ModelProfile.id == profile_id, shared_model_clause())
                            .execution_options(populate_existing=True))
        if not profile or not profile.enabled:
            raise ModelAccessError("The saved shared Chat selection is unavailable", 409)
    if profile.task_type != "chat":
        raise ModelAccessError("The saved selection is not a Chat model", 409)
    fingerprint = snapshot.get("model_profile_fingerprint")
    if actor_id and not fingerprint:
        raise ModelAccessError("The saved model configuration is incomplete", 409)
    if fingerprint and fingerprint != model_profile_fingerprint(db, profile):
        raise ModelAccessError("Model configuration or credentials changed; start a fresh request", 409)
    return profile
