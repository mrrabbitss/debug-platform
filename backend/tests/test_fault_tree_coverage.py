from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_loads
from app.diagnostic_models import LogEvidenceMatch, LogTriageRun
from app.models import Artifact, Case, KnowledgeDocument
from app.services import diagnostic_methods, diagnostic_planning
from app.services.agent_trace_runtime import create_live_agent_run
from app.services.diagnosis_contract import validate_llm_diagnosis
from app.services.diagnostic_log_search import search_persisted_log_evidence
from app.services.diagnostic_methods import DiagnosticMethodDocument
from app.services.diagnostic_planning_prompt import fault_tree_items_for_prompt
from app.services.diagnostic_planning_contract import (
    PlanningRound,
    validate_planning_round,
)
from app.services.diagnostic_planning_coverage import validate_fault_tree_bindings
from app.services.diagnostic_tools import SearchLogInput
from app.services.fault_tree_coverage import compile_fault_tree_items


class _JobContext:
    def update(self, *_args, **_kwargs) -> None:
        return None

    def raise_if_cancelled(self) -> None:
        return None


def _method(content: str) -> DiagnosticMethodDocument:
    return DiagnosticMethodDocument(
        id="DOC-tree",
        title="Synthetic AP fault tree",
        source_type="fault_tree",
        version=1,
        device_type="AP",
        module="WLAN",
        content=content,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        role="FAULT_TREE",
    )


def test_fault_tree_compiler_extracts_flow_root_causes_and_decisions() -> None:
    content = """# AP offline tree

```
│ 步骤1: identify the AP
│ 1.1 inspect Status=[0]
│ 场景1: physical link failure
```

## Decision table
| 判断点 | 日志关键字 | 判断结论 |
| --- | --- | --- |
| AP是否离线 | `Status=[0]` | AP离线 |
| 链路检测 | `TestLinkOK failed` | 链路失败 |
"""

    first = compile_fault_tree_items([_method(content)])
    second = compile_fault_tree_items([_method(content)])

    assert [item.id for item in first] == [item.id for item in second]
    assert [(item.category, item.label) for item in first] == [
        ("FLOW_STEP", "步骤1"),
        ("FLOW_STEP", "1.1"),
        ("ROOT_CAUSE", "场景1"),
        ("DECISION", "AP是否离线"),
        ("DECISION", "链路检测"),
    ]
    assert first[3].evidence_hints == ["Status=[0]"]


def test_fault_tree_prompt_gives_every_node_an_auditable_retrieval_entry() -> None:
    tree = _method("""# AP offline tree

```
│ 步骤1: inspect topology
│ - Parent: 33 means direct GW connection
│ 步骤2: distinguish link and protocol failures
│ 2.1 inspect `TestLinkOK failed`
│ 场景1: physical link failure
│ - AP cannot obtain an IP address
```

| 判断点 | 日志关键字 | 判断结论 |
| --- | --- | --- |
| AP是否离线 | `Status=[0]` | AP离线 |
""")
    log_method = DiagnosticMethodDocument(
        id="DOC-log-method",
        title="Synthetic log method",
        source_type="analysis_skill",
        version=1,
        device_type="GENERAL",
        module="WLAN",
        content="""# 日志关键词
- `AddAPTopoTree, APInst: X, Parent: Y`
- `RefreshTopoTree APInstId: X TestLinkOK failed`
""",
        content_sha256="b" * 64,
        role="LOG_ANALYSIS_METHOD",
    )
    methods = [tree, log_method]
    items = compile_fault_tree_items(methods)
    patterns = diagnostic_methods.compile_diagnostic_patterns(methods)

    prompt_items = fault_tree_items_for_prompt(items, patterns)

    assert prompt_items
    assert all(
        item["recommended_patterns"] or item["recommended_search_terms"]
        for item in prompt_items
    )
    topology = next(item for item in prompt_items if item["label"] == "步骤1")
    assert any("Parent" in pattern["text"] for pattern in topology["recommended_patterns"])
    assert all(
        {"id", "text", "document_id", "heading", "line_start"} <= set(pattern)
        for item in prompt_items
        for pattern in item["recommended_patterns"]
    )


def test_unattempted_nodes_cannot_be_concluded_without_bound_tools() -> None:
    items = compile_fault_tree_items([_method("""# AP offline tree

```
│ 步骤1: inspect topology
│ 步骤2: inspect protocol state
```
""")])
    first_id, second_id = [item.id for item in items]
    planning_round = PlanningRound.model_validate({
        "fault_tree_assessments": [
            {
                "item_id": item.id,
                "method_document_id": item.method_document_id,
                "status": "INSUFFICIENT_EVIDENCE",
                "rationale": "No evidence is available.",
                "next_action": "Collect the required log.",
            }
            for item in items
        ],
        "checks": [{
            "check_id": "check-first",
            "method_document_id": "DOC-tree",
            "description": "Inspect the first node",
            "evidence_needed": "Topology evidence",
            "completion_rule": "Record support, exclusion, or an evidence gap",
            "fault_tree_item_ids": [first_id],
        }],
        "tool_calls": [{
            "call_id": "search-first",
            "tool_name": "search_log",
            "arguments": {"keywords": ["topology"]},
            "method_document_ids": ["DOC-tree"],
            "rationale": "Search the first node",
            "fault_tree_item_ids": [first_id],
        }],
    })

    with pytest.raises(ValueError, match="cannot receive terminal conclusions"):
        validate_fault_tree_bindings(
            planning_round,
            items=items,
            unattempted_item_ids={first_id, second_id},
            valid_evidence_ids=set(),
        )


def test_unknown_tool_pattern_is_rejected_before_execution() -> None:
    method = _method("# AP offline tree\n")
    case = Case(id="CASE-pattern-gate", title="AP频繁离线", device_type="AP")
    planning_round = PlanningRound.model_validate({
        "read_document_ids": [method.id],
        "method_assessments": [{
            "method_document_id": method.id,
            "relevance": "RELEVANT",
            "rationale": "The symptom overlaps the AP offline tree.",
        }],
        "checks": [{
            "check_id": "check-pattern",
            "method_document_id": method.id,
            "description": "Search the compiled offline signature",
            "evidence_needed": "A matching log line",
            "completion_rule": "Record support, exclusion, or an evidence gap",
        }],
        "tool_calls": [{
            "call_id": "search-pattern",
            "tool_name": "search_log",
            "arguments": {
                "pattern_ids": ["DPAT-hallucinated"],
                "method_document_ids": [method.id],
            },
            "method_document_ids": [method.id],
            "rationale": "Search the diagnostic log",
        }],
    })

    with pytest.raises(ValueError, match="unknown diagnostic pattern IDs"):
        validate_planning_round(
            planning_round,
            round_number=1,
            case=case,
            methods=[method],
            diagnostic_patterns=[],
        )


def test_multiround_planner_only_accepts_complete_fault_tree_coverage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'fault-tree-planning.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(diagnostic_methods, "_LOCAL_METHOD_FILES", {})
    calls = 0

    class _Provider:
        provider_id = "openai_compatible"
        model_name = "glm-5.2"
        is_mock = False
        last_usage = {"prompt_tokens": 10, "completion_tokens": 5}
        last_duration_ms = 1

        async def generate_json(self, _system, user, **_kwargs):
            nonlocal calls
            calls += 1
            prompt = json_loads(user, {})
            items = prompt["fault_tree_items"]
            item_ids = [item["id"] for item in items]
            base = {
                "read_document_ids": ["DOC-runtime-tree"],
                "method_assessments": [{
                    "method_document_id": "DOC-runtime-tree",
                    "relevance": "RELEVANT",
                    "rationale": "The case directly matches the AP offline tree",
                }],
                "hypotheses": ["Synthetic AP offline branch"],
                "checks": [{
                    "check_id": f"check-{calls}",
                    "method_document_id": "DOC-runtime-tree",
                    "description": "Evaluate every pending tree node",
                    "evidence_needed": "Log or knowledge evidence",
                    "completion_rule": "Support, exclude, or record an evidence gap",
                    "fault_tree_item_ids": item_ids if calls == 1 else [],
                }],
                "search_queries": [],
                "tool_calls": [],
                "evidence_gaps": ["Synthetic test contains no uploaded evidence"],
                "continue_analysis": calls < 2,
                "stop_reason": "MORE_EVIDENCE_NEEDED" if calls < 2 else "ALL_TREE_ITEMS_CONCLUDED",
            }
            if calls == 1:
                base["tool_calls"] = [{
                    "call_id": "search-all-tree-items",
                    "tool_name": "search_log",
                    "arguments": {
                        "keywords": ["Status=[0]", "TestLinkOK failed"],
                        "method_document_ids": ["DOC-runtime-tree"],
                    },
                    "method_document_ids": ["DOC-runtime-tree"],
                    "fault_tree_item_ids": item_ids,
                    "rationale": "Search evidence for every compiled tree item",
                }]
                base["fault_tree_assessments"] = [{
                    "item_id": item["id"],
                    "method_document_id": item["method_document_id"],
                    "status": "INSUFFICIENT_EVIDENCE",
                    "rationale": "No case log evidence exists in this isolated test",
                    "evidence_ids": [],
                    "next_action": "Upload the required GW and AP logs",
                } for item in items]
            return base

    provider = _Provider()
    monkeypatch.setattr(diagnostic_planning, "get_llm_provider", lambda: provider)
    content = """# AP frequent offline

```
│ 步骤1: inspect AP status
│ 场景1: physical link failure
```

| 判断点 | 日志关键字 | 判断结论 |
| --- | --- | --- |
| AP是否离线 | `Status=[0]` | AP离线 |
| 链路检测 | `TestLinkOK failed` | 链路失败 |
"""
    with factory() as db:
        case = Case(
            id="CASE-tree-coverage",
            title="AP频繁离线",
            description="AP频繁离线",
            device_type="AP",
            model_egress_approved=True,
        )
        db.add_all([
            case,
            KnowledgeDocument(
                id="DOC-runtime-tree",
                title="AP offline tree",
                source_type="fault_tree",
                device_type="GW",
                content=content,
                active=True,
                review_status="ACTIVE",
            ),
        ])
        db.flush()
        run = create_live_agent_run(
            db,
            operation="comprehensive_diagnosis",
            case_id=case.id,
            resource_type="analysis",
            resource_id="RUN-tree-coverage",
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

    coverage = result.public_plan["fault_tree_coverage"]
    assert calls == 2
    assert result.public_plan["planner_accepted"] is True
    assert result.public_plan["stop_reason"] == "ALL_TREE_ITEMS_CONCLUDED"
    assert coverage["total"] == 4
    assert coverage["attempted"] == 4
    assert coverage["concluded"] == 4
    assert coverage["complete"] is True
    assert coverage["status_counts"]["INSUFFICIENT_EVIDENCE"] == 4
    engine.dispose()


def test_persisted_log_tool_search_is_not_limited_to_prompt_candidates(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'full-log-search.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        case = Case(id="CASE-log-search", title="AP offline", device_type="AP")
        artifact = Artifact(
            id="ART-log-search",
            case_id=case.id,
            original_name="synthetic.log",
            stored_path="synthetic.log",
            sha256="a" * 64,
            size_bytes=10,
            status="PARSED",
            active_parse_run_id="PRUN-log-search",
        )
        triage = LogTriageRun(
            id="LTRIAGE-log-search",
            case_id=case.id,
            artifact_id=artifact.id,
            parse_run_id="PRUN-log-search",
            status="COMPLETED",
        )
        db.add_all([case, artifact, triage])
        db.flush()
        db.add_all([
            LogEvidenceMatch(
                id=f"LEM-{index:04d}",
                triage_run_id=triage.id,
                case_id=case.id,
                artifact_id=artifact.id,
                source_file="synthetic.log",
                line_start=index + 1,
                line_end=index + 1,
                bucket="METHOD_REQUIRED",
                relevance_score=1.0 - index / 1000,
                pattern_id=f"DPAT-{index:04d}",
                pattern_text="ordinary pattern",
                message=(
                    "late target listen port check failed"
                    if index == 549 else f"ordinary event {index}"
                ),
                occurrence_count=1,
                metadata_json="{}",
            )
            for index in range(550)
        ])
        db.commit()

    result = search_persisted_log_evidence(
        triage_run_ids=["LTRIAGE-log-search"],
        artifact_sources=[{"artifact_id": "ART-log-search", "device_type": "AP"}],
        payload=SearchLogInput(keywords=["listen port check failed"], top_k=10),
        session_factory=factory,
    )

    assert result["total_candidates"] == 1
    assert result["results"][0]["evidence_id"] == "LEM-0549"
    assert result["results"][0]["line_start"] == 550
    engine.dispose()


def test_final_diagnosis_requires_every_evidence_gated_tree_conclusion() -> None:
    required = {
        "FTITEM-one": {
            "id": "FTITEM-one",
            "method_document_id": "DOC-tree",
            "status": "SUPPORTED",
        },
    }
    payload = {
        "summary": "The synthetic evidence supports one fault-tree node.",
        "confirmed_facts": [{"statement": "AP is offline", "evidence_ids": ["EV-one"]}],
        "hypotheses": [{
            "title": "Physical link failure",
            "description": "Synthetic hypothesis",
            "supporting_evidence": ["EV-one"],
            "contradicting_evidence": [],
            "confidence_score": 0.8,
            "confidence_level": "HIGH",
            "priority": "P1",
        }],
        "recommended_actions": [],
        "missing_information": [],
        "suspected_modules": ["WLAN"],
        "limitations": [],
        "fault_tree_conclusions": [{
            "item_id": "FTITEM-one",
            "method_document_id": "DOC-tree",
            "status": "SUPPORTED",
            "conclusion": "The node is supported by the synthetic event.",
            "evidence_ids": ["EV-one"],
            "next_action": "Verify the physical cable.",
        }],
    }

    validated = validate_llm_diagnosis(payload, {"EV-one"}, required)
    assert validated["fault_tree_conclusions"][0]["item_id"] == "FTITEM-one"
    with pytest.raises(ValueError, match="every required fault-tree item"):
        validate_llm_diagnosis({**payload, "fault_tree_conclusions": []}, {"EV-one"}, required)
