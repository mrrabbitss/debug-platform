from collections.abc import Iterable
import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id
from app.models import ModelProfile
from app.model_access_models import ModelProfileAccess
from app.services.secrets import decrypt_secret, encrypt_secret, secret_hint


PROVIDERS_BY_TASK = {
    "chat": {"mock", "openai_compatible"},
    "embedding": {
        "hashing",
        "sentence_transformers",
        "openai_compatible",
        "llama_cpp_local",
    },
    "reranker": {
        "disabled",
        "sentence_transformers",
        "qwen_rerank_api",
        "llama_cpp_local",
    },
}

MODE_BY_PROVIDER = {
    "mock": "builtin",
    "hashing": "builtin",
    "disabled": "builtin",
    "sentence_transformers": "local",
    "openai_compatible": "api",
    "qwen_rerank_api": "api",
    "llama_cpp_local": "api",
}

MANAGED_LOCAL_PROVIDER = "llama_cpp_local"
MANAGED_SIDECAR_API_KEY_ENV = "BUNDLED_GGUF_API_KEY"
MANAGED_EMBEDDING_URL_ENV = "BUNDLED_GGUF_EMBEDDING_URL"
MANAGED_RERANKER_URL_ENV = "BUNDLED_GGUF_RERANKER_URL"
_MIN_MANAGED_TOKEN_LENGTH = 32
_MANAGED_AUTO_RESTORE_KEY = "launcher_auto_restore_pending"

COMPATIBLE_CHAT_PROVIDERS = (
    "Qwen Model Studio OpenAI-compatible API",
    "GLM OpenAI-compatible API",
    "internal OpenAI-compatible gateway",
)

def validate_model_endpoint(base_url: str) -> None:
    """Validate API URL syntax; user-selected HTTP(S) hosts need no allowlist."""
    value = base_url.strip()
    if len(value) > 2048 or any(character.isspace() or ord(character) < 32 or ord(character) == 127
                                for character in value):
        raise ValueError("Model Base URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Base URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Model Base URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Model Base URL must include a hostname")
    if port == 0 or "\\" in parsed.netloc or parsed.netloc.endswith(":"):
        raise ValueError("Model Base URL is invalid")
    if parsed.username or parsed.password:
        raise ValueError("Model Base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Model Base URL must not contain a query string or fragment")

def validate_managed_sidecar_endpoint(base_url: str) -> None:
    """Validate the installer-managed llama.cpp endpoint identity.

    The bundled launcher owns these profiles and supplies a random bearer token.
    Requiring a literal loopback address and an explicit port prevents DNS rebinding
    and keeps this narrow exception from applying to user-configured API gateways.
    """
    value = base_url.strip()
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Managed llama.cpp Base URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Managed llama.cpp Base URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Managed llama.cpp Base URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Managed llama.cpp Base URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError(
            "Managed llama.cpp Base URL must not contain a query string or fragment"
        )
    if port is None or port == 0:
        raise ValueError("Managed llama.cpp Base URL must include an explicit port")
    try:
        address = ipaddress.ip_address(parsed.hostname.split("%", 1)[0])
    except ValueError as exc:
        raise ValueError(
            "Managed llama.cpp Base URL must use a literal loopback address"
        ) from exc
    if not address.is_loopback:
        raise ValueError(
            "Managed llama.cpp Base URL must use a literal loopback address"
        )


def validate_model_proxy_url(task_type: str, mode: str, proxy_url: str | None) -> None:
    """Validate an explicitly selected Chat proxy without exposing credentials."""
    value = (proxy_url or "").strip()
    if not value:
        return
    if task_type != "chat" or mode != "api":
        raise ValueError("A proxy can only be configured for an API Chat model")
    if len(value) > 2048 or any(
        character.isspace() or ord(character) < 32 or ord(character) == 127
        for character in value
    ):
        raise ValueError("Model proxy URL is invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Model proxy URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Model proxy URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Model proxy URL must include a hostname")
    if port == 0 or "\\" in parsed.netloc or parsed.netloc.endswith(":"):
        raise ValueError("Model proxy URL is invalid")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Model proxy URL must not contain a path, query string or fragment")

def validate_model_profile(
    task_type: str,
    mode: str,
    provider: str,
    model_name: str,
    base_url: str | None,
    proxy_url: str | None = None,
    *,
    allow_managed: bool = False,
) -> None:
    if task_type not in PROVIDERS_BY_TASK:
        raise ValueError(f"Unsupported model task: {task_type}")
    if provider not in PROVIDERS_BY_TASK[task_type]:
        raise ValueError(f"Provider {provider!r} cannot be used for {task_type}")
    if provider == MANAGED_LOCAL_PROVIDER and not allow_managed:
        raise ValueError(
            "Bundled llama.cpp profiles are created and secured by the launcher"
        )
    expected_mode = MODE_BY_PROVIDER[provider]
    if mode != expected_mode:
        raise ValueError(f"Provider {provider!r} requires mode {expected_mode!r}")
    if provider not in {"mock", "hashing", "disabled"} and not model_name.strip():
        raise ValueError("Model name or local model path is required")
    if mode == "api" and not (base_url or "").strip():
        raise ValueError("Base URL is required for API models")
    if mode == "api":
        if provider == MANAGED_LOCAL_PROVIDER:
            validate_managed_sidecar_endpoint(base_url or "")
        else:
            validate_model_endpoint(base_url or "")
    validate_model_proxy_url(task_type, mode, proxy_url)


def model_profile_to_dict(profile: ModelProfile) -> dict[str, Any]:
    managed_key = (
        _managed_sidecar_api_key()
        if profile.provider == MANAGED_LOCAL_PROVIDER
        else ""
    )
    return {
        "id": profile.id,
        "name": profile.name,
        "task_type": profile.task_type,
        "mode": profile.mode,
        "provider": profile.provider,
        "model_name": profile.model_name,
        "base_url": profile.base_url,
        "api_key_configured": bool(profile.api_key_ciphertext or managed_key),
        "api_key_hint": (
            profile.api_key_hint
            or ("managed by launcher" if managed_key else None)
        ),
        "proxy_url_configured": bool(profile.proxy_url_ciphertext),
        "proxy_url_hint": profile.proxy_url_hint,
        "certificate_revocation_check_skipped": profile_uses_proxy(profile),
        "config": json_loads(profile.config_json, {}),
        "enabled": profile.enabled,
        "is_active": profile.is_active,
        "created_at": profile.created_at,
        "updated_at": profile.updated_at,
    }


def profile_uses_proxy(profile: ModelProfile | None) -> bool:
    return bool(
        profile
        and profile.task_type == "chat"
        and profile.mode == "api"
        and profile.provider == "openai_compatible"
        and profile.proxy_url_ciphertext
    )


def get_profile_api_key(profile: ModelProfile) -> str:
    saved = decrypt_secret(profile.api_key_ciphertext, "API key")
    if saved:
        return saved
    if profile.provider == MANAGED_LOCAL_PROVIDER:
        return _managed_sidecar_api_key()
    return ""


def _managed_sidecar_api_key() -> str:
    value = os.environ.get(MANAGED_SIDECAR_API_KEY_ENV, "").strip()
    return value if len(value) >= _MIN_MANAGED_TOKEN_LENGTH else ""


def profile_api_key_available(profile: ModelProfile) -> bool:
    return bool(get_profile_api_key(profile))


def set_profile_api_key(profile: ModelProfile, api_key: str | None) -> None:
    if api_key is None:
        return
    cleaned = api_key.strip()
    profile.api_key_ciphertext = encrypt_secret(cleaned) if cleaned else None
    profile.api_key_hint = secret_hint(cleaned) if cleaned else None


def _proxy_url_hint(proxy_url: str) -> str:
    parsed = urlsplit(proxy_url)
    hostname = parsed.hostname or ""
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.lower()}://{rendered_host}{port}"


def get_profile_proxy_url(profile: ModelProfile) -> str:
    return decrypt_secret(profile.proxy_url_ciphertext, "model proxy URL")


def set_profile_proxy_url(profile: ModelProfile, proxy_url: str | None) -> None:
    if proxy_url is None:
        return
    cleaned = proxy_url.strip()
    validate_model_proxy_url(profile.task_type, profile.mode, cleaned)
    profile.proxy_url_ciphertext = encrypt_secret(cleaned) if cleaned else None
    profile.proxy_url_hint = _proxy_url_hint(cleaned) if cleaned else None


def get_active_model_profile(task_type: str, db: Session | None = None) -> ModelProfile | None:
    owns_session = db is None
    session = db or SessionLocal()
    try:
        from app.services.model_access import shared_model_clause
        return session.scalars(
            select(ModelProfile).where(
                ModelProfile.task_type == task_type,
                ModelProfile.enabled.is_(True),
                ModelProfile.is_active.is_(True),
                shared_model_clause() if task_type == "chat" else True,
            ).order_by(ModelProfile.updated_at.desc()).limit(1)
        ).first()
    except SQLAlchemyError:
        return None
    finally:
        if owns_session:
            session.close()


def activate_model_profile(db: Session, profile: ModelProfile) -> None:
    from app.services.model_access import lock_model_configuration
    lock_model_configuration(db, profile)
    if profile.task_type == "chat":
        access = db.get(ModelProfileAccess, profile.id, populate_existing=True)
        if not access or access.visibility != "SHARED":
            raise ValueError("The shared Chat default must be a shared profile")
    if not profile.enabled:
        raise ValueError("Disabled model profiles cannot be activated")
    proxy_url = get_profile_proxy_url(profile) if profile.proxy_url_ciphertext else None
    validate_model_profile(
        profile.task_type,
        profile.mode,
        profile.provider,
        profile.model_name,
        profile.base_url,
        proxy_url,
        allow_managed=True,
    )
    if profile.mode == "api" and not profile_api_key_available(profile):
        if profile.provider == MANAGED_LOCAL_PROVIDER:
            raise ValueError(
                f"The managed llama.cpp sidecar requires a launcher-provided "
                f"{MANAGED_SIDECAR_API_KEY_ENV} token of at least "
                f"{_MIN_MANAGED_TOKEN_LENGTH} characters"
            )
        raise ValueError("An API key is required before this profile can be activated")
    # A manual activation is an explicit user choice. Clear any launcher-created
    # recovery marker for this task so a later sidecar restart cannot steal the
    # selection back from a custom profile or the built-in fallback.
    managed_profiles = db.scalars(
        select(ModelProfile).where(
            ModelProfile.task_type == profile.task_type,
            ModelProfile.provider == MANAGED_LOCAL_PROVIDER,
        )
    ).all()
    for managed_profile in managed_profiles:
        config = json_loads(managed_profile.config_json, {})
        if isinstance(config, dict) and config.pop(_MANAGED_AUTO_RESTORE_KEY, None):
            managed_profile.config_json = json_dumps(config)
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
        db.flush()
        if not db.get(ModelProfileAccess, profile.id):
            db.add(ModelProfileAccess(profile_id=profile.id, visibility="SHARED"))
    db.commit()


def _sync_bundled_gguf_profiles(db: Session) -> None:
    """Upsert fixed profiles advertised by a healthy bundled launcher.

    Environment presence is a readiness contract: the launcher must only expose
    the URLs after both sidecars have passed their health probes. Tokens are never
    persisted in the database, so copying a database does not copy sidecar access.
    """
    token_available = bool(_managed_sidecar_api_key())
    specs = (
        {
            "id": "MODEL-embedding-bundled-gguf",
            "task_type": "embedding",
            "name": "内置 BGE GGUF（CPU）",
            "url": os.environ.get(MANAGED_EMBEDDING_URL_ENV, "").strip(),
            "model_name": os.environ.get(
                "BUNDLED_GGUF_EMBEDDING_MODEL",
                "bge-base-zh-v1.5-gguf",
            ).strip(),
            "config": {
                "builtin": True,
                "managed_sidecar": True,
                "dimension": 768,
                "normalize": True,
                "query_instruction": "为这个句子生成表示以用于检索相关文章：",
                "timeout_seconds": 120,
                "max_retries": 1,
                "batch_size": 16,
            },
        },
        {
            "id": "MODEL-reranker-bundled-gguf",
            "task_type": "reranker",
            "name": "内置 Qwen3 Reranker GGUF（CPU）",
            "url": os.environ.get(MANAGED_RERANKER_URL_ENV, "").strip(),
            "model_name": os.environ.get(
                "BUNDLED_GGUF_RERANKER_MODEL",
                "qwen3-reranker-0.6b-gguf",
            ).strip(),
            "config": {
                "builtin": True,
                "managed_sidecar": True,
                "endpoint_path": "/v1/rerank",
                "timeout_seconds": 120,
                "candidate_count": 30,
            },
        },
    )
    default_fallback_ids = {
        "embedding": "MODEL-embedding-hashing",
        "reranker": "MODEL-reranker-disabled",
    }
    for spec in specs:
        profile = db.get(ModelProfile, spec["id"])
        url = str(spec["url"])
        usable = token_available and bool(url)
        if usable:
            try:
                validate_managed_sidecar_endpoint(url)
            except ValueError:
                usable = False
        if not usable:
            if profile is not None:
                config = json_loads(profile.config_json, {})
                if profile.is_active and isinstance(config, dict):
                    # Only an active managed profile may request automatic
                    # recovery. An already inactive profile represents a user
                    # selection and must remain inactive after recovery.
                    config[_MANAGED_AUTO_RESTORE_KEY] = True
                    profile.config_json = json_dumps(config)
                profile.enabled = False
                profile.is_active = False
            continue

        active = db.scalars(
            select(ModelProfile).where(
                ModelProfile.task_type == spec["task_type"],
                ModelProfile.is_active.is_(True),
            ).limit(1)
        ).first()
        if profile is None:
            replace_default = bool(
                active
                and active.id == default_fallback_ids[str(spec["task_type"])]
            )
            if replace_default:
                active.is_active = False
            profile = ModelProfile(
                id=str(spec["id"]),
                task_type=str(spec["task_type"]),
                mode="api",
                provider=MANAGED_LOCAL_PROVIDER,
                name=str(spec["name"]),
                model_name=str(spec["model_name"]),
                base_url=url,
                config_json=json_dumps(spec["config"]),
                enabled=True,
                is_active=active is None or replace_default,
            )
            db.add(profile)
        else:
            previous_config = json_loads(profile.config_json, {})
            restore_pending = bool(
                isinstance(previous_config, dict)
                and previous_config.get(_MANAGED_AUTO_RESTORE_KEY)
            )
            restore_from_fallback = bool(
                restore_pending
                and active is not None
                and active.id == default_fallback_ids[str(spec["task_type"])]
            )
            profile.mode = "api"
            profile.provider = MANAGED_LOCAL_PROVIDER
            profile.name = str(spec["name"])
            profile.model_name = str(spec["model_name"])
            profile.base_url = url
            profile.config_json = json_dumps(spec["config"])
            profile.api_key_ciphertext = None
            profile.api_key_hint = None
            profile.proxy_url_ciphertext = None
            profile.proxy_url_hint = None
            profile.enabled = True
            if restore_from_fallback:
                # The database enforces one active profile per task. Persist the
                # fallback deactivation before marking the managed profile active
                # so an autoflush cannot transiently violate that unique index.
                db.execute(
                    update(ModelProfile)
                    .where(ModelProfile.id == active.id)
                    .values(is_active=False)
                )
                db.flush()
                profile.is_active = True
            # An existing inactive managed profile records that the user chose
            # another provider. Only first appearance may replace the built-in
            # fallback. A launcher-created marker permits one narrow exception:
            # restore only from the unchanged default fallback after a transient
            # sidecar outage. Rebuilding config clears that one-shot marker.
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
            id="MODEL-reranker-disabled",
            name="不使用 Reranker",
            task_type="reranker",
            mode="builtin",
            provider="disabled",
            model_name="disabled",
            config_json=json_dumps({"builtin": True}),
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
                "trust_environment_proxy": True,
            }),
            is_active=True,
        )
        set_profile_api_key(env_profile, settings.llm_api_key)
        profiles.append(env_profile)
    _add_profiles(db, profiles)
    _sync_bundled_gguf_profiles(db)
    for profile in db.scalars(select(ModelProfile).where(ModelProfile.provider == MANAGED_LOCAL_PROVIDER)):
        if not db.get(ModelProfileAccess, profile.id):
            db.add(ModelProfileAccess(profile_id=profile.id, visibility="SHARED"))
    db.flush()

    if settings.model_disable_in_process_local:
        db.execute(
            update(ModelProfile)
            .where(
                ModelProfile.provider == "sentence_transformers",
                ModelProfile.is_active.is_(True),
            )
            .values(is_active=False)
        )
        db.commit()

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


async def test_profile_connection(profile: ModelProfile) -> dict:
    """Probe a profile already authorized by the caller; do not return upstream bodies."""
    from starlette.concurrency import run_in_threadpool
    from app.services.llm import get_llm_provider
    from app.services.retrieval_models import embed_texts, rerank_documents

    if profile.task_type == "chat":
        provider = get_llm_provider(profile)
        text = await provider.generate_text("你是连接测试助手。", "仅回复 MODEL_CONNECTION_OK",
                                            purpose="connection_test")
        ok = provider.is_mock or "MODEL_CONNECTION_OK" in text
        return {"ok": ok, "response": "MODEL_CONNECTION_OK" if ok else "Unexpected test response",
                "model": provider.model_name, "proxy_url_configured": getattr(provider, "proxy_configured", False)}
    if profile.task_type == "embedding":
        vectors = await run_in_threadpool(lambda: embed_texts(profile, ["GW 无法上线", "AP 认证失败"],
                                                             purpose="connection_test"))
        dimension = len(vectors[0]) if vectors else 0
        return {"ok": bool(dimension), "dimension": dimension, "vectors": len(vectors)}
    ranking = await run_in_threadpool(lambda: rerank_documents("AP 认证失败如何排查",
        ["检查 EAP 和四次握手日志", "查询设备外壳颜色"], 2, profile, purpose="connection_test"))
    return {"ok": ranking is None or bool(ranking), "ranking": ranking or [], "disabled": ranking is None}
