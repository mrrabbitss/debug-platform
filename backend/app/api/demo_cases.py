from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.schemas import DemoCaseImportOut
from app.services.demo_cases import import_ap_frequent_offline_demo


router = APIRouter(prefix="/demo-cases", tags=["demo-cases"])
Db = Annotated[Session, Depends(get_db)]


@router.post("/ap-frequent-offline", response_model=DemoCaseImportOut)
def import_ap_offline_demo(request: Request, db: Db) -> dict[str, Any]:
    principal = getattr(request.state, "principal", {}) or {}
    if principal.get("role") == "VIEWER":
        raise HTTPException(403, "Viewer role is read-only")
    try:
        return import_ap_frequent_offline_demo(db, principal=principal)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(503, str(exc)) from exc
