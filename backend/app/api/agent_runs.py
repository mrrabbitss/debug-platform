from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.utils import json_loads
from app.models import AgentRun, Case
from app.services.agent_trace import agent_run_to_dict
from app.services.agentic_search import agentic_search
from app.services.audit import record_audit_event


router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])
Db = Annotated[Session, Depends(get_db)]


def _require_admin(request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", {}) or {}
    if principal.get("role") != "ADMIN":
        raise HTTPException(403, "Administrator role required")
    return principal


@router.get("")
def list_agent_runs(
    request: Request,
    db: Db,
    case_id: str | None = Query(default=None),
    resource_id: str | None = Query(default=None),
    operation: str | None = Query(default=None, max_length=64),
    status: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    _require_admin(request)
    query = select(AgentRun)
    if case_id:
        query = query.where(AgentRun.case_id == case_id)
    if resource_id:
        query = query.where(AgentRun.resource_id == resource_id)
    if operation:
        query = query.where(AgentRun.operation == operation)
    if status:
        query = query.where(AgentRun.status == status)
    runs = list(db.scalars(
        query.order_by(AgentRun.created_at.desc()).limit(limit)
    ).all())
    return [agent_run_to_dict(db, run) for run in runs]


@router.get("/{run_id}")
def get_agent_run(run_id: str, request: Request, db: Db) -> dict[str, Any]:
    _require_admin(request)
    run = db.get(AgentRun, run_id)
    if not run:
        raise HTTPException(404, "Agent run not found")
    return agent_run_to_dict(db, run, include_events=True)


@router.post("/{run_id}/replay")
def replay_agent_run(run_id: str, request: Request, db: Db) -> dict[str, Any]:
    principal = _require_admin(request)
    run = db.get(AgentRun, run_id)
    if not run:
        raise HTTPException(404, "Agent run not found")
    payload = json_loads(run.replay_payload_json, {})
    if run.operation != "agentic_search" or not run.case_id or not payload.get("query"):
        raise HTTPException(
            409,
            "This run has no content-safe read-only replay payload",
        )
    if not db.get(Case, run.case_id):
        raise HTTPException(409, "The source case no longer exists")
    result = agentic_search(
        db,
        case_id=run.case_id,
        query=str(payload["query"]),
        top_k=int(payload.get("top_k") or 12),
        max_hops=int(payload.get("max_hops") or 2),
        requested_modules=(
            [str(item) for item in payload["modules"]]
            if isinstance(payload.get("modules"), list)
            else None
        ),
        record_memory=False,
        execution_mode="replay",
        replay_of_run_id=run.id,
        created_by=str(principal.get("id") or "admin"),
        joint_diagnostic_scope=bool(payload.get("joint_diagnostic_scope", False)),
    )
    record_audit_event(
        "agent.run.replay",
        actor_id=str(principal.get("id") or "admin"),
        actor_type=str(principal.get("type") or "system"),
        resource_type="agent_run",
        resource_id=str(result["run_id"]),
        details={"replay_of_run_id": run.id, "operation": run.operation},
    )
    return result
