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
