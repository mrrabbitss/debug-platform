import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import Job


logger = logging.getLogger(__name__)
ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING")
TERMINAL_JOB_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


@dataclass(frozen=True)
class JobHandler:
    function: Callable[..., Any]
    argument_names: tuple[str, ...]


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def update(self, progress: int, message: str) -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if not job or job.status != "RUNNING":
                return
            job.progress = max(0, min(100, progress))
            job.message = message
            db.commit()


class JobRunner:
    """Database-backed local job runner with restart recovery.

    The database is the source of truth. The thread pool is only the execution
    mechanism, so a fresh backend process can reconstruct queued/running jobs
    from their registered kind and input JSON.
    """

    def __init__(self, max_workers: int | None = None) -> None:
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers or get_settings().job_workers,
            thread_name_prefix="gw-ap-job",
        )
        self.handlers: dict[str, JobHandler] = {}
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()

    def register(
        self,
        kind: str,
        function: Callable[..., Any],
        argument_names: tuple[str, ...],
    ) -> None:
        self.handlers[kind] = JobHandler(function=function, argument_names=argument_names)

    def submit(
        self,
        db: Session,
        kind: str,
        function: Callable[..., Any],
        *args: Any,
        input_data: dict | None = None,
        deduplicate: bool = True,
    ) -> Job:
        serialized_input = json_dumps(input_data or {})
        if deduplicate:
            existing = db.scalars(
                select(Job).where(
                    Job.kind == kind,
                    Job.input_json == serialized_input,
                    Job.status.in_(ACTIVE_JOB_STATUSES),
                ).order_by(Job.created_at.desc()).limit(1)
            ).first()
            if existing:
                if existing.status == "QUEUED":
                    self._schedule(existing.id, function, args)
                return existing

        job = Job(id=new_id("JOB"), kind=kind, input_json=serialized_input)
        db.add(job)
        db.commit()
        db.refresh(job)
        self._schedule(job.id, function, args)
        return job

    def resume_incomplete(self) -> int:
        """Requeue jobs interrupted by a backend restart and schedule them once."""
        with SessionLocal() as db:
            jobs = list(db.scalars(
                select(Job).where(Job.status.in_(ACTIVE_JOB_STATUSES)).order_by(Job.created_at)
            ).all())
            for job in jobs:
                if job.status == "RUNNING":
                    job.status = "QUEUED"
                    job.progress = 0
                    job.message = "Backend restarted; job queued for recovery"
                    job.started_at = None
            db.commit()
            job_ids = [job.id for job in jobs]

        for job_id in job_ids:
            self._schedule(job_id)
        return len(job_ids)

    def shutdown(self, wait: bool = True) -> None:
        self.executor.shutdown(wait=wait, cancel_futures=False)

    def _schedule(
        self,
        job_id: str,
        fallback_function: Callable[..., Any] | None = None,
        fallback_args: tuple[Any, ...] = (),
    ) -> None:
        with self._lock:
            if job_id in self._scheduled:
                return
            self._scheduled.add(job_id)
        try:
            future = self.executor.submit(self._run, job_id, fallback_function, fallback_args)
        except RuntimeError:
            with self._lock:
                self._scheduled.discard(job_id)
            raise
        future.add_done_callback(lambda _: self._mark_unscheduled(job_id))

    def _mark_unscheduled(self, job_id: str) -> None:
        with self._lock:
            self._scheduled.discard(job_id)

    def _resolve_handler(
        self,
        job: Job,
        fallback_function: Callable[..., Any] | None,
        fallback_args: tuple[Any, ...],
    ) -> tuple[Callable[..., Any], tuple[Any, ...]]:
        handler = self.handlers.get(job.kind)
        if handler:
            input_data = json_loads(job.input_json, {})
            missing = [name for name in handler.argument_names if name not in input_data]
            if missing:
                raise ValueError(f"Job input is missing required fields: {', '.join(missing)}")
            return handler.function, tuple(input_data[name] for name in handler.argument_names)
        if fallback_function:
            return fallback_function, fallback_args
        raise ValueError(f"No handler registered for job kind: {job.kind}")

    def _run(
        self,
        job_id: str,
        fallback_function: Callable[..., Any] | None = None,
        fallback_args: tuple[Any, ...] = (),
    ) -> None:
        function: Callable[..., Any]
        args: tuple[Any, ...]
        try:
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if not job:
                    return
                function, args = self._resolve_handler(job, fallback_function, fallback_args)
                claimed = db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "QUEUED")
                    .values(status="RUNNING", started_at=utcnow(), progress=1, message="Running")
                )
                if claimed.rowcount != 1:
                    db.rollback()
                    return
                db.commit()

            result = function(JobContext(job_id), *args)
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job and job.status == "RUNNING":
                    job.status = "COMPLETED"
                    job.progress = 100
                    job.message = "Completed"
                    job.result_json = json_dumps(result if result is not None else {})
                    job.completed_at = utcnow()
                    db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Background job %s failed", job_id)
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if job and job.status not in TERMINAL_JOB_STATUSES:
                    job.status = "FAILED"
                    job.error_message = str(exc) or type(exc).__name__
                    job.message = "Failed"
                    job.completed_at = utcnow()
                    db.commit()


job_runner = JobRunner()
