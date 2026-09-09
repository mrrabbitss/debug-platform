"""Durable server Chat knowledge assistant; model output only creates reviewable drafts."""
from app.core.db import SessionLocal
from app.core.utils import json_dumps
from app.models import Job
from app.services.assistant_state import locked_session, claim_worker, release_generation
from app.services.assistant_runtime import Runtime, RunPaused
from app.services.assistant_controller import plan
from app.services.jobs import JobCancelledError, JobLeaseLostError


def finish_interrupted(runtime, error):
    """An old worker must never replace a newer correction, retry or cancellation."""
    with SessionLocal() as db:
        row, value = locked_session(db, runtime.session_id)
        job = db.get(Job, value.get("job_id")) if value.get("job_id") else None
        attempt = getattr(runtime.ctx, "assistant_attempt", None)
        worker_matches = (job and job.attempt == attempt and value.get("worker_token") ==
                          getattr(runtime.ctx, "assistant_token", None)) if attempt is not None else (
                              not value.get("worker_token") or (job and value.get("worker_attempt") != job.attempt))
        if (value.get("request_version") != runtime.version or value.get("status") != "READING"
                or not job or job.id != runtime.ctx.job_id
                or job.lease_owner != getattr(runtime.ctx, "lease_owner", None)
                or not worker_matches):
            return
        if job.status not in {"RUNNING", "CANCEL_REQUESTED", "CANCELLED"} or isinstance(error, JobLeaseLostError):
            return
        release_generation(db, value)
        if isinstance(error, RunPaused):
            value.update(status="PAUSED", error=str(error))
            result = {"session_id": runtime.session_id, "paused": True}
            runtime.ctx.complete_in_transaction(db, result, "进度已保存，可继续阅读")
        elif isinstance(error, JobCancelledError):
            value.update(status="CANCELLED", error=None)
        else:
            message = str(error) if type(error) is ValueError else "整理未完成；阅读记录已保存。请检查模型配置或纠偏后重试。"
            value.update(status="FAILED", error=message)
        row.payload_json = json_dumps(value)
        db.commit()


def plan_job(ctx, session_id: str, request_version: int):
    runtime = Runtime(ctx, session_id, request_version)
    try:
        with SessionLocal() as db:
            row, value = locked_session(db, session_id)
            claim_worker(db, row, value, ctx, request_version, {"READING"})
            if value.get("schema_version") != 2:
                raise ValueError("旧整理会话需要重新创建，以生成可校验的具体方案")
            db.commit()
            paths = [item["path"] for item in value.get("files", [])] + value.get("selected_paths", [])
        runtime.reading_scope(paths)
        for path in dict.fromkeys(paths):
            runtime.read(path)
        return plan(runtime)
    except RunPaused as error:
        finish_interrupted(runtime, error)
        return {"session_id": session_id, "paused": True}
    except (JobCancelledError, JobLeaseLostError) as error:
        finish_interrupted(runtime, error)
        raise
    except Exception as error:
        finish_interrupted(runtime, error)
        # Jobs and audit logs contain no uploaded text, model output or model URLs.
        message = str(error) if type(error) is ValueError else "知识助手读取或方案校验失败；进度已保存，未发布变更"
        raise ValueError(message) from None
