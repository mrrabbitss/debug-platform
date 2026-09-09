"""The exposed MCP transport uses the same knowledge permission policy as REST."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.transport_security import TransportSecuritySettings

from app.mcp.auth import create_debugplatform_mcp_auth_resolver
from app.mcp.contracts import MCPPrincipal, MCPToolCallContext, MCPToolError
from app.mcp.debugplatform_registry import DEBUGPLATFORM_MCP_TOOL_NAMES, create_debugplatform_mcp_registry
from app.mcp.transport import create_mcp_http_transport
from app.models import KnowledgeDocument
from app.services.knowledge_compiler import digest
from tests.test_workbench_access import access_env  # noqa: F401


def _context(role="ENGINEER", subject="USR-engineer"):
    return MCPToolCallContext(principal=MCPPrincipal(subject=subject, claims={"role": role}),
        request_id="workbench-access", protocol_version="2025-11-25")


def _call(registry, name, arguments, role="ENGINEER"):
    return asyncio.run(registry.call_tool(name, arguments, _context(role)))


def _routing_arguments():
    return {
        "debug_get_knowledge_routing_context": {"document_ids": ["DOC-draft"], "consent_host_model_data": True},
        "debug_apply_knowledge_routing": {"decisions": [{"document_id": "DOC-draft", "expected_lock_version": 1,
            "content_sha256": "a" * 64, "category_id": "CAT-synthetic", "confidence": 1.0,
            "rationale": "Synthetic permission probe"}], "confirm_draft_update": True},
    }


def _sections_arguments(factory, document_id):
    with factory() as db:
        content = db.get(KnowledgeDocument, document_id).content
    return {"document_id": document_id, "content_sha256": digest(content), "consent_host_model_data": True}


def test_mcp_role_gates_run_before_any_routing_domain_side_effect(access_env, monkeypatch):
    observed = []
    monkeypatch.setattr("app.mcp.debugplatform_registry.knowledge_routing_context",
        lambda *args, **kwargs: observed.append("context") or {"documents": []})
    monkeypatch.setattr("app.mcp.debugplatform_registry.apply_knowledge_routing",
        lambda *args, **kwargs: observed.append("apply") or [])
    registry = create_debugplatform_mcp_registry(session_factory=access_env.factory)
    for name, arguments in _routing_arguments().items():
        for role in ("ENGINEER", "VIEWER", "invalid"):
            with pytest.raises(MCPToolError, match="Only administrators"):
                _call(registry, name, arguments, role)
        assert observed == []
    for name, arguments in _routing_arguments().items():
        result = _call(registry, name, arguments, "ADMIN")
        assert result["backend_chat_calls"] == 0
    assert observed == ["context", "apply"]


def test_mcp_published_sections_are_readable_but_drafts_are_not(access_env):
    registry = create_debugplatform_mcp_registry(session_factory=access_env.factory)
    for role in ("ENGINEER", "VIEWER"):
        result = _call(registry, "debug_read_knowledge_sections", _sections_arguments(access_env.factory, "DOC-published"), role)
        assert result["document_id"] == "DOC-published" and result["backend_chat_calls"] == 0
        assert "Synthetic published content" in str(result)
        for document_id in ("DOC-draft", "DOC-restricted", "DOC-archived"):
            with pytest.raises(MCPToolError, match="No access"):
                _call(registry, "debug_read_knowledge_sections", _sections_arguments(access_env.factory, document_id), role)
    result = _call(registry, "debug_read_knowledge_sections", _sections_arguments(access_env.factory, "DOC-draft"), "ADMIN")
    assert result["document_id"] == "DOC-draft"


def test_exposed_mcp_bearer_transport_enforces_admin_only_management(access_env):
    registry = create_debugplatform_mcp_registry(session_factory=access_env.factory)
    transport = create_mcp_http_transport(registry,
        create_debugplatform_mcp_auth_resolver(access_env.settings, access_env.factory), stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=["testserver"], allowed_origins=["https://testserver"]))

    @asynccontextmanager
    async def lifespan(_app):
        async with transport.lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(transport.route("/mcp"))
    with TestClient(app, base_url="https://testserver") as client:
        def rpc(user, method, params):
            response = client.post("/mcp", headers={
                "Authorization": "Bearer " + access_env.headers[user]["X-API-Key"],
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            }, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            assert response.status_code == 200, response.text
            return response.json()["result"]

        listed = rpc("engineer", "tools/list", {})
        assert {tool["name"] for tool in listed["tools"]} == set(DEBUGPLATFORM_MCP_TOOL_NAMES)
        # Advertised tool names and client-supplied document IDs cannot bypass roles.
        for user in ("engineer", "viewer"):
            for name, arguments in _routing_arguments().items():
                result = rpc(user, "tools/call", {"name": name, "arguments": arguments})
                assert result["isError"] is True
                assert "Only administrators" in result["content"][0]["text"]
            result = rpc(user, "tools/call", {"name": "debug_read_knowledge_sections",
                "arguments": _sections_arguments(access_env.factory, "DOC-published")})
            assert result.get("isError", False) is False
            result = rpc(user, "tools/call", {"name": "debug_read_knowledge_sections",
                "arguments": _sections_arguments(access_env.factory, "DOC-draft")})
            assert result["isError"] is True
        result = rpc("admin", "tools/call", {"name": "debug_read_knowledge_sections",
            "arguments": _sections_arguments(access_env.factory, "DOC-draft")})
        assert result.get("isError", False) is False
        result = rpc("engineer", "tools/call", {"name": "debug_apply_knowledge_routing",
            "arguments": {**_routing_arguments()["debug_apply_knowledge_routing"], "role": "ADMIN"}})
        assert result["isError"] is True
