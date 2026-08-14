import asyncio
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_dumps, json_loads
from app.diagnostic_models import LogTriageRun
from app.models import AgentRun, AgentTraceEvent, Artifact, Case, KnowledgeDocument, LogEvent
from app.services import (
    diagnosis,
    diagnostic_methods,
    diagnostic_planning,
    diagnostic_planning_contract,
    diagnostic_tools,
    log_triage,
    log_triage_planning,
)
from app.services.diagnosis import _evidence_for_persistence
from app.services.agent_trace_runtime import create_live_agent_run
from app.services.agentic.tools import ToolContext, ToolPermission
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    compile_diagnostic_patterns,
    load_applicable_diagnostic_methods,
)


class _JobContext:
    def update(self, progress: int, message: str) -> None:
        pass

    def raise_if_cancelled(self) -> None:
        pass


def _method(
    content: str,
    *,
    document_id: str = "DOC-method",
    source_type: str = "analysis_skill",
) -> DiagnosticMethodDocument:
    return DiagnosticMethodDocument(
        id=document_id,
        title="Synthetic analysis method",
        source_type=source_type,
        version=3,
        device_type="AP",
        module="DC",
        content=content,
        content_sha256="a" * 64,
        role="LOG_ANALYSIS_METHOD",
    )


def test_diagnostic_tool_registry_reads_methods_and_searches_log_evidence() -> None:
    methods = [_method("## 日志关键词\n- `Heartbeat timeout`", document_id="DOC-tools")]
    patterns = compile_diagnostic_patterns(methods)
    environment = diagnostic_tools.DiagnosticToolEnvironment(
        case=Case(id="CASE-tools", title="AP频繁离线", device_type="AP"),
        methods=methods,
        patterns=patterns,
        evidence=[{
            "evidence_id": "LEM-tools",
            "source_type": "log_triage_match",
            "artifact_id": "ART-tools",
            "source_file": "nested/AP.log",
            "line_start": 42,
            "line_end": 42,
            "pattern_id": patterns[0].id,
            "content": "Heartbeat timeout; AP offline",
            "score": 0.8,
        }],
    )
    registry = diagnostic_tools.build_diagnostic_tool_registry(environment)
    context = ToolContext(role="ENGINEER", case_id="CASE-tools")

    assert registry.get("search_log", role="ENGINEER").permission == ToolPermission.READ
    read_result = diagnostic_tools.invoke_diagnostic_tool(
        registry,
        context,
        tool_name="read_diagnostic_documents",
        arguments={"document_ids": ["DOC-tools"]},
    )
    search_result = diagnostic_tools.invoke_diagnostic_tool(
        registry,
        context,
        tool_name="search_log",
        arguments={
            "keywords": ["offline"],
            "pattern_ids": [patterns[0].id],
            "artifact_ids": ["ART-tools"],
            "method_document_ids": ["DOC-tools"],
        },
    )

    assert read_result.output["documents"][0]["content"].endswith("Heartbeat timeout`")
    assert search_result.output["returned"] == 1
    assert search_result.output["results"][0]["evidence_id"] == "LEM-tools"
    assert search_result.output["results"][0]["line_start"] == 42
    with pytest.raises(ValueError, match="unknown method"):
        diagnostic_tools.invoke_diagnostic_tool(
            registry,
            context,
            tool_name="search_log",
            arguments={"keywords": ["offline"], "method_document_ids": ["DOC-unknown"]},
        )


def test_method_compiler_extracts_table_inline_and_template_patterns() -> None:
    document = _method("""# Synthetic method

## 必查日志关键词

- `Heartbeat timeout`
- CtrlPointVerify failed
- `get WLANConfiguration!`

| 日志格式 | 说明 |
| --- | --- |
| `send MID %u failed for [XXX]` | synthetic |
""")
    patterns = compile_diagnostic_patterns([document])
    texts = {item.text for item in patterns}

    assert "Heartbeat timeout" in texts
    assert "CtrlPointVerify failed" in texts
    assert "get WLANConfiguration!" in texts
    assert "send MID %u failed for [XXX]" in texts
    template = next(item for item in patterns if item.text.startswith("send MID"))
    assert template.match_kind == "template"
    assert re.search(template.regex, "send MID 545 failed for [radio-1]", re.IGNORECASE)
    assert template.document_id == "DOC-method"
    assert template.document_version == 3


def test_method_compiler_normalizes_markdown_and_symbolic_placeholders() -> None:
    document = _method("""# AP offline method

## 日志关键词

| 日志格式 | 说明 |
| --- | --- |
| **`SyntheticLeave APInst offline:%u`** | AP 离线 |
| `RefreshSyntheticTopo APInstId: X link failed` | 链路失败 |
| `AddSyntheticTopo, APInst: X, Parent: Y` | 拓扑变化 |
""")

    patterns = compile_diagnostic_patterns([document])
    texts = {item.text for item in patterns}

    assert "SyntheticLeave APInst offline:%u" in texts
    assert not any("**" in item or "`" in item or item.startswith("|") for item in texts)
    leave = next(item for item in patterns if item.text.startswith("SyntheticLeave"))
    refresh = next(item for item in patterns if item.text.startswith("RefreshSyntheticTopo"))
    topology = next(item for item in patterns if item.text.startswith("AddSyntheticTopo"))
    assert re.search(leave.regex, "SyntheticLeave APInst offline:12", re.IGNORECASE)
    assert re.search(refresh.regex, "RefreshSyntheticTopo APInstId: 12 link failed", re.IGNORECASE)
    assert re.search(topology.regex, "AddSyntheticTopo, APInst: 12, Parent: 3", re.IGNORECASE)


def test_method_catalog_loads_published_gw_ap_joint_diagnostic_documents(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'methods.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(diagnostic_methods, "_LOCAL_METHOD_FILES", {})
    with factory() as db:
        case = Case(id="CASE-method", title="AP issue", device_type="AP")
        db.add(case)
        db.add_all([
            KnowledgeDocument(
                id="DOC-ap",
                title="AP method",
                source_type="fault_tree",
                device_type="AP",
                content="# AP",
                active=True,
                review_status="ACTIVE",
            ),
            KnowledgeDocument(
                id="DOC-general",
                title="General method",
                source_type="analysis_method",
                device_type="GENERAL",
                content="# General",
                active=True,
                review_status="ACTIVE",
            ),
            KnowledgeDocument(
                id="DOC-gw",
                title="GW method",
                source_type="fault_tree",
                device_type="GW",
                content="# GW",
                active=True,
                review_status="ACTIVE",
            ),
            KnowledgeDocument(
                id="DOC-draft",
                title="Draft method",
                source_type="fault_tree",
                device_type="AP",
                content="# Draft",
                active=False,
                review_status="DRAFT",
            ),
        ])
        db.commit()
        loaded = load_applicable_diagnostic_methods(db, case)

    assert {item.id for item in loaded} == {"DOC-ap", "DOC-general", "DOC-gw"}
    assert [item.id for item in loaded] == ["DOC-ap", "DOC-general", "DOC-gw"]
    engine.dispose()


def test_log_scanner_creates_ranked_clusters_and_occurrence_index(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'triage.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(log_triage, "SessionLocal", factory)
    with factory() as db:
        db.add(Case(id="CASE-triage", title="Heartbeat issue", device_type="AP"))
        db.add(Artifact(
            id="ART-triage",
            case_id="CASE-triage",
            original_name="synthetic.log",
            stored_path="synthetic.log",
            sha256="a" * 64,
            size_bytes=100,
            status="PARSED",
            active_parse_run_id="PRUN-1",
        ))
        db.add(LogTriageRun(
            id="LTRIAGE-test",
            case_id="CASE-triage",
            artifact_id="ART-triage",
            parse_run_id="PRUN-1",
        ))
        db.add_all([
            LogEvent(
                id="EVT-1",
                case_id="CASE-triage",
                artifact_id="ART-triage",
                parse_run_id="PRUN-1",
                source_file="synthetic.log",
                line_start=10,
                line_end=10,
                message="Heartbeat timeout 100",
                raw_text="Heartbeat timeout 100",
            ),
            LogEvent(
                id="EVT-2",
                case_id="CASE-triage",
                artifact_id="ART-triage",
                parse_run_id="PRUN-1",
                source_file="synthetic.log",
                line_start=20,
                line_end=20,
                message="Heartbeat timeout 200",
                raw_text="Heartbeat timeout 200",
            ),
            LogEvent(
                id="EVT-3",
                case_id="CASE-triage",
                artifact_id="ART-triage",
                parse_run_id="PRUN-1",
                source_file="synthetic.log",
                line_start=30,
                line_end=30,
                message="method-required marker",
                raw_text="method-required marker",
            ),
            LogEvent(
                id="EVT-4",
                case_id="CASE-triage",
                artifact_id="ART-triage",
                parse_run_id="PRUN-1",
                source_file="synthetic.log",
                line_start=40,
                line_end=40,
                message="ordinary notice",
                raw_text="ordinary notice",
            ),
        ])
        db.commit()
        triage = db.get(LogTriageRun, "LTRIAGE-test")

    searchers = [
        {
            "id": "DPAT-selected",
            "text": "Heartbeat timeout",
            "regex": re.compile("Heartbeat timeout", re.IGNORECASE),
            "match_kind": "literal",
            "bucket": log_triage.LLM_BUCKET,
            "score": 0.9,
            "reason": "selected",
            "document_id": "DOC-1",
            "document_version": 1,
            "source_type": "analysis_skill",
            "heading": "keywords",
            "line_start": 5,
        },
        {
            "id": "DPAT-required",
            "text": "method-required",
            "regex": re.compile("method-required", re.IGNORECASE),
            "match_kind": "literal",
            "bucket": log_triage.METHOD_BUCKET,
            "score": 0.55,
            "reason": "required",
            "document_id": "DOC-1",
            "document_version": 1,
            "source_type": "analysis_skill",
            "heading": "keywords",
            "line_start": 6,
        },
    ]
    matches, occurrences, summary = log_triage._scan_events(
        _JobContext(),
        triage=triage,
        searchers=searchers,
    )

    assert summary["total_events"] == 4
    assert summary["matched_events"] == 3
    assert summary["other_events"] == 1
    assert len(occurrences) == 3
    selected = next(row for row in matches if row["pattern_id"] == "DPAT-selected")
    assert selected["bucket"] == "LLM_RELEVANT"
    assert selected["occurrence_count"] == 2
    assert selected["line_start"] == 10
    assert selected["line_end"] == 20
    engine.dispose()


def test_log_scanner_reads_complete_extracted_text_not_only_structured_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    extract_root = tmp_path / "extracted"
    extract_root.mkdir()
    source = extract_root / "collect.log"
    source.write_text(
        "command-only diagnostic marker\n"
        "NOTICE 2026-01-01 00:00:00.000 structured event\n"
        "ordinary command output\n",
        encoding="utf-8",
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'raw-triage.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(log_triage, "SessionLocal", factory)
    with factory() as db:
        db.add(Case(id="CASE-raw", title="Command issue", device_type="AP"))
        db.add(Artifact(
            id="ART-raw",
            case_id="CASE-raw",
            original_name="collect.log",
            stored_path="collect.log",
            sha256="c" * 64,
            size_bytes=source.stat().st_size,
            status="PARSED",
            active_parse_run_id="PRUN-raw",
            metadata_json=json_dumps({
                "extract_root": str(extract_root),
                "manifest": [{"path": source.name, "size": source.stat().st_size}],
            }),
        ))
        db.add(LogTriageRun(
            id="LTRIAGE-raw",
            case_id="CASE-raw",
            artifact_id="ART-raw",
            parse_run_id="PRUN-raw",
        ))
        db.add(LogEvent(
            id="EVT-raw",
            case_id="CASE-raw",
            artifact_id="ART-raw",
            parse_run_id="PRUN-raw",
            source_file=source.name,
            line_start=2,
            line_end=2,
            message="structured event",
            raw_text="NOTICE 2026-01-01 00:00:00.000 structured event",
        ))
        db.add(LogEvent(
            id="EVT-fallback",
            case_id="CASE-raw",
            artifact_id="ART-raw",
            parse_run_id="PRUN-raw",
            source_file="unavailable.log",
            line_start=7,
            line_end=7,
            message="fallback-required marker",
            raw_text="fallback-required marker",
        ))
        db.commit()
        triage = db.get(LogTriageRun, "LTRIAGE-raw")

    matches, occurrences, summary = log_triage._scan_events(
        _JobContext(),
        triage=triage,
        searchers=[{
            "id": "DPAT-command",
            "text": "command-only diagnostic marker",
            "regex": re.compile("command-only diagnostic marker", re.IGNORECASE),
            "match_kind": "literal",
            "bucket": log_triage.METHOD_BUCKET,
            "score": 0.55,
            "reason": "required",
            "document_id": "DOC-raw",
            "document_version": 1,
            "source_type": "analysis_skill",
            "heading": "command output",
            "line_start": 1,
        }, {
            "id": "DPAT-fallback",
            "text": "fallback-required",
            "regex": re.compile("fallback-required", re.IGNORECASE),
            "match_kind": "literal",
            "bucket": log_triage.METHOD_BUCKET,
            "score": 0.55,
            "reason": "required",
            "document_id": "DOC-raw",
            "document_version": 1,
            "source_type": "analysis_skill",
            "heading": "fallback",
            "line_start": 2,
        }],
    )

    assert summary["raw_text_scan_completed"] is True
    assert summary["raw_text_source_count"] == 1
    assert summary["structured_event_fallback_count"] == 1
    assert summary["total_scanned_lines"] == 4
    assert summary["total_events"] == 2
    assert summary["matched_events"] == 1
    assert len(occurrences) == 1
    command_match = next(item for item in matches if item["pattern_id"] == "DPAT-command")
    assert command_match["source_file"] == source.name
    assert command_match["line_start"] == 1
    assert command_match["message"] == "command-only diagnostic marker"
    fallback_match = next(item for item in matches if item["pattern_id"] == "DPAT-fallback")
    assert fallback_match["source_file"] == "unavailable.log"
    assert fallback_match["line_start"] == 7
    engine.dispose()


def test_log_plan_uses_tool_read_attestation_when_model_omits_ids(monkeypatch) -> None:
    documents = [_method("## 日志关键词\n- `Heartbeat timeout`", document_id="DOC-1")]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-plan", title="heartbeat", device_type="AP")

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {"prompt_tokens": 100, "completion_tokens": 20}
        last_duration_ms = 5

        async def generate_json(self, *args, **kwargs):
            return {
                "read_document_ids": [],
                "selected_pattern_ids": [patterns[0].id],
                "additional_keywords": [],
                "screening_steps": ["scan"],
            }

    monkeypatch.setattr(log_triage, "get_llm_provider", lambda: _Provider())
    plan, metadata = log_triage._safe_plan(case, documents, patterns)

    assert plan["planner_mode"] == "llm"
    assert plan["read_document_ids"] == ["DOC-1"]
    assert plan["read_attestation_source"] == "TOOL_RUNTIME"
    assert any(
        call["tool_name"] == "read_diagnostic_documents"
        and call["status"] == "COMPLETED"
        for call in plan["tool_calls"]
    )
    assert metadata["fallback"] is False
    assert metadata["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
    }
    assert metadata["duration_ms"] == 5
    assert metadata["retry_count"] == 0


def test_log_plan_falls_back_when_provider_cannot_be_created(monkeypatch) -> None:
    documents = [_method("## 日志关键词\n- `Heartbeat timeout`", document_id="DOC-provider")]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-provider", title="heartbeat", device_type="AP")

    def fail_provider():
        raise log_triage_planning.LLMError("API key is required")

    monkeypatch.setattr(log_triage, "get_llm_provider", fail_provider)
    plan, metadata = log_triage._safe_plan(case, documents, patterns)

    assert plan["planner_mode"] == "deterministic_fallback"
    assert metadata["fallback"] is True
    assert metadata["error_type"] == "LLMError"
    assert metadata["failure"]["code"] == "MODEL_REQUEST_FAILED"
    assert metadata["usage"] == {}


def test_log_plan_preserves_safe_model_failure_classification(monkeypatch) -> None:
    documents = [_method("## 日志关键词\n- `Heartbeat timeout`", document_id="DOC-timeout")]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-timeout", title="heartbeat", device_type="AP")

    def fail_provider():
        raise log_triage_planning.LLMError(
            "Model connection through the configured proxy timed out",
            code="MODEL_TIMEOUT",
            upstream_error_type="APITimeoutError",
        )

    monkeypatch.setattr(log_triage, "get_llm_provider", fail_provider)
    plan, metadata = log_triage._safe_plan(case, documents, patterns)

    assert plan["planner_mode"] == "deterministic_fallback"
    assert metadata["failure"] == {
        "code": "MODEL_TIMEOUT",
        "message": "模型请求超时；请检查代理超时和模型 Profile 的超时秒数。",
        "field_path": None,
        "error_type": "LLMError",
        "upstream_error_type": "APITimeoutError",
        "finish_reason": None,
        "thinking_mode": None,
        "retry_count": 0,
    }


def test_llm_log_plan_corrects_invalid_first_response_and_aggregates_usage() -> None:
    documents = [_method("## 日志关键词\n- `Synthetic offline marker`", document_id="DOC-retry")]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-retry", title="AP频繁离线", device_type="AP")

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage: dict[str, int] = {}
        last_duration_ms = 0

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def generate_json(self, _system, user, **_kwargs):
            self.calls.append(json_loads(user, {}))
            call_number = len(self.calls)
            self.last_usage = {
                "prompt_tokens": 10 + call_number,
                "completion_tokens": 2 + call_number,
            }
            self.last_duration_ms = 4 + call_number
            return {
                "read_document_ids": [],
                "selected_pattern_ids": [
                    "DPAT-unknown" if call_number == 1 else patterns[0].id
                ],
                "additional_keywords": ["offline"],
                "screening_steps": ["scan"],
            }

    provider = _Provider()
    plan, metadata = asyncio.run(log_triage._plan_with_model(
        case, documents, patterns, provider=provider,
    ))

    assert len(provider.calls) == 2
    assert provider.calls[1]["correction"]["required_read_document_ids"] == ["DOC-retry"]
    assert plan["planner_mode"] == "llm"
    assert plan["planning_attempts"] == 2
    assert metadata["retry_count"] == 1
    assert metadata["usage"] == {
        "prompt_tokens": 23,
        "completion_tokens": 7,
        "total_tokens": 30,
    }
    assert metadata["duration_ms"] == 11


def test_llm_log_plan_bounds_and_deduplicates_selected_searchers() -> None:
    content = "## 日志关键词\n" + "\n".join(
        f"- `SyntheticOfflineMarker{index:03d}`" for index in range(65)
    )
    documents = [_method(content, document_id="DOC-bounded")]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-bounded", title="AP频繁离线", device_type="AP")

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {"prompt_tokens": 90, "completion_tokens": 10}
        last_duration_ms = 5

        async def generate_json(self, *_args, **_kwargs):
            return {
                "read_document_ids": ["DOC-bounded"],
                "selected_pattern_ids": [item.id for item in patterns],
                "additional_keywords": [
                    {
                        "keyword": f"extra-{index % 35}",
                        "reason": "synthetic relevance",
                        "relevance": 0.9,
                    }
                    for index in range(40)
                ],
                "screening_steps": ["scan"],
            }

    plan, metadata = asyncio.run(log_triage._plan_with_model(
        case, documents, patterns, provider=_Provider(),
    ))

    assert len(patterns) == 65
    assert len(plan["selected_pattern_ids"]) == log_triage.MAX_LLM_SELECTED_PATTERNS
    assert plan["selected_pattern_candidate_count"] == 65
    assert plan["selected_pattern_selection_truncated"] is True
    assert len(plan["additional_keywords"]) == log_triage.MAX_LLM_ADDITIONAL_KEYWORDS
    assert plan["additional_keyword_candidate_count"] == 35
    assert plan["additional_keyword_selection_truncated"] is True
    assert metadata["usage"]["total_tokens"] == 100


def test_glm_shaped_log_plan_is_normalized_before_validation(monkeypatch) -> None:
    documents = [_method(
        "## 日志关键词\n- `SyntheticTopo, apInst=[X] Status=[0]`\n"
        "- `SyntheticLeave APInst offline:%u`",
        document_id="DOC-glm",
    )]
    patterns = compile_diagnostic_patterns(documents)
    selected = next(item for item in patterns if item.text.startswith("SyntheticTopo,"))
    case = Case(id="CASE-glm", title="AP频繁离线", device_type="AP")

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {"prompt_tokens": 27551, "completion_tokens": 3423, "total_tokens": 30974}
        last_duration_ms = 34260

        async def generate_json(self, *args, **kwargs):
            return {
                "read_document_ids": ["DOC-glm"],
                "selected_pattern_ids": [selected.id],
                "additional_keywords": ["offline", "Status=[0]"],
                "plan": "检查拓扑状态、心跳和离线事件。",
            }

    monkeypatch.setattr(log_triage, "get_llm_provider", lambda: _Provider())
    plan, metadata = asyncio.run(log_triage._plan_with_model(case, documents, patterns))

    assert plan["planner_mode"] == "llm"
    assert plan["selected_pattern_ids"] == [selected.id]
    assert [item["keyword"] for item in plan["additional_keywords"]] == [
        "offline", "Status=[0]",
    ]
    assert plan["screening_steps"] == ["检查拓扑状态、心跳和离线事件。"]
    assert plan["rationale"] == "检查拓扑状态、心跳和离线事件。"
    assert metadata["usage"]["total_tokens"] == 30974


def test_glm_schema_name_envelope_is_unwrapped_without_retry() -> None:
    documents = [_method(
        "## 日志关键词\n- `Synthetic offline marker`",
        document_id="DOC-envelope",
    )]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-envelope", title="AP频繁离线", device_type="AP")

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {"prompt_tokens": 21, "completion_tokens": 8}
        last_duration_ms = 17

        def __init__(self) -> None:
            self.calls = 0

        async def generate_json(self, *_args, **_kwargs):
            self.calls += 1
            return {"log_triage_plan": {
                "read_document_ids": ["DOC-envelope"],
                "selected_pattern_ids": [patterns[0].id],
                "additional_keywords": ["offline"],
                "screening_steps": ["scan"],
            }}

    provider = _Provider()
    plan, metadata = asyncio.run(log_triage._plan_with_model(
        case, documents, patterns, provider=provider,
    ))

    assert provider.calls == 1
    assert plan["selected_pattern_ids"] == [patterns[0].id]
    assert metadata["retry_count"] == 0
    assert metadata["usage"]["total_tokens"] == 29


def test_ap_offline_fallback_selects_clean_matchable_method_patterns() -> None:
    documents = [_method("""## 日志关键词

| 日志格式 | 说明 |
| --- | --- |
| **`SyntheticLeave APInst offline:%u`** | AP 离线 |
| `[Abnormal] curTime[%u], iAdvrTimeOut[%d], lastEventTime[%u]` | 心跳超时 |
| `SyntheticTopo, apInst=[X] Status=[0]` | 拓扑离线 |
""")]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-offline", title="AP频繁离线", description="AP频繁离线", device_type="AP")

    plan = log_triage._deterministic_plan(case, documents, patterns, reason="test")
    selected = {
        pattern.id: pattern
        for pattern in patterns
        if pattern.id in plan["selected_pattern_ids"]
    }

    assert selected
    assert all("|" not in item.text and "**" not in item.text and "`" not in item.text for item in selected.values())
    samples = [
        "SyntheticLeave APInst offline:12",
        "[Abnormal] curTime[1234], iAdvrTimeOut[250], lastEventTime[900]",
        "SyntheticTopo, apInst=[12] Status=[0]",
    ]
    assert all(
        any(re.search(pattern.regex, sample, re.IGNORECASE) for pattern in selected.values())
        for sample in samples
    )


def test_compiled_searchers_preserve_model_relevance_order() -> None:
    documents = [_method(
        "## 日志关键词\n- `First marker`\n- `Second marker`\n- `Third marker`"
    )]
    patterns = compile_diagnostic_patterns(documents)
    plan = {
        "selected_pattern_ids": [patterns[2].id, patterns[0].id, patterns[1].id],
        "additional_keywords": [],
    }

    searchers = {
        item["id"]: item for item in log_triage._compiled_searchers(patterns, plan)
    }

    assert searchers[patterns[2].id]["score"] > searchers[patterns[0].id]["score"]
    assert searchers[patterns[0].id]["score"] > searchers[patterns[1].id]["score"]


def test_glm_planner_receives_complete_method_content(monkeypatch) -> None:
    content = "## 日志关键词\n- `Heartbeat timeout`\n" + "完整方法正文" * 200
    documents = [_method(content, document_id="DOC-full")]
    patterns = compile_diagnostic_patterns(documents)
    case = Case(id="CASE-full", title="heartbeat", device_type="AP")
    captured: dict[str, str] = {}

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {}
        last_duration_ms = 1

        async def generate_json(self, system, user, **kwargs):
            captured["user"] = user
            return {
                "read_document_ids": ["DOC-full"],
                "selected_pattern_ids": [patterns[0].id],
                "additional_keywords": [],
                "screening_steps": ["scan"],
            }

    monkeypatch.setattr(log_triage, "get_llm_provider", lambda: _Provider())
    plan, metadata = asyncio.run(log_triage._plan_with_model(case, documents, patterns))

    rendered = json_loads(captured["user"], {})
    assert rendered["mandatory_method_documents"][0]["content"] == content
    assert plan["planner_mode"] == "llm"
    assert metadata["model"] == "glm-5.2"


def test_analysis_snapshot_omits_method_body_but_keeps_provenance() -> None:
    persisted = _evidence_for_persistence([{
        "evidence_id": "LOCALDOC-private",
        "source_type": "analysis_skill",
        "title": "Local method",
        "content": "private method body",
        "content_sha256": "d" * 64,
    }, {
        "evidence_id": "EVT-safe",
        "source_type": "log_event",
        "content": "runtime evidence remains available to the case",
    }])

    assert persisted[0]["content_omitted"] is True
    assert "content" not in persisted[0]
    assert persisted[0]["content_sha256"] == "d" * 64
    assert persisted[1]["content"] == "runtime evidence remains available to the case"


def test_final_synthesis_receives_complete_method_body_but_truncates_log_items() -> None:
    method_content = "完整筛查方法" * 1000
    log_content = "ordinary log detail " * 1000
    compact = diagnosis._compact_evidence_for_prompt([{
        "evidence_id": "DOC-full-method",
        "source_type": "fault_tree",
        "content": method_content,
    }, {
        "evidence_id": "EVT-long",
        "source_type": "log_event",
        "content": log_content,
    }])

    assert compact[0]["content"] == method_content
    assert compact[1]["content"].endswith("…[truncated]")
    assert len(compact[1]["content"]) < len(log_content)


def test_comprehensive_planner_executes_at_least_two_llm_rounds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'planning.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(diagnostic_methods, "_LOCAL_METHOD_FILES", {})
    calls = 0

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {"prompt_tokens": 100, "completion_tokens": 30}

        async def generate_json(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return {
                "read_document_ids": ["DOC-planning"],
                "method_assessments": [{
                    "method_document_id": "DOC-planning",
                    "relevance": "RELEVANT",
                    "rationale": "The synthetic case matches the method",
                    "matched_signals": ["synthetic"],
                }],
                "hypotheses": [f"hypothesis-{calls}"],
                "checks": [{
                    "check_id": f"check-{calls}",
                    "method_document_id": "DOC-planning",
                    "description": "Check synthetic evidence",
                    "evidence_needed": "Synthetic match",
                    "completion_rule": "Evidence is present or absent",
                }],
                "search_queries": [{
                    "query_id": f"query-{calls}",
                    "query": f"synthetic query {calls}",
                    "method_document_ids": ["DOC-planning"],
                    "rationale": "Verify the synthetic method",
                    "expected_evidence": "Synthetic evidence",
                }],
                "evidence_gaps": [],
                "continue_analysis": calls < 2,
                "stop_reason": "ENOUGH_EVIDENCE" if calls >= 2 else "MORE_EVIDENCE_NEEDED",
            }

    monkeypatch.setattr(diagnostic_planning, "get_llm_provider", lambda: _Provider())
    monkeypatch.setattr(diagnostic_planning, "agentic_search", lambda *args, **kwargs: {
        "run_id": "ARUN-search",
        "plan": {},
        "summary": {},
        "results": [{
            "evidence_id": "EVIDENCE-search",
            "source_type": "knowledge",
            "title": "Synthetic result",
            "content": "Synthetic result content",
            "source_score": 0.8,
        }],
        "paths": [],
    })
    with factory() as db:
        case = Case(
            id="CASE-planning",
            title="Synthetic planning",
            device_type="AP",
            model_egress_approved=True,
        )
        artifact = Artifact(
            id="ART-planning",
            case_id=case.id,
            original_name="synthetic.log",
            stored_path="synthetic.log",
            sha256="b" * 64,
            size_bytes=10,
            status="PARSED",
            active_parse_run_id="PRUN-planning",
        )
        db.add_all([
            case,
            artifact,
            KnowledgeDocument(
                id="DOC-planning",
                title="Synthetic fault tree",
                source_type="fault_tree",
                device_type="AP",
                content="# Fault tree\n\nCheck synthetic evidence.",
                active=True,
                review_status="ACTIVE",
            ),
            LogTriageRun(
                id="LTRIAGE-planning",
                case_id=case.id,
                artifact_id=artifact.id,
                parse_run_id="PRUN-planning",
                status="COMPLETED",
            ),
        ])
        db.flush()
        run = create_live_agent_run(
            db,
            operation="comprehensive_diagnosis",
            case_id=case.id,
            resource_type="analysis",
            resource_id="RUN-planning",
            input_summary={"case_id": case.id},
        )
        db.commit()

    result = diagnostic_planning.run_diagnostic_planning(
        _JobContext(),
        case=case,
        agent_run_id=run.id,
        baseline_search={"summary": {}, "results": []},
        session_factory=factory,
    )

    assert calls == 2
    assert result.public_plan["planner_mode"] == "llm_multiround"
    assert len(result.public_plan["rounds"]) == 2
    assert result.public_plan["stop_reason"] == "ENOUGH_EVIDENCE"
    assert result.public_plan["method_coverage"]["all_documents_read"] is True
    assert len(result.supplemental_results) == 2
    engine.dispose()


def test_comprehensive_planner_records_usage_when_round_validation_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'planning-invalid.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(diagnostic_methods, "_LOCAL_METHOD_FILES", {})

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {"prompt_tokens": 31, "completion_tokens": 9, "total_tokens": 40}

        async def generate_json(self, *args, **kwargs):
            return {"read_document_ids": [], "checks": "not-a-list"}

    monkeypatch.setattr(diagnostic_planning, "get_llm_provider", lambda: _Provider())
    with factory() as db:
        case = Case(
            id="CASE-invalid-plan", title="Invalid round", device_type="AP",
            model_egress_approved=True,
        )
        db.add_all([
            case,
            KnowledgeDocument(
                id="DOC-invalid-plan", title="Joint method", source_type="fault_tree",
                device_type="GW", content="# Joint method", active=True,
                review_status="ACTIVE",
            ),
        ])
        db.flush()
        run = create_live_agent_run(
            db, operation="comprehensive_diagnosis", case_id=case.id,
            resource_type="analysis", resource_id="RUN-invalid-plan",
            input_summary={"case_id": case.id},
        )
        db.commit()

    result = diagnostic_planning.run_diagnostic_planning(
        _JobContext(), case=case, agent_run_id=run.id,
        baseline_search={"summary": {}, "results": []}, session_factory=factory,
    )

    with factory() as db:
        persisted = db.get(AgentRun, run.id)
        failed = db.scalars(
            select(AgentTraceEvent).where(
                AgentTraceEvent.run_id == run.id,
                AgentTraceEvent.stage == "llm_planning_round_1",
            )
        ).one()
    assert result.public_plan["planner_mode"] == "deterministic_fallback"
    assert result.public_plan["planner_accepted"] is False
    assert result.public_plan["planner_failure"]["code"] == (
        "PLANNER_SCHEMA_VALIDATION_ERROR"
    )
    assert any(
        call["tool_name"] == "read_diagnostic_documents"
        and call["status"] == "COMPLETED"
        for call in result.public_plan["tool_calls"]
    )
    assert failed.status == "FAILED"
    assert failed.input_tokens == 93
    assert failed.output_tokens == 27
    assert failed.retry_count == 2
    assert persisted.total_tokens == 120
    engine.dispose()


def test_comprehensive_planner_normalizes_glm_rich_objects_and_retries(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'planning-glm-shape.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(diagnostic_methods, "_LOCAL_METHOD_FILES", {})

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False

        def __init__(self) -> None:
            self.calls: list[dict] = []
            self.last_usage = {}
            self.last_duration_ms = 0

        async def generate_json(self, _system, user, **_kwargs):
            payload = json_loads(user, {})
            self.calls.append(payload)
            call_number = len(self.calls)
            self.last_usage = {
                "prompt_tokens": 100 * call_number,
                "completion_tokens": 20 * call_number,
            }
            self.last_duration_ms = 10 * call_number
            result = {
                "read_document_ids": ["DOC-glm-tree"],
                "hypotheses": [{
                    "id": "H1",
                    "description": "AP offline may follow heartbeat timeout",
                }],
                "checks": [{
                    "id": "C1",
                    "document_id": "DOC-glm-tree",
                    "description": "Inspect heartbeat and offline transitions",
                    "expected_evidence": "Matching AP/GW log evidence",
                    "success_criteria": "Record support, contradiction, or a gap",
                }],
                "search_queries": [{
                    "id": "SQ1",
                    "query": "AP offline heartbeat timeout topology state",
                    "document_ids": ["DOC-glm-tree"],
                    "purpose": "Verify the fault-tree branch",
                    "expected_result": "Relevant evidence",
                }],
                "evidence_gaps": [{"description": "No uploaded log in this test"}],
                "continue_analysis": False,
                "stop_reason": "ENOUGH_EVIDENCE",
            }
            if call_number == 1:
                return result
            result["method_assessments"] = [{
                "document_id": "DOC-glm-tree",
                "relevance": "相关",
                "reason": "The case symptom directly overlaps the fault tree",
                "signals": [{"text": "AP offline"}],
            }]
            return result

    provider = _Provider()
    with factory() as db:
        case = Case(
            id="CASE-glm-shape", title="AP频繁离线", description="AP频繁离线",
            device_type="AP", model_egress_approved=True,
        )
        db.add_all([
            case,
            KnowledgeDocument(
                id="DOC-glm-tree", title="AP offline fault tree",
                source_type="fault_tree", device_type="GW",
                content="# AP频繁离线\n\n检查 heartbeat timeout 与拓扑状态。",
                active=True, review_status="ACTIVE",
            ),
        ])
        db.flush()
        run = create_live_agent_run(
            db, operation="comprehensive_diagnosis", case_id=case.id,
            resource_type="analysis", resource_id="RUN-glm-shape",
            input_summary={"case_id": case.id},
        )
        db.commit()

    monkeypatch.setattr(diagnostic_planning, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(diagnostic_planning, "agentic_search", lambda *args, **kwargs: {
        "run_id": "ARUN-glm-search",
        "plan": {"selected_modules": ["knowledge"]},
        "summary": {"knowledge_scope": "GW_AP_JOINT"},
        "results": [],
        "paths": [],
    })
    result = diagnostic_planning.run_diagnostic_planning(
        _JobContext(), case=case, agent_run_id=run.id,
        baseline_search={"summary": {}, "results": []}, session_factory=factory,
    )

    assert len(provider.calls) == 3
    assert provider.calls[1]["correction"]["symptom_relevant_fault_tree_ids"] == [
        "DOC-glm-tree",
    ]
    assert "日志分析方法" in provider.calls[1]["correction"]["instruction"]
    first_round = result.public_plan["rounds"][0]
    assert first_round["planning_attempts"] == 2
    assert first_round["method_assessments"][0]["relevance"] == "RELEVANT"
    assert first_round["checks"][0]["method_document_id"] == "DOC-glm-tree"
    assert first_round["search_queries"][0]["method_document_ids"] == [
        "DOC-glm-tree",
    ]
    assert result.public_plan["search_query_count"] == 1
    assert result.public_plan["planner_mode"] == "llm_multiround"
    with factory() as db:
        first_trace = db.scalars(select(AgentTraceEvent).where(
            AgentTraceEvent.run_id == run.id,
            AgentTraceEvent.stage == "llm_planning_round_1",
        )).one()
        search_trace = db.scalars(select(AgentTraceEvent).where(
            AgentTraceEvent.run_id == run.id,
            AgentTraceEvent.stage == "execute_agent_tool",
            AgentTraceEvent.tool_name == "search_knowledge",
        )).one()
    assert first_trace.retry_count == 1
    assert first_trace.input_tokens == 300
    assert first_trace.output_tokens == 60
    assert search_trace.status == "COMPLETED"
    engine.dispose()


def test_comprehensive_planner_rejects_symptom_matching_tree_as_not_relevant() -> None:
    case = Case(
        id="CASE-relevance-gate", title="AP频繁离线",
        description="AP频繁离线", device_type="AP",
    )
    method = DiagnosticMethodDocument(
        id="DOC-relevance-gate", title="AP offline tree", source_type="fault_tree",
        version=1, device_type="AP", module=None,
        content="# AP频繁离线\n检查离线与心跳。", content_sha256="c" * 64,
        role="FAULT_TREE",
    )
    parsed = diagnostic_planning_contract.PlanningRound.model_validate({
        "read_document_ids": [method.id],
        "method_assessments": [{
            "method_document_id": method.id,
            "relevance": "NOT_RELEVANT",
            "rationale": "No relation",
        }],
        "hypotheses": [],
        "checks": [],
        "search_queries": [],
        "evidence_gaps": [],
    })

    with pytest.raises(ValueError, match="overlaps the case symptom"):
        diagnostic_planning_contract.validate_planning_round(
            parsed, round_number=1, case=case, methods=[method],
        )


def test_first_round_requires_search_coverage_for_each_relevant_method() -> None:
    case = Case(
        id="CASE-search-coverage", title="AP frequent offline", device_type="AP",
    )
    methods = [
        DiagnosticMethodDocument(
            id=f"DOC-search-{index}", title=f"Method {index}",
            source_type="analysis_method", version=1, device_type="AP",
            module=None, content=f"Method body {index}",
            content_sha256=str(index) * 64, role="LOG_ANALYSIS_METHOD",
        )
        for index in (1, 2)
    ]
    parsed = diagnostic_planning_contract.PlanningRound.model_validate({
        "read_document_ids": [method.id for method in methods],
        "method_assessments": [{
            "method_document_id": method.id,
            "relevance": "RELEVANT",
            "rationale": "Applicable",
        } for method in methods],
        "checks": [{
            "check_id": f"check-{index}",
            "method_document_id": method.id,
            "description": "Check the method",
            "evidence_needed": "Evidence",
            "completion_rule": "Support or exclude",
        } for index, method in enumerate(methods, start=1)],
        "search_queries": [{
            "query_id": "query-one",
            "query": "search method one",
            "method_document_ids": [methods[0].id],
            "rationale": "Verify method one",
            "expected_evidence": "Evidence",
        }],
    })

    with pytest.raises(ValueError, match="Every relevant method"):
        diagnostic_planning_contract.validate_planning_round(
            parsed, round_number=1, case=case, methods=methods,
        )


def test_comprehensive_planner_stops_at_twenty_round_hard_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'planning-twenty.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(diagnostic_methods, "_LOCAL_METHOD_FILES", {})
    calls = 0

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {}

        async def generate_json(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            return {
                "read_document_ids": ["DOC-twenty"],
                "method_assessments": [{
                    "method_document_id": "DOC-twenty",
                    "relevance": "POSSIBLY_RELEVANT",
                    "rationale": "Continue bounded verification",
                    "matched_signals": [],
                }],
                "hypotheses": [f"hypothesis-{calls}"],
                "checks": [{
                    "check_id": f"check-{calls}",
                    "method_document_id": "DOC-twenty",
                    "description": "Continue bounded verification",
                    "evidence_needed": "More evidence",
                    "completion_rule": "Reach hard round budget",
                }],
                "search_queries": [{
                    "query_id": f"query-{calls}",
                    "query": f"bounded verification query {calls}",
                    "method_document_ids": ["DOC-twenty"],
                    "rationale": "Continue bounded verification",
                    "expected_evidence": "More evidence",
                }],
                "evidence_gaps": ["More evidence required"],
                "continue_analysis": True,
                "stop_reason": "MORE_EVIDENCE_NEEDED",
            }

    monkeypatch.setattr(diagnostic_planning, "get_llm_provider", lambda: _Provider())
    with factory() as db:
        case = Case(
            id="CASE-twenty", title="Twenty round planning", device_type="AP",
            model_egress_approved=True,
        )
        db.add_all([
            case,
            KnowledgeDocument(
                id="DOC-twenty", title="GW/AP joint method", source_type="fault_tree",
                device_type="GW", content="# Joint method", active=True,
                review_status="ACTIVE",
            ),
        ])
        db.flush()
        run = create_live_agent_run(
            db, operation="comprehensive_diagnosis", case_id=case.id,
            resource_type="analysis", resource_id="RUN-twenty",
            input_summary={"case_id": case.id},
        )
        db.commit()

    result = diagnostic_planning.run_diagnostic_planning(
        _JobContext(), case=case, agent_run_id=run.id,
        baseline_search={"summary": {}, "results": []}, session_factory=factory,
    )

    assert calls == 20
    assert len(result.public_plan["rounds"]) == 20
    assert result.public_plan["stop_reason"] == "MAX_PLANNING_ROUNDS"
    assert result.public_plan["method_coverage"]["all_documents_read"] is True
    engine.dispose()


def test_comprehensive_diagnosis_never_calls_real_model_without_case_consent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'no-egress.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(diagnostic_methods, "_LOCAL_METHOD_FILES", {})

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False

        async def generate_json(self, *args, **kwargs):
            raise AssertionError("The model must not be called without case consent")

    provider = _Provider()
    monkeypatch.setattr(diagnostic_planning, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(diagnosis, "get_llm_provider", lambda: provider)
    with factory() as db:
        case = Case(
            id="CASE-no-egress",
            title="No external egress",
            device_type="AP",
            model_egress_approved=False,
        )
        db.add(case)
        db.flush()
        run = create_live_agent_run(
            db,
            operation="comprehensive_diagnosis",
            case_id=case.id,
            resource_type="analysis",
            resource_id="RUN-no-egress",
            input_summary={"case_id": case.id},
        )
        db.commit()

    planning = diagnostic_planning.run_diagnostic_planning(
        _JobContext(),
        case=case,
        agent_run_id=run.id,
        baseline_search={"summary": {}, "results": []},
        session_factory=factory,
    )
    deterministic = {"case": {"id": case.id}, "summary": "local only"}
    synthesis = asyncio.run(diagnosis._augment_with_llm(case, deterministic, []))

    assert planning.public_plan["planner_mode"] == "deterministic_fallback"
    assert planning.public_plan["stop_reason"] == "MODEL_EGRESS_NOT_APPROVED"
    assert synthesis is deterministic
    engine.dispose()
