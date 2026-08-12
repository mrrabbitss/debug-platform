from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_loads
from app.models import AnalysisRun, Artifact, Case, LogEvent
from app.services import diagnosis


class _JobContext:
    def update(self, progress: int, message: str) -> None:
        pass

    def complete_in_transaction(self, db, result, message: str = "Completed") -> None:
        pass

    def raise_if_cancelled(self) -> None:
        pass


def test_analysis_records_safe_model_configuration_snapshot(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'model-snapshot.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        db.add(Case(id="CASE-snapshot", title="snapshot", description=""))
        db.commit()

    monkeypatch.setattr(diagnosis, "SessionLocal", session_factory)
    monkeypatch.setattr(diagnosis, "agentic_search", lambda *args, **kwargs: {
        "results": [],
        "plan": {"selected_modules": [], "algorithms": [], "rationale": []},
        "trace": [],
        "paths": [],
        "summary": {},
    })
    monkeypatch.setattr(diagnosis, "_find_related_symbols", lambda case_id, events: [])
    monkeypatch.setattr(diagnosis, "get_active_chat_model_info", lambda: {
        "profile_id": "MODEL-qwen",
        "profile_name": "Qwen production",
        "provider": "openai_compatible",
        "model": "qwen-plus",
        "mode": "api",
        "base_url": "https://model.example.com/v1",
        "config": {"temperature": 0.1, "timeout_seconds": 60},
        "is_mock": False,
    })

    async def keep_deterministic_result(case, result, evidence):
        return result

    monkeypatch.setattr(diagnosis, "_augment_with_llm", keep_deterministic_result)
    diagnosis._analyze_case_impl(_JobContext(), "CASE-snapshot")

    with session_factory() as db:
        run = db.scalar(select(AnalysisRun).where(AnalysisRun.case_id == "CASE-snapshot"))
        assert run is not None
        assert run.status == "COMPLETED"
        assert run.model_profile_id == "MODEL-qwen"
        assert run.prompt_version == "v3-multiround-evidence"
        snapshot = json_loads(run.model_config_json, {})
        assert snapshot["profile_name"] == "Qwen production"
        assert snapshot["base_url"] == "https://model.example.com/v1"
        assert snapshot["config"]["timeout_seconds"] == 60
        assert "api_key" not in run.model_config_json.lower()

    engine.dispose()


def test_joint_diagnosis_collects_events_from_each_active_artifact(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'joint-events.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        case = Case(id="CASE-joint-events", title="joint", device_type="AP")
        gw = Artifact(
            id="ART-gw", case_id=case.id, original_name="gw.log",
            stored_path="gw.log", sha256="a" * 64, size_bytes=10,
            status="PARSED", active_parse_run_id="PRUN-gw",
            source_device_type="GW", source_device_role="PRIMARY",
        )
        ap = Artifact(
            id="ART-ap", case_id=case.id, original_name="ap.log",
            stored_path="ap.log", sha256="b" * 64, size_bytes=10,
            status="PARSED", active_parse_run_id="PRUN-ap",
            source_device_type="AP", source_device_role="SECONDARY",
        )
        db.add_all([case, gw, ap])
        for artifact, parse_run, prefix, count in (
            (gw, "PRUN-gw", "GW", 80), (ap, "PRUN-ap", "AP", 3),
        ):
            for index in range(count):
                db.add(LogEvent(
                    id=f"EVT-{prefix}-{index}", case_id=case.id,
                    artifact_id=artifact.id, parse_run_id=parse_run,
                    source_file=artifact.original_name, line_start=index + 1,
                    line_end=index + 1, level="ERROR", module="WLAN",
                    component=prefix, event_code=f"{prefix}_EVENT",
                    message=f"{prefix} event {index}", raw_text=f"{prefix} event {index}",
                    confidence=1.0,
                ))
        db.commit()

    captured: dict[str, object] = {}
    monkeypatch.setattr(diagnosis, "SessionLocal", session_factory)
    monkeypatch.setattr(diagnosis, "agentic_search", lambda *args, **kwargs: {
        "results": [], "plan": {}, "trace": [], "paths": [], "summary": {},
    })
    monkeypatch.setattr(diagnosis, "run_diagnostic_planning", lambda *args, **kwargs: type(
        "Planning", (), {"public_plan": {}, "method_documents": [], "evidence": [], "supplemental_results": []}
    )())
    monkeypatch.setattr(diagnosis, "_find_related_symbols", lambda case_id, events: [])
    monkeypatch.setattr(diagnosis, "extract_memories_from_analysis", lambda *args, **kwargs: None)
    monkeypatch.setattr(diagnosis, "get_active_chat_model_info", lambda: {
        "profile_id": "mock", "provider": "mock", "model": "rule-engine",
        "is_mock": True,
    })

    async def capture(case, result, evidence):
        captured["evidence"] = evidence
        return result

    monkeypatch.setattr(diagnosis, "_augment_with_llm", capture)
    diagnosis._analyze_case_impl(_JobContext(), "CASE-joint-events")

    log_evidence = [
        item for item in captured["evidence"]
        if item.get("source_type") == "log_event"
    ]
    assert {item["artifact_source"]["device_type"] for item in log_evidence} == {"GW", "AP"}
    assert any(item["artifact_source"]["device_role"] == "PRIMARY" for item in log_evidence)
    assert any(item["artifact_source"]["device_role"] == "SECONDARY" for item in log_evidence)
    engine.dispose()
