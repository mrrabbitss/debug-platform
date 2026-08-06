from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import Job
from app.schemas import JobOut
from app.services.jobs import job_runner


router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Db) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, db: Db) -> Job:
    try:
        return job_runner.request_cancel(db, job_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Job not found" else 409
        raise HTTPException(status_code, str(exc)) from exc

@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(job_id: str, db: Db) -> Job:
    try:
        return job_runner.retry(db, job_id)
    except ValueError as exc:
        status_code = 404 if str(exc) == "Job not found" else 409
        raise HTTPException(status_code, str(exc)) from exc
