from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import PlainTextResponse
from starlette.routing import Route

import app.main as main_module
import app.mcp.integration as integration
import app.services.audit as audit_service
from app.core.config import Settings


class _DummySession:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback


def _disable_startup_side_effects(monkeypatch, events: list[str] | None = None) -> None:
    observed = events if events is not None else []
    monkeypatch.setattr(main_module.job_runner, "start", lambda: observed.append("jobs-start"))
    monkeypatch.setattr(
        main_module.job_runner,
        "resume_incomplete",
        lambda: observed.append("jobs-resume"),
    )
    monkeypatch.setattr(
        main_module.job_runner,
        "shutdown",
        lambda *, wait: observed.append(f"jobs-stop:{wait}"),
    )
    monkeypatch.setattr(main_module, "run_database_migrations", lambda: None)
    monkeypatch.setattr(main_module, "SessionLocal", _DummySession)
    monkeypatch.setattr(main_module, "seed_knowledge_categories", lambda db: None)
    monkeypatch.setattr(main_module, "seed_model_profiles", lambda db: None)
    monkeypatch.setattr(main_module, "seed_builtin_knowledge", lambda db, path: None)
    monkeypatch.setattr(main_module, "assign_uncategorized_documents", lambda db: None)
    monkeypatch.setattr(main_module, "ensure_builtin_embedding_index", lambda db: None)
    monkeypatch.setattr(audit_service, "record_audit_event", lambda *args, **kwargs: None)


def test_main_registers_exact_mcp_route_before_root_or_static_fallback() -> None:
    paths = [getattr(route, "path", None) for route in main_module.app.routes]
    assert paths.count("/mcp") == 1
    mcp_index = paths.index("/mcp")
    fallback_indexes = [
        index
        for index, path in enumerate(paths)
        if path in {"/", "/{full_path:path}"}
    ]
    assert all(mcp_index < index for index in fallback_indexes)


def test_disabled_mcp_configuration_does_not_install_a_route(monkeypatch) -> None:
    app = FastAPI()
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        data_root="./data",
        storage_root="./data/storage",
        mcp_enabled=False,
    )
    called = False

    def unexpected_factory(settings):
        del settings
        nonlocal called
        called = True
        raise AssertionError("disabled MCP must not create a transport")

    monkeypatch.setattr(
        integration,
        "create_debugplatform_mcp_transport",
        unexpected_factory,
    )
    result = integration.configure_debugplatform_mcp(app, settings)

    assert result is None
    assert called is False
    assert app.state.mcp_transport is None
    assert "/mcp" not in [getattr(route, "path", None) for route in app.routes]


def test_main_lifespan_starts_and_stops_mcp_session_manager(monkeypatch) -> None:
    events: list[str] = []
    _disable_startup_side_effects(monkeypatch, events)

    class FakeTransport:
        @asynccontextmanager
        async def lifespan(self):
            events.append("mcp-start")
            try:
                yield
            finally:
                events.append("mcp-stop")

    monkeypatch.setattr(main_module.app.state, "mcp_transport", FakeTransport())

    async def exercise() -> None:
        async with main_module.lifespan(main_module.app):
            events.append("app-ready")

    asyncio.run(exercise())

    assert events == [
        "jobs-start",
        "jobs-resume",
        "mcp-start",
        "app-ready",
        "mcp-stop",
        "jobs-stop:False",
    ]


def test_main_mcp_route_is_live_and_fail_closed_without_bearer(monkeypatch) -> None:
    _disable_startup_side_effects(monkeypatch)
    with TestClient(
        main_module.app,
        base_url="http://127.0.0.1:8000",
        follow_redirects=False,
    ) as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "main-route-test", "version": "1.0"},
                },
            },
        )

    assert response.status_code == 401
    assert response.history == []
    assert response.json()["error"] == "invalid_token"
    assert response.headers["WWW-Authenticate"].startswith("Bearer ")


def test_transport_route_factory_uses_exact_supported_methods(monkeypatch) -> None:
    app = FastAPI()
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        data_root="./data",
        storage_root="./data/storage",
        mcp_enabled=True,
    )

    class FakeTransport:
        def route(self, path: str) -> Route:
            async def endpoint(request):
                del request
                return PlainTextResponse("ok")

            return Route(path, endpoint=endpoint, methods=["GET", "POST", "DELETE"])

    transport = FakeTransport()
    monkeypatch.setattr(
        integration,
        "create_debugplatform_mcp_transport",
        lambda current: transport,
    )

    assert integration.configure_debugplatform_mcp(app, settings) is transport
    route = next(route for route in app.routes if getattr(route, "path", None) == "/mcp")
    assert route.methods == {"GET", "HEAD", "POST", "DELETE"}


def test_real_integration_accepts_static_bearer_and_keeps_backend_chat_disabled() -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///:memory:",
        data_root="./data",
        storage_root="./data/storage",
        mcp_enabled=True,
        mcp_bearer_token="integration-secret",
    )
    transport = integration.create_debugplatform_mcp_transport(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        del app
        async with transport.lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(transport.route("/mcp"))
    base_headers = {
        "Authorization": "Bearer integration-secret",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }

    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        initialized = client.post(
            "/mcp",
            headers=base_headers,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "integration-test", "version": "1.0"},
                },
            },
        )
        assert initialized.status_code == 200, initialized.text
        session_id = initialized.headers["Mcp-Session-Id"]
        session_headers = {
            **base_headers,
            "Mcp-Session-Id": session_id,
            "MCP-Protocol-Version": "2025-11-25",
        }
        notification = client.post(
            "/mcp",
            headers=session_headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        assert notification.status_code in {200, 202}
        called = client.post(
            "/mcp",
            headers=session_headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "debug_status", "arguments": {}},
            },
        )

    assert called.status_code == 200, called.text
    status = called.json()["result"]["structuredContent"]
    assert status["ready"] is True
    assert status["authenticated_role"] == "ADMIN"
    assert status["inference_owner"] == "host_cli"
    assert status["backend_chat_allowed"] is False
    assert status["backend_chat_calls"] == 0
