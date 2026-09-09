"""Content-free, durable model-work milestones; never infer work from elapsed time."""
from contextvars import ContextVar
from functools import wraps
from datetime import timedelta

from sqlalchemy import case as sql_case, select, update

from app.core.utils import json_dumps, json_loads, utcnow
from app.models import Job


active_job_context: ContextVar = ContextVar("active_job_progress", default=None)


def report_progress(ctx, progress, message, *, stage, stage_index, stage_count,
                    completed_units=None, total_units=None, unit=None, round_number=None,
                    waiting_for_model=False):
    details = {"stage": stage, "stage_index": stage_index, "stage_count": stage_count,
               "completed_units": completed_units, "total_units": total_units,
               "unit": unit, "round_number": round_number, "waiting_for_model": waiting_for_model}
    # Older adapters and domain test contexts only implement the two-argument API.
    reporter = getattr(ctx, "report_progress", None)
    if reporter:
        reporter(progress, message, details)
    else:
        ctx.update(progress, message)


def track_model_request(function):
    """Mark the actual API wait, including SDK retries, without another model call."""
    @wraps(function)
    async def tracked(*args, **kwargs):
        ctx = active_job_context.get()
        if ctx is not None:
            ctx.model_wait(True)
        try:
            result = await function(*args, **kwargs)
        except BaseException:
            # Keep the failure at its last confirmed stage. No extra write can
            # mask cancellation, lost leases, or the original model exception.
            raise
        if ctx is not None:
            ctx.model_wait(False)
        return result
    return tracked


def write_job_progress(ctx, session_factory, progress, message, details=None):
    """Persist one lease-fenced progress update using the runner session factory."""
    from app.services.jobs import JobLeaseLostError, JobTimeoutError

    now = utcnow()
    percentage = max(0, min(99, int(progress))) if progress is not None else None
    with session_factory() as db:
        # Acquire the lease-fenced write before reading the old metadata.
        # This avoids a SQLite read-to-write upgrade while a heartbeat runs.
        changed = db.execute(
            update(Job)
            .where(*ctx._owned_running_clause())
            .values(
                progress=(sql_case((Job.progress < percentage, percentage), else_=Job.progress)
                          if percentage is not None else Job.progress),
                message=message[:4000] if message is not None else Job.message,
                heartbeat_at=now,
                lease_expires_at=now + timedelta(seconds=ctx.lease_seconds),
            )
            .returning(Job.progress_json, Job.progress)
            .execution_options(synchronize_session=False)
        ).first()
        if changed is not None:
            previous = json_loads(changed.progress_json, {})
            previous = previous if isinstance(previous, dict) else {}
            current = {**previous, **(details or {})}
            if current:
                stage_changed = current.get("stage") != previous.get("stage")
                if stage_changed or not current.get("stage_started_at"):
                    current["stage_started_at"] = now.isoformat()
                if current.get("waiting_for_model"):
                    if stage_changed or not previous.get("waiting_for_model") or not current.get("model_started_at"):
                        current["model_started_at"] = now.isoformat()
                else:
                    current["model_started_at"] = None
                current.update(completed_percent=changed.progress, updated_at=now.isoformat())
                db.execute(update(Job).where(*ctx._owned_running_clause())
                           .values(progress_json=json_dumps(current)))
            expired = db.scalar(select(Job.id).where(
                Job.id == ctx.job_id,
                Job.deadline_at.is_not(None),
                Job.deadline_at <= now,
            ))
            db.commit()
            if expired:
                raise JobTimeoutError("Background job exceeded its runtime budget")
            return
        db.rollback()
    ctx.raise_if_cancelled()
    raise JobLeaseLostError("Background job lease is no longer owned")
