from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence, TypeAlias

from mcp import types


JSONSchema: TypeAlias = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class MCPPrincipal:
    """Authenticated CLI identity available to MCP tool providers.

    The bearer token itself is deliberately not exposed to tools.  A resolver may
    attach stable, non-secret claims (for example a platform user id or role) for
    a business registry to use when it performs case-level authorization.
    """

    subject: str
    client_id: str = "debug-platform-cli"
    scopes: tuple[str, ...] = ()
    claims: Mapping[str, Any] = field(default_factory=dict)


BearerAuthResult: TypeAlias = MCPPrincipal | None
BearerAuthResolver: TypeAlias = Callable[
    [str],
    BearerAuthResult | Awaitable[BearerAuthResult],
]


@dataclass(frozen=True, slots=True)
class MCPToolDefinition:
    name: str
    description: str
    input_schema: JSONSchema
    output_schema: JSONSchema | None = None
    title: str | None = None
    annotations: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MCPToolCallContext:
    principal: MCPPrincipal
    request_id: int | str | None
    protocol_version: str


class MCPToolProvider(Protocol):
    """Business-tool boundary consumed by the transport adapter.

    Implementations own authorization and domain validation.  The transport only
    authenticates the bearer token, translates MCP types and invokes this API; it
    never performs model inference.
    """

    async def list_tools(self, principal: MCPPrincipal) -> Sequence[MCPToolDefinition]: ...

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: MCPToolCallContext,
    ) -> Any | types.CallToolResult: ...


class MCPToolError(Exception):
    """Expected, model-visible tool failure with a safe message."""

