from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.utils import json_loads, utcnow
from app.services.host_agent_session_contracts import (
    HostAgentPlanningRound,
    HostAgentPlanningRoundAppend,
    HostAgentSessionView,
)
from app.services.host_agent_sessions import (
    MAX_COVERAGE_JSON_CHARS,
    HostAgentSessionConflictError,
    HostAgentSessionError,
    _bounded_json,
    _cas_update,
    _ensure_lease,
    _ensure_mutable,
    _require_record,
    _safe_json,
    hash_host_agent_tool_arguments,
    host_agent_session_to_view,
)


MAX_PLANNING_ROUNDS_JSON_CHARS = 2_000_000


def append_host_agent_planning_round(
    db: Session,
    session_id: str,
    request: HostAgentPlanningRoundAppend,
    *,
    coverage: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> HostAgentSessionView:
    """Persist one normalized host-model round with optimistic concurrency."""
    current_time = now or utcnow()
    record = _require_record(db, session_id)
    _ensure_mutable(record, now=current_time)
    _ensure_lease(record, request.lease_owner, now=current_time)
    raw_rounds = json_loads(record.planning_rounds_json, [])
    rounds = [
        HostAgentPlanningRound.model_validate(item)
        for item in raw_rounds if isinstance(item, dict)
    ] if isinstance(raw_rounds, list) else []
    payload = _safe_json(request.payload)
    summary = _safe_json(request.summary)
    if not isinstance(payload, dict) or not isinstance(summary, dict):
        raise HostAgentSessionError("Planning round payload and summary must be objects")
    candidate = HostAgentPlanningRound(
        round_number=request.round_number,
        payload_hash=hash_host_agent_tool_arguments(payload),
        payload=payload,
        summary=summary,
        recorded_at=current_time,
    )
    for existing in rounds:
        if existing.round_number != request.round_number:
            continue
        if (
            existing.payload_hash == candidate.payload_hash
            and existing.summary == candidate.summary
        ):
            return host_agent_session_to_view(record)
        raise HostAgentSessionConflictError(
            "Planning round was already recorded with a different payload"
        )
    expected_round = rounds[-1].round_number + 1 if rounds else 1
    if request.round_number != expected_round:
        raise HostAgentSessionConflictError(
            f"Expected host diagnostic round {expected_round}, "
            f"got {request.round_number}"
        )
    rounds.append(candidate)
    rounds_json = _bounded_json(
        [item.model_dump(mode="json") for item in rounds],
        max_chars=MAX_PLANNING_ROUNDS_JSON_CHARS,
        label="Host-agent planning rounds",
    )
    values = {"planning_rounds_json": rounds_json}
    if coverage is not None:
        safe_coverage = _safe_json(coverage)
        if not isinstance(safe_coverage, dict):
            raise HostAgentSessionError("Planning coverage must be an object")
        values["coverage_json"] = _bounded_json(
            safe_coverage,
            max_chars=MAX_COVERAGE_JSON_CHARS,
            label="Host-agent coverage",
        )
    return _cas_update(
        db,
        record,
        expected_version=request.expected_version,
        values=values,
        now=current_time,
    )
