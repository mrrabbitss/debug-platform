import asyncio
import re
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_dumps, json_loads
from app.diagnostic_models import LogTriageRun
from app.models import Artifact, Case, KnowledgeDocument, LogEvent
from app.services import diagnosis, diagnostic_methods, diagnostic_planning, log_triage
from app.services.diagnosis import _evidence_for_persistence
from app.services.agent_trace_runtime import create_live_agent_run
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


def test_method_catalog_only_loads_published_applicable_documents(
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

    assert {item.id for item in loaded} == {"DOC-ap", "DOC-general"}
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


def test_llm_log_plan_must_attest_every_method_document(monkeypatch) -> None:
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

    assert plan["planner_mode"] == "deterministic_fallback"
    assert plan["read_document_ids"] == ["DOC-1"]
    assert metadata["fallback"] is True
    assert metadata["error_type"] == "ValueError"


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
                "hypotheses": [f"hypothesis-{calls}"],
                "checks": [{
                    "check_id": f"check-{calls}",
                    "method_document_id": "DOC-planning",
                    "description": "Check synthetic evidence",
                    "evidence_needed": "Synthetic match",
                    "completion_rule": "Evidence is present or absent",
                }],
                "search_queries": [f"synthetic query {calls}"],
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
