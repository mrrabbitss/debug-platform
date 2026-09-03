import json
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.models import AgentRun, AnalysisRun, Artifact, Case, LogEvent
from app.services import host_diagnosis, host_diagnostic_runtime
from app.services.diagnostic_methods import DiagnosticMethodDocument
from app.services.host_agent_session_contracts import (
    HostAgentSessionStatusTransition,
    HostAgentToolReceiptInput,
)
from app.services.host_agent_sessions import (
    hash_host_agent_tool_arguments,
    record_host_agent_tool_receipt,
    require_host_agent_session,
    transition_host_agent_session,
)


def _database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'host-finalize.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def _seed_case(factory) -> None:
    with factory() as db:
        case = Case(
            id="CASE-host-finalize",
            title="AP offline",
            description="AP loses heartbeat",
            device_type="AP",
        )
        artifact = Artifact(
            id="ART-host-finalize",
            case_id=case.id,
            kind="debug_log",
            original_name="ap.log",
            stored_path="artifacts/ap.log",
            sha256="d" * 64,
            size_bytes=128,
            status="PARSED",
            active_parse_run_id="PRUN-host-finalize",
        )
        event = LogEvent(
            id="EVT-host-finalize",
            case_id=case.id,
            artifact_id=artifact.id,
            parse_run_id=artifact.active_parse_run_id,
            source_file="ap.log",
            line_start=21,
            line_end=21,
            level="ERROR",
            module="topology",
            component="ap_manager",
            event_code="HEARTBEAT_TIMEOUT",
            message="heartbeat timeout",
            raw_text="heartbeat timeout",
            confidence=0.99,
        )
        db.add_all([case, artifact, event])
        db.commit()


def _diagnosis() -> dict:
    return {
        "summary": "The AP is offline after its heartbeat timed out.",
        "confirmed_facts": [{
            "statement": "The active AP log records a heartbeat timeout.",
            "evidence_ids": ["EVT-host-finalize"],
        }],
        "hypotheses": [{
            "rank": 1,
            "title": "Heartbeat timeout",
            "description": "The controller stopped receiving AP heartbeats.",
            "supporting_evidence": ["EVT-host-finalize"],
            "contradicting_evidence": [],
            "confidence_score": 0.9,
            "confidence_level": "HIGH",
            "priority": "P1",
            "needs_human_review": True,
        }],
        "recommended_actions": [{
            "priority": "P1",
            "action": "Inspect the heartbeat path.",
            "reason": "The timeout is present in the active parse generation.",
            "expected_result": "Heartbeat delivery resumes or the failed hop is isolated.",
        }],
        "missing_information": [],
        "suspected_modules": ["ap_manager"],
        "limitations": ["The client model identity is self-reported."],
        "fault_tree_conclusions": [],
    }


def _method_only_planning_round(method_id: str, *, continue_analysis: bool) -> dict:
    return {
        "read_document_ids": [method_id],
        "method_assessments": [{
            "method_document_id": method_id,
            "relevance": "NOT_RELEVANT",
            "rationale": "The generic method was read but adds no case-specific branch.",
            "matched_signals": [],
        }],
        "hypotheses": [],
        "checks": [],
        "search_queries": [],
        "tool_calls": [],
        "evidence_gaps": [],
        "fault_tree_assessments": [],
        "continue_analysis": continue_analysis,
        "stop_reason": (
            "MORE_EVIDENCE_NEEDED" if continue_analysis else "ENOUGH_EVIDENCE"
        ),
    }


def test_host_cli_finalization_persists_native_analysis_without_backend_llm(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    _seed_case(factory)
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [],
    )
    monkeypatch.setattr(
        "app.services.llm.get_llm_provider",
        lambda: (_ for _ in ()).throw(AssertionError("backend LLM must not run")),
    )
    started = host_diagnosis.begin_host_diagnosis(
        "CASE-host-finalize",
        executor="claude_code",
        client_model_claim="claude-code-current-session",
        skill_version="gw-ap-debug@1.0.0",
        created_by="USR-owner",
        session_factory=factory,
    )
    run = started["run"]
    with factory() as db:
        searching = transition_host_agent_session(
            db,
            run["id"],
            HostAgentSessionStatusTransition(
                expected_version=run["version"],
                status="SEARCHING",
                reason="Host model is evaluating the initial evidence",
            ),
        )
    result = host_diagnosis.finalize_host_diagnosis(
        host_diagnosis.HostDiagnosisInput(
            session_id=searching.id,
            expected_version=searching.version,
            diagnosis=_diagnosis(),
        ),
        session_factory=factory,
    )

    with factory() as db:
        analysis = db.get(AnalysisRun, result["analysis_id"])
        assert analysis is not None
        assert analysis.provider == "host_cli"
        assert analysis.model_profile_id is None
        assert analysis.agent_run_id == searching.agent_run_id
        assert '"backend_chat_calls": 0' in analysis.result_json
        agent_run = db.get(AgentRun, analysis.agent_run_id)
        assert agent_run.status == "COMPLETED"
        assert agent_run.stop_reason == "HOST_AGENT_COMPLETED"
        assert db.get(Case, "CASE-host-finalize").status == "ANALYZED"
        session = require_host_agent_session(db, searching.id)
        assert session.status == "COMPLETED"
    engine.dispose()


def test_host_finalization_rejects_snapshot_drift(tmp_path: Path, monkeypatch) -> None:
    engine, factory = _database(tmp_path)
    _seed_case(factory)
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [],
    )
    started = host_diagnosis.begin_host_diagnosis(
        "CASE-host-finalize",
        executor="codex",
        client_model_claim="codex-current-session",
        skill_version="gw-ap-debug@1.0.0",
        created_by="USR-owner",
        session_factory=factory,
    )["run"]
    with factory() as db:
        searching = transition_host_agent_session(
            db,
            started["id"],
            HostAgentSessionStatusTransition(
                expected_version=started["version"],
                status="SEARCHING",
            ),
        )
        artifact = db.get(Artifact, "ART-host-finalize")
        artifact.active_parse_run_id = "PRUN-new-generation"
        db.commit()

    with pytest.raises(host_diagnosis.HostDiagnosisSnapshotDriftError):
        host_diagnosis.finalize_host_diagnosis(
            host_diagnosis.HostDiagnosisInput(
                session_id=searching.id,
                expected_version=searching.version,
                diagnosis=_diagnosis(),
            ),
            session_factory=factory,
        )
    with factory() as db:
        assert db.scalar(select(AnalysisRun)) is None
    engine.dispose()


def test_method_without_fault_tree_persists_two_planning_rounds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    _seed_case(factory)
    method = DiagnosticMethodDocument(
        id="LOCALDOC-method-only",
        title="Generic log analysis",
        source_type="analysis_skill",
        version=1,
        device_type=None,
        module=None,
        content="Review available evidence and document remaining gaps.",
        content_sha256="e" * 64,
        role="LOG_ANALYSIS_METHOD",
    )
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [method],
    )
    monkeypatch.setattr(
        "app.services.llm.get_llm_provider",
        lambda: (_ for _ in ()).throw(AssertionError("backend LLM must not run")),
    )
    started = host_diagnosis.begin_host_diagnosis(
        "CASE-host-finalize",
        executor="codex",
        client_model_claim="codex-current-session",
        skill_version="gw-ap-debug@1.0.0",
        created_by="USR-owner",
        session_factory=factory,
    )["run"]
    with factory() as db:
        methods_read = transition_host_agent_session(
            db,
            started["id"],
            HostAgentSessionStatusTransition(
                expected_version=started["version"],
                status="METHODS_READ",
            ),
        )
    first = host_diagnosis.submit_host_planning_round(
        methods_read.id,
        expected_version=methods_read.version,
        round_number=1,
        payload=_method_only_planning_round(method.id, continue_analysis=True),
        session_factory=factory,
    )
    second = host_diagnosis.submit_host_planning_round(
        methods_read.id,
        expected_version=first["run"]["version"],
        round_number=2,
        payload=_method_only_planning_round(method.id, continue_analysis=False),
        session_factory=factory,
    )
    assert [item["round_number"] for item in second["run"]["planning_rounds"]] == [1, 2]
    assert second["run"]["coverage"] == {}
    with factory() as db:
        searching = transition_host_agent_session(
            db,
            methods_read.id,
            HostAgentSessionStatusTransition(
                expected_version=second["run"]["version"],
                status="SEARCHING",
            ),
        )
    result = host_diagnosis.finalize_host_diagnosis(
        host_diagnosis.HostDiagnosisInput(
            session_id=searching.id,
            expected_version=searching.version,
            diagnosis=_diagnosis(),
        ),
        session_factory=factory,
    )
    with factory() as db:
        session = require_host_agent_session(db, searching.id)
        analysis = db.get(AnalysisRun, result["analysis_id"])
        agent_run = db.get(AgentRun, session.agent_run_id)
        result_json = json.loads(analysis.result_json)
        assert session.status == "COMPLETED"
        assert len(session.planning_rounds) == 2
        assert session.coverage == {}
        assert result_json["diagnostic_planning"]["rounds_completed"] == 2
        assert agent_run.total_tokens == 0
        assert agent_run.status == "COMPLETED"
    engine.dispose()


def test_planning_assessment_records_the_round_that_updated_coverage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    _seed_case(factory)
    content = """# AP offline fault tree

```text
场景1: Heartbeat evidence is incomplete
```
"""
    method = DiagnosticMethodDocument(
        id="LOCALDOC-fault-tree-round",
        title="AP offline fault tree",
        source_type="fault_tree",
        version=1,
        device_type=None,
        module=None,
        content=content,
        content_sha256="a" * 64,
        role="FAULT_TREE",
    )
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [method],
    )
    # This regression targets the persisted coverage metadata. The full
    # method/read/search/evidence binding contract has separate tests.
    monkeypatch.setattr(host_diagnosis, "validate_planning_round", lambda *args, **kwargs: None)
    started = host_diagnosis.begin_host_diagnosis(
        "CASE-host-finalize",
        executor="codex",
        client_model_claim="codex-current-session",
        skill_version="gw-ap-debug@1.0.0",
        created_by="USR-owner",
        session_factory=factory,
    )["run"]
    item_id = next(iter(started["coverage"]))
    with factory() as db:
        methods_read = transition_host_agent_session(
            db,
            started["id"],
            HostAgentSessionStatusTransition(
                expected_version=started["version"],
                status="METHODS_READ",
            ),
        )
    payload = _method_only_planning_round(method.id, continue_analysis=True)
    payload["fault_tree_assessments"] = [{
        "item_id": item_id,
        "method_document_id": method.id,
        "status": "INSUFFICIENT_EVIDENCE",
        "rationale": "The current case does not contain the required heartbeat proof.",
        "evidence_ids": [],
        "next_action": "Collect the missing heartbeat evidence.",
    }]
    accepted = host_diagnosis.submit_host_planning_round(
        methods_read.id,
        expected_version=methods_read.version,
        round_number=1,
        payload=payload,
        session_factory=factory,
    )
    assert accepted["run"]["coverage"][item_id]["last_round"] == 1
    engine.dispose()


def test_planning_receipt_hash_uses_tool_defaults(tmp_path: Path, monkeypatch) -> None:
    engine, factory = _database(tmp_path)
    _seed_case(factory)
    method = DiagnosticMethodDocument(
        id="LOCALDOC-defaults",
        title="Log search method",
        source_type="analysis_skill",
        version=1,
        device_type=None,
        module=None,
        content="Search heartbeat evidence before drawing a conclusion.",
        content_sha256="f" * 64,
        role="LOG_ANALYSIS_METHOD",
    )
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [method],
    )
    monkeypatch.setattr(
        "app.services.llm.get_llm_provider",
        lambda: (_ for _ in ()).throw(AssertionError("backend LLM must not run")),
    )
    started = host_diagnosis.begin_host_diagnosis(
        "CASE-host-finalize",
        executor="claude_code",
        client_model_claim="claude-code-current-session",
        skill_version="gw-ap-debug@1.0.0",
        created_by="USR-owner",
        session_factory=factory,
    )["run"]
    with factory() as db:
        methods_read = transition_host_agent_session(
            db,
            started["id"],
            HostAgentSessionStatusTransition(
                expected_version=started["version"],
                status="METHODS_READ",
            ),
        )
        executed_arguments = host_diagnostic_runtime.normalize_host_diagnostic_tool_arguments(
            "search_log",
            {"keywords": ["heartbeat"], "method_document_ids": [method.id]},
        )
        assert executed_arguments["top_k"] == 20
        assert executed_arguments["pattern_ids"] == []
        assert executed_arguments["artifact_ids"] == []
        read_arguments = host_diagnostic_runtime.normalize_host_diagnostic_tool_arguments(
            "read_diagnostic_documents",
            {"document_ids": [method.id]},
        )
        read_receipt = record_host_agent_tool_receipt(
            db,
            methods_read.id,
            HostAgentToolReceiptInput(
                expected_version=methods_read.version,
                call_id="call-method-read",
                tool_name="debug_read_diagnostic_documents",
                arguments_hash=hash_host_agent_tool_arguments(read_arguments),
                evidence_ids=[method.id],
            ),
        )
        assert method.id in read_receipt.allowed_evidence_ids
        assert method.id not in read_receipt.evidence_cache
        with_receipt = record_host_agent_tool_receipt(
            db,
            methods_read.id,
            HostAgentToolReceiptInput(
                expected_version=read_receipt.version,
                call_id="call-defaults",
                tool_name="debug_search_log",
                arguments_hash=hash_host_agent_tool_arguments(executed_arguments),
            ),
        )
    planning_payload = {
            "read_document_ids": [method.id],
            "method_assessments": [{
                "method_document_id": method.id,
                "relevance": "RELEVANT",
                "rationale": "The method directly covers heartbeat evidence.",
                "matched_signals": ["heartbeat"],
            }],
            "hypotheses": ["Heartbeat path failed"],
            "checks": [{
                "check_id": "check-heartbeat",
                "method_document_id": method.id,
                "description": "Search heartbeat records",
                "evidence_needed": "Timestamped heartbeat failures",
                "completion_rule": "Record support, contradiction, or a gap",
                "fault_tree_item_ids": [],
            }],
            "search_queries": [],
            "tool_calls": [{
                "call_id": "call-defaults",
                "tool_name": "search_log",
                "arguments": {"keywords": ["heartbeat"]},
                "method_document_ids": [method.id],
                "rationale": "Find case-specific heartbeat evidence",
                "fault_tree_item_ids": [],
            }],
            "evidence_gaps": [],
            "fault_tree_assessments": [],
            "continue_analysis": True,
            "stop_reason": "MORE_EVIDENCE_NEEDED",
    }
    accepted = host_diagnosis.submit_host_planning_round(
        methods_read.id,
        expected_version=with_receipt.version,
        round_number=1,
        payload=planning_payload,
        session_factory=factory,
    )
    assert accepted["accepted"] is True
    assert accepted["run"]["planning_rounds"][0]["round_number"] == 1
    with pytest.raises(host_diagnosis.HostDiagnosisError, match="reuses an earlier"):
        host_diagnosis.submit_host_planning_round(
            methods_read.id,
            expected_version=accepted["run"]["version"],
            round_number=2,
            payload=planning_payload,
            session_factory=factory,
        )
    evidence_arguments = host_diagnostic_runtime.normalize_host_diagnostic_tool_arguments(
        "get_evidence",
        {"evidence_ids": [method.id]},
    )
    with factory() as db:
        locator_receipt = record_host_agent_tool_receipt(
            db,
            methods_read.id,
            HostAgentToolReceiptInput(
                expected_version=accepted["run"]["version"],
                call_id="call-method-as-evidence",
                tool_name="debug_get_evidence",
                arguments_hash=hash_host_agent_tool_arguments(evidence_arguments),
            ),
        )
    locator_payload = deepcopy(planning_payload)
    locator_payload["tool_calls"] = [{
        "call_id": "call-method-as-evidence",
        "tool_name": "get_evidence",
        "arguments": {"evidence_ids": [method.id]},
        "method_document_ids": [],
        "rationale": "Attempt to treat a method attestation as evidence",
        "fault_tree_item_ids": [],
    }]
    with pytest.raises(ValueError, match="unknown evidence"):
        host_diagnosis.submit_host_planning_round(
            methods_read.id,
            expected_version=locator_receipt.version,
            round_number=2,
            payload=locator_payload,
            session_factory=factory,
        )
    engine.dispose()
