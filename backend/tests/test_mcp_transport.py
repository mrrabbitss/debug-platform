from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.mcp import (
    MCPPrincipal,
    MCPToolCallContext,
    MCPToolRegistry,
    create_mcp_http_transport,
)


PROTOCOL_VERSION = "2025-11-25"


class EchoInput(BaseModel):
    message: str


def _transport(seen_contexts: list[MCPToolCallContext]):
    registry = MCPToolRegistry()

    async def echo(payload: EchoInput, context: MCPToolCallContext) -> dict[str, str]:
        seen_contexts.append(context)
        return {
            "message": payload.message,
            "subject": context.principal.subject,
        }

    registry.register(
        name="debug_echo",
        title="Echo",
        description="Return a test message and the authenticated platform identity.",
        input_model=EchoInput,
        handler=echo,
        annotations={"readOnlyHint": True},
    )

    async def resolve_bearer(token: str) -> MCPPrincipal | None:
        if token != "valid-personal-token":
            return None
        return MCPPrincipal(
            subject="USR-mcp-test",
            client_id="codex-cli",
            scopes=("cases:read",),
            claims={"role": "ENGINEER"},
        )

    return create_mcp_http_transport(registry, resolve_bearer)


def _headers(session_id: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": "Bearer valid-personal-token",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if session_id is not None:
        headers["Mcp-Session-Id"] = session_id
        headers["MCP-Protocol-Version"] = PROTOCOL_VERSION
    return headers


def _initialize(client: TestClient, path: str) -> str:
    response = client.post(
        path,
        headers=_headers(),
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "transport-test", "version": "1.0"},
            },
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    assert body["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert body["result"]["capabilities"]["tools"] == {"listChanged": False}
    session_id = response.headers.get("Mcp-Session-Id")
    assert session_id

    initialized = client.post(
        path,
        headers=_headers(session_id),
        json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        },
    )
    assert initialized.status_code in {200, 202}
    return session_id


def test_streamable_http_initialize_list_and_call_through_fastapi_route() -> None:
    seen_contexts: list[MCPToolCallContext] = []
    transport = _transport(seen_contexts)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        async with transport.lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(transport.route("/mcp"))

    with TestClient(app, base_url="http://127.0.0.1:8000", follow_redirects=False) as client:
        session_id = _initialize(client, "/mcp")

        listed = client.post(
            "/mcp",
            headers=_headers(session_id),
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert listed.status_code == 200, listed.text
        tools = listed.json()["result"]["tools"]
        assert [tool["name"] for tool in tools] == ["debug_echo"]
        assert tools[0]["inputSchema"]["required"] == ["message"]
        assert tools[0]["annotations"]["readOnlyHint"] is True

        called = client.post(
            "/mcp",
            headers=_headers(session_id),
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "debug_echo", "arguments": {"message": "hello"}},
            },
        )
        assert called.status_code == 200, called.text
        result = called.json()["result"]
        assert result["isError"] is False
        assert result["structuredContent"] == {
            "message": "hello",
            "subject": "USR-mcp-test",
        }

    assert len(seen_contexts) == 1
    context = seen_contexts[0]
    assert context.principal.subject == "USR-mcp-test"
    assert context.principal.client_id == "codex-cli"
    assert context.principal.claims == {"role": "ENGINEER"}
    assert context.request_id == 3
    assert context.protocol_version == PROTOCOL_VERSION


def test_streamable_http_rejects_missing_or_invalid_bearer_token() -> None:
    transport = _transport([])
    with TestClient(transport.app, base_url="http://127.0.0.1:8000") as client:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "transport-test", "version": "1.0"},
            },
        }
        missing = client.post(
            "/",
            headers={"Accept": "application/json, text/event-stream"},
            json=request,
        )
        invalid = client.post(
            "/",
            headers={
                "Authorization": "Bearer wrong-token",
                "Accept": "application/json, text/event-stream",
            },
            json=request,
        )

    for response in (missing, invalid):
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_token"
        assert response.headers["WWW-Authenticate"].startswith("Bearer ")


def test_tool_argument_errors_are_safe_mcp_results() -> None:
    transport = _transport([])
    with TestClient(transport.app, base_url="http://127.0.0.1:8000") as client:
        session_id = _initialize(client, "/")
        response = client.post(
            "/",
            headers=_headers(session_id),
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "debug_echo", "arguments": {}},
            },
        )

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "Invalid arguments for debug_echo" in result["content"][0]["text"]
