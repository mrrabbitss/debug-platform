from __future__ import annotations

from typing import Any

from app.services.agent_trace_runtime import append_live_trace


def append_log_planning_trace(
    db: Any,
    *,
    run_id: str,
    plan: dict[str, Any],
    model_result: dict[str, Any],
    duration_ms: int,
) -> None:
    fallback = bool(model_result.get("fallback"))
    usage = model_result.get("usage", {})
    failure = model_result.get("failure") or {}
    append_live_trace(
        db,
        run_id,
        stage="llm_log_plan",
        tool_name="chat_completion",
        status="FAILED" if fallback else "COMPLETED",
        duration_ms=duration_ms,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        retry_count=int(model_result.get("retry_count") or 0),
        output_summary={
            "selected_patterns": len(plan.get("selected_pattern_ids", [])),
            "additional_keywords": len(plan.get("additional_keywords", [])),
            "fallback": fallback,
        },
        evidence_ids=list(plan.get("read_document_ids", [])),
        stop_reason="PLANNER_VALIDATION_FALLBACK" if fallback else None,
        metadata={
            "planner_mode": plan.get("planner_mode"),
            "fallback": fallback,
            "error_type": model_result.get("error_type"),
            "validation_code": failure.get("code"),
            "validation_path": failure.get("field_path"),
            "upstream_error_type": failure.get("upstream_error_type"),
            "thinking_mode": model_result.get("thinking_mode"),
            "finish_reason": model_result.get("finish_reason"),
        },
        commit=False,
    )
    if fallback:
        append_live_trace(
            db,
            run_id,
            stage="log_planner_fallback",
            tool_name="deterministic_planner",
            status="COMPLETED",
            output_summary={
                "selected_patterns": len(plan.get("selected_pattern_ids", [])),
            },
            evidence_ids=list(plan.get("read_document_ids", [])),
            stop_reason="PLANNER_VALIDATION_FALLBACK",
            metadata={
                "validation_code": failure.get("code"),
                "upstream_error_type": failure.get("upstream_error_type"),
                "thinking_mode": model_result.get("thinking_mode"),
                "finish_reason": model_result.get("finish_reason"),
            },
            commit=False,
        )
