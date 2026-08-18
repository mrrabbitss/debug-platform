"""Content-safe API serialization for persisted Agent trajectories."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils import json_loads
from app.models import AgentRun, AgentTraceEvent
from app.services.evidence_display import (
    labels_for_evidence_ids,
    resolve_evidence_labels,
)


def agent_run_to_dict(
    db: Session,
    run: AgentRun,
    *,
    include_events: bool = False,
) -> dict[str, Any]:
    replay_payload = json_loads(run.replay_payload_json, {})
    run_evidence_ids = json_loads(run.evidence_ids_json, [])
    events: list[AgentTraceEvent] = []
    if include_events:
        events = list(db.scalars(
            select(AgentTraceEvent)
            .where(AgentTraceEvent.run_id == run.id)
            .order_by(AgentTraceEvent.sequence)
        ).all())
    evidence_label_map = resolve_evidence_labels(db, [
        *run_evidence_ids,
        *(
            evidence_id
            for event in events
            for evidence_id in json_loads(event.evidence_ids_json, [])
        ),
    ])
    result = {
        "run_id": run.id,
        "case_id": run.case_id,
        "resource_type": run.resource_type,
        "resource_id": run.resource_id,
        "operation": run.operation,
        "execution_mode": run.execution_mode,
        "status": run.status,
        "model_profile_id": run.model_profile_id,
        "model_name": run.model_name,
        "model_config": json_loads(run.model_config_json, {}),
        "prompt_version": run.prompt_version,
        "input_summary_hash": run.input_summary_hash,
        "output_summary_hash": run.output_summary_hash,
        "usage": {
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "total_tokens": run.total_tokens,
            "estimated_cost": run.estimated_cost,
        },
        "duration_ms": run.duration_ms,
        "retry_count": run.retry_count,
        "evidence_ids": run_evidence_ids,
        "evidence_labels": labels_for_evidence_ids(
            run_evidence_ids, evidence_label_map,
        ),
        "stop_reason": run.stop_reason,
        "approval_status": run.approval_status,
        "replay_of_run_id": run.replay_of_run_id,
        "replay_payload": replay_payload,
        "replay_supported": bool(
            run.operation == "agentic_search"
            and run.case_id
            and replay_payload.get("query")
        ),
        "score": json_loads(run.score_json, {}),
        "created_by": run.created_by,
        "created_at": run.created_at,
        "started_at": run.started_at,
        "completed_at": run.completed_at,
    }
    if include_events:
        result["events"] = [
            _event_to_dict(event, evidence_label_map)
            for event in events
        ]
    return result


def _event_to_dict(
    event: AgentTraceEvent,
    evidence_label_map: dict[str, str],
) -> dict[str, Any]:
    evidence_ids = json_loads(event.evidence_ids_json, [])
    return {
        "id": event.id,
        "sequence": event.sequence,
        "stage": event.stage,
        "tool_name": event.tool_name,
        "status": event.status,
        "input_summary_hash": event.input_summary_hash,
        "output_summary_hash": event.output_summary_hash,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "duration_ms": event.duration_ms,
        "retry_count": event.retry_count,
        "evidence_ids": evidence_ids,
        "evidence_labels": labels_for_evidence_ids(
            evidence_ids, evidence_label_map,
        ),
        "stop_reason": event.stop_reason,
        "metadata": json_loads(event.metadata_json, {}),
        "created_at": event.created_at,
    }
