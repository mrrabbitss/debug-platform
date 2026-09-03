from __future__ import annotations

import inspect
import json
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi.encoders import jsonable_encoder
from mcp import types
from mcp.server import Server, ServerRequestContext
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware, get_access_token
from mcp.server.auth.middleware.bearer_auth import (
    AuthenticatedUser,
    BearerAuthBackend,
    RequireAuthMiddleware,
)
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from app.mcp.contracts import (
    BearerAuthResolver,
    MCPPrincipal,
    MCPToolCallContext,
    MCPToolDefinition,
    MCPToolError,
    MCPToolProvider,
)


LOGGER = logging.getLogger(__name__)


class _RequireBearerOnHTTP:
    """Apply the SDK bearer guard to HTTP while preserving ASGI lifespan."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app
        self._protected = RequireAuthMiddleware(app, required_scopes=[])

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            user = scope.get("user")
            if isinstance(user, AuthenticatedUser):
                access_token = user.access_token
                principal = dict(access_token.claims or {})
                principal.setdefault("id", access_token.subject or access_token.client_id)
                principal.setdefault("type", "mcp_bearer")
                principal.setdefault("role", "VIEWER")
                scope.setdefault("state", {})["principal"] = principal
            await self._protected(scope, receive, send)
            return
        await self._app(scope, receive, send)


class _ExactRouteEndpoint:
    """Adapt an exact host route (for example ``/mcp``) to the child root."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        child_scope = dict(scope)
        child_scope["path"] = "/"
        child_scope["raw_path"] = b"/"
        child_scope["root_path"] = ""
        await self._app(child_scope, receive, send)


class _ResolverTokenVerifier(TokenVerifier):
    def __init__(self, resolver: BearerAuthResolver) -> None:
        self._resolver = resolver

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            principal = self._resolver(token)
            if inspect.isawaitable(principal):
                principal = await principal
        except Exception:
            LOGGER.exception("MCP bearer-token resolver failed")
            return None
        if principal is None:
            return None
        if not principal.subject:
            LOGGER.error("MCP bearer-token resolver returned an empty subject")
            return None
        return AccessToken(
            token=token,
            client_id=principal.client_id,
            subject=principal.subject,
            scopes=list(principal.scopes),
            claims=dict(principal.claims),
        )


def _current_principal() -> MCPPrincipal:
    token = get_access_token()
    if token is None or not token.subject:
        # RequireAuthMiddleware should make this unreachable.  Keep a fail-closed
        # guard here so a future mounting error cannot create an anonymous tool call.
        raise RuntimeError("authenticated MCP principal is unavailable")
    return MCPPrincipal(
        subject=token.subject,
        client_id=token.client_id,
        scopes=tuple(token.scopes),
        claims=dict(token.claims or {}),
    )


def _protocol_tool(definition: MCPToolDefinition) -> types.Tool:
    annotations = None
    if definition.annotations is not None:
        annotations = types.ToolAnnotations.model_validate(dict(definition.annotations))
    return types.Tool(
        name=definition.name,
        title=definition.title,
        description=definition.description,
        inputSchema=dict(definition.input_schema),
        outputSchema=dict(definition.output_schema) if definition.output_schema is not None else None,
        annotations=annotations,
    )


def _call_result(value: Any) -> types.CallToolResult:
    if isinstance(value, types.CallToolResult):
        return value
    payload = jsonable_encoder(value)
    if isinstance(payload, str):
        text = payload
    else:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return types.CallToolResult(
        content=[types.TextContent(text=text)],
        structuredContent=payload,
    )


def _error_result(message: str) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(text=message)],
        isError=True,
    )


def _tool_call_context(context: ServerRequestContext[Any, Any]) -> MCPToolCallContext:
    return MCPToolCallContext(
        principal=_current_principal(),
        request_id=context.request_id,
        protocol_version=context.protocol_version,
    )


@dataclass(frozen=True, slots=True)
class MCPHTTPTransport:
    """Mountable Streamable HTTP MCP adapter.

    ``app`` can run standalone (its own lifespan starts the SDK session manager).
    When it is mounted under an existing FastAPI app, nested ASGI lifespans are
    not started automatically; the host lifespan must enter ``lifespan()``.
    To serve an exact ``/mcp`` route without Starlette's mount trailing-slash
    redirect, attach ``exact_route_endpoint`` as a ``Route`` supporting GET,
    POST and DELETE.
    """

    server: Server[Any]
    app: ASGIApp
    exact_route_endpoint: ASGIApp
    endpoint_path: str = "/"

    @asynccontextmanager
    async def lifespan(self) -> AsyncIterator[None]:
        async with self.server.session_manager.run():
            yield

    def route(self, path: str = "/mcp", *, name: str = "mcp") -> Route:
        """Return an exact FastAPI/Starlette route for this transport."""

        if not path.startswith("/") or path.endswith("/"):
            raise ValueError("MCP route path must start with '/' and omit a trailing slash")
        return Route(
            path,
            endpoint=self.exact_route_endpoint,
            methods=["GET", "POST", "DELETE"],
            name=name,
        )


def create_mcp_http_transport(
    tool_provider: MCPToolProvider,
    auth_resolver: BearerAuthResolver,
    *,
    server_name: str = "gw-ap-debug-platform",
    server_version: str = "0.1.0",
    instructions: str | None = None,
    stateless_http: bool = False,
    max_request_body_size: int = 4 * 1024 * 1024,
    transport_security: TransportSecuritySettings | None = None,
    host: str = "127.0.0.1",
) -> MCPHTTPTransport:
    """Build a standard Streamable HTTP MCP endpoint with injectable tools/auth.

    Authentication accepts ``Authorization: Bearer`` and delegates token
    validation to ``auth_resolver``.  Model-provider credentials are neither
    accepted nor used here.  For a remote deployment, pass explicit
    ``transport_security`` host/origin allowlists instead of relying on the
    SDK's localhost defaults.
    """

    async def list_tools(
        context: ServerRequestContext[Any, Any],
        params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        del params
        definitions = await tool_provider.list_tools(_current_principal())
        return types.ListToolsResult(tools=[_protocol_tool(item) for item in definitions])

    async def call_tool(
        context: ServerRequestContext[Any, Any],
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        arguments: Mapping[str, Any] = params.arguments or {}
        try:
            result = await tool_provider.call_tool(
                params.name,
                arguments,
                _tool_call_context(context),
            )
            return _call_result(result)
        except MCPToolError as exc:
            return _error_result(str(exc))

    server: Server[Any] = Server(
        server_name,
        version=server_version,
        instructions=instructions,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )
    protocol_app = server.streamable_http_app(
        streamable_http_path="/",
        json_response=True,
        stateless_http=stateless_http,
        max_request_body_size=max_request_body_size,
        transport_security=transport_security,
        host=host,
    )

    # Reuse the official SDK authentication implementation without publishing
    # misleading OAuth discovery metadata for legacy API keys/personal tokens.
    verifier = _ResolverTokenVerifier(auth_resolver)
    protected_app: ASGIApp = _RequireBearerOnHTTP(protocol_app)
    protected_app = AuthContextMiddleware(protected_app)
    protected_app = AuthenticationMiddleware(
        protected_app,
        backend=BearerAuthBackend(verifier),
    )
    return MCPHTTPTransport(
        server=server,
        app=protected_app,
        exact_route_endpoint=_ExactRouteEndpoint(protected_app),
    )
