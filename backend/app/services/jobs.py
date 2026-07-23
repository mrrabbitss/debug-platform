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
ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")
TERMINAL_JOB_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")


class JobCancelledError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobHandler:
    function: Callable[..., Any]
    argument_names: tuple[str, ...]
    cancellable: bool


class JobContext:
    def __init__(self, job_id: str):
        self.job_id = job_id

    def update(self, progress: int, message: str) -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if job and job.status == "CANCEL_REQUESTED":
                raise JobCancelledError("Job cancellation requested")
            if not job or job.status != "RUNNING":
                return
            job.progress = max(0, min(100, progress))
            job.message = message
            db.commit()

    def raise_if_cancelled(self) -> None:
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if job and job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
                raise JobCancelledError("Job cancellation requested")

    def complete_in_transaction(
        self,
        db: Session,
        result: Any,
        message: str = "Completed",
    ) -> None:
        """Claim the commit point in the same transaction as handler output.

        Cancellation and publication both use conditional updates. Whichever
        obtains the database write lock first wins, so a late cancellation can
        never roll back an already-published parse or diagnosis generation.
        """
        with db.no_autoflush:
            completed = db.execute(
                update(Job)
                .where(Job.id == self.job_id, Job.status == "RUNNING")
                .values(
                    status="COMPLETED",
                    progress=100,
                    message=message,
                    result_json=json_dumps(result if result is not None else {}),
                    completed_at=utcnow(),
                )
            )
        if completed.rowcount == 1:
            return
        current_status = db.scalar(select(Job.status).where(Job.id == self.job_id))
        if current_status in {"CANCEL_REQUESTED", "CANCELLED"}:
            raise JobCancelledError("Job cancellation requested")
        if current_status == "COMPLETED":
            return
        raise RuntimeError(f"Job cannot publish results from status: {current_status or 'missing'}")


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
        *,
        cancellable: bool = False,
    ) -> None:
        self.handlers[kind] = JobHandler(
            function=function,
            argument_names=argument_names,
            cancellable=cancellable,
        )

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
            job_ids: list[str] = []
            for job in jobs:
                if job.status == "CANCEL_REQUESTED":
                    job.status = "CANCELLED"
                    job.message = "Cancelled during backend restart"
                    job.completed_at = utcnow()
                    continue
                if job.status == "RUNNING":
                    job.status = "QUEUED"
                    job.progress = 0
                    job.message = "Backend restarted; job queued for recovery"
                    job.started_at = None
                job_ids.append(job.id)
            db.commit()

        for job_id in job_ids:
            self._schedule(job_id)
        return len(job_ids)

    def request_cancel(self, db: Session, job_id: str) -> Job:
        job = db.get(Job, job_id)
        if not job:
            raise ValueError("Job not found")
        handler = self.handlers.get(job.kind)
        if job.status in {"QUEUED", "RUNNING"} and (not handler or not handler.cancellable):
            raise ValueError(f"Job kind is not safely cancellable: {job.kind}")
        if job.status == "QUEUED":
            cancelled = db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "QUEUED")
                .values(
                    status="CANCELLED",
                    message="Cancelled before execution",
                    completed_at=utcnow(),
                )
            )
            if cancelled.rowcount != 1:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "RUNNING")
                    .values(status="CANCEL_REQUESTED", message="Cancellation requested")
                )
        elif job.status == "RUNNING":
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status == "RUNNING")
                .values(status="CANCEL_REQUESTED", message="Cancellation requested")
            )
        db.commit()
        db.expire(job)
        db.refresh(job)
        return job

    def retry(self, db: Session, job_id: str) -> Job:
        previous = db.get(Job, job_id)
        if not previous:
            raise ValueError("Job not found")
        if previous.status not in {"FAILED", "CANCELLED"}:
            raise ValueError("Only failed or cancelled jobs can be retried")
        if previous.kind not in self.handlers:
            raise ValueError(f"No handler registered for job kind: {previous.kind}")
        input_data = json_loads(previous.input_json, {})
        input_data["_retry_of_job_id"] = previous.id
        input_data["_attempt"] = int(input_data.get("_attempt", 1)) + 1
        job = Job(
            id=new_id("JOB"),
            kind=previous.kind,
            input_json=json_dumps(input_data),
            message=f"Retry attempt {input_data['_attempt']}",
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        self._schedule(job.id)
        return job

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
                completed = db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "RUNNING")
                    .values(
                        status="COMPLETED",
                        progress=100,
                        message="Completed",
                        result_json=json_dumps(result if result is not None else {}),
                        completed_at=utcnow(),
                    )
                )
                if completed.rowcount != 1:
                    db.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.status == "CANCEL_REQUESTED")
                        .values(status="CANCELLED", message="Cancelled", completed_at=utcnow())
                    )
                db.commit()
        except JobCancelledError:
            with SessionLocal() as db:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status.in_(ACTIVE_JOB_STATUSES))
                    .values(status="CANCELLED", message="Cancelled", completed_at=utcnow())
                )
                db.commit()
        except Exception as exc:  # noqa: BLE001
            logger.exception("Background job %s failed", job_id)
            with SessionLocal() as db:
                cancelled = db.execute(
                    update(Job)
                    .where(Job.id == job_id, Job.status == "CANCEL_REQUESTED")
                    .values(status="CANCELLED", message="Cancelled", completed_at=utcnow())
                )
                if cancelled.rowcount != 1:
                    db.execute(
                        update(Job)
                        .where(Job.id == job_id, Job.status.in_(("QUEUED", "RUNNING")))
                        .values(
                            status="FAILED",
                            error_message=str(exc) or type(exc).__name__,
                            message="Failed",
                            completed_at=utcnow(),
                        )
                    )
                db.commit()


job_runner = JobRunner()
