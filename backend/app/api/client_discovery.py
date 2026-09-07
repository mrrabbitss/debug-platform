"""Authenticated client discovery and administrator-only operational status."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.services.client_capabilities import client_capabilities
from app.services.health import system_status_report
from app.services.model_capacity import model_capacity_status

router = APIRouter(tags=["system"])
Db = Annotated[Session, Depends(get_db)]


@router.get("/system/status")
def system_status(db: Db) -> dict:
    settings = get_settings()
    report = system_status_report(db, settings.storage_root, settings.job_workers)
    report.update(app=settings.app_name, environment=settings.app_env, deployment_mode=settings.deployment_mode,
                  server_id=settings.server_instance_id or "standalone", model_capacity=model_capacity_status())
    return report


@router.get("/system/client-info")
def client_info() -> dict:
    return client_capabilities(get_settings())
