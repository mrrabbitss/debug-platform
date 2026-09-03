from __future__ import annotations

from fastapi import FastAPI
from mcp.server.transport_security import TransportSecuritySettings

from app.core.config import Settings
from app.mcp.auth import create_debugplatform_mcp_auth_resolver
from app.mcp.transport import MCPHTTPTransport, create_mcp_http_transport


def create_debugplatform_mcp_transport(settings: Settings) -> MCPHTTPTransport:
    # Imported lazily so configuration-only commands remain usable even when an
    # optional distribution intentionally omits the Debug Platform tool bundle.
    from app.mcp.debugplatform_registry import (
        DEBUGPLATFORM_MCP_SERVER_VERSION,
        create_debugplatform_mcp_registry,
    )

    registry = create_debugplatform_mcp_registry(
        public_base_url=settings.mcp_public_base_url,
    )
    resolver = create_debugplatform_mcp_auth_resolver(settings)
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.mcp_allowed_host_list,
        allowed_origins=settings.mcp_allowed_origin_list,
    )
    return create_mcp_http_transport(
        registry,
        resolver,
        server_name="gw-ap-debug-platform",
        server_version=DEBUGPLATFORM_MCP_SERVER_VERSION,
        instructions=(
            "Use these tools as the Debug Platform evidence and persistence plane. "
            "The active CLI model owns planning and diagnosis reasoning."
        ),
        max_request_body_size=settings.mcp_max_request_body_bytes,
        transport_security=transport_security,
    )


def configure_debugplatform_mcp(
    app: FastAPI,
    settings: Settings,
) -> MCPHTTPTransport | None:
    """Install the exact MCP route before the static-frontend catch-all."""

    if not settings.mcp_enabled:
        app.state.mcp_transport = None
        return None
    transport = create_debugplatform_mcp_transport(settings)
    app.state.mcp_transport = transport
    app.router.routes.append(transport.route("/mcp"))
    return transport
