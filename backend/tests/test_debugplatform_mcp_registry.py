import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.mcp.contracts import MCPPrincipal, MCPToolCallContext, MCPToolError
from app.mcp.debugplatform_registry import (
    DEBUGPLATFORM_MCP_TOOL_NAMES,
    create_debugplatform_mcp_registry,
)
from app.mcp.transport import create_mcp_http_transport
from app.models import AnalysisRun, Artifact, Case, LogEvent, UserAccount
from app.services import host_diagnostic_runtime
from app.services.diagnostic_methods import DiagnosticMethodDocument


def _database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'debugplatform-mcp.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def _context(*, subject: str = "USR-engineer", role: str = "ENGINEER"):
    return MCPToolCallContext(
        principal=MCPPrincipal(subject=subject, claims={"role": role}),
        request_id="test-request",
        protocol_version="2025-11-25",
    )


def _call(registry, name: str, arguments: dict, *, context=None):
    return asyncio.run(
        registry.call_tool(name, arguments, context or _context())
    )


def _seed_case(factory, *, owner_id: str | None = None) -> None:
    with factory() as db:
        if owner_id:
            db.add(UserAccount(
                id=owner_id,
                username=owner_id.casefold(),
                display_name=owner_id,
                role="ENGINEER",
            ))
            db.flush()
        case = Case(
            id="CASE-mcp",
            title="AP heartbeat timeout",
            description="AP repeatedly becomes offline",
            device_type="AP",
            owner_id=owner_id,
        )
        artifact = Artifact(
            id="ART-mcp",
            case_id=case.id,
            kind="debug_log",
            original_name="collectDebuginfo.txt",
            stored_path="artifacts/mcp/collectDebuginfo.txt",
            sha256="a" * 64,
            size_bytes=100,
            status="PARSED",
            active_parse_run_id="PARSE-mcp",
        )
        event = LogEvent(
            id="EVT-mcp",
            case_id=case.id,
            artifact_id=artifact.id,
            parse_run_id=artifact.active_parse_run_id,
            source_file=artifact.original_name,
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


def _diagnosis() -> dict:
    return {
        "summary": "The AP is offline after its heartbeat timed out.",
        "confirmed_facts": [{
            "statement": "The active AP log records a heartbeat timeout.",
            "evidence_ids": ["EVT-mcp"],
        }],
        "hypotheses": [{
            "rank": 1,
            "title": "Heartbeat timeout",
            "description": "The controller stopped receiving AP heartbeats.",
            "supporting_evidence": ["EVT-mcp"],
            "contradicting_evidence": [],
            "confidence_score": 0.9,
            "confidence_level": "HIGH",
            "priority": "P1",
            "needs_human_review": True,
        }],
        "recommended_actions": [{
            "priority": "P1",
            "action": "Inspect the heartbeat path.",
            "reason": "The current parse generation contains the timeout.",
            "expected_result": "The failed heartbeat hop is isolated.",
        }],
        "missing_information": [],
        "suspected_modules": ["ap_manager"],
        "limitations": ["The client model identity is self-reported."],
        "fault_tree_conclusions": [],
    }


def _method(document_id: str) -> DiagnosticMethodDocument:
    return DiagnosticMethodDocument(
        id=document_id,
        title=document_id,
        source_type="analysis_method",
        version=1,
        device_type="AP",
        module="topology",
        content="Inspect bounded heartbeat evidence and keep unresolved gaps explicit.",
        content_sha256=("a" if document_id.endswith("1") else "b") * 64,
        role="LOG_ANALYSIS_METHOD",
    )


def test_registry_advertises_exact_stable_tool_set_and_host_inference(tmp_path: Path):
    engine, factory = _database(tmp_path)
    registry = create_debugplatform_mcp_registry(session_factory=factory)
    definitions = asyncio.run(registry.list_tools(_context().principal))
    assert tuple(item.name for item in definitions) == DEBUGPLATFORM_MCP_TOOL_NAMES

    status = _call(registry, "debug_status", {})
    assert status["inference_owner"] == "host_cli"
    assert status["backend_chat_allowed"] is False
    assert status["backend_chat_calls"] == 0
    assert status["tool_count"] == 18
    assert status["knowledge_routing"] == {
        "inference_owner": "host_cli",
        "backend_chat_allowed": False,
        "draft_only": True,
        "max_documents": 20,
    }

    planning_definition = next(
        item for item in definitions if item.name == "debug_submit_planning_round"
    )
    planning_schema = planning_definition.input_schema
    planning_ref = planning_schema["properties"]["planning"]["$ref"]
    planning_name = planning_ref.rsplit("/", 1)[-1]
    assessment_schema = planning_schema["$defs"][planning_name]["properties"][
        "fault_tree_assessments"
    ]["items"]
    assessment_ref = assessment_schema["$ref"]
    assessment_name = assessment_ref.rsplit("/", 1)[-1]
    assert {
        "item_id",
        "method_document_id",
        "rationale",
    }.issubset(planning_schema["$defs"][assessment_name]["required"])

    diagnosis_definition = next(
        item for item in definitions if item.name == "debug_finalize_diagnosis"
    )
    diagnosis_schema = diagnosis_definition.input_schema
    diagnosis_ref = diagnosis_schema["properties"]["diagnosis"]["$ref"]
    diagnosis_name = diagnosis_ref.rsplit("/", 1)[-1]
    assert "fault_tree_conclusions" in diagnosis_schema["$defs"][diagnosis_name][
        "properties"
    ]
    engine.dispose()


def test_actual_registry_works_over_streamable_http_protocol(tmp_path: Path) -> None:
    engine, factory = _database(tmp_path)
    registry = create_debugplatform_mcp_registry(session_factory=factory)

    async def resolve(token: str):
        return _context().principal if token == "test-token" else None

    transport = create_mcp_http_transport(registry, resolve)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with transport.lifespan():
            yield

    app = FastAPI(lifespan=lifespan)
    app.router.routes.append(transport.route("/mcp"))
    headers = {
        "Authorization": "Bearer test-token",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        initialized = client.post("/mcp", headers=headers, json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "registry-test", "version": "1.0"},
            },
        })
        assert initialized.status_code == 200, initialized.text
        session_id = initialized.headers["Mcp-Session-Id"]
        session_headers = {
            **headers,
            "Mcp-Session-Id": session_id,
            "MCP-Protocol-Version": "2025-11-25",
        }
        client.post("/mcp", headers=session_headers, json={
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        })
        listed = client.post("/mcp", headers=session_headers, json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        })
        assert listed.status_code == 200, listed.text
        assert tuple(
            tool["name"] for tool in listed.json()["result"]["tools"]
        ) == DEBUGPLATFORM_MCP_TOOL_NAMES
        status = client.post("/mcp", headers=session_headers, json={
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "debug_status", "arguments": {}},
        })
        assert status.status_code == 200, status.text
        result = status.json()["result"]
        assert result["isError"] is False
        assert result["structuredContent"]["inference_owner"] == "host_cli"
        assert result["structuredContent"]["backend_chat_calls"] == 0
    engine.dispose()


def test_case_scope_is_enforced_for_mcp_arguments(tmp_path: Path):
    engine, factory = _database(tmp_path)
    _seed_case(factory, owner_id="USR-owner")
    registry = create_debugplatform_mcp_registry(session_factory=factory)

    cases = _call(
        registry,
        "debug_list_cases",
        {},
        context=_context(subject="USR-other", role="ENGINEER"),
    )
    assert cases["cases"] == []
    with pytest.raises(MCPToolError, match="No access"):
        _call(
            registry,
            "debug_get_case_context",
            {"case_id": "CASE-mcp"},
            context=_context(subject="USR-other", role="ENGINEER"),
        )
    engine.dispose()


def test_listing_methods_does_not_attest_that_their_content_was_read(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _database(tmp_path)
    _seed_case(factory)
    methods = [_method("METHOD-1"), _method("METHOD-2")]
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: methods,
    )
    registry = create_debugplatform_mcp_registry(session_factory=factory)
    started = _call(registry, "debug_begin_host_diagnosis", {
        "case_id": "CASE-mcp",
        "executor": "claude_code",
    })["run"]

    listed = _call(registry, "debug_list_diagnostic_documents", {
        "session_id": started["run_id"],
        "expected_version": started["state_version"],
    })
    assert listed["evidence_ids"] == []
    assert listed["run"]["status"] == "CREATED"

    partial = _call(registry, "debug_read_diagnostic_documents", {
        "session_id": started["run_id"],
        "expected_version": listed["run"]["state_version"],
        "document_ids": ["METHOD-1"],
    })
    assert partial["run"]["status"] == "CREATED"
    assert partial["evidence_ids"] == ["METHOD-1"]

    complete = _call(registry, "debug_read_diagnostic_documents", {
        "session_id": started["run_id"],
        "expected_version": partial["run"]["state_version"],
        "document_ids": ["METHOD-2"],
    })
    assert complete["run"]["status"] == "METHODS_READ"
    assert set(complete["run"]["allowed_evidence_ids"]) >= {
        "METHOD-1",
        "METHOD-2",
    }
    engine.dispose()


def test_host_cli_mcp_flow_persists_analysis_without_backend_model(
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
    registry = create_debugplatform_mcp_registry(session_factory=factory)

    started = _call(registry, "debug_begin_host_diagnosis", {
        "case_id": "CASE-mcp",
        "executor": "codex",
        "client_model_claim": "codex-current-session",
        "skill_version": "gw-ap-debug@1.0.0",
    })
    run = started["run"]
    assert run["status"] == "CREATED"
    assert run["run_id"] == run["id"]
    assert "evidence_cache" not in run

    searched = _call(registry, "debug_search_log", {
        "session_id": run["run_id"],
        "expected_version": run["state_version"],
        "keywords": ["heartbeat timeout"],
        "top_k": 10,
    })
    assert searched["call_id"].startswith("MCALL-")
    assert searched["evidence_ids"] == ["EVT-mcp"]
    assert searched["run"]["status"] == "SEARCHING"
    assert "secret-value" not in str(searched["output"])

    finalized = _call(registry, "debug_finalize_diagnosis", {
        "session_id": run["run_id"],
        "expected_version": searched["run"]["state_version"],
        "diagnosis": _diagnosis(),
    })
    assert finalized["status"] == "COMPLETED"
    with factory() as db:
        analysis = db.get(AnalysisRun, finalized["analysis_id"])
        assert analysis is not None
        assert analysis.provider == "host_cli"
        assert analysis.model_profile_id is None
        assert '"backend_chat_calls": 0' in analysis.result_json
    engine.dispose()


def test_planning_round_cannot_forge_a_tool_receipt(tmp_path: Path, monkeypatch) -> None:
    engine, factory = _database(tmp_path)
    _seed_case(factory)
    monkeypatch.setattr(
        host_diagnostic_runtime,
        "load_applicable_diagnostic_methods",
        lambda _db, _case: [],
    )
    registry = create_debugplatform_mcp_registry(session_factory=factory)
    started = _call(registry, "debug_begin_host_diagnosis", {
        "case_id": "CASE-mcp",
        "executor": "claude_code",
    })["run"]
    searched = _call(registry, "debug_search_log", {
        "session_id": started["run_id"],
        "expected_version": started["state_version"],
        "keywords": ["heartbeat timeout"],
    })

    with pytest.raises(MCPToolError, match="no server receipt"):
        _call(registry, "debug_submit_planning_round", {
            "session_id": started["run_id"],
            "expected_version": searched["run"]["state_version"],
            "round_number": 1,
            "planning": {
                "tool_calls": [{
                    "call_id": "invented-call-id",
                    "tool_name": "search_log",
                    "arguments": {"keywords": ["heartbeat timeout"], "top_k": 20},
                    "rationale": "Try to forge an observation.",
                }],
                "continue_analysis": True,
                "stop_reason": "MORE_EVIDENCE_NEEDED",
            },
        })
    engine.dispose()
