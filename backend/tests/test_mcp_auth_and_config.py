from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.db import Base
from app.core.utils import utcnow
from app.mcp.auth import DebugPlatformBearerAuthResolver
from app.models import UserAccount
from app.services.access_control import issue_access_token


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "sqlite:///:memory:",
        "data_root": "./data",
        "storage_root": "./data/storage",
        "mcp_enabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def test_mcp_configuration_defaults_and_allowlists() -> None:
    settings = _settings(
        mcp_allowed_hosts="debug.example.internal:443, localhost:* ,",
        mcp_allowed_origins="https://debug.example.internal, http://localhost:*",
        mcp_public_base_url="https://debug.example.internal/",
    )

    assert settings.mcp_enabled is True
    assert settings.mcp_public_base_url == "https://debug.example.internal"
    assert settings.mcp_max_request_body_bytes == 4 * 1024 * 1024
    assert settings.mcp_allowed_host_list == [
        "debug.example.internal:443",
        "localhost:*",
    ]
    assert settings.mcp_allowed_origin_list == [
        "https://debug.example.internal",
        "http://localhost:*",
    ]


def test_enabled_mcp_rejects_an_empty_host_allowlist() -> None:
    with pytest.raises(ValueError, match="MCP_ALLOWED_HOSTS"):
        _settings(mcp_allowed_hosts=" , ")


def test_enabled_mcp_rejects_a_non_http_public_url() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        _settings(mcp_public_base_url="javascript:alert(1)")


def test_bearer_resolver_supports_static_and_legacy_tokens() -> None:
    factory = _session_factory()
    resolver = DebugPlatformBearerAuthResolver(
        _settings(
            auth_mode="rbac",
            auth_allow_legacy_admin=True,
            api_key="legacy-secret",
            mcp_bearer_token="mcp-secret",
        ),
        factory,
    )

    static = asyncio.run(resolver("mcp-secret"))
    legacy = asyncio.run(resolver("legacy-secret"))
    missing = asyncio.run(resolver("wrong-secret"))

    assert static is not None
    assert static.subject == "mcp-static-bearer"
    assert static.claims["role"] == "ADMIN"
    assert legacy is not None
    assert legacy.subject == "legacy-api-key"
    assert legacy.claims["type"] == "api_key"
    assert missing is None


def test_bearer_resolver_maps_database_personal_access_token() -> None:
    factory = _session_factory()
    with factory() as db:
        user = UserAccount(
            id="USR-mcp-engineer",
            username="mcp-engineer",
            display_name="MCP Engineer",
            role="ENGINEER",
            active=True,
        )
        db.add(user)
        db.commit()
        _, raw_token = issue_access_token(db, user, name="claude-code")

    resolver = DebugPlatformBearerAuthResolver(
        _settings(auth_mode="rbac", auth_allow_legacy_admin=False),
        factory,
    )
    principal = asyncio.run(resolver(raw_token))

    assert principal is not None
    assert principal.subject == "USR-mcp-engineer"
    assert principal.client_id == "debug-platform-personal-token"
    assert principal.claims["role"] == "ENGINEER"
    assert principal.claims["type"] == "user_token"
    assert raw_token not in repr(principal)


def test_bearer_resolver_rejects_expired_personal_token_and_disabled_legacy_key() -> None:
    factory = _session_factory()
    with factory() as db:
        user = UserAccount(
            id="USR-mcp-expired",
            username="mcp-expired",
            display_name="Expired MCP User",
            role="VIEWER",
            active=True,
        )
        db.add(user)
        db.commit()
        token, raw_token = issue_access_token(db, user)
        token.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()

    resolver = DebugPlatformBearerAuthResolver(
        _settings(
            auth_mode="rbac",
            auth_allow_legacy_admin=False,
            api_key="disabled-legacy-secret",
        ),
        factory,
    )

    assert asyncio.run(resolver(raw_token)) is None
    assert asyncio.run(resolver("disabled-legacy-secret")) is None
