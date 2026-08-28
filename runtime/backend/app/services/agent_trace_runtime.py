from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import AgentRun, AgentTraceEvent
from app.services.agent_trace import (
    _event_metadata,
    sanitize_model_config,
    score_trajectory,
    summary_hash,
)


def create_live_agent_run(
    db: Session,
    *,
    operation: str,
    case_id: str | None,
    resource_type: str,
    resource_id: str | None,
    input_summary: Any,
    execution_mode: str = "llm_planner",
    model_profile_id: str | None = None,
    model_name: str | None = None,
    model_config: dict[str, Any] | None = None,
    prompt_version: str = "",
    created_by: str | None = None,
    approval_status: str = "READ_ONLY_AUTO",
) -> AgentRun:
    run = AgentRun(
        id=new_id("ARUN"),
        case_id=case_id,
        resource_type=resource_type[:64],
        resource_id=(resource_id or case_id),
        operation=operation[:64],
        execution_mode=execution_mode[:32],
        status="QUEUED",
        model_profile_id=model_profile_id,
        model_name=(model_name or "")[:512] or None,
        model_config_json=json_dumps(sanitize_model_config(model_config)),
        prompt_version=prompt_version[:128],
        input_summary_hash=summary_hash(input_summary),
        output_summary_hash=None,
        evidence_ids_json="[]",
        stop_reason="QUEUED",
        approval_status=approval_status[:64],
        created_by=created_by,
        completed_at=None,
    )
    db.add(run)
    db.flush()
    return run


def append_live_trace(
    db: Session,
    run_id: str,
    *,
    stage: str,
    status: str,
    tool_name: str | None = None,
    input_summary: Any = None,
    output_summary: Any = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    retry_count: int = 0,
    evidence_ids: list[str] | None = None,
    stop_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = True,
) -> AgentTraceEvent:
    run = db.get(AgentRun, run_id)
    if not run:
        raise ValueError("Agent run not found")
    sequence = int(db.scalar(
        select(func.max(AgentTraceEvent.sequence)).where(AgentTraceEvent.run_id == run_id)
    ) or 0) + 1
    normalized_evidence = list(dict.fromkeys(
        str(item)[:128]
        for item in (evidence_ids or [])
        if str(item).strip()
    ))[:250]
    safe_event = {
        "stage": stage,
        "status": status,
        **(metadata or {}),
    }
    event = AgentTraceEvent(
        id=new_id("ATRACE"),
        run_id=run_id,
        sequence=sequence,
        stage=stage[:128],
        tool_name=(tool_name or stage)[:128] or None,
        status=status[:32],
        input_summary_hash=summary_hash(input_summary or {"stage": stage}),
        output_summary_hash=(
            summary_hash(output_summary) if output_summary is not None else None
        ),
        input_tokens=max(0, int(input_tokens)),
        output_tokens=max(0, int(output_tokens)),
        duration_ms=max(0, int(duration_ms)),
        retry_count=max(0, int(retry_count)),
        evidence_ids_json=json_dumps(normalized_evidence),
        stop_reason=stop_reason[:128] if stop_reason else None,
        metadata_json=json_dumps(_event_metadata(safe_event)),
    )
    db.add(event)
    # SessionLocal deliberately disables autoflush. Persist the allocated
    # sequence before another event is appended in the same transaction so
    # the next MAX(sequence) query cannot reuse it.
    db.flush()
    run.status = "RUNNING" if status not in {"FAILED", "CANCELLED"} else status
    run.stop_reason = stop_reason[:128] if stop_reason else "RUNNING"
    run.input_tokens += max(0, int(input_tokens))
    run.output_tokens += max(0, int(output_tokens))
    run.total_tokens = run.input_tokens + run.output_tokens
    run.retry_count += max(0, int(retry_count))
    if normalized_evidence:
        existing = json_loads(run.evidence_ids_json, [])
        run.evidence_ids_json = json_dumps(
            list(dict.fromkeys([*existing, *normalized_evidence]))[:1000]
        )
    if commit:
        db.commit()
        db.refresh(event)
    return event


def finish_live_agent_run(
    db: Session,
    run_id: str,
    *,
    status: str,
    stop_reason: str,
    output_summary: Any,
    duration_ms: int,
    evidence_ids: list[str] | None = None,
    approval_status: str | None = None,
    budget_ms: int | None = None,
) -> AgentRun:
    run = db.get(AgentRun, run_id)
    if not run:
        raise ValueError("Agent run not found")
    normalized_evidence = list(dict.fromkeys([
        *json_loads(run.evidence_ids_json, []),
        *(evidence_ids or []),
    ]))[:1000]
    events = list(db.scalars(
        select(AgentTraceEvent)
        .where(AgentTraceEvent.run_id == run_id)
        .order_by(AgentTraceEvent.sequence)
    ).all())
    score_input = [
        {
            "stage": event.stage,
            "status": event.status,
            "duration_ms": event.duration_ms,
            "evidence_ids": json_loads(event.evidence_ids_json, []),
        }
        for event in events
    ]
    run.status = status[:32]
    run.stop_reason = stop_reason[:128]
    run.output_summary_hash = summary_hash(output_summary)
    run.duration_ms = max(0, int(duration_ms))
    run.evidence_ids_json = json_dumps(normalized_evidence)
    run.score_json = json_dumps(score_trajectory(
        score_input,
        evidence_ids=normalized_evidence,
        stop_reason=stop_reason,
        duration_ms=duration_ms,
        budget_ms=budget_ms,
    ))
    if approval_status:
        run.approval_status = approval_status[:64]
    run.completed_at = utcnow()
    db.commit()
    db.refresh(run)
    return run
