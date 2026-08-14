import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_loads
from app.models import AuditEvent
from app.services import audit, llm


def _session_factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_audit_details_redact_secrets_and_never_store_content(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _session_factory(tmp_path)
    monkeypatch.setattr(audit, "SessionLocal", session_factory)

    audit.record_audit_event(
        "model.egress",
        details={
            "api_key": "sk-should-never-be-stored",
            "authorization": "Bearer secret",
            "request_chars": 1234,
            "content_recorded": False,
        },
    )

    with session_factory() as db:
        row = db.scalar(select(AuditEvent))
        assert row is not None
        details = json_loads(row.details_json, {})
    assert details["api_key"] == "[REDACTED]"
    assert details["authorization"] == "[REDACTED]"
    assert details["request_chars"] == 1234
    assert details["content_recorded"] is False
    assert "sk-should-never-be-stored" not in row.details_json
    engine.dispose()


def test_audit_middleware_records_mutation_metadata_without_body(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _session_factory(tmp_path)
    monkeypatch.setattr(audit, "SessionLocal", session_factory)
    app = FastAPI()
    app.add_middleware(audit.AuditMiddleware)

    @app.post("/cases/{case_id}")
    def mutate(case_id: str) -> dict:
        return {"case_id": case_id}

    with TestClient(app) as client:
        response = client.post("/cases/CASE-audit?mode=test", json={"secret": "not audited"})
    assert response.status_code == 200

    with session_factory() as db:
        row = db.scalar(select(AuditEvent))
        assert row is not None
        details = json_loads(row.details_json, {})
    assert row.action == "http.mutate"
    assert row.case_id == "CASE-audit"
    assert details["query_parameter_names"] == ["mode"]
    assert details["body_recorded"] is False
    assert "not audited" not in row.details_json
    engine.dispose()


def test_invalid_model_json_is_audited_as_failed(monkeypatch) -> None:
    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
            )

    provider = object.__new__(llm.OpenAICompatibleProvider)
    provider.model_name = "test-model"
    provider.temperature = 0.0
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    captured: list[dict] = []
    monkeypatch.setattr(provider, "_record_egress", lambda **details: captured.append(details))

    with pytest.raises(llm.LLMError, match="invalid JSON"):
        asyncio.run(provider.generate_json("system", "user"))

    assert len(captured) == 1
    assert captured[0]["outcome"] == "FAILED"
    assert captured[0]["error_type"] == "JSONDecodeError"
    assert captured[0]["response"].usage.total_tokens == 6


def test_json_generation_requests_json_object_and_tracks_usage(monkeypatch) -> None:
    captured_request: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured_request.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18),
            )

    provider = object.__new__(llm.OpenAICompatibleProvider)
    provider.model_name = "glm-5.2"
    provider.temperature = 0.1
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(llm, "record_model_egress", lambda *args, **kwargs: None)

    result = asyncio.run(provider.generate_json("system", "user", schema_name="log_triage_plan"))

    assert result == {"ok": True}
    assert captured_request["response_format"] == {"type": "json_object"}
    assert "log_triage_plan" in captured_request["messages"][0]["content"]
    assert "不是 JSON 外层字段" in captured_request["messages"][0]["content"]
    assert provider.last_usage == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


@pytest.mark.parametrize(
    ("thinking_mode", "expected_extra_body"),
    [
        ("enabled", {"thinking": {"type": "enabled"}}),
        ("disabled", {"thinking": {"type": "disabled"}}),
        ("inherit", None),
    ],
)
def test_json_generation_sends_explicit_thinking_mode(
    monkeypatch,
    thinking_mode: str,
    expected_extra_body: dict | None,
) -> None:
    captured_request: dict = {}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured_request.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content='{"ok":true}'),
                    finish_reason="stop",
                )],
                usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
            )

    provider = object.__new__(llm.OpenAICompatibleProvider)
    provider.model_name = "glm-5.2"
    provider.temperature = 0.1
    provider.thinking_mode = thinking_mode
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(llm, "record_model_egress", lambda *args, **kwargs: None)

    asyncio.run(provider.generate_json("system", "user"))

    if expected_extra_body is None:
        assert "extra_body" not in captured_request
    else:
        assert captured_request["extra_body"] == expected_extra_body
    assert provider.last_finish_reason == "stop"


def test_thinking_mode_keeps_legacy_profiles_compatible() -> None:
    assert llm._thinking_mode({}) == "inherit"
    assert llm._thinking_mode({"thinking_enabled": True}) == "enabled"
    assert llm._thinking_mode({"thinking_enabled": False}) == "disabled"
    assert llm._thinking_mode({"thinking_mode": "disabled"}) == "disabled"


def test_json_generation_falls_back_when_gateway_rejects_json_mode(monkeypatch) -> None:
    requests: list[dict] = []

    class FakeCompletions:
        async def create(self, **kwargs):
            requests.append(kwargs)
            if "response_format" in kwargs:
                raise RuntimeError("response_format is not supported")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=None),
            )

    provider = object.__new__(llm.OpenAICompatibleProvider)
    provider.model_name = "legacy-compatible-model"
    provider.temperature = 0.1
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(llm, "record_model_egress", lambda *args, **kwargs: None)

    result = asyncio.run(provider.generate_json("system", "user"))

    assert result == {"ok": True}
    assert len(requests) == 2
    assert requests[0]["response_format"] == {"type": "json_object"}
    assert "response_format" not in requests[1]
    assert provider.last_usage["total_tokens"] == 5


def test_json_generation_unwraps_schema_name_envelope(monkeypatch) -> None:
    class FakeCompletions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(
                    content='{"diagnostic_planning_round":{"round":1}}',
                ))],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8),
            )

    provider = object.__new__(llm.OpenAICompatibleProvider)
    provider.model_name = "glm-5.2"
    provider.temperature = 0.1
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(llm, "record_model_egress", lambda *args, **kwargs: None)

    result = asyncio.run(provider.generate_json(
        "system", "user", schema_name="diagnostic_planning_round",
    ))

    assert result == {"round": 1}
    assert provider.last_usage["total_tokens"] == 8
