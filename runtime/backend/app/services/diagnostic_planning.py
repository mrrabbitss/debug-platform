from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, mask_sensitive
from app.diagnostic_models import LogEvidenceMatch, LogTriageRun
from app.models import Artifact, Case
from app.services.agent_trace_runtime import append_live_trace
from app.services.agent_runtime import (
    ContextWindowPolicy,
    EvidenceSpillStore,
    configured_context_policy,
)
from app.services.agent_runtime.budget import merge_usage_totals
from app.services.agentic.tools import ToolContext
from app.services.agentic_search import agentic_search
from app.services.diagnostic_agent_budget import (
    DiagnosticAgentBudget,
    DiagnosticAgentBudgetTracker,
    configured_diagnostic_agent_budget,
)
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    DiagnosticPattern,
    compile_diagnostic_patterns,
    load_applicable_diagnostic_methods,
)
from app.services.diagnostic_scope import normalize_artifact_source
from app.services.diagnostic_log_search import search_persisted_log_evidence
from app.services.diagnostic_planning_agent import execute_llm_planning_rounds
from app.services.diagnostic_planning_coverage import (
    coverage_snapshot,
    initial_fault_tree_coverage,
)
from app.services.diagnostic_planning_context import (
    build_governed_planning_prompt,
    context_attempt_summary,
    observe_context_attempt,
)
from app.services.diagnostic_tools import (
    DiagnosticToolEnvironment,
    build_diagnostic_tool_registry,
    invoke_diagnostic_tool,
    summarize_method_usage,
)
from app.services.diagnostic_planning_contract import (
    PlanningRound as _PlanningRound,
    normalize_planning_round as _normalize_planning_round,
    symptom_relevant_method_ids as _symptom_relevant_method_ids,
    validate_planning_round as _validate_planning_round,
)
from app.services.jobs import JobContext
from app.services.llm import LLMError, get_llm_provider
from app.services.fault_tree_coverage import FaultTreeCoverageItem, compile_fault_tree_items


DIAGNOSTIC_PLANNER_PROMPT_VERSION = "diagnostic-tool-agent-v4-fault-tree-coverage"
MAX_PLANNING_ROUNDS = 20
MAX_PLANNING_ATTEMPTS = 3


@dataclass
class DiagnosticPlanningResult:
    public_plan: dict[str, Any]
    method_documents: list[DiagnosticMethodDocument]
    evidence: list[dict[str, Any]]
    supplemental_results: list[dict[str, Any]]


def _triage_evidence(
    case_id: str,
    session_factory: Any,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    artifact_sources: dict[str, dict[str, Any]] = {}
    with session_factory() as db:
        parsed_artifacts = list(db.scalars(
            select(Artifact).where(
                Artifact.case_id == case_id,
                Artifact.active_parse_run_id.is_not(None),
            )
        ).all())
        case = db.get(Case, case_id)
        artifact_sources = {
            artifact.id: normalize_artifact_source(artifact, case)
            for artifact in parsed_artifacts
        }
        completed_triages = list(db.scalars(
            select(LogTriageRun)
            .where(
                LogTriageRun.case_id == case_id,
                LogTriageRun.status == "COMPLETED",
            )
            .order_by(LogTriageRun.created_at.desc())
        ).all())
        latest_by_generation: dict[tuple[str, str | None], LogTriageRun] = {}
        for triage in completed_triages:
            latest_by_generation.setdefault(
                (triage.artifact_id, triage.parse_run_id),
                triage,
            )
        active_generations = {
            (artifact.id, artifact.active_parse_run_id)
            for artifact in parsed_artifacts
        }
        missing_artifacts = [
            artifact.id
            for artifact in parsed_artifacts
            if (artifact.id, artifact.active_parse_run_id) not in latest_by_generation
        ]
        selected_triages = [
            latest_by_generation[key]
            for key in active_generations
            if key in latest_by_generation
        ]
        triage_ids = [triage.id for triage in selected_triages]
        rows: list[LogEvidenceMatch] = []
        # Reserve an equal candidate budget per active artifact so a large GW
        # log cannot hide all AP evidence (or vice versa) before LLM planning.
        selected_triages = selected_triages[:500]
        per_artifact_limit = max(1, 500 // max(1, len(selected_triages)))
        rows_by_triage: list[list[LogEvidenceMatch]] = []
        for triage in selected_triages:
            rows_by_triage.append(list(db.scalars(
                select(LogEvidenceMatch)
                .where(LogEvidenceMatch.triage_run_id == triage.id)
                .order_by(
                    LogEvidenceMatch.bucket.asc(),
                    LogEvidenceMatch.relevance_score.desc(),
                    LogEvidenceMatch.occurrence_count.desc(),
                )
                .limit(per_artifact_limit)
            ).all()))
        for position in range(per_artifact_limit):
            for triage_rows in rows_by_triage:
                if position < len(triage_rows):
                    rows.append(triage_rows[position])
    evidence = [
        {
            "evidence_id": row.id,
            "source_type": "log_triage_match",
            "artifact_id": row.artifact_id,
            "bucket": row.bucket,
            "source_file": row.source_file,
            "line_start": row.line_start,
            "line_end": row.line_end,
            "content": mask_sensitive(row.message),
            "pattern_id": row.pattern_id,
            "pattern_text": row.pattern_text,
            "reason": row.reason,
            "meaning": row.meaning or row.reason,
            "occurrence_count": row.occurrence_count,
            "score": row.relevance_score,
            "method_document_id": row.method_document_id,
            "metadata": json_loads(row.metadata_json, {}),
            "artifact_source": artifact_sources.get(row.artifact_id, {}),
        }
        for row in rows
    ]
    return evidence, {
        "parsed_artifact_count": len(parsed_artifacts),
        "completed_triage_count": len(selected_triages),
        "missing_artifact_ids": missing_artifacts,
        "triage_run_ids": triage_ids,
        "artifact_sources": list(artifact_sources.values()),
    }


async def _request_planning_round(
    provider: Any,
    *,
    round_number: int,
    case: Case,
    methods: list[DiagnosticMethodDocument],
    triage_evidence: list[dict[str, Any]],
    prior_rounds: list[dict[str, Any]],
    search_observations: list[dict[str, Any]],
    tool_manifest: list[dict[str, Any]],
    document_observation: dict[str, Any],
    fault_tree_items: list[FaultTreeCoverageItem],
    diagnostic_patterns: list[DiagnosticPattern],
    fault_tree_coverage: dict[str, Any],
    unattempted_fault_tree_item_ids: set[str],
    valid_evidence_ids: set[str],
    context_policy: ContextWindowPolicy,
    spill_store: EvidenceSpillStore,
) -> _PlanningRound:
    expected = {method.id for method in methods}
    cumulative_usage = dict.fromkeys((
        "prompt_tokens", "completion_tokens", "total_tokens",
        "cached_tokens", "reasoning_tokens",
    ), 0)
    cumulative_duration_ms = 0
    context_attempts: list[dict[str, Any]] = []
    validation_error: ValidationError | ValueError | None = None
    for attempt in range(1, MAX_PLANNING_ATTEMPTS + 1):
        correction: dict[str, Any] | None = None
        if validation_error is not None:
            correction = {
                "attempt": attempt,
                "previous_error_type": type(validation_error).__name__,
                "previous_error": str(validation_error)[:1500],
                "required_read_document_ids": sorted(expected),
                "required_method_assessment_ids": sorted(expected),
                "symptom_relevant_fault_tree_ids": sorted(
                    _symptom_relevant_method_ids(case, methods)
                ),
                "required_fault_tree_item_ids": sorted(
                    item.id for item in fault_tree_items
                ),
                "unattempted_fault_tree_item_ids": sorted(
                    unattempted_fault_tree_item_ids
                ),
                "instruction": (
                    "重新输出完整 JSON 对象并严格遵守 output_contract。每个相关故障树"
                    "或日志分析方法都必须产生绑定其 method_document_id 的 check，且至少"
                    "一个 search_query 或 search_knowledge/search_log tool_call 的"
                    " method_document_ids 必须引用它。尚未在本轮绑定检查和证据工具的"
                    " fault_tree_item 必须保持 PENDING。"
                ),
            }
        request_prompt, context_metrics = build_governed_planning_prompt(
            round_number=round_number,
            case=case,
            methods=methods,
            triage_evidence=triage_evidence,
            prior_rounds=prior_rounds,
            search_observations=search_observations,
            tool_manifest=tool_manifest,
            document_observation=document_observation,
            fault_tree_items=fault_tree_items,
            diagnostic_patterns=diagnostic_patterns,
            fault_tree_coverage=fault_tree_coverage,
            unattempted_fault_tree_item_ids=unattempted_fault_tree_item_ids,
            output_contract=_PlanningRound.model_json_schema(),
            context_policy=context_policy,
            spill_store=spill_store,
            correction=correction,
        )
        context_metrics["attempt"] = attempt
        context_attempts.append(context_metrics)
        provider.last_context_metrics = context_attempt_summary(
            context_attempts,
            total_prompt_tokens=cumulative_usage["prompt_tokens"],
        )
        if not context_metrics.get("within_budget"):
            raise ValueError("Context input exceeds the configured model budget")
        try:
            raw = await provider.generate_json(
                "你是受预算约束的 GW/AP 综合诊断 Planner。逐轮形成假设、执行可验证检查、寻找反证并决定是否停止。",
                json_dumps(request_prompt),
                schema_name="diagnostic_planning_round",
                purpose=f"diagnostic_planning_round_{round_number}",
            )
        except LLMError:
            attempt_usage = getattr(provider, "last_usage", {}) or {}
            observe_context_attempt(context_metrics, attempt_usage)
            merge_usage_totals(cumulative_usage, attempt_usage)
            cumulative_duration_ms += int(getattr(provider, "last_duration_ms", 0) or 0)
            provider.last_context_metrics = context_attempt_summary(
                context_attempts,
                total_prompt_tokens=cumulative_usage["prompt_tokens"],
            )
            provider.last_usage = cumulative_usage
            provider.last_duration_ms = cumulative_duration_ms
            provider.last_validation_retry_count = max(0, attempt - 1)
            raise
        attempt_usage = getattr(provider, "last_usage", {}) or {}
        observe_context_attempt(context_metrics, attempt_usage)
        merge_usage_totals(cumulative_usage, attempt_usage)
        cumulative_duration_ms += int(getattr(provider, "last_duration_ms", 0) or 0)
        try:
            normalized = _normalize_planning_round(raw)
            if isinstance(normalized, dict):
                normalized["read_document_ids"] = sorted(expected)
            parsed = _PlanningRound.model_validate(normalized)
            _validate_planning_round(
                parsed,
                round_number=round_number,
                case=case,
                methods=methods,
                fault_tree_items=fault_tree_items,
                diagnostic_patterns=diagnostic_patterns,
                unattempted_fault_tree_item_ids=unattempted_fault_tree_item_ids,
                valid_evidence_ids=valid_evidence_ids,
                valid_evidence_locator_ids={
                    *valid_evidence_ids,
                    *spill_store.handle_ids,
                },
            )
        except (ValidationError, ValueError) as exc:
            validation_error = exc
            if attempt < MAX_PLANNING_ATTEMPTS:
                continue
            provider.last_usage = cumulative_usage
            provider.last_duration_ms = cumulative_duration_ms
            provider.last_validation_retry_count = attempt - 1
            provider.last_context_metrics = context_attempt_summary(
                context_attempts,
                total_prompt_tokens=cumulative_usage["prompt_tokens"],
            )
            raise
        provider.last_usage = cumulative_usage
        provider.last_duration_ms = cumulative_duration_ms
        provider.last_validation_retry_count = attempt - 1
        provider.last_context_metrics = context_attempt_summary(
            context_attempts,
            total_prompt_tokens=cumulative_usage["prompt_tokens"],
        )
        return parsed
    raise AssertionError("Planning attempts exhausted without a result")


def _search_query_result(
    case_id: str,
    query: str,
    session_factory: Any,
    top_k: int = 10,
) -> dict[str, Any]:
    with session_factory() as db:
        result = agentic_search(
            db,
            case_id=case_id,
            query=query,
            top_k=max(1, min(top_k, 20)),
            max_hops=2,
            record_memory=False,
            execution_mode="diagnostic_llm_planner",
            joint_diagnostic_scope=True,
        )
    return {
        "query": query,
        "run_id": result.get("run_id"),
        "plan": result.get("plan", {}),
        "summary": result.get("summary", {}),
        "results": result.get("results", []),
        "paths": result.get("paths", []),
    }


def run_diagnostic_planning(
    ctx: JobContext,
    *,
    case: Case,
    agent_run_id: str,
    baseline_search: dict[str, Any],
    session_factory: Any = SessionLocal,
    budget: DiagnosticAgentBudget | None = None,
) -> DiagnosticPlanningResult:
    agent_budget = budget or configured_diagnostic_agent_budget()
    with session_factory() as db:
        current_case = db.get(Case, case.id)
        if not current_case:
            raise ValueError("Case not found")
        methods = load_applicable_diagnostic_methods(db, current_case)
    patterns = compile_diagnostic_patterns(methods)
    fault_tree_items = compile_fault_tree_items(methods)
    triage_evidence, triage_coverage = _triage_evidence(case.id, session_factory)
    baseline_evidence = [
        item for item in baseline_search.get("results", []) if isinstance(item, dict)
    ]
    provider = get_llm_provider()
    context_policy = configured_context_policy(provider)
    spill_store = EvidenceSpillStore(
        chunk_tokens=context_policy.spill_chunk_tokens,
    )
    tool_environment = DiagnosticToolEnvironment(
        case=case,
        methods=methods,
        patterns=patterns,
        fault_tree_items=fault_tree_items,
        evidence=[*triage_evidence, *baseline_evidence],
        knowledge_search=lambda query, top_k: _search_query_result(
            case.id, query, session_factory, top_k,
        ),
        log_search=lambda payload: search_persisted_log_evidence(
            triage_run_ids=triage_coverage["triage_run_ids"],
            artifact_sources=triage_coverage["artifact_sources"],
            payload=payload,
            session_factory=session_factory,
        ),
        spill_store=spill_store,
    )
    tool_registry = build_diagnostic_tool_registry(tool_environment)
    tool_context = ToolContext(role="ENGINEER", case_id=case.id)
    catalog_invocation = invoke_diagnostic_tool(
        tool_registry,
        tool_context,
        tool_name="list_diagnostic_documents",
        arguments={},
    )
    read_invocation = invoke_diagnostic_tool(
        tool_registry,
        tool_context,
        tool_name="read_diagnostic_documents",
        arguments={"document_ids": [method.id for method in methods]},
    ) if methods else None
    policy_tool_calls = [{
        "round": 0,
        "call_id": "policy-list-methods",
        "tool_name": catalog_invocation.tool_name,
        "arguments": catalog_invocation.arguments,
        "method_document_ids": [method.id for method in methods],
        "rationale": "后端策略先建立完整的联合诊断方法目录。",
        "status": "COMPLETED",
        "returned": len(catalog_invocation.output.get("documents", [])),
        "evidence_ids": catalog_invocation.evidence_ids,
        "invoked_by": "POLICY",
    }]
    if read_invocation:
        policy_tool_calls.append({
            "round": 0,
            "call_id": "policy-read-methods",
            "tool_name": read_invocation.tool_name,
            "arguments": {"document_count": len(methods)},
            "method_document_ids": [method.id for method in methods],
            "rationale": "后端强制读取全部 GW、AP 与通用方法全文后才允许模型规划。",
            "status": "COMPLETED",
            "returned": len(read_invocation.output.get("documents", [])),
            "evidence_ids": read_invocation.evidence_ids,
            "invoked_by": "POLICY",
        })
    llm_allowed = not provider.is_mock and case.model_egress_approved
    if llm_allowed and triage_coverage["missing_artifact_ids"]:
        raise ValueError(
            "Complete LLM log planning for every active parsed artifact before comprehensive diagnosis"
        )

    with session_factory() as db:
        append_live_trace(
            db,
            agent_run_id,
            stage="list_diagnostic_documents",
            tool_name="list_diagnostic_documents",
            status="COMPLETED",
            duration_ms=catalog_invocation.duration_ms,
            output_summary={
                "documents": len(methods),
                "patterns": len(patterns),
                "triage_evidence": len(triage_evidence),
                "fault_tree_items": len(fault_tree_items),
            },
            evidence_ids=[method.id for method in methods],
            metadata={
                "candidate_count": len(methods),
                "knowledge_scope": "GW_AP_JOINT",
                "artifact_sources": triage_coverage["artifact_sources"],
                "agent_budget": agent_budget.model_dump(mode="json"),
                "context_policy": context_policy.model_dump(mode="json"),
            },
            commit=False,
        )
        if read_invocation:
            append_live_trace(
                db,
                agent_run_id,
                stage="read_diagnostic_documents",
                tool_name="read_diagnostic_documents",
                status="COMPLETED",
                duration_ms=read_invocation.duration_ms,
                input_summary={"document_ids": [method.id for method in methods]},
                output_summary={
                    "documents": len(read_invocation.output.get("documents", [])),
                    "patterns": len(read_invocation.output.get("patterns", [])),
                    "fault_tree_items": len(
                        read_invocation.output.get("fault_tree_items", [])
                    ),
                },
                evidence_ids=read_invocation.evidence_ids,
                metadata={"candidate_count": len(methods), "role": "POLICY"},
                commit=False,
            )
        for method in methods:
            append_live_trace(
                db,
                agent_run_id,
                stage="read_method_document",
                tool_name="read_method_document",
                status="COMPLETED",
                input_summary={"document_id": method.id},
                output_summary={
                    "document_id": method.id,
                    "version": method.version,
                    "sha256": method.content_sha256,
                },
                evidence_ids=[method.id],
                metadata={
                    "document_id": method.id,
                    "title": method.title,
                    "version": method.version,
                    "role": method.role,
                },
                commit=False,
            )
        db.commit()

    if not llm_allowed:
        fallback_stop_reason = (
            "MOCK_PROVIDER_DETERMINISTIC_BASELINE"
            if provider.is_mock
            else "MODEL_EGRESS_NOT_APPROVED"
        )
        fallback_plan = {
            "planner_mode": "deterministic_fallback",
            "agent_mode": "typed_read_only_tools",
            "prompt_version": DIAGNOSTIC_PLANNER_PROMPT_VERSION,
            "rounds": [{
                "round": 1,
                "read_document_ids": [method.id for method in methods],
                "method_assessments": [
                    {
                        "method_document_id": method.id,
                        "relevance": "POSSIBLY_RELEVANT",
                        "rationale": "确定性回退保留全部适用方法，等待证据确认相关性。",
                        "matched_signals": [],
                    }
                    for method in methods
                ],
                "checks": [
                    {
                        "method_document_id": method.id,
                        "status": "COVERED_BY_LOG_TRIAGE",
                    }
                    for method in methods
                ],
                "stop_reason": fallback_stop_reason,
            }],
            "method_coverage": {
                **triage_coverage,
                "required_document_ids": [method.id for method in methods],
                "all_documents_read": True,
            },
            "method_catalog": [method.public_snapshot() for method in methods],
            "fault_tree_coverage": coverage_snapshot(
                initial_fault_tree_coverage(fault_tree_items)
            ),
            "tool_calls": policy_tool_calls,
            "stop_reason": fallback_stop_reason,
            "budget": DiagnosticAgentBudgetTracker(agent_budget).snapshot(),
            "context_governance": {
                "policy": context_policy.model_dump(mode="json"),
                "spill_handle_total": 0,
            },
        }
        fallback_plan["method_usage"] = summarize_method_usage(
            methods, fallback_plan["rounds"], policy_tool_calls,
        )
        with session_factory() as db:
            append_live_trace(
                db,
                agent_run_id,
                stage="diagnostic_planner_fallback",
                tool_name="deterministic_planner",
                status="COMPLETED",
                output_summary=fallback_plan,
                evidence_ids=[item["evidence_id"] for item in triage_evidence[:250]],
                stop_reason=fallback_stop_reason,
                metadata={"reason": fallback_stop_reason},
            )
        return DiagnosticPlanningResult(
            public_plan=fallback_plan,
            method_documents=methods,
            evidence=triage_evidence,
            supplemental_results=[],
        )

    (
        prior_rounds,
        supplemental_results,
        seen_queries,
        stop_reason,
        executed_tool_calls,
        planner_failure,
        fault_tree_coverage,
        budget_snapshot,
    ) = asyncio.run(
        execute_llm_planning_rounds(
            ctx,
            provider=provider,
            case=case,
            methods=methods,
            triage_evidence=triage_evidence,
            baseline_search=baseline_search,
            agent_run_id=agent_run_id,
            session_factory=session_factory,
            tool_registry=tool_registry,
            tool_context=tool_context,
            document_observation=(
                read_invocation.output if read_invocation else {"documents": []}
            ),
            fault_tree_items=fault_tree_items,
            diagnostic_patterns=patterns,
            request_round=_request_planning_round,
            budget=agent_budget,
            context_policy=context_policy,
            spill_store=spill_store,
        )
    )

    all_tool_calls = [*policy_tool_calls, *executed_tool_calls]
    public_plan = {
        "planner_mode": (
            "llm_multiround_with_fallback"
            if prior_rounds and planner_failure
            else "llm_multiround"
            if prior_rounds
            else "deterministic_fallback"
        ),
        "planner_accepted": (
            planner_failure is None
            and (not fault_tree_items or fault_tree_coverage.get("complete") is True)
        ),
        "agent_mode": "typed_read_only_tools",
        "prompt_version": DIAGNOSTIC_PLANNER_PROMPT_VERSION,
        "rounds": prior_rounds,
        "method_coverage": {
            **triage_coverage,
            "required_document_ids": [method.id for method in methods],
            "all_documents_read": read_invocation is not None or not methods,
            "read_attestation_source": "TOOL_RUNTIME",
        },
        "method_catalog": [method.public_snapshot() for method in methods],
        "fault_tree_coverage": fault_tree_coverage,
        "tool_calls": all_tool_calls,
        "search_query_count": len(seen_queries),
        "stop_reason": stop_reason,
        "planner_failure": planner_failure,
        "budget": budget_snapshot,
        "context_governance": {
            **(getattr(provider, "last_context_metrics", {}) or {}),
            "policy": context_policy.model_dump(mode="json"),
            "spill_handle_total": len(spill_store.handle_ids),
        },
    }
    public_plan["method_usage"] = summarize_method_usage(
        methods, prior_rounds, all_tool_calls,
    )
    return DiagnosticPlanningResult(
        public_plan=public_plan,
        method_documents=methods,
        evidence=triage_evidence,
        supplemental_results=supplemental_results,
    )
