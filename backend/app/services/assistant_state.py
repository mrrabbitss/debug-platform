"""Assistant state fencing and transactional outbox using the existing job table."""
import hashlib
import json
from sqlalchemy import select, update
from app.core.timeouts import AI_JOB_TIMEOUT_SECONDS
from app.core.config import get_settings
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import Job, KnowledgeGraphState, UserAccount
from app.workbench_models import WorkbenchRecord
from app.services.jobs import JobCancelledError, JobLeaseLostError, JobTimeoutError


def digest(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require_admin_actor(db, actor):
    account = db.get(UserAccount, actor) if actor else None
    if account:
        if account.active and account.role in {"ADMIN", "EXPERT"}:
            return
        raise ValueError("管理员或专家身份已失效，不能继续此任务")
    settings = get_settings()
    if actor == "local-development" and settings.auth_mode == "local":
        return
    if actor == "legacy-api-key" and (settings.auth_mode in {"local", "api_key"} or settings.auth_allow_legacy_admin):
        return
    raise ValueError("管理员或专家身份已失效，不能继续此任务")


def locked_session(db, session_id):
    row = db.get(WorkbenchRecord, session_id)
    if not row or row.kind != "assistant":
        raise ValueError("整理会话不存在")
    # Also acquire a write fence on SQLite, where FOR UPDATE is ignored.
    claimed = db.execute(update(WorkbenchRecord).where(WorkbenchRecord.id == row.id,
        WorkbenchRecord.version == row.version).values(version=row.version).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        raise ValueError("会话已变化，请刷新")
    db.refresh(row)
    return row, json_loads(row.payload_json, {})


def require_worker(db, row, value, ctx, request_version, statuses):
    if value.get("request_version") != request_version or value.get("status") not in statuses:
        raise JobCancelledError("整理请求已更新或取消")
    job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
    if job and job.id == ctx.job_id and job.status in {"CANCEL_REQUESTED", "CANCELLED"}:
        raise JobCancelledError("整理任务已请求取消")
    if (not job or job.id != ctx.job_id or job.status != "RUNNING"
            or (getattr(ctx, "lease_owner", None) and job.lease_owner != ctx.lease_owner)):
        raise JobLeaseLostError("整理任务不再持有执行租约")
    expired = db.scalar(select(Job.id).where(Job.id == job.id, Job.lease_expires_at.is_not(None),
                                            Job.lease_expires_at <= utcnow()))
    if expired:
        raise JobLeaseLostError("整理任务租约已过期")
    if db.scalar(select(Job.id).where(Job.id == job.id, Job.deadline_at.is_not(None), Job.deadline_at <= utcnow())):
        raise JobTimeoutError("整理任务本次运行时间已用完")
    attempt = getattr(ctx, "assistant_attempt", None)
    if attempt is not None and (attempt != job.attempt or value.get("worker_token") != ctx.assistant_token):
        raise JobLeaseLostError("整理任务已由新的执行轮次接管")
    fenced = db.execute(update(Job).where(Job.id == job.id, Job.status == "RUNNING",
        Job.attempt == job.attempt, Job.lease_owner == job.lease_owner).values(attempt=job.attempt))
    if fenced.rowcount != 1:
        raise JobLeaseLostError("任务执行状态已变化")
    require_admin_actor(db, value.get("requested_by") or row.owner_id)
    return job


def claim_worker(db, row, value, ctx, request_version, statuses):
    job = require_worker(db, row, value, ctx, request_version, statuses)
    if value.get("worker_job_id") == job.id and value.get("worker_attempt") == job.attempt:
        raise JobLeaseLostError("同一执行轮次已经有工作者")
    ctx.assistant_attempt, ctx.assistant_token = job.attempt, new_id("AWORK")
    value.update(worker_job_id=job.id, worker_attempt=job.attempt, worker_token=ctx.assistant_token)
    row.payload_json = json_dumps(value)


def enqueue(db, row, value, kind, *, reviewer=None):
    """Caller commits once; dispatcher can never see a half-linked request."""
    from app.services.storage_capacity import require_storage_capacity
    require_storage_capacity()
    arguments = {"session_id": row.id, "request_version": value["request_version"]}
    if reviewer is not None:
        arguments["reviewer"] = reviewer
    job = Job(id=new_id("JOB"), kind=kind, status="QUEUED", input_json=json_dumps(arguments),
              idempotency_key=digest({**arguments, "record_version": row.version}),
              max_attempts=3 if kind == "assistant_publish" else 1, timeout_seconds=AI_JOB_TIMEOUT_SECONDS,
              available_at=utcnow(), resource_limits_json="{}")
    value["job_id"] = job.id
    value.update(worker_token=None, worker_job_id=None, worker_attempt=None)
    row.payload_json = json_dumps(value)
    db.add(job)
    db.flush()
    return job


def release_generation(db, value, *, revoke_approval=True):
    generation = value.get("building_generation_id")
    if generation:
        state = db.get(KnowledgeGraphState, "domain")
        if state and state.building_generation_id == generation:
            db.execute(update(KnowledgeGraphState).where(KnowledgeGraphState.id == "domain",
                KnowledgeGraphState.building_generation_id == generation).values(building_generation_id=None,
                    status="STALE" if state.active_generation_id else "NOT_BUILT"))
    value["building_generation_id"] = None
    if revoke_approval:
        value.update(approved_digest=None, approved_by=None, approved_at=None)


def cancel_session(db, row, value, *, pause=False):
    if value.get("status") == "PUBLISHED":
        raise ValueError("该方案已发布")
    job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
    if job:
        db.execute(update(Job).where(Job.id == job.id, Job.status == "QUEUED").values(
            status="CANCELLED", completed_at=utcnow()))
        db.execute(update(Job).where(Job.id == job.id, Job.status == "RUNNING").values(status="CANCEL_REQUESTED"))
    was_publication = value.get("status") in {"APPROVED", "BUILDING", "PUBLISH_FAILED"}
    release_generation(db, value)
    value.update(status="REVIEW" if was_publication else ("PAUSED" if pause else "CANCELLED"), error=None,
                 request_version=value["request_version"] + 1)
    # The concrete plan survives publication cancellation; its new digest needs
    # a fresh review. Reading checkpoints survive cancellation and consent edits.
    from app.services.assistant_plan import review_digest
    value["review_digest"] = review_digest(value) if was_publication else None
    row.payload_json = json_dumps(value)


def recover_abandoned_assistant_sessions(db):
    """Call after jobs' expired-lease updates, before that transaction commits.

    Only expired/finished workers may release their own generation latch. The
    exact approved revision survives a crash; changed/revoked approvals do not.
    """
    recovered = 0
    ids = list(db.scalars(select(WorkbenchRecord.id).where(WorkbenchRecord.kind == "assistant")))
    for session_id in ids:
        row = db.get(WorkbenchRecord, session_id)
        value = json_loads(row.payload_json, {})
        if value.get("status") not in {"READING", "APPROVED", "BUILDING", "PUBLISH_FAILED"}:
            continue
        job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
        if job and job.status in {"RUNNING", "CANCEL_REQUESTED"}:
            continue
        if job and job.status == "QUEUED" and value["status"] == "READING":
            continue
        row, value = locked_session(db, session_id)
        # Recheck after acquiring the fence: a dispatcher may already have moved on.
        if job:
            db.refresh(job)
            if job.status in {"RUNNING", "CANCEL_REQUESTED"}:
                continue
        if value.get("status") not in {"READING", "APPROVED", "BUILDING", "PUBLISH_FAILED"}:
            continue
        job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
        if job and job.status == "QUEUED" and value["status"] == "READING":
            continue
        publishing = value["status"] != "READING"
        from app.services.assistant_plan import review_digest
        if publishing and job and job.status == "QUEUED" and job.kind == "assistant_publish":
            arguments = json_loads(job.input_json, {})
            valid = bool(value.get("approved_at") and value.get("approved_by")
                and value.get("approved_digest") == review_digest(value)
                and arguments.get("session_id") == row.id
                and arguments.get("request_version") == value.get("request_version")
                and arguments.get("reviewer") == value.get("approved_by"))
            if valid:
                try:
                    require_admin_actor(db, value["approved_by"])
                except ValueError:
                    valid = False
            if valid:
                if value["status"] == "APPROVED" and not value.get("building_generation_id"):
                    continue
                release_generation(db, value, revoke_approval=False)
                value.update(status="APPROVED", worker_job_id=None, worker_attempt=None, worker_token=None,
                             error="已审批内容已恢复，继续构建发布；无需重复审批。")
                row.payload_json = json_dumps(value)
                recovered += 1
                continue
        if value["status"] == "PUBLISH_FAILED":
            # A terminal failure remains reviewable/retryable, without an
            # unbounded dispatcher retry loop or silently discarding approval.
            continue
        release_generation(db, value)
        if publishing and job and job.status == "QUEUED":
            job.status, job.completed_at = "CANCELLED", utcnow()
        value.update(status="REVIEW" if publishing else "FAILED", request_version=value["request_version"] + 1,
                     error="任务中断；阅读记录已保存，发布需重新核对确认。")
        value["review_digest"] = review_digest(value) if publishing else None
        row.payload_json = json_dumps(value)
        recovered += 1
    return recovered
