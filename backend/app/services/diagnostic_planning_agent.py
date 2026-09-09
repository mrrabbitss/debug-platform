from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.core.utils import json_dumps
from app.models import Case
from app.services.agent_trace_runtime import append_live_trace
from app.services.agent_runtime import ContextWindowPolicy, EvidenceSpillStore
from app.services.agentic.tools import ToolContext
from app.services.diagnostic_agent_budget import (
    TIME_BUDGET,
    DiagnosticAgentBudget,
    DiagnosticAgentBudgetTracker,
    budget_failure_details,
)
from app.services.diagnostic_fault_tree_baseline import (
    complete_fault_tree_with_deterministic_evidence,
    is_case_log_evidence,
)
from app.services.diagnostic_methods import DiagnosticMethodDocument, DiagnosticPattern
from app.services.diagnostic_progress import report_planning_progress
from app.services.diagnostic_planning_coverage import (
    TERMINAL_FAULT_TREE_STATUSES,
    coverage_snapshot,
    initial_fault_tree_coverage,
)
from app.services.diagnostic_tools import invoke_diagnostic_tool
from app.services.fault_tree_coverage import FaultTreeCoverageItem
from app.services.jobs import JobContext
from app.services.llm import LLMError
from app.services.planning_diagnostics import planning_failure_details


def _valid_evidence_id_sets(
    triage_evidence: list[dict[str, Any]],
    baseline_search: dict[str, Any],
    supplemental_results: list[dict[str, Any]],
) -> tuple[set[str], set[str]]:
    items = [
        *triage_evidence,
        *baseline_search.get("results", []),
        *supplemental_results,
    ]
    all_ids = {
        str(item["evidence_id"])
        for item in items
        if isinstance(item, dict) and item.get("evidence_id")
    }
    case_ids = {
        str(item["evidence_id"])
        for item in items
        if isinstance(item, dict)
        and item.get("evidence_id")
        and is_case_log_evidence(item)
    }
    return all_ids, case_ids


def _apply_fault_tree_assessments(
    coverage: dict[str, dict[str, Any]],
    planning_round: Any,
    round_number: int,
) -> None:
    for assessment in planning_round.fault_tree_assessments:
        current = coverage.get(assessment.item_id)
        if current is None:
            continue
        if (
            assessment.status == "PENDING"
            and current.get("status") in TERMINAL_FAULT_TREE_STATUSES
        ):
            continue
        current.update({
            "status": assessment.status,
            "rationale": assessment.rationale,
            "evidence_ids": list(dict.fromkeys(assessment.evidence_ids)),
            "next_action": assessment.next_action,
            "last_round": round_number,
        })


def _execute_policy_evidence_fetch(
    ctx: JobContext,
    *,
    search_call: Any,
    search_invocation: Any,
    round_number: int,
    budget_tracker: DiagnosticAgentBudgetTracker,
    tool_registry: Any,
    tool_context: ToolContext,
    supplemental_results: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    session_factory: Any,
    agent_run_id: str,
) -> tuple[dict[str, Any] | None, str | None, set[str]]:
    """Hydrate non-empty model search hits with honest policy provenance."""

    evidence_ids = list(dict.fromkeys(search_invocation.evidence_ids))[:100]
    if not evidence_ids:
        return None, None, set()
    if not budget_tracker.can_invoke_tool():
        return None, budget_tracker.stop_reason, set()
    ctx.raise_if_cancelled()
    invocation = invoke_diagnostic_tool(
        tool_registry,
        tool_context,
        tool_name="get_evidence",
        arguments={"evidence_ids": evidence_ids},
    )
    budget_tracker.record_tool_call()
    if set(invocation.evidence_ids) != set(evidence_ids):
        raise ValueError(
            "Policy evidence hydration did not resolve every model search result"
        )
    budget_reason = budget_tracker.record_tool_output(invocation.output)
    output_results = invocation.output.get("results", [])
    supplemental_results.extend(
        item
        for item in output_results
        if str(item.get("source_type") or "") != "context_spill"
    )
    observations.append({
        "round": round_number,
        "tool_name": "get_evidence",
        "invoked_by": "POLICY_EVIDENCE_HYDRATION",
        "arguments": invocation.arguments,
        "summary": {
            "returned": invocation.output.get("returned", 0),
            "total_candidates": invocation.output.get("total_candidates", 0),
        },
        "results": output_results,
    })
    rendered_call = {
        "round": round_number,
        "call_id": f"policy-evidence-fetch-{search_call.call_id}",
        "tool_name": "get_evidence",
        "arguments": invocation.arguments,
        "invoked_by": "POLICY_EVIDENCE_HYDRATION",
        "hydrated_from_call_id": search_call.call_id,
        "method_document_ids": search_call.method_document_ids,
        "rationale": (
            "对模型 search_log 返回的证据 ID 执行有界只读取回，"
            "明确记录搜索到证据详情的因果链。"
        ),
        "fault_tree_item_ids": search_call.fault_tree_item_ids,
        "status": "COMPLETED",
        "returned": int(invocation.output.get("returned") or 0),
        "total_candidates": int(
            invocation.output.get("total_candidates") or 0
        ),
        "evidence_ids": invocation.evidence_ids,
    }
    executed_tool_calls.append(rendered_call)
    with session_factory() as db:
        append_live_trace(
            db,
            agent_run_id,
            stage="execute_agent_tool",
            tool_name="get_evidence",
            status="COMPLETED",
            duration_ms=invocation.duration_ms,
            input_summary=invocation.arguments,
            output_summary={
                "returned": invocation.output.get("returned", 0),
                "total_candidates": invocation.output.get(
                    "total_candidates", 0,
                ),
            },
            evidence_ids=invocation.evidence_ids,
            metadata={
                "round": round_number,
                "invoked_by": "POLICY_EVIDENCE_HYDRATION",
                "source_call_id": search_call.call_id,
                "candidate_count": invocation.output.get(
                    "total_candidates", 0,
                ),
                "returned_count": invocation.output.get("returned", 0),
                "fault_tree_item_ids": search_call.fault_tree_item_ids,
            },
        )
    return rendered_call, budget_reason, set(invocation.evidence_ids)


def _execute_planned_tool_calls(
    ctx: JobContext,
    *,
    planning_round: Any,
    round_number: int,
    budget: DiagnosticAgentBudget,
    budget_tracker: DiagnosticAgentBudgetTracker,
    prior_tool_calls: dict[str, dict[str, Any]],
    coverage: dict[str, dict[str, Any]],
    tool_registry: Any,
    tool_context: ToolContext,
    seen_queries: set[str],
    supplemental_results: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    session_factory: Any,
    agent_run_id: str,
) -> tuple[list[dict[str, Any]], str | None, set[str]]:
    rendered_calls: list[dict[str, Any]] = []
    new_evidence_ids: set[str] = set()
    budget_reason: str | None = None
    evidence_fetch_completed = any(
        call.get("invoked_by") == "POLICY_EVIDENCE_HYDRATION"
        and call.get("status") == "COMPLETED"
        and call.get("evidence_ids")
        for call in executed_tool_calls
    )
    for planned_call in planning_round.tool_calls[:budget.max_tool_calls_per_round]:
        call_arguments = dict(planned_call.arguments)
        if planned_call.method_document_ids:
            call_arguments.setdefault(
                "method_document_ids", planned_call.method_document_ids,
            )
        dedupe_key = f"{planned_call.tool_name}:{json_dumps(call_arguments)}"
        invoked_by = (
            "POLICY_REPAIR"
            if str(planned_call.call_id).startswith("policy-")
            else "MODEL"
        )
        if dedupe_key in prior_tool_calls:
            previous_call = prior_tool_calls[dedupe_key]
            reused_call = {
                "round": round_number,
                "call_id": planned_call.call_id,
                "tool_name": planned_call.tool_name,
                "invoked_by": invoked_by,
                "method_document_ids": planned_call.method_document_ids,
                "rationale": planned_call.rationale,
                "fault_tree_item_ids": planned_call.fault_tree_item_ids,
                "status": "REUSED_DUPLICATE",
                "returned": previous_call.get("returned", 0),
                "total_candidates": previous_call.get("total_candidates", 0),
                "evidence_ids": previous_call.get("evidence_ids", []),
            }
            rendered_calls.append(reused_call)
            executed_tool_calls.append(reused_call)
            for item_id in planned_call.fault_tree_item_ids:
                if item_id in coverage:
                    coverage[item_id]["attempted"] = True
            continue
        if not budget_tracker.can_invoke_tool():
            budget_reason = budget_tracker.stop_reason
            break
        ctx.raise_if_cancelled()
        report_planning_progress(ctx, coverage, round_number,
                                 f"第 {round_number} 轮：正在读取原始证据并核对故障树")
        invocation = invoke_diagnostic_tool(
            tool_registry,
            tool_context,
            tool_name=planned_call.tool_name,
            arguments=call_arguments,
        )
        budget_tracker.record_tool_call()
        output_budget_reason = budget_tracker.record_tool_output(invocation.output)
        new_evidence_ids.update(invocation.evidence_ids)
        output_results = invocation.output.get("results", [])
        if planned_call.tool_name == "search_knowledge":
            query = str(invocation.arguments.get("query") or "").strip()
            if query:
                seen_queries.add(query.casefold())
        supplemental_results.extend(
            item
            for item in output_results
            if str(item.get("source_type") or "") != "context_spill"
        )
        observations.append({
            "round": round_number,
            "tool_name": planned_call.tool_name,
            "invoked_by": invoked_by,
            "arguments": invocation.arguments,
            "summary": {
                "returned": invocation.output.get("returned", 0),
                "total_candidates": invocation.output.get("total_candidates", 0),
            },
            "results": output_results,
        })
        rendered_call = {
            "round": round_number,
            "call_id": planned_call.call_id,
            "tool_name": planned_call.tool_name,
            "arguments": invocation.arguments,
            "invoked_by": invoked_by,
            "method_document_ids": planned_call.method_document_ids,
            "rationale": planned_call.rationale,
            "fault_tree_item_ids": planned_call.fault_tree_item_ids,
            "status": "COMPLETED",
            "returned": int(invocation.output.get("returned") or 0),
            "total_candidates": int(
                invocation.output.get("total_candidates") or 0
            ),
            "evidence_ids": invocation.evidence_ids,
        }
        rendered_calls.append(rendered_call)
        executed_tool_calls.append(rendered_call)
        prior_tool_calls[dedupe_key] = rendered_call
        for item_id in planned_call.fault_tree_item_ids:
            if item_id in coverage:
                coverage[item_id]["attempted"] = True
        with session_factory() as db:
            append_live_trace(
                db,
                agent_run_id,
                stage="execute_agent_tool",
                tool_name=planned_call.tool_name,
                status="COMPLETED",
                duration_ms=invocation.duration_ms,
                input_summary=invocation.arguments,
                output_summary={
                    "returned": invocation.output.get("returned", 0),
                    "total_candidates": invocation.output.get(
                        "total_candidates", 0,
                    ),
                },
                evidence_ids=invocation.evidence_ids,
                metadata={
                    "round": round_number,
                    "invoked_by": invoked_by,
                    "candidate_count": invocation.output.get("total_candidates", 0),
                    "returned_count": invocation.output.get("returned", 0),
                    "document_id": ",".join(
                        planned_call.method_document_ids
                    )[:500],
                    "fault_tree_item_ids": planned_call.fault_tree_item_ids,
                },
            )
        if output_budget_reason:
            budget_reason = output_budget_reason
            break
        if (
            planned_call.tool_name == "search_log"
            and invocation.evidence_ids
            and invoked_by == "MODEL"
            and not evidence_fetch_completed
        ):
            # The per-round limit bounds model-planned calls. This one policy
            # read is still charged to the aggregate tool budget, but must not
            # silently displace a validated fourth model call.
            fetched_call, fetch_budget_reason, fetched_ids = (
                _execute_policy_evidence_fetch(
                    ctx,
                    search_call=planned_call,
                    search_invocation=invocation,
                    round_number=round_number,
                    budget_tracker=budget_tracker,
                    tool_registry=tool_registry,
                    tool_context=tool_context,
                    supplemental_results=supplemental_results,
                    observations=observations,
                    executed_tool_calls=executed_tool_calls,
                    session_factory=session_factory,
                    agent_run_id=agent_run_id,
                )
            )
            if fetched_call is not None:
                rendered_calls.append(fetched_call)
                new_evidence_ids.update(fetched_ids)
                evidence_fetch_completed = True
            if fetch_budget_reason:
                budget_reason = fetch_budget_reason
                break
    return rendered_calls, budget_reason, new_evidence_ids


def _reconcile_fault_tree_coverage(
    *,
    coverage: dict[str, Any],
    failure: dict[str, Any] | None,
    stop_reason: str,
    active_round: int,
    fault_tree_items: list[FaultTreeCoverageItem],
    diagnostic_patterns: list[DiagnosticPattern],
    triage_evidence: list[dict[str, Any]],
    baseline_search: dict[str, Any],
    supplemental_results: list[dict[str, Any]],
    executed_tool_calls: list[dict[str, Any]],
    session_factory: Any,
    agent_run_id: str,
) -> dict[str, Any]:
    if not fault_tree_items:
        return coverage
    fallback_applied = not coverage["complete"] or failure is not None
    fallback_reason = str(
        (failure or {}).get("code")
        or (
            stop_reason if fallback_applied
            else "MODEL_COVERAGE_EVIDENCE_RECONCILIATION"
        )
    )
    all_evidence = [
        *triage_evidence,
        *[
            item for item in baseline_search.get("results", [])
            if isinstance(item, dict)
        ],
        *supplemental_results,
    ]
    reconciled = complete_fault_tree_with_deterministic_evidence(
        coverage,
        items=fault_tree_items,
        evidence=all_evidence,
        patterns=diagnostic_patterns,
        round_number=max(1, active_round),
        reason=fallback_reason,
        fallback_applied=fallback_applied,
    )
    evidence_ids = list(dict.fromkeys(
        str(evidence_id)
        for item in reconciled.get("items", [])
        for evidence_id in item.get("evidence_ids", [])
    ))
    executed_tool_calls.append({
        "round": active_round or 1,
        "call_id": "policy-deterministic-fault-tree-scan",
        "tool_name": "deterministic_fault_tree_scan",
        "invoked_by": "POLICY_FALLBACK" if fallback_applied else "POLICY",
        "method_document_ids": list(dict.fromkeys(
            item.method_document_id for item in fault_tree_items
        )),
        "rationale": (
            "按已编译故障树 Pattern 对当前案例证据执行只读确定性扫描，"
            "补足证据不足节点或与模型覆盖账本交叉核验；不生成或猜测证据。"
        ),
        "fault_tree_item_ids": [item.id for item in fault_tree_items],
        "status": "COMPLETED",
        "returned": len(evidence_ids),
        "total_candidates": len(all_evidence),
        "evidence_ids": evidence_ids,
    })
    with session_factory() as db:
        append_live_trace(
            db,
            agent_run_id,
            stage="deterministic_fault_tree_evidence",
            tool_name="deterministic_fault_tree_scan",
            status="COMPLETED",
            output_summary={
                "total": reconciled.get("total", 0),
                "attempted": reconciled.get("attempted", 0),
                "concluded": reconciled.get("concluded", 0),
                "status_counts": reconciled.get("status_counts", {}),
            },
            evidence_ids=evidence_ids,
            stop_reason=fallback_reason,
            metadata={
                "reason": fallback_reason,
                "fallback_applied": fallback_applied,
                "resolution_source_counts": reconciled.get(
                    "resolution_source_counts", {}
                ),
            },
        )
    return reconciled


async def execute_llm_planning_rounds(
    ctx: JobContext,
    *,
    provider: Any,
    case: Case,
    methods: list[DiagnosticMethodDocument],
    triage_evidence: list[dict[str, Any]],
    baseline_search: dict[str, Any],
    agent_run_id: str,
    session_factory: Any,
    tool_registry: Any,
    tool_context: ToolContext,
    document_observation: dict[str, Any],
    fault_tree_items: list[FaultTreeCoverageItem],
    diagnostic_patterns: list[DiagnosticPattern],
    request_round: Any,
    budget: DiagnosticAgentBudget,
    context_policy: ContextWindowPolicy,
    spill_store: EvidenceSpillStore,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    str,
    list[dict[str, Any]],
    dict[str, Any] | None,
    dict[str, Any],
    dict[str, Any],
]:
    """Execute the model and typed tools until coverage is complete or the hard limit."""
    prior_rounds: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = [{
        "query": "initial deterministic retrieval",
        "summary": baseline_search.get("summary", {}),
        "results": baseline_search.get("results", [])[:20],
    }]
    supplemental_results: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    prior_tool_calls: dict[str, dict[str, Any]] = {}
    executed_tool_calls: list[dict[str, Any]] = []
    coverage = initial_fault_tree_coverage(fault_tree_items)
    budget_tracker = DiagnosticAgentBudgetTracker(budget)
    failure: dict[str, Any] | None = None
    stop_reason = "MAX_PLANNING_ROUNDS"
    budget_stop_triggered = False
    active_round_usage_recorded = False
    active_round = 0
    active_round_started = perf_counter()
    try:
        for round_number in range(1, budget.max_rounds + 1):
            active_round = round_number
            active_round_usage_recorded = False
            budget_reason = budget_tracker.check_limits()
            if budget_reason:
                stop_reason = budget_reason
                failure = budget_failure_details(
                    budget_reason,
                    budget_tracker.snapshot(),
                    finish_reason=getattr(provider, "last_finish_reason", None),
                )
                budget_stop_triggered = True
                break
            report_planning_progress(ctx, coverage, round_number,
                                     f"正在进行第 {round_number} 轮推理，选择下一步需要核对的证据")
            ctx.raise_if_cancelled()
            started = perf_counter()
            active_round_started = started
            coverage_before = coverage_snapshot(coverage)
            valid_locator_ids, valid_evidence_ids = _valid_evidence_id_sets(
                triage_evidence, baseline_search, supplemental_results,
            )
            # Clear the mutable provider snapshot before evaluating any
            # request arguments. A context or tool-manifest failure must not
            # charge the preceding round's usage a second time.
            provider.last_usage = {}
            provider.last_duration_ms = 0
            provider.last_validation_retry_count = 0
            provider.last_finish_reason = None
            try:
                planning_round = await asyncio.wait_for(
                    request_round(
                        provider,
                        round_number=round_number,
                        case=case,
                        methods=methods,
                        triage_evidence=triage_evidence,
                        prior_rounds=prior_rounds,
                        search_observations=observations,
                        tool_manifest=tool_registry.manifest(role=tool_context.role),
                        document_observation=document_observation,
                        fault_tree_items=fault_tree_items,
                        diagnostic_patterns=diagnostic_patterns,
                        fault_tree_coverage=coverage_before,
                        unattempted_fault_tree_item_ids={
                            item_id
                            for item_id, item in coverage.items()
                            if not item.get("attempted")
                        },
                        valid_evidence_ids=valid_evidence_ids,
                        valid_evidence_locator_ids=valid_locator_ids,
                        context_policy=context_policy,
                        spill_store=spill_store,
                    ),
                    timeout=max(0.001, budget_tracker.remaining_duration_ms / 1000),
                )
            except TimeoutError:
                budget_tracker.stop_reason = TIME_BUDGET
                stop_reason = TIME_BUDGET
                failure = budget_failure_details(
                    stop_reason,
                    budget_tracker.snapshot(),
                    finish_reason=getattr(provider, "last_finish_reason", None),
                )
                budget_stop_triggered = True
                break
            rendered = planning_round.model_dump(mode="json")
            rendered["round"] = round_number
            rendered["planner_repairs"] = list(
                getattr(provider, "last_plan_repairs", []) or []
            )
            rendered["planning_attempts"] = int(
                getattr(provider, "last_validation_retry_count", 0) or 0
            ) + 1
            context_metrics = getattr(provider, "last_context_metrics", {}) or {}
            rendered["context_governance"] = context_metrics
            _apply_fault_tree_assessments(coverage, planning_round, round_number)
            prior_rounds.append(rendered)
            usage = getattr(provider, "last_usage", {}) or {}
            budget_tracker.rounds_completed = round_number
            budget_reason = budget_tracker.record_model_usage(usage)
            active_round_usage_recorded = True
            with session_factory() as db:
                append_live_trace(
                    db,
                    agent_run_id,
                    stage=f"llm_planning_round_{round_number}",
                    tool_name="chat_completion",
                    status="COMPLETED",
                    duration_ms=int((perf_counter() - started) * 1000),
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    retry_count=int(
                        getattr(provider, "last_validation_retry_count", 0) or 0
                    ),
                    output_summary={
                        "hypotheses": len(planning_round.hypotheses),
                        "method_assessments": len(planning_round.method_assessments),
                        "checks": len(planning_round.checks),
                        "tool_calls": len(planning_round.tool_calls),
                        "fault_tree_assessments": len(
                            planning_round.fault_tree_assessments
                        ),
                        "planner_repairs": len(rendered["planner_repairs"]),
                        "continue": planning_round.continue_analysis,
                    },
                    evidence_ids=planning_round.read_document_ids,
                    metadata={
                        "round": round_number,
                        "stop_reason": planning_round.stop_reason,
                        "finish_reason": getattr(provider, "last_finish_reason", None),
                        "fault_tree_pending_before": int(
                            coverage_before.get("status_counts", {}).get("PENDING", 0)
                        ),
                        "aggregate_tokens": budget_tracker.total_tokens,
                        "token_budget": budget.max_total_tokens,
                        "context_governance": context_metrics,
                        "planner_repair_codes": [
                            str(item.get("code") or "")
                            for item in rendered["planner_repairs"]
                            if isinstance(item, dict)
                        ],
                    },
                )
            rendered_calls: list[dict[str, Any]] = []
            if budget_reason:
                rendered["executed_tool_calls"] = rendered_calls
                rendered["fault_tree_coverage_after_round"] = coverage_snapshot(coverage)
                successful_stop = (
                    round_number >= budget.min_rounds
                    and not planning_round.continue_analysis
                    and (
                        rendered["fault_tree_coverage_after_round"]["complete"]
                        or not fault_tree_items
                    )
                )
                if successful_stop:
                    budget_tracker.stop_reason = None
                    rendered["agent_budget_after_round"] = budget_tracker.snapshot()
                    stop_reason = (
                        planning_round.stop_reason or "MODEL_SUFFICIENT_EVIDENCE"
                    )
                    break
                rendered["agent_budget_after_round"] = budget_tracker.snapshot()
                stop_reason = budget_reason
                failure = budget_failure_details(
                    budget_reason,
                    budget_tracker.snapshot(),
                    finish_reason=getattr(provider, "last_finish_reason", None),
                )
                budget_stop_triggered = True
                break
            queries_before = len(seen_queries)
            tool_calls_before = budget_tracker.tool_calls
            rendered_calls, tool_budget_reason, new_evidence_ids = (
                _execute_planned_tool_calls(
                    ctx,
                    planning_round=planning_round,
                    round_number=round_number,
                    budget=budget,
                    budget_tracker=budget_tracker,
                    prior_tool_calls=prior_tool_calls,
                    coverage=coverage,
                    tool_registry=tool_registry,
                    tool_context=tool_context,
                    seen_queries=seen_queries,
                    supplemental_results=supplemental_results,
                    observations=observations,
                    executed_tool_calls=executed_tool_calls,
                    session_factory=session_factory,
                    agent_run_id=agent_run_id,
                )
            )
            budget_reason = budget_reason or tool_budget_reason
            rendered["executed_tool_calls"] = rendered_calls
            rendered["fault_tree_coverage_after_round"] = coverage_snapshot(coverage)
            budget_reason = budget_reason or budget_tracker.record_round_progress(
                round_number=round_number,
                coverage_before=coverage_before,
                coverage_after=rendered["fault_tree_coverage_after_round"],
                new_tool_calls=budget_tracker.tool_calls - tool_calls_before,
                new_evidence_ids=len(new_evidence_ids),
                new_queries=len(seen_queries) - queries_before,
            )
            rendered["agent_budget_after_round"] = budget_tracker.snapshot()
            coverage_complete = rendered["fault_tree_coverage_after_round"]["complete"]
            successful_stop = (
                round_number >= budget.min_rounds
                and not planning_round.continue_analysis
                and (coverage_complete or not fault_tree_items)
            )
            if successful_stop:
                budget_tracker.stop_reason = None
                rendered["agent_budget_after_round"] = budget_tracker.snapshot()
                stop_reason = planning_round.stop_reason or "MODEL_SUFFICIENT_EVIDENCE"
                break
            if budget_reason:
                stop_reason = budget_reason
                failure = budget_failure_details(
                    budget_reason,
                    budget_tracker.snapshot(),
                    finish_reason=getattr(provider, "last_finish_reason", None),
                )
                budget_stop_triggered = True
                break
            if not planning_round.continue_analysis and not coverage_complete:
                rendered["coverage_forced_continue"] = True
    except (LLMError, ValidationError, ValueError) as exc:
        stop_reason = "PLANNER_VALIDATION_FALLBACK"
        failure = planning_failure_details(exc, provider)
        usage = getattr(provider, "last_usage", {}) or {}
        if not active_round_usage_recorded:
            budget_tracker.record_model_usage(usage)
        metadata = {
            "round": active_round or 1,
            "error_type": failure["error_type"],
            "validation_code": failure["code"],
            "validation_path": failure.get("field_path"),
            "finish_reason": failure.get("finish_reason"),
            "agent_budget": budget_tracker.snapshot(),
            "context_governance": (
                getattr(provider, "last_context_metrics", {}) or {}
            ),
            "planner_repairs": list(
                getattr(provider, "last_plan_repairs", []) or []
            ),
        }
        with session_factory() as db:
            append_live_trace(
                db,
                agent_run_id,
                stage=f"llm_planning_round_{active_round or 1}",
                tool_name="chat_completion",
                status="FAILED",
                duration_ms=int((perf_counter() - active_round_started) * 1000),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                retry_count=int(
                    getattr(provider, "last_validation_retry_count", 0) or 0
                ),
                output_summary={"completed_rounds": len(prior_rounds)},
                stop_reason=stop_reason,
                metadata=metadata,
                commit=False,
            )
            append_live_trace(
                db,
                agent_run_id,
                stage="diagnostic_planner_fallback",
                tool_name="deterministic_planner",
                status="COMPLETED",
                output_summary={"completed_rounds": len(prior_rounds)},
                stop_reason=stop_reason,
                metadata=metadata,
            )
    if budget_stop_triggered and failure:
        metadata = {
            "round": active_round,
            "error_type": failure["error_type"],
            "validation_code": failure["code"],
            "validation_path": failure.get("field_path"),
            "finish_reason": failure.get("finish_reason"),
            "agent_budget": budget_tracker.snapshot(),
            "context_governance": (
                getattr(provider, "last_context_metrics", {}) or {}
            ),
        }
        with session_factory() as db:
            append_live_trace(
                db,
                agent_run_id,
                stage="diagnostic_planner_fallback",
                tool_name="deterministic_planner",
                status="COMPLETED",
                output_summary={"completed_rounds": len(prior_rounds)},
                stop_reason=stop_reason,
                metadata=metadata,
            )
    final_coverage = coverage_snapshot(coverage)
    if fault_tree_items and not final_coverage["complete"] and failure is None:
        stop_reason = "FAULT_TREE_COVERAGE_INCOMPLETE"
        failure = {
            "code": "FAULT_TREE_COVERAGE_INCOMPLETE",
            "message": (
                "The model did not investigate and conclude every compiled fault-tree item "
                f"within {budget.max_rounds} rounds"
            ),
            "field_path": "fault_tree_coverage",
            "error_type": "FaultTreeCoverageError",
            "finish_reason": getattr(provider, "last_finish_reason", None),
        }
    final_coverage = _reconcile_fault_tree_coverage(
        coverage=final_coverage,
        failure=failure,
        stop_reason=stop_reason,
        active_round=active_round,
        fault_tree_items=fault_tree_items,
        diagnostic_patterns=diagnostic_patterns,
        triage_evidence=triage_evidence,
        baseline_search=baseline_search,
        supplemental_results=supplemental_results,
        executed_tool_calls=executed_tool_calls,
        session_factory=session_factory,
        agent_run_id=agent_run_id,
    )
    return (
        prior_rounds,
        supplemental_results,
        seen_queries,
        stop_reason,
        executed_tool_calls,
        failure,
        final_coverage,
        budget_tracker.snapshot(),
    )
