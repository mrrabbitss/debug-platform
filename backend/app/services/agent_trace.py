"""Content-safe persistence and scoring for agent execution trajectories."""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, mask_sensitive, new_id, utcnow
from app.models import AgentRun, AgentTraceEvent
from app.services.agent_trace_serialization import agent_run_to_dict as agent_run_to_dict


SAFE_REPLAY_KEYS = {
    "case_id",
    "query",
    "top_k",
    "max_hops",
    "modules",
    "execution_mode",
    "budgets",
}
BLOCKED_METADATA_FRAGMENTS = {
    "content",
    "document",
    "log",
    "raw",
    "secret",
    "token",
    "password",
    "api_key",
    "base_url",
    "prompt",
}
SAFE_EVENT_METADATA_KEYS = {
    "algorithm",
    "backend",
    "candidate_count",
    "circuit_state",
    "document_id",
    "error_type",
    "fallback",
    "finish_reason",
    "model",
    "model_profile_id",
    "agent_mode",
    "planner_mode",
    "planner_stop_reason",
    "provider",
    "reason",
    "returned",
    "returned_count",
    "role",
    "round",
    "selected_modules",
    "stop_reason",
    "tool_permission",
    "validation_code",
    "validation_path",
    "version",
    "context_governance",
}
SAFE_CONTEXT_METRIC_KEYS = {
    "attempt_count",
    "context_window_tokens",
    "input_budget_tokens",
    "estimated_input_tokens",
    "peak_estimated_input_tokens",
    "actual_input_tokens_total",
    "peak_actual_input_tokens",
    "peak_actual_occupancy",
    "within_budget",
    "compaction_count",
    "spill_handle_total",
}
SAFE_CONTEXT_SECTION_KEYS = {
    "original_tokens",
    "kept_tokens",
    "allocation_tokens",
    "original_items",
    "omitted_items",
}


def summary_hash(value: Any) -> str:
    rendered = json_dumps(value)
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _safe_scalar(value: Any, *, max_chars: int = 500) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return mask_sensitive(value).replace("\x00", "")[:max_chars]
    return str(value)[:max_chars]


def sanitize_model_config(value: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in (value or {}).items():
        normalized = str(key).casefold()
        if any(fragment in normalized for fragment in BLOCKED_METADATA_FRAGMENTS):
            continue
        if isinstance(item, dict):
            result[str(key)[:128]] = sanitize_model_config(item)
        elif isinstance(item, list):
            result[str(key)[:128]] = [
                sanitize_model_config(entry) if isinstance(entry, dict) else _safe_scalar(entry)
                for entry in item[:50]
            ]
        else:
            result[str(key)[:128]] = _safe_scalar(item)
    return result


def sanitize_replay_payload(value: dict[str, Any] | None) -> dict[str, Any]:
    payload = value or {}
    result: dict[str, Any] = {}
    query = payload.get("query")
    if isinstance(query, str):
        result["query_hash"] = summary_hash(query)
        masked = mask_sensitive(query).replace("\x00", "")
        if len(masked) <= 2_000 and masked.count("\n") <= 10:
            result["query"] = masked
        else:
            result["query_omitted_reason"] = "query exceeded safe replay text limits"
    for key in SAFE_REPLAY_KEYS - {"query"}:
        if key not in payload:
            continue
        item = payload[key]
        if isinstance(item, list):
            result[key] = [_safe_scalar(entry, max_chars=128) for entry in item[:50]]
        elif isinstance(item, dict):
            result[key] = sanitize_model_config(item)
        else:
            result[key] = _safe_scalar(item)
    return result


def _event_metadata(event: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    nested = event.get("metadata")
    candidates = {**(nested if isinstance(nested, dict) else {}), **event}
    for key, value in candidates.items():
        if key not in SAFE_EVENT_METADATA_KEYS:
            continue
        if key == "context_governance" and isinstance(value, dict):
            safe_context = {
                metric: _safe_scalar(item)
                for metric, item in value.items()
                if metric in SAFE_CONTEXT_METRIC_KEYS
            }
            sections = value.get("sections")
            if isinstance(sections, dict):
                safe_context["sections"] = {
                    str(name)[:64]: {
                        metric: _safe_scalar(item)
                        for metric, item in section.items()
                        if metric in SAFE_CONTEXT_SECTION_KEYS
                    }
                    for name, section in list(sections.items())[:10]
                    if isinstance(section, dict)
                }
            metadata[key] = safe_context
            continue
        if isinstance(value, list):
            metadata[key] = [_safe_scalar(item, max_chars=128) for item in value[:50]]
        else:
            metadata[key] = _safe_scalar(value)
    return metadata


def score_trajectory(
    events: list[dict[str, Any]],
    *,
    evidence_ids: list[str],
    stop_reason: str,
    duration_ms: int,
    budget_ms: int | None = None,
) -> dict[str, Any]:
    completed = sum(1 for event in events if event.get("status") in {"COMPLETED", "SKIPPED"})
    failed = sum(1 for event in events if event.get("status") == "FAILED")
    stage_success = completed / max(len(events), 1)
    evidence_quality = 1.0 if all(str(item).strip() for item in evidence_ids) else 0.0
    budget_quality = 1.0 if budget_ms is None or duration_ms <= budget_ms else 0.0
    stop_quality = 1.0 if stop_reason not in {"UNKNOWN", "UNBOUNDED", "ERROR"} else 0.0
    score = round(
        stage_success * 0.35
        + evidence_quality * 0.25
        + budget_quality * 0.2
        + stop_quality * 0.2,
        6,
    )
    return {
        "score": score,
        "stage_success": round(stage_success, 6),
        "failed_stages": failed,
        "evidence_quality": evidence_quality,
        "budget_quality": budget_quality,
        "stop_quality": stop_quality,
    }


def record_agent_run(
    db: Session,
    *,
    operation: str,
    execution_mode: str,
    input_summary: Any,
    output_summary: Any,
    events: list[dict[str, Any]],
    evidence_ids: list[str],
    stop_reason: str,
    approval_status: str,
    duration_ms: int,
    case_id: str | None = None,
    resource_type: str = "case",
    resource_id: str | None = None,
    model_profile_id: str | None = None,
    model_name: str | None = None,
    model_config: dict[str, Any] | None = None,
    prompt_version: str = "",
    usage: dict[str, Any] | None = None,
    retry_count: int = 0,
    estimated_cost: float = 0.0,
    replay_of_run_id: str | None = None,
    replay_payload: dict[str, Any] | None = None,
    created_by: str | None = None,
    status: str = "COMPLETED",
    budget_ms: int | None = None,
) -> AgentRun:
    normalized_evidence = list(dict.fromkeys(
        str(item)[:128] for item in evidence_ids if str(item).strip()
    ))[:1000]
    usage = usage or {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0) or (input_tokens + output_tokens)
    replay = sanitize_replay_payload(replay_payload)
    run = AgentRun(
        id=new_id("ARUN"),
        case_id=case_id,
        resource_type=resource_type[:64],
        resource_id=(resource_id or case_id),
        operation=operation[:64],
        execution_mode=execution_mode[:32],
        status=status[:32],
        model_profile_id=model_profile_id,
        model_name=(model_name or "")[:512] or None,
        model_config_json=json_dumps(sanitize_model_config(model_config)),
        prompt_version=prompt_version[:128],
        input_summary_hash=summary_hash(input_summary),
        output_summary_hash=summary_hash(output_summary),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        estimated_cost=max(0.0, float(estimated_cost)),
        duration_ms=max(0, int(duration_ms)),
        retry_count=max(0, int(retry_count)),
        evidence_ids_json=json_dumps(normalized_evidence),
        stop_reason=stop_reason[:128],
        approval_status=approval_status[:64],
        replay_of_run_id=replay_of_run_id,
        replay_payload_json=json_dumps(replay),
        score_json=json_dumps(score_trajectory(
            events,
            evidence_ids=normalized_evidence,
            stop_reason=stop_reason,
            duration_ms=duration_ms,
            budget_ms=budget_ms,
        )),
        created_by=created_by,
        completed_at=utcnow(),
    )
    db.add(run)
    db.flush()
    for sequence, event in enumerate(events, start=1):
        event_evidence = list(dict.fromkeys(
            str(item)[:128]
            for item in event.get("evidence_ids", [])
            if str(item).strip()
        ))[:250]
        event_input = event.get("input_summary", {
            "stage": event.get("stage"),
            "tool": event.get("tool_name") or event.get("stage"),
        })
        event_output = event.get("output_summary", {
            "status": event.get("status"),
            "candidate_count": event.get("candidate_count"),
            "reason": event.get("reason"),
        })
        db.add(AgentTraceEvent(
            id=new_id("ATRACE"),
            run_id=run.id,
            sequence=sequence,
            stage=str(event.get("stage") or f"stage-{sequence}")[:128],
            tool_name=str(event.get("tool_name") or event.get("stage") or "")[:128] or None,
            status=str(event.get("status") or "COMPLETED")[:32],
            input_summary_hash=summary_hash(event_input),
            output_summary_hash=summary_hash(event_output),
            input_tokens=max(0, int(event.get("input_tokens") or 0)),
            output_tokens=max(0, int(event.get("output_tokens") or 0)),
            duration_ms=max(0, int(event.get("duration_ms") or 0)),
            retry_count=max(0, int(event.get("retry_count") or 0)),
            evidence_ids_json=json_dumps(event_evidence),
            stop_reason=(str(event.get("stop_reason"))[:128] if event.get("stop_reason") else None),
            metadata_json=json_dumps(_event_metadata(event)),
        ))
    db.commit()
    db.refresh(run)
    return run


def update_resource_approval(
    db: Session,
    *,
    resource_type: str,
    resource_id: str,
    approval_status: str,
    stop_reason: str | None = None,
) -> AgentRun | None:
    run = db.scalar(
        select(AgentRun)
        .where(
            AgentRun.resource_type == resource_type,
            AgentRun.resource_id == resource_id,
        )
        .order_by(AgentRun.created_at.desc())
        .limit(1)
    )
    if not run:
        return None
    run.approval_status = approval_status[:64]
    if stop_reason:
        run.stop_reason = stop_reason[:128]
    db.commit()
    db.refresh(run)
    return run
