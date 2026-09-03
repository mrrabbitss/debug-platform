from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api import demo_cases as demo_api
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, json_loads
from app.diagnostic_models import (
    LogEvidenceHit,
    LogEvidenceMatch,
    LogEvidenceOccurrence,
    LogTriageRun,
)
from app.models import (
    AgentRun,
    AgentTraceEvent,
    AnalysisRun,
    Artifact,
    Case,
    LogEvent,
    UserAccount,
)
from app.services import demo_cases, report
from app.services.demo_case_contract import (
    DEMO_FIXTURES,
    load_demo_fixtures,
    load_demo_snapshot,
)
from app.services.storage import StorageService


def _factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'demo.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _principal() -> dict[str, str]:
    return {
        "id": "USER-demo-test",
        "username": "demo-test",
        "role": "ADMIN",
        "type": "user_token",
    }


def _seed_user(factory) -> None:
    with factory() as db:
        db.add(UserAccount(
            id=_principal()["id"],
            username="demo-test",
            display_name="Demo Test",
            role="ADMIN",
        ))
        db.commit()


def test_demo_fixtures_are_integrity_pinned_and_synthetic() -> None:
    attributes = (Path(__file__).resolve().parents[2] / ".gitattributes").read_text(encoding="utf-8")
    assert "sample_data/demo_ap_frequent_offline/** -text" in attributes
    loaded = load_demo_fixtures()
    assert len(loaded) == len(DEMO_FIXTURES) == 2
    combined = "\n".join(text for _, _, _, text in loaded)
    assert "DEMO-" in combined
    assert "SYNTHETIC-" in combined
    assert "192.0.2." in combined
    assert not re.search(r"\b10\.(?:\d{1,3}\.){2}\d{1,3}\b", combined)
    assert not re.search(r"\b172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3}\b", combined)
    assert "API_KEY" not in combined
    snapshot = load_demo_snapshot()
    assert snapshot["provenance"]["kind"] == "recorded_real_model_run"
    assert snapshot["provenance"]["endpoint_origin"] == "https://wawapii.com"
    assert snapshot["provenance"]["model"] == "glm-5.2"
    assert snapshot["provenance"]["sanitization"] == {
        "credentials_omitted": True,
        "database_primary_keys_rebased_on_import": True,
        "memory_bodies_omitted": True,
        "private_method_bodies_omitted": True,
        "private_method_example_identifiers_replaced": True,
        "raw_prompts_omitted": True,
    }
    assert len(snapshot["source_evidence"]) == 152
    assert snapshot["analysis"]["agent_run"]["usage"] == {
        "duration_ms": 146082,
        "estimated_cost": 0.0,
        "input_tokens": 305655,
        "output_tokens": 15798,
        "retry_count": 0,
        "total_tokens": 321453,
    }


def test_demo_import_is_idempotent_browsable_and_evidence_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    _seed_user(factory)
    managed_storage = StorageService(tmp_path / "storage")
    monkeypatch.setattr(demo_cases, "storage", managed_storage)
    monkeypatch.setattr(report, "SessionLocal", factory)
    monkeypatch.setattr(report, "storage", managed_storage)

    with factory() as db:
        created = demo_cases.import_ap_frequent_offline_demo(
            db, principal=_principal(),
        )
        case_id = created["case"].id
        analysis_id = created["analysis_run_id"]
        assert created == {
            **created,
            "created": True,
            "restored": False,
            "artifact_count": 2,
            "event_count": 74,
            "triage_run_count": 2,
            "demo_snapshot": True,
            "model_called": False,
            "recorded_model_run": True,
            "recorded_model": "glm-5.2",
            "recorded_total_tokens": 321453,
        }

    with factory() as db:
        existing = demo_cases.import_ap_frequent_offline_demo(
            db, principal=_principal(),
        )
        assert existing["created"] is False
        assert existing["case"].id == case_id
        assert existing["analysis_run_id"] == analysis_id
        assert int(db.scalar(select(func.count(Case.id))) or 0) == 1
        assert int(db.scalar(select(func.count(Artifact.id))) or 0) == 2
        assert int(db.scalar(select(func.count(LogEvent.id))) or 0) == 74
        assert int(db.scalar(select(func.count(LogTriageRun.id))) or 0) == 2
        assert int(db.scalar(select(func.count(AnalysisRun.id))) or 0) == 1

        case = db.get(Case, case_id)
        assert case is not None
        assert case.model_egress_approved is False
        assert case.owner_id == _principal()["id"]
        assert "SYNTHETIC DEMO" in case.description

        artifacts = list(db.scalars(
            select(Artifact).where(Artifact.case_id == case_id)
        ).all())
        assert {
            (artifact.source_device_type, artifact.source_device_role)
            for artifact in artifacts
        } == {("GW", "PRIMARY"), ("AP", "SECONDARY")}
        for artifact in artifacts:
            assert artifact.status == "PARSED"
            metadata = json_loads(artifact.metadata_json, {})
            assert metadata["demo_fixture"]["synthetic"] is True
            assert metadata["demo_fixture"]["model_egress"] is False
            assert metadata["demo_fixture"]["recorded_model_run"] is True
            assert metadata["demo_fixture"]["snapshot_import_model_egress"] is False
            assert metadata["manifest_file_count"] == 1
            source = managed_storage.resolve_path(artifact.stored_path)
            extracted = managed_storage.resolve_path(metadata["extract_root"])
            assert source.is_file()
            assert (extracted / artifact.original_name).is_file()

        triages = list(db.scalars(
            select(LogTriageRun).where(LogTriageRun.case_id == case_id)
        ).all())
        for triage in triages:
            plan = json_loads(triage.plan_json, {})
            summary = json_loads(triage.summary_json, {})
            coverage = json_loads(triage.method_coverage_json, {})
            assert triage.status == "COMPLETED"
            assert plan["demo_snapshot"] is True
            assert plan["recorded_model_run"] is True
            assert plan["snapshot_import_model_called"] is False
            assert summary["demo_snapshot"] is True
            assert summary["recorded_model_run"] is True
            assert summary["planner_fallback"] is False
            assert summary["planner_status"] == "ACCEPTED"
            assert summary["planner_thinking_mode"] == "disabled"
            assert summary["exact_hit_count"] > summary["matched_events"] > 0
            assert summary["other_events"] > 0
            assert coverage["all_required_read"] is True
            assert coverage["document_count"] == 2
            bucket_counts = dict(db.execute(
                select(LogEvidenceMatch.bucket, func.count(LogEvidenceMatch.id))
                .where(LogEvidenceMatch.triage_run_id == triage.id)
                .group_by(LogEvidenceMatch.bucket)
            ).all())
            assert bucket_counts["LLM_RELEVANT"] > 0
            assert bucket_counts["METHOD_REQUIRED"] > 0
            assert sum(bucket_counts.values()) == summary["recorded_match_group_count"]
            assert int(db.scalar(select(func.count(LogEvidenceOccurrence.id)).where(
                LogEvidenceOccurrence.triage_run_id == triage.id,
            )) or 0) == summary["matched_events"]
            repeated = db.scalars(
                select(LogEvidenceMatch).where(
                    LogEvidenceMatch.triage_run_id == triage.id,
                    LogEvidenceMatch.bucket == "LLM_RELEVANT",
                    LogEvidenceMatch.occurrence_count > 1,
                ).limit(1)
            ).first()
            assert repeated is not None
            hits = list(db.scalars(
                select(LogEvidenceHit).where(LogEvidenceHit.match_id == repeated.id)
            ).all())
            assert len(hits) == repeated.occurrence_count
            assert all(hit.line_start > 0 for hit in hits)

        analysis = db.get(AnalysisRun, analysis_id)
        assert analysis is not None
        assert analysis.provider == "openai_compatible"
        assert analysis.model == "glm-5.2"
        result = json.loads(analysis.result_json)
        evidence = json.loads(analysis.evidence_json)
        assert result["demo_snapshot"] is True
        assert result["recorded_model_run"] is True
        assert result["synthesis_status"]["mode"] == "LLM_EVIDENCE_VALIDATED"
        assert result["synthesis_status"]["accepted"] is True
        assert result["diagnostic_planning"]["planner_mode"] == "llm_multiround"
        assert len(result["diagnostic_planning"]["rounds"]) == 2
        assert result["diagnostic_planning"]["recorded_model_run"] is True
        assert result["recorded_run_provenance"]["endpoint_origin"] == "https://wawapii.com"
        coverage = result["diagnostic_planning"]["fault_tree_coverage"]
        assert coverage["complete"] is True
        assert coverage["attempted"] == coverage["concluded"] == coverage["total"] == 27
        assert coverage["status_counts"] == {
            "PENDING": 0,
            "SUPPORTED": 21,
            "EXCLUDED": 1,
            "INSUFFICIENT_EVIDENCE": 5,
        }
        roots = {
            item["label"]: item["status"]
            for item in coverage["items"] if item["category"] == "ROOT_CAUSE"
        }
        assert roots == {
            "场景1": "EXCLUDED",
            "场景2": "SUPPORTED",
            "场景3": "INSUFFICIENT_EVIDENCE",
        }
        assert {item["event_code"] for item in result["hypotheses"]} == {
            "AP_UDM_PROCESS_ABNORMAL",
            "AP_UDM_LISTEN_PORT_FAILED",
            "AP_UDM_HEARTBEAT_SEND_FAILED",
            "GW_AP_HEARTBEAT_TIMEOUT",
            None,
        }
        assert len(evidence) == 152
        known_evidence = {item["evidence_id"] for item in evidence}
        assert all(
            set(item["supporting_evidence"]).issubset(known_evidence)
            and set(item["contradicting_evidence"]).issubset(known_evidence)
            for item in result["hypotheses"]
        )
        assert all(
            set(item["evidence_ids"]).issubset(known_evidence)
            for item in result["fault_tree_conclusions"]
        )
        rendered_result = json.dumps(result, ensure_ascii=False)
        assert "DREF-" not in rendered_result
        assert "{{" not in rendered_result
        assert "evidence_hints" not in rendered_result
        agent_runs = list(db.scalars(
            select(AgentRun).where(AgentRun.case_id == case_id)
        ).all())
        assert len(agent_runs) == 3
        assert {run.operation for run in agent_runs} == {
            "log_triage_planning", "comprehensive_diagnosis",
        }
        assert all(run.execution_mode == "recorded_llm_snapshot" for run in agent_runs)
        assert {run.total_tokens for run in agent_runs} == {31073, 31363, 321453}
        assert {run.stop_reason for run in agent_runs} == {
            "ALL_METHODS_SCANNED",
            "DIAGNOSIS_CONVERGED_ROOT_CAUSE_IDENTIFIED",
        }
        analysis_agent = next(
            run for run in agent_runs if run.operation == "comprehensive_diagnosis"
        )
        traces = list(db.scalars(
            select(AgentTraceEvent)
            .where(AgentTraceEvent.run_id == analysis_agent.id)
            .order_by(AgentTraceEvent.sequence)
        ).all())
        assert len(traces) == 16
        assert [trace.stage for trace in traces].count("final_diagnostic_synthesis") == 1
        assert sum(trace.input_tokens for trace in traces) == 305655
        assert sum(trace.output_tokens for trace in traces) == 15798

    rendered = report.render_html(case_id, analysis_id)
    assert "GW_collectDebuginfo_demo.txt - 第" in rendered
    assert "AP_collectDebuginfo_demo.txt - 第" in rendered
    assert not re.search(r"\b(?:EVT|LDE)-[A-Za-z0-9_.:-]+\b", rendered)
    engine.dispose()


def test_demo_import_api_returns_the_same_persistent_case(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    _seed_user(factory)
    managed_storage = StorageService(tmp_path / "api-storage")
    monkeypatch.setattr(demo_cases, "storage", managed_storage)
    app = FastAPI()

    @app.middleware("http")
    async def set_principal(request: Request, call_next):
        request.state.principal = _principal()
        return await call_next(request)

    def override_db():
        with factory() as db:
            yield db

    app.include_router(demo_api.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        first = client.post("/api/v1/demo-cases/ap-frequent-offline")
        second = client.post("/api/v1/demo-cases/ap-frequent-offline")
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_payload = first.json()
    second_payload = second.json()
    assert first_payload["created"] is True
    assert first_payload["model_called"] is False
    assert first_payload["recorded_model_run"] is True
    assert first_payload["recorded_model"] == "glm-5.2"
    assert first_payload["recorded_total_tokens"] == 321453
    assert first_payload["case"]["model_egress_approved"] is False
    assert second_payload["created"] is False
    assert second_payload["case"]["id"] == first_payload["case"]["id"]
    engine.dispose()


def test_demo_reimport_preserves_newer_completed_host_cli_analysis(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _factory(tmp_path)
    _seed_user(factory)
    managed_storage = StorageService(tmp_path / "host-cli-storage")
    monkeypatch.setattr(demo_cases, "storage", managed_storage)

    with factory() as db:
        first = demo_cases.import_ap_frequent_offline_demo(
            db, principal=_principal(),
        )
        case_id = first["case"].id
        recorded_analysis_id = first["analysis_run_id"]
        recorded_analysis = db.get(AnalysisRun, recorded_analysis_id)
        assert recorded_analysis is not None
        newer_at = recorded_analysis.created_at + timedelta(seconds=1)
        host_analysis_id = "RUN-host-cli-newer"
        db.add(AnalysisRun(
            id=host_analysis_id,
            case_id=case_id,
            status="COMPLETED",
            provider="host_cli",
            model="gpt-5.6-luna",
            model_config_json=json_dumps({"execution_mode": "host_cli_mcp"}),
            prompt_version="host-cli-evidence-v1",
            result_json=json_dumps({
                "analysis_engine": "host_cli_mcp",
                "recorded_model_run": False,
            }),
            evidence_json="[]",
            created_at=newer_at,
            completed_at=newer_at,
        ))
        db.commit()

    with factory() as db:
        existing = demo_cases.import_ap_frequent_offline_demo(
            db, principal=_principal(),
        )
        assert existing["created"] is False
        assert existing["case"].id == case_id
        assert existing["analysis_run_id"] == recorded_analysis_id
        assert set(db.scalars(
            select(AnalysisRun.id).where(AnalysisRun.case_id == case_id)
        ).all()) == {recorded_analysis_id, host_analysis_id}
        assert int(db.scalar(select(func.count(Artifact.id)).where(
            Artifact.case_id == case_id,
        )) or 0) == 2

    engine.dispose()
