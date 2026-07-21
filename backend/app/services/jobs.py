import traceback
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, new_id, utcnow
from app.models import Job


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def update(self, progress: int, message: str) -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if not job:
                return
            job.progress = max(0, min(100, progress))
            job.message = message
            db.commit()


class JobRunner:
    def __init__(self) -> None:
        self.executor = ThreadPoolExecutor(max_workers=get_settings().job_workers, thread_name_prefix="gw-ap-job")

    def submit(self, db: Session, kind: str, fn: Callable[..., Any], *args: Any, input_data: dict | None = None) -> Job:
        job = Job(id=new_id("JOB"), kind=kind, input_json=json_dumps(input_data or {}))
        db.add(job)
        db.commit()
        db.refresh(job)
        self.executor.submit(self._run, job.id, fn, args)
        return job

    @staticmethod
    def _run(job_id: str, fn: Callable[..., Any], args: tuple[Any, ...]) -> None:
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if not job:
                return
            job.status = "RUNNING"
            job.started_at = utcnow()
            job.progress = 1
            db.commit()
        try:
            result = fn(JobContext(job_id), *args)
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job:
                    job.status = "COMPLETED"
                    job.progress = 100
                    job.message = "Completed"
                    job.result_json = json_dumps(result if result is not None else {})
                    job.completed_at = utcnow()
                    db.commit()
        except Exception as exc:  # noqa: BLE001
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job:
                    job.status = "FAILED"
                    job.error_message = f"{exc}\n{traceback.format_exc(limit=8)}"
                    job.message = "Failed"
                    job.completed_at = utcnow()
                    db.commit()


job_runner = JobRunner()
