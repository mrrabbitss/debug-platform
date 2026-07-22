from collections.abc import Iterable
import ipaddress
import socket
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id
from app.models import ModelProfile
from app.services.secrets import decrypt_secret, encrypt_secret, secret_hint


PROVIDERS_BY_TASK = {
    "chat": {"mock", "openai_compatible"},
    "embedding": {"hashing", "sentence_transformers", "openai_compatible"},
    "reranker": {"disabled", "sentence_transformers", "qwen_rerank_api"},
}

MODE_BY_PROVIDER = {
    "mock": "builtin",
    "hashing": "builtin",
    "disabled": "builtin",
    "sentence_transformers": "local",
    "openai_compatible": "api",
    "qwen_rerank_api": "api",
}

_ALWAYS_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data.ec2.internal",
}


def _host_is_allowlisted(host: str, entries: Iterable[str]) -> bool:
    normalized = host.rstrip(".").lower()
    for raw_entry in entries:
        entry = raw_entry.strip().rstrip(".").lower()
        if not entry:
            continue
        if entry.startswith("*."):
            entry = entry[1:]
        if entry.startswith("."):
            if normalized == entry[1:] or normalized.endswith(entry):
                return True
        elif normalized == entry:
            return True
    return False


def _resolved_addresses(host: str, port: int | None) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        return {
            ipaddress.ip_address(item[4][0].split("%", 1)[0])
            for item in socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError):
        # A model profile may be saved while DNS or VPN is unavailable. The same
        # validation runs again immediately before a request is sent.
        return set()


def _reject_unsafe_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    *,
    allowlisted: bool,
    allow_private: bool,
) -> None:
    if address.is_link_local or address.is_unspecified or address.is_multicast or address.is_reserved:
        raise ValueError("Model endpoint resolves to a blocked network address")
    if address.is_loopback and not allowlisted:
        raise ValueError("Loopback model endpoints must be explicitly allowlisted")
    if address.is_private and not (allowlisted or allow_private):
        raise ValueError(
            "Private-network model endpoints require MODEL_ENDPOINT_ALLOWLIST "
            "or MODEL_ALLOW_PRIVATE_ENDPOINTS=true"
        )


def validate_model_endpoint(base_url: str) -> None:
    """Reject unsafe model gateway URLs while allowing explicit intranet deployments."""
    value = base_url.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Model Base URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Model Base URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Model Base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Model Base URL must not contain a query string or fragment")

    settings = get_settings()
    host = parsed.hostname.rstrip(".").lower()
    allowlisted = _host_is_allowlisted(host, settings.model_endpoint_allowlist_entries)
    if host in _ALWAYS_BLOCKED_HOSTS or host.startswith("metadata."):
        raise ValueError("Cloud metadata endpoints cannot be used as model gateways")
    if settings.app_env == "prod" and not allowlisted:
        raise ValueError("Production model endpoints must be listed in MODEL_ENDPOINT_ALLOWLIST")
    if parsed.scheme.lower() == "http" and not allowlisted:
        raise ValueError("HTTP model endpoints must be explicitly allowlisted; use HTTPS otherwise")
    if (host == "localhost" or host.endswith(".localhost")) and not allowlisted:
        raise ValueError("Loopback model endpoints must be explicitly allowlisted")
    if "." not in host and not allowlisted and not settings.model_allow_private_endpoints:
        raise ValueError("Single-label/internal model endpoints must be explicitly allowed")

    try:
        literal_address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        addresses = set() if allowlisted else _resolved_addresses(host, port)
    else:
        addresses = {literal_address}
    for address in addresses:
        _reject_unsafe_address(
            address,
            allowlisted=allowlisted,
            allow_private=settings.model_allow_private_endpoints,
        )


def validate_model_profile(
    task_type: str,
    mode: str,
    provider: str,
    model_name: str,
    base_url: str | None,
) -> None:
    if task_type not in PROVIDERS_BY_TASK:
        raise ValueError(f"Unsupported model task: {task_type}")
    if provider not in PROVIDERS_BY_TASK[task_type]:
        raise ValueError(f"Provider {provider!r} cannot be used for {task_type}")
    expected_mode = MODE_BY_PROVIDER[provider]
    if mode != expected_mode:
        raise ValueError(f"Provider {provider!r} requires mode {expected_mode!r}")
    if provider not in {"mock", "hashing", "disabled"} and not model_name.strip():
        raise ValueError("Model name or local model path is required")
    if mode == "api" and not (base_url or "").strip():
        raise ValueError("Base URL is required for API models")
    if mode == "api":
        validate_model_endpoint(base_url or "")


def model_profile_to_dict(profile: ModelProfile) -> dict[str, Any]:
    return {
        "id": profile.id,
        "name": profile.name,
        "task_type": profile.task_type,
        "mode": profile.mode,
        "provider": profile.provider,
        "model_name": profile.model_name,
        "base_url": profile.base_url,
        "api_key_configured": bool(profile.api_key_ciphertext),
        "api_key_hint": profile.api_key_hint,
        "config": json_loads(profile.config_json, {}),
        "enabled": profile.enabled,
        "is_active": profile.is_active,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def get_profile_api_key(profile: ModelProfile) -> str:
    return decrypt_secret(profile.api_key_ciphertext)


def set_profile_api_key(profile: ModelProfile, api_key: str | None) -> None:
    if api_key is None:
        return
    cleaned = api_key.strip()
    profile.api_key_ciphertext = encrypt_secret(cleaned) if cleaned else None
    profile.api_key_hint = secret_hint(cleaned) if cleaned else None


def get_active_model_profile(task_type: str, db: Session | None = None) -> ModelProfile | None:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        return session.scalars(
            select(ModelProfile).where(
                ModelProfile.task_type == task_type,
                ModelProfile.enabled.is_(True),
                ModelProfile.is_active.is_(True),
            ).order_by(ModelProfile.updated_at.desc()).limit(1)
        ).first()
    except SQLAlchemyError:
        return None
    finally:
        if owns_session:
            session.close()


def activate_model_profile(db: Session, profile: ModelProfile) -> None:
    if not profile.enabled:
        raise ValueError("Disabled model profiles cannot be activated")
    validate_model_profile(profile.task_type, profile.mode, profile.provider, profile.model_name, profile.base_url)
    if profile.mode == "api" and not profile.api_key_ciphertext:
        raise ValueError("An API key is required before this profile can be activated")
    db.execute(
        update(ModelProfile)
        .where(ModelProfile.task_type == profile.task_type)
        .values(is_active=False)
    )
    profile.is_active = True
    db.commit()


def _add_profiles(db: Session, profiles: Iterable[ModelProfile]) -> None:
    for profile in profiles:
        if not db.get(ModelProfile, profile.id):
            db.add(profile)
    db.commit()


def seed_model_profiles(db: Session) -> None:
    settings = get_settings()
    chat_profiles = list(db.scalars(select(ModelProfile).where(ModelProfile.task_type == "chat")).all())
    use_env_chat = (
        not chat_profiles
        and settings.llm_provider == "openai_compatible"
        and bool(settings.llm_api_key and settings.llm_base_url and settings.llm_model)
    )
    profiles = [
        ModelProfile(
            id="MODEL-chat-rule-engine",
            name="规则引擎 / Mock",
            task_type="chat",
            mode="builtin",
            provider="mock",
            model_name="rule-engine",
            config_json=json_dumps({"builtin": True}),
            is_active=not chat_profiles and not use_env_chat,
        ),
        ModelProfile(
            id="MODEL-embedding-hashing",
            name="内置字符向量（无需模型）",
            task_type="embedding",
            mode="builtin",
            provider="hashing",
            model_name="hashing-char-384",
            config_json=json_dumps({"builtin": True, "dimension": 384}),
            is_active=False,
        ),
        ModelProfile(
            id="MODEL-embedding-local-bge",
            name="本地 BGE 中文向量",
            task_type="embedding",
            mode="local",
            provider="sentence_transformers",
            model_name="BAAI/bge-small-zh-v1.5",
            config_json=json_dumps({"device": "cpu", "batch_size": 16, "normalize": True}),
            is_active=False,
        ),
        ModelProfile(
            id="MODEL-reranker-disabled",
            name="不使用 Reranker",
            task_type="reranker",
            mode="builtin",
            provider="disabled",
            model_name="disabled",
            config_json=json_dumps({"builtin": True}),
            is_active=False,
        ),
        ModelProfile(
            id="MODEL-reranker-local-qwen",
            name="本地 Qwen3 Reranker 0.6B",
            task_type="reranker",
            mode="local",
            provider="sentence_transformers",
            model_name="Qwen/Qwen3-Reranker-0.6B",
            config_json=json_dumps({
                "device": "cpu",
                "candidate_count": 30,
                "instruction": "Given a network troubleshooting query, retrieve passages that help diagnose and solve it.",
            }),
            is_active=False,
        ),
    ]
    if use_env_chat:
        env_profile = ModelProfile(
            id="MODEL-chat-env",
            name="环境变量中的 OpenAI-Compatible 模型",
            task_type="chat",
            mode="api",
            provider="openai_compatible",
            model_name=settings.llm_model,
            base_url=settings.llm_base_url,
            config_json=json_dumps({
                "temperature": settings.llm_temperature,
                "timeout_seconds": settings.llm_timeout_seconds,
                "max_retries": settings.llm_max_retries,
            }),
            is_active=True,
        )
        set_profile_api_key(env_profile, settings.llm_api_key)
        profiles.append(env_profile)
    _add_profiles(db, profiles)

    for task_type, fallback_id in {
        "chat": "MODEL-chat-rule-engine",
        "embedding": "MODEL-embedding-hashing",
        "reranker": "MODEL-reranker-disabled",
    }.items():
        active = db.scalars(
            select(ModelProfile).where(ModelProfile.task_type == task_type, ModelProfile.is_active.is_(True))
        ).first()
        if not active:
            fallback = db.get(ModelProfile, fallback_id)
            if fallback:
                fallback.is_active = True
    db.commit()


def new_model_profile_id() -> str:
    return new_id("MODEL")
