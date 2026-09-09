from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Job
from app.schemas import JobOut
from app.services.jobs import job_runner


router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


def _protect_system_job(request: Request, job: Job, db: Session) -> None:
    from app.services.knowledge_access import authorize_routing_job
    if authorize_routing_job(db, job.id, getattr(request.state, "principal", {}), method=request.method):
        return
    if (
        job.kind in {"download_model_files", "route_markdown_knowledge"}
        and getattr(request.state, "principal", {}).get("role") != "ADMIN"
    ):
        raise HTTPException(403, "Administrator role required for this job")


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, request: Request, db: Db) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    _protect_system_job(request, job, db)
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, request: Request, db: Db) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    _protect_system_job(request, job, db)
    try:
        return job_runner.request_cancel(db, job_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Job not found" else 409
        raise HTTPException(status_code, str(exc)) from exc

@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, request: Request, db: Db) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    _protect_system_job(request, job, db)
    try:
        return job_runner.retry(db, job_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Job not found" else 409
        raise HTTPException(status_code, str(exc)) from exc
