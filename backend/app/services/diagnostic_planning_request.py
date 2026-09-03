from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.core.utils import json_dumps
from app.models import Case
from app.services.agent_runtime import ContextWindowPolicy, EvidenceSpillStore
from app.services.agent_runtime.budget import merge_usage_totals
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    DiagnosticPattern,
)
from app.services.diagnostic_planning_context import (
    build_governed_planning_prompt,
    context_attempt_summary,
    observe_context_attempt,
)
from app.services.diagnostic_planning_contract import (
    PlanningRound,
    normalize_planning_round,
    symptom_relevant_method_ids,
    validate_planning_round,
)
from app.services.diagnostic_planning_repair import repair_planning_payload
from app.services.fault_tree_coverage import FaultTreeCoverageItem
from app.services.llm import LLMError


MAX_PLANNING_ATTEMPTS = 3


async def request_planning_round(
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
    valid_evidence_locator_ids: set[str],
    context_policy: ContextWindowPolicy,
    spill_store: EvidenceSpillStore,
) -> PlanningRound:
    # Provider observability fields are mutable snapshots. Clear the prior
    # round before context construction so a pre-request context failure cannot
    # be mistaken for fresh usage or a fresh upstream finish reason.
    provider.last_usage = {}
    provider.last_duration_ms = 0
    provider.last_validation_retry_count = 0
    provider.last_finish_reason = None
    provider.last_plan_repairs = []
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
                    symptom_relevant_method_ids(case, methods)
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
            output_contract=PlanningRound.model_json_schema(),
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
            normalized = normalize_planning_round(raw)
            if isinstance(normalized, dict):
                normalized, plan_repairs = repair_planning_payload(
                    normalized,
                    round_number=round_number,
                    case=case,
                    methods=methods,
                    diagnostic_patterns=diagnostic_patterns,
                    fault_tree_items=fault_tree_items,
                    symptom_relevant_method_ids=symptom_relevant_method_ids(
                        case, methods,
                    ),
                    unattempted_fault_tree_item_ids=(
                        unattempted_fault_tree_item_ids
                    ),
                    valid_evidence_ids=valid_evidence_ids,
                    valid_evidence_locator_ids={
                        *valid_evidence_locator_ids,
                        *spill_store.handle_ids,
                    },
                )
                provider.last_plan_repairs = plan_repairs
            parsed = PlanningRound.model_validate(normalized)
            validate_planning_round(
                parsed,
                round_number=round_number,
                case=case,
                methods=methods,
                fault_tree_items=fault_tree_items,
                diagnostic_patterns=diagnostic_patterns,
                unattempted_fault_tree_item_ids=unattempted_fault_tree_item_ids,
                valid_evidence_ids=valid_evidence_ids,
                valid_evidence_locator_ids={
                    *valid_evidence_locator_ids,
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
