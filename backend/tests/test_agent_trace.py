from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api import agent_runs
from app.core.db import Base, configure_sqlite_engine, get_db
from app.models import AgentRun, AgentTraceEvent, Case
from app.services.agent_trace import (
    agent_run_to_dict,
    record_agent_run,
    sanitize_model_config,
    sanitize_replay_payload,
)


def _factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'agent-trace.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _app(factory, *, role: str = "ADMIN") -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def set_principal(request: Request, call_next):
        request.state.principal = {
            "id": "USER-test",
            "username": "test",
            "role": role,
            "type": "user_token",
        }
        return await call_next(request)

    def override_db():
        with factory() as db:
            yield db

    app.include_router(agent_runs.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    return app


def test_agent_trace_persists_only_hashes_and_allowlisted_metadata(tmp_path: Path) -> None:
    engine, factory = _factory(tmp_path)
    with factory() as db:
        db.add(Case(id="CASE-trace", title="Trace", device_type="AP"))
        db.commit()
        run = record_agent_run(
            db,
            case_id="CASE-trace",
            operation="agentic_search",
            execution_mode="deterministic",
            input_summary={"query": "AUTH timeout", "raw_log": "must not persist"},
            output_summary={"returned": 1},
            events=[{
                "stage": "knowledge",
                "tool_name": "knowledge.search",
                "status": "COMPLETED",
                "candidate_count": 1,
                "duration_ms": 12,
                "evidence_ids": ["KCHUNK-safe"],
                "raw_content": "must not persist",
            }],
            evidence_ids=["KCHUNK-safe"],
            stop_reason="COMPLETED",
            approval_status="READ_ONLY_AUTO",
            duration_ms=15,
            model_config={
                "provider": "openai_compatible",
                "api_key": "company-secret",
                "base_url": "https://internal.invalid/v1",
                "temperature": 0,
            },
            replay_payload={
                "case_id": "CASE-trace",
                "query": "AUTH timeout",
                "top_k": 5,
                "modules": ["knowledge"],
            },
        )
        payload = agent_run_to_dict(db, run, include_events=True)

        assert len(payload["input_summary_hash"]) == 64
        assert payload["model_config"] == {
            "provider": "openai_compatible",
            "temperature": 0,
        }
        assert payload["replay_payload"]["query"] == "AUTH timeout"
        assert payload["events"][0]["metadata"] == {"candidate_count": 1}
        rendered = str(payload)
        assert "must not persist" not in rendered
        assert "company-secret" not in rendered
        assert "internal.invalid" not in rendered
        assert db.scalar(select(AgentTraceEvent).where(
            AgentTraceEvent.run_id == run.id
        )) is not None
    engine.dispose()


def test_unsafe_replay_text_is_hash_only_and_model_secrets_are_removed() -> None:
    replay = sanitize_replay_payload({
        "query": "company log line\n" * 20,
        "modules": ["knowledge"],
    })
    assert "query" not in replay
    assert len(replay["query_hash"]) == 64
    assert replay["query_omitted_reason"]
    assert sanitize_model_config({
        "token_budget": 10,
        "password": "bad",
        "nested": {"api_key": "bad", "mode": "local"},
    }) == {"nested": {"mode": "local"}}


def test_admin_can_inspect_and_read_only_replay_agent_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    monkeypatch.setattr(agent_runs, "record_audit_event", lambda *args, **kwargs: None)
    with factory() as db:
        db.add(Case(id="CASE-replay", title="Replay", device_type="AP"))
        db.commit()
        original = record_agent_run(
            db,
            case_id="CASE-replay",
            operation="agentic_search",
            execution_mode="deterministic",
            input_summary={"case_id": "CASE-replay", "query": "AUTH timeout"},
            output_summary={"returned": 0},
            events=[{"stage": "knowledge", "status": "COMPLETED"}],
            evidence_ids=[],
            stop_reason="NO_RESULTS",
            approval_status="READ_ONLY_AUTO",
            duration_ms=1,
            replay_payload={
                "case_id": "CASE-replay",
                "query": "AUTH timeout",
                "top_k": 3,
                "max_hops": 1,
                "modules": ["knowledge"],
            },
        )

    with TestClient(_app(factory)) as client:
        listed = client.get("/api/v1/agent-runs")
        assert listed.status_code == 200
        assert listed.json()[0]["run_id"] == original.id
        detail = client.get(f"/api/v1/agent-runs/{original.id}")
        assert detail.status_code == 200
        assert detail.json()["events"][0]["stage"] == "knowledge"
        replayed = client.post(f"/api/v1/agent-runs/{original.id}/replay")
        assert replayed.status_code == 200, replayed.text
        assert replayed.json()["run_id"] != original.id

    with factory() as db:
        latest = db.scalar(select(AgentRun).where(
            AgentRun.replay_of_run_id == original.id
        ))
        assert latest is not None
        assert latest.execution_mode == "replay"
        assert latest.approval_status == "READ_ONLY_AUTO"

    with TestClient(_app(factory, role="VIEWER")) as client:
        assert client.get("/api/v1/agent-runs").status_code == 403
    engine.dispose()
