from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.models import Artifact, Case, LogEvent
from app.services import host_diagnostic_runtime


def _database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'host-diagnostic.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def test_host_snapshot_and_log_tool_do_not_resolve_backend_chat_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    with factory() as db:
        case = Case(
            id="CASE-host-runtime",
            title="AP heartbeat timeout",
            description="AP repeatedly becomes offline",
            device_type="AP",
        )
        artifact = Artifact(
            id="ART-host-runtime",
            case_id=case.id,
            kind="debug_log",
            original_name="collectDebuginfo.txt",
            stored_path="artifacts/host-runtime/collectDebuginfo.txt",
            sha256="a" * 64,
            size_bytes=100,
            status="PARSED",
            active_parse_run_id="PARSE-host-runtime",
        )
        event = LogEvent(
            id="EVT-host-runtime",
            case_id=case.id,
            artifact_id=artifact.id,
            parse_run_id="PARSE-host-runtime",
            source_file="collectDebuginfo.txt",
            line_start=42,
            line_end=42,
            level="ERROR",
            module="topology",
            component="ap_manager",
            event_code="AP_OFFLINE",
            message="AP heartbeat timeout token=secret-value",
            raw_text="AP heartbeat timeout token=secret-value",
            confidence=0.95,
        )
        db.add_all([case, artifact, event])
        db.commit()

    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [],
    )
    monkeypatch.setattr(
        "app.services.llm.get_llm_provider",
        lambda: (_ for _ in ()).throw(AssertionError("backend Chat provider was resolved")),
    )

    snapshot = host_diagnostic_runtime.load_host_diagnostic_snapshot(
        "CASE-host-runtime",
        session_factory=factory,
    )
    context = host_diagnostic_runtime.host_diagnostic_context(snapshot)
    assert context["inference_owner"] == "host_cli"
    assert context["backend_chat_allowed"] is False
    assert context["parse_generations"] == {
        "ART-host-runtime": "PARSE-host-runtime",
    }
    assert context["initial_evidence"][0]["evidence_id"] == "EVT-host-runtime"
    assert "secret-value" not in context["initial_evidence"][0]["content"]
    assert context["schemas"]["diagnosis"]["type"] == "object"

    invocation = host_diagnostic_runtime.invoke_host_diagnostic_tool(
        snapshot,
        tool_name="search_log",
        arguments={
            "keywords": ["heartbeat timeout"],
            "top_k": 10,
        },
        session_factory=factory,
    )
    assert invocation["evidence_ids"] == ["EVT-host-runtime"]
    assert invocation["output"]["returned"] == 1
    engine.dispose()


def test_host_get_evidence_uses_only_explicit_cross_call_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    with factory() as db:
        db.add(Case(
            id="CASE-host-cache",
            title="Cached evidence",
            description="",
        ))
        db.commit()
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [],
    )
    snapshot = host_diagnostic_runtime.load_host_diagnostic_snapshot(
        "CASE-host-cache",
        session_factory=factory,
    )
    cached = {
        "evidence_id": "KE-host-cache",
        "source_type": "knowledge_chunk",
        "title": "Known method",
        "content": "Check the AP heartbeat interval.",
    }
    invocation = host_diagnostic_runtime.invoke_host_diagnostic_tool(
        snapshot,
        tool_name="get_evidence",
        arguments={"evidence_ids": ["KE-host-cache", "KE-unknown"]},
        prior_evidence=[cached],
        session_factory=factory,
    )
    assert invocation["evidence_ids"] == ["KE-host-cache"]
    assert invocation["output"]["returned"] == 1
    engine.dispose()
