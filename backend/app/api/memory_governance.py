from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.db import get_db
from app.models import AgentMemory
from app.services.audit import record_audit_event
from app.services.memory import memory_to_dict
from app.services.memory_governance import review_memory

router = APIRouter(tags=["memory-governance"])
Db = Annotated[Session, Depends(get_db)]


def administrator(request: Request) -> dict:
    principal = getattr(request.state, "principal", {})
    if principal.get("role") != "ADMIN":
        raise HTTPException(403, "Administrator role required")
    return principal


class MemoryReview(BaseModel):
    action: Literal["PUBLISH", "REJECT", "ARCHIVE"]
    expected_version: int = Field(ge=1)
    comment: str = Field(min_length=1, max_length=4000)
    expiry_days: int = Field(default=180, ge=1, le=730)


@router.get("/memory-governance/candidates")
def list_candidates(request: Request, db: Db, review_status: str | None = Query(default=None, pattern="^(CANDIDATE|PUBLISHED|REJECTED|ARCHIVED)$"),
                    limit: int = Query(default=100, ge=1, le=500)) -> list[dict]:
    administrator(request)
    query = select(AgentMemory)
    if review_status:
        query = query.where(AgentMemory.review_status == review_status)
    return [memory_to_dict(row) for row in db.scalars(query.order_by(AgentMemory.updated_at.desc()).limit(limit))]


@router.post("/memory-governance/candidates/{memory_id}/review")
def review_candidate(memory_id: str, payload: MemoryReview, request: Request, db: Db) -> dict:
    principal = administrator(request)
    memory = db.get(AgentMemory, memory_id)
    if not memory:
        raise HTTPException(404, "Candidate memory not found")
    try:
        review_memory(db, memory, reviewer=principal["id"], **payload.model_dump())
        db.commit()
        db.refresh(memory)
    except (ValueError, StaleDataError) as error:
        db.rollback()
        raise HTTPException(409, "Memory review conflict; refresh and verify the candidate" if isinstance(error, StaleDataError) else str(error)) from error
    record_audit_event("memory.review", actor_id=principal["id"], actor_type=principal.get("type", "user_token"),
                       resource_type="memory", resource_id=memory.id,
                       details={"action": payload.action, "review_version": memory.review_version})
    return memory_to_dict(memory)
