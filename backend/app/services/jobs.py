from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import Job
from app.services.storage_capacity import require_storage_capacity


logger = logging.getLogger(__name__)
ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING", "CANCEL_REQUESTED")
TERMINAL_JOB_STATUSES = ("COMPLETED", "FAILED", "CANCELLED", "DEAD_LETTER")


class JobCancelledError(RuntimeError):
    pass


class JobTimeoutError(RuntimeError):
    pass


class JobLeaseLostError(RuntimeError):
    pass


@dataclass(frozen=True)
class JobHandler:
    function: Callable[..., Any]
    argument_names: tuple[str, ...]
    cancellable: bool
    max_attempts: int | None = None
    timeout_seconds: int | None = None
    resource_limits: dict[str, Any] | None = None


class JobContext:
    def __init__(
        self,
        job_id: str,
        *,
        lease_owner: str | None = None,
        lease_seconds: int | None = None,
    ) -> None:
        self.job_id = job_id
        self.lease_owner = lease_owner
        self.lease_seconds = max(
            10,
            int(lease_seconds or get_settings().job_lease_seconds),
        )

    def _owned_running_clause(self):
        conditions = [Job.id == self.job_id, Job.status == "RUNNING"]
        if self.lease_owner:
            conditions.append(Job.lease_owner == self.lease_owner)
        return conditions

    def heartbeat(self) -> None:
        now = utcnow()
        with SessionLocal() as db:
            renewed = db.execute(
                update(Job)
                .where(*self._owned_running_clause())
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                )
                .execution_options(synchronize_session=False)
            )
            if renewed.rowcount == 1:
                db.commit()
                return
            db.rollback()
            job = db.get(Job, self.job_id)
            if job and job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
                raise JobCancelledError("Job cancellation requested")
            if job and job.status == "COMPLETED":
                return
            raise JobLeaseLostError("Background job lease is no longer owned")

    def update(self, progress: int, message: str) -> None:
        now = utcnow()
        with SessionLocal() as db:
            changed = db.execute(
                update(Job)
                .where(*self._owned_running_clause())
                .values(
                    progress=max(0, min(100, progress)),
                    message=message[:4000],
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                )
                .execution_options(synchronize_session=False)
            )
            if changed.rowcount == 1:
                expired = db.scalar(select(Job.id).where(
                    Job.id == self.job_id,
                    Job.deadline_at.is_not(None),
                    Job.deadline_at <= now,
                ))
                db.commit()
                if expired:
                    raise JobTimeoutError("Background job exceeded its runtime budget")
                return
            db.rollback()
        self.raise_if_cancelled()
        raise JobLeaseLostError("Background job lease is no longer owned")

    def raise_if_cancelled(self, *, allow_completed: bool = False) -> None:
        now = utcnow()
        with SessionLocal() as db:
            job = db.get(Job, self.job_id)
            if job and job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
                raise JobCancelledError("Job cancellation requested")
            if not job:
                raise JobLeaseLostError("Background job no longer exists")
            if allow_completed and job.status == "COMPLETED":
                return
            if self.lease_owner and job.lease_owner != self.lease_owner:
                raise JobLeaseLostError("Background job lease is no longer owned")
            expired = db.scalar(select(Job.id).where(
                Job.id == self.job_id,
                Job.deadline_at.is_not(None),
                Job.deadline_at <= now,
            ))
            if expired:
                raise JobTimeoutError("Background job exceeded its runtime budget")

    def complete_in_transaction(
        self,
        db: Session,
        result: Any,
        message: str = "Completed",
    ) -> None:
        """Atomically publish handler output and the job completion marker."""
        conditions = [Job.id == self.job_id, Job.status == "RUNNING"]
        if self.lease_owner:
            conditions.append(Job.lease_owner == self.lease_owner)
        with db.no_autoflush:
            completed = db.execute(
                update(Job)
                .where(*conditions)
                .values(
                    status="COMPLETED",
                    progress=100,
                    message=message[:4000],
                    result_json=json_dumps(result if result is not None else {}),
                    completed_at=utcnow(),
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )
            )
        if completed.rowcount == 1:
            return
        current_status = db.scalar(select(Job.status).where(Job.id == self.job_id))
        if current_status in {"CANCEL_REQUESTED", "CANCELLED"}:
            raise JobCancelledError("Job cancellation requested")
        if current_status == "COMPLETED":
            return
        raise JobLeaseLostError(
            f"Job cannot publish results from status: {current_status or 'missing'}"
        )


class JobRunner:
    """Lease-based persistent runner for one or more backend instances."""

    def __init__(
        self,
        max_workers: int | None = None,
        *,
        lease_seconds: int | None = None,
        heartbeat_seconds: int | None = None,
        dispatch_seconds: float | None = None,
        retry_base_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        self.max_workers = max_workers or settings.job_workers
        self.lease_seconds = max(10, int(lease_seconds or settings.job_lease_seconds))
        self.heartbeat_seconds = max(
            1,
            min(
                int(heartbeat_seconds or settings.job_heartbeat_seconds),
                max(1, self.lease_seconds // 3),
            ),
        )
        self.dispatch_seconds = max(
            0.05,
            float(dispatch_seconds or settings.job_dispatch_seconds),
        )
        self.retry_base_seconds = max(
            0.01,
            float(retry_base_seconds or settings.job_retry_base_seconds),
        )
        self.worker_id = (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        )[:160]
        self.executor: ThreadPoolExecutor | None = None
        self._dispatcher: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._shutdown = True
        self.handlers: dict[str, JobHandler] = {}
        self._scheduled: set[str] = set()
        self._lock = threading.Lock()

    def _new_executor(self) -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="gw-ap-job",
        )

    def start(self) -> None:
        with self._lock:
            if not self._shutdown:
                return
            self.executor = self._new_executor()
            self._stop_event = threading.Event()
            self._shutdown = False
            dispatcher = threading.Thread(
                target=self._dispatcher_loop,
                name="gw-ap-job-dispatcher",
                daemon=True,
            )
            self._dispatcher = dispatcher
        dispatcher.start()

    def register(
        self,
        kind: str,
        function: Callable[..., Any],
        argument_names: tuple[str, ...],
        *,
        cancellable: bool = False,
        max_attempts: int | None = None,
        timeout_seconds: int | None = None,
        resource_limits: dict[str, Any] | None = None,
    ) -> None:
        self.handlers[kind] = JobHandler(
            function=function,
            argument_names=argument_names,
            cancellable=cancellable,
            max_attempts=max_attempts,
            timeout_seconds=timeout_seconds,
            resource_limits=resource_limits,
        )

    def submit(
        self,
        db: Session,
        kind: str,
        function: Callable[..., Any],
        *args: Any,
        input_data: dict | None = None,
        deduplicate: bool = True,
        idempotency_key: str | None = None,
        max_attempts: int | None = None,
        timeout_seconds: int | None = None,
        resource_limits: dict[str, Any] | None = None,
    ) -> Job:
        settings = get_settings()
        serialized_input = json_dumps(input_data or {})
        handler = self.handlers.get(kind)
        limits = {
            **((handler.resource_limits or {}) if handler else {}),
            **(resource_limits or {}),
        }
        max_input_bytes = int(limits.get("max_input_bytes") or 0)
        if max_input_bytes and len(serialized_input.encode("utf-8")) > max_input_bytes:
            raise ValueError("Job input exceeds its configured resource quota")
        normalized_key = (idempotency_key or hashlib.sha256(
            f"{kind}\0{serialized_input}".encode("utf-8")
        ).hexdigest())[:128]
        if deduplicate:
            existing = db.scalars(
                select(Job).where(
                    Job.kind == kind,
                    Job.idempotency_key == normalized_key,
                    Job.status.in_(ACTIVE_JOB_STATUSES),
                ).order_by(Job.created_at.desc()).limit(1)
            ).first()
            if existing:
                if existing.status == "QUEUED":
                    self._schedule(existing.id, function, args)
                return existing

        require_storage_capacity()
        attempts = max(
            1,
            int(max_attempts or (handler.max_attempts if handler else 0)
                or settings.job_max_attempts),
        )
        timeout = max(
            1,
            int(timeout_seconds or (handler.timeout_seconds if handler else 0)
                or limits.get("max_runtime_seconds")
                or settings.job_default_timeout_seconds),
        )
        job = Job(
            id=new_id("JOB"),
            kind=kind,
            input_json=serialized_input,
            idempotency_key=normalized_key,
            max_attempts=attempts,
            available_at=utcnow(),
            timeout_seconds=timeout,
            resource_limits_json=json_dumps(limits),
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        self._schedule(job.id, function, args)
        return job

    def resume_incomplete(self) -> int:
        """Recover expired leases and schedule all due jobs."""
        with SessionLocal() as db:
            discovered = len(list(db.scalars(
                select(Job.id).where(Job.status.in_(ACTIVE_JOB_STATUSES))
            ).all()))
        self.start()
        self._recover_and_schedule()
        return discovered

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
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
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
        if previous.status not in {"FAILED", "CANCELLED", "DEAD_LETTER"}:
            raise ValueError("Only failed, dead-letter or cancelled jobs can be retried")
        if previous.kind not in self.handlers:
            raise ValueError(f"No handler registered for job kind: {previous.kind}")
        input_data = json_loads(previous.input_json, {})
        input_data["_retry_of_job_id"] = previous.id
        input_data["_manual_retry"] = True
        job = Job(
            id=new_id("JOB"),
            kind=previous.kind,
            input_json=json_dumps(input_data),
            idempotency_key=hashlib.sha256(
                f"manual-retry\0{previous.id}\0{uuid.uuid4().hex}".encode("utf-8")
            ).hexdigest(),
            message="Manual retry queued",
            max_attempts=previous.max_attempts,
            available_at=utcnow(),
            timeout_seconds=previous.timeout_seconds,
            resource_limits_json=previous.resource_limits_json,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        self._schedule(job.id)
        return job

    def shutdown(self, wait: bool = True) -> None:
        with self._lock:
            if self._shutdown:
                return
            executor = self.executor
            dispatcher = self._dispatcher
            self._shutdown = True
            self._stop_event.set()
        if dispatcher and dispatcher is not threading.current_thread():
            dispatcher.join(timeout=2)
        if executor:
            executor.shutdown(wait=wait, cancel_futures=False)
        with self._lock:
            self.executor = None
            self._dispatcher = None

    def _dispatcher_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._recover_and_schedule()
            except Exception:  # noqa: BLE001
                logger.exception("Background job dispatcher iteration failed")
            self._stop_event.wait(self.dispatch_seconds)

    def _recover_and_schedule(self) -> int:
        now = utcnow()
        with SessionLocal() as db:
            db.execute(
                update(Job)
                .where(
                    Job.status == "CANCEL_REQUESTED",
                    or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
                )
                .values(
                    status="CANCELLED",
                    message="Cancellation completed after lease expiry",
                    completed_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            db.execute(
                update(Job)
                .where(
                    Job.status == "RUNNING",
                    or_(Job.lease_expires_at.is_(None), Job.lease_expires_at <= now),
                )
                .values(
                    status="QUEUED",
                    progress=0,
                    message="Worker lease expired; queued for recovery",
                    available_at=now,
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                    deadline_at=None,
                )
                .execution_options(synchronize_session=False)
            )
            from app.services.knowledge_publication import recover_abandoned_publications
            recover_abandoned_publications(db)
            from app.services.assistant_state import recover_abandoned_assistant_sessions
            recover_abandoned_assistant_sessions(db)
            db.commit()
            job_ids = list(db.scalars(
                select(Job.id)
                .where(
                    Job.status == "QUEUED",
                    Job.available_at <= now,
                )
                .order_by(Job.available_at, Job.created_at)
                .limit(max(20, self.max_workers * 8))
            ).all())
        for job_id in job_ids:
            self._schedule(job_id)
        return len(job_ids)

    def _schedule(
        self,
        job_id: str,
        fallback_function: Callable[..., Any] | None = None,
        fallback_args: tuple[Any, ...] = (),
    ) -> None:
        self.start()
        with self._lock:
            if job_id in self._scheduled:
                return
            self._scheduled.add(job_id)
            executor = self.executor
        if executor is None:
            with self._lock:
                self._scheduled.discard(job_id)
            raise RuntimeError("Background job runner is not started")
        try:
            future = executor.submit(self._run, job_id, fallback_function, fallback_args)
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

    def _heartbeat_loop(self, job_id: str, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.heartbeat_seconds):
            now = utcnow()
            try:
                with SessionLocal() as db:
                    renewed = db.execute(
                        update(Job)
                        .where(
                            Job.id == job_id,
                            Job.status == "RUNNING",
                            Job.lease_owner == self.worker_id,
                        )
                        .values(
                            heartbeat_at=now,
                            lease_expires_at=(
                                now + timedelta(seconds=self.lease_seconds)
                            ),
                        )
                        .execution_options(synchronize_session=False)
                    )
                    db.commit()
                    if renewed.rowcount != 1:
                        return
            except Exception:  # noqa: BLE001
                logger.exception("Unable to renew lease for background job %s", job_id)

    def _mark_cancelled(self, job_id: str) -> None:
        with SessionLocal() as db:
            db.execute(
                update(Job)
                .where(Job.id == job_id, Job.status.in_(ACTIVE_JOB_STATUSES), Job.lease_owner == self.worker_id)
                .values(
                    status="CANCELLED",
                    message="Cancelled",
                    completed_at=utcnow(),
                    lease_owner=None,
                    lease_expires_at=None,
                    heartbeat_at=None,
                )
            )
            db.commit()

    def _fail_or_retry(self, job_id: str, exc: Exception) -> None:
        now = utcnow()
        error_message = (str(exc) or type(exc).__name__)[:4000]
        with SessionLocal() as db:
            job = db.scalars(update(Job).where(Job.id == job_id,
                Job.status.in_(ACTIVE_JOB_STATUSES), Job.lease_owner == self.worker_id)
                .values(status=Job.status).returning(Job)).first()
            if not job or job.status not in ACTIVE_JOB_STATUSES:
                return
            if job.status == "CANCEL_REQUESTED":
                job.status = "CANCELLED"
                job.message = "Cancelled"
                job.completed_at = now
            elif job.attempt < job.max_attempts:
                delay = min(
                    300.0,
                    self.retry_base_seconds * (2 ** max(0, job.attempt - 1)),
                )
                job.status = "QUEUED"
                job.progress = 0
                job.message = (
                    f"Retry {job.attempt + 1}/{job.max_attempts} scheduled "
                    f"after {delay:.2f}s backoff"
                )
                job.available_at = now + timedelta(seconds=delay)
                job.error_message = error_message
                job.deadline_at = None
            else:
                job.status = "DEAD_LETTER"
                job.message = "Retry budget exhausted; moved to dead letter"
                job.error_message = error_message
                job.dead_letter_reason = error_message
                job.dead_letter_at = now
                job.completed_at = now
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            db.commit()

    def _run(
        self,
        job_id: str,
        fallback_function: Callable[..., Any] | None = None,
        fallback_args: tuple[Any, ...] = (),
    ) -> None:
        heartbeat_stop = threading.Event()
        heartbeat_thread: threading.Thread | None = None
        try:
            with SessionLocal() as db:
                job = db.get(Job, job_id)
                if not job:
                    return
                now = utcnow()
                claimed = db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status == "QUEUED",
                        Job.available_at <= now,
                    )
                    .values(
                        status="RUNNING",
                        started_at=now,
                        progress=1,
                        message="Running",
                        attempt=Job.attempt + 1,
                        lease_owner=self.worker_id,
                        heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                        deadline_at=now + timedelta(seconds=job.timeout_seconds),
                    )
                    .execution_options(synchronize_session=False)
                )
                if claimed.rowcount != 1:
                    db.rollback()
                    return
                db.commit()
                db.refresh(job)
                function, args = self._resolve_handler(
                    job,
                    fallback_function,
                    fallback_args,
                )

            context = JobContext(
                job_id,
                lease_owner=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
            heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                args=(job_id, heartbeat_stop),
                name=f"gw-ap-heartbeat-{job_id[-8:]}",
                daemon=True,
            )
            heartbeat_thread.start()
            result = function(context, *args)
            context.raise_if_cancelled(allow_completed=True)
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
            with SessionLocal() as db:
                completed = db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status == "RUNNING",
                        Job.lease_owner == self.worker_id,
                    )
                    .values(
                        status="COMPLETED",
                        progress=100,
                        message="Completed",
                        result_json=json_dumps(result if result is not None else {}),
                        completed_at=utcnow(),
                        lease_owner=None,
                        lease_expires_at=None,
                        heartbeat_at=None,
                    )
                )
                if completed.rowcount != 1:
                    current_status = db.scalar(select(Job.status).where(Job.id == job_id))
                    if current_status == "CANCEL_REQUESTED":
                        db.execute(
                            update(Job)
                            .where(Job.id == job_id, Job.status == "CANCEL_REQUESTED")
                            .values(
                                status="CANCELLED",
                                message="Cancelled",
                                completed_at=utcnow(),
                                lease_owner=None,
                                lease_expires_at=None,
                                heartbeat_at=None,
                            )
                        )
                    elif current_status != "COMPLETED":
                        raise JobLeaseLostError(
                            f"Job completion lost lease from status {current_status}"
                        )
                db.commit()
        except JobCancelledError:
            self._mark_cancelled(job_id)
        except JobLeaseLostError:
            pass  # The replacement worker owns all further state changes.
        except Exception as exc:  # noqa: BLE001
            logger.exception("Background job %s failed", job_id)
            self._fail_or_retry(job_id, exc)
        finally:
            heartbeat_stop.set()
            if heartbeat_thread and heartbeat_thread.is_alive():
                heartbeat_thread.join(timeout=2)


job_runner = JobRunner()
