from __future__ import annotations

import asyncio
import secrets
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.mcp.contracts import BearerAuthResolver, MCPPrincipal
from app.services.access_control import authenticate_access_token


SessionFactory = Callable[[], Session]


class DebugPlatformBearerAuthResolver:
    """Resolve MCP Bearer credentials without exposing the raw token to tools."""

    __slots__ = ("_session_factory", "_settings")

    def __init__(self, settings: Settings, session_factory: SessionFactory) -> None:
        self._settings = settings
        self._session_factory = session_factory

    @staticmethod
    def _matches(raw_token: str, configured_token: str | None) -> bool:
        return bool(configured_token) and secrets.compare_digest(
            raw_token,
            configured_token or "",
        )

    def _legacy_api_key_allowed(self) -> bool:
        return (
            self._settings.auth_mode in {"local", "api_key"}
            or self._settings.auth_allow_legacy_admin
        )

    def _authenticate_personal_token(self, raw_token: str) -> dict[str, str] | None:
        with self._session_factory() as db:
            return authenticate_access_token(db, raw_token)

    async def __call__(self, raw_token: str) -> MCPPrincipal | None:
        if not raw_token:
            return None
        if self._matches(raw_token, self._settings.mcp_bearer_token):
            return MCPPrincipal(
                subject="mcp-static-bearer",
                client_id="debug-platform-mcp-static",
                scopes=("debugplatform:mcp",),
                claims={
                    "id": "mcp-static-bearer",
                    "type": "mcp_bearer",
                    "role": "ADMIN",
                },
            )
        if (
            self._legacy_api_key_allowed()
            and self._matches(raw_token, self._settings.api_key)
        ):
            return MCPPrincipal(
                subject="legacy-api-key",
                client_id="debug-platform-legacy-api-key",
                scopes=("debugplatform:mcp",),
                claims={
                    "id": "legacy-api-key",
                    "type": "api_key",
                    "role": "ADMIN",
                },
            )

        principal = await asyncio.to_thread(self._authenticate_personal_token, raw_token)
        if principal is None:
            return None
        return MCPPrincipal(
            subject=principal["id"],
            client_id="debug-platform-personal-token",
            scopes=("debugplatform:mcp",),
            claims=dict(principal),
        )


def create_debugplatform_mcp_auth_resolver(
    settings: Settings | None = None,
    session_factory: SessionFactory | None = None,
) -> BearerAuthResolver:
    if settings is None:
        settings = get_settings()
    if session_factory is None:
        from app.core.db import SessionLocal

        session_factory = SessionLocal
    return DebugPlatformBearerAuthResolver(settings, session_factory)

