"""LAN mode invariants, independent of the desktop launcher's implicit defaults."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from app.core.config import Settings


def validate_server_settings(settings: Settings) -> None:
    if settings.app_env != "prod" or settings.auth_mode != "rbac":
        raise ValueError("LAN server requires APP_ENV=prod and AUTH_MODE=rbac")
    if settings.auth_allow_legacy_admin or settings.api_key or settings.mcp_bearer_token:
        raise ValueError("LAN server requires individual tokens; shared administrator credentials must be disabled")
    url = urlsplit(settings.mcp_public_base_url)
    if (url.scheme != "https" or not url.hostname or url.username or url.password
            or url.path not in {"", "/"} or url.query or url.fragment):
        raise ValueError("LAN server public URL must be a complete HTTPS origin")
    if not settings.mcp_enabled:
        raise ValueError("LAN server requires MCP_ENABLED=true")
    if not re.fullmatch(r"GWAP-[a-f0-9]{32}", settings.server_instance_id):
        raise ValueError("LAN server requires a stable SERVER_INSTANCE_ID")
    if settings.mcp_allowed_host_list != [url.netloc]:
        raise ValueError("LAN MCP Host allowlist must match the exact public authority")
    if settings.mcp_allowed_origin_list != [settings.mcp_public_base_url]:
        raise ValueError("LAN MCP Origin allowlist must match the public origin")
    if settings.cors_origin_list != [settings.mcp_public_base_url]:
        raise ValueError("LAN CORS must match the public origin")
    hosts = [value.strip() for value in settings.trusted_hosts.split(",") if value.strip()]
    if url.hostname not in hosts or any("*" in value for value in hosts):
        raise ValueError("LAN TRUSTED_HOSTS must contain the public host without wildcards")
