from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_loads
from app.models import Job
from app.schemas import JobOut, ModelDownloadRequest
from app.services.audit import record_audit_event
from app.services.jobs import job_runner
from app.services.model_downloads import (
    MODEL_DOWNLOAD_JOB_KIND,
    SUPPORTED_MODEL_DOWNLOADS,
    download_model_job,
    model_download_catalog,
    proxy_url_hint,
    validate_download_proxy,
    validate_model_mirror,
)
from app.services.secrets import encrypt_secret


router = APIRouter(tags=["model-downloads"])
Db = Annotated[Session, Depends(get_db)]

job_runner.register(
    MODEL_DOWNLOAD_JOB_KIND,
    download_model_job,
    ("model_id", "mirror_base", "revision", "proxy_url_ciphertext"),
    cancellable=True,
    max_attempts=3,
    timeout_seconds=get_settings().model_download_timeout_seconds,
    resource_limits={"max_input_bytes": 16 * 1024},
)


def _job_payload(job: Job) -> dict:
    payload = JobOut.model_validate(job).model_dump(mode="json")
    safe_input = json_loads(job.input_json, {})
    payload.update({
        "model_id": safe_input.get("model_id"),
        "mirror_base": safe_input.get("mirror_base"),
        "revision": safe_input.get("revision"),
        "proxy_configured": bool(safe_input.get("proxy_url_ciphertext")),
    })
    return payload


@router.get("/system/model-downloads")
def list_model_downloads(db: Db) -> dict:
    payload = model_download_catalog()
    jobs = list(db.scalars(
        select(Job)
        .where(Job.kind == MODEL_DOWNLOAD_JOB_KIND)
        .order_by(Job.created_at.desc())
        .limit(20)
    ).all())
    payload["jobs"] = [_job_payload(job) for job in jobs]
    return payload


@router.post("/system/model-downloads", response_model=JobOut)
def start_model_download(
    payload: ModelDownloadRequest,
    request: Request,
    db: Db,
) -> Job:
    if payload.model_id not in SUPPORTED_MODEL_DOWNLOADS:
        raise HTTPException(400, "Unsupported model download")
    try:
        mirror_base = validate_model_mirror(payload.mirror_base)
        proxy_url = validate_download_proxy(payload.proxy_url)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    encrypted_proxy = encrypt_secret(proxy_url) if proxy_url else ""
    input_data = {
        "model_id": payload.model_id,
        "mirror_base": mirror_base,
        "revision": payload.revision,
        "proxy_url_ciphertext": encrypted_proxy,
    }
    identity = hashlib.sha256(
        f"{MODEL_DOWNLOAD_JOB_KIND}\0{payload.model_id}".encode("utf-8")
    ).hexdigest()
    settings = get_settings()
    try:
        job = job_runner.submit(
            db,
            MODEL_DOWNLOAD_JOB_KIND,
            download_model_job,
            payload.model_id,
            mirror_base,
            payload.revision,
            encrypted_proxy,
            input_data=input_data,
            idempotency_key=identity,
            max_attempts=3,
            timeout_seconds=settings.model_download_timeout_seconds,
            resource_limits={
                "max_input_bytes": 16 * 1024,
                "max_runtime_seconds": settings.model_download_timeout_seconds,
                "max_files": settings.model_download_max_files,
                "max_file_bytes": settings.model_download_max_file_bytes,
                "max_total_bytes": settings.model_download_max_total_bytes,
            },
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    principal = getattr(request.state, "principal", {})
    record_audit_event(
        "model.weights_download_queued",
        actor_id=principal.get("id"),
        actor_type=principal.get("type", "system"),
        resource_type="model_weights",
        resource_id=payload.model_id,
        details={
            "job_id": job.id,
            "model_id": payload.model_id,
            "mirror": mirror_base,
            "revision": payload.revision,
            "proxy_configured": bool(proxy_url),
            "proxy_hint": proxy_url_hint(proxy_url),
            "credentials_recorded": False,
        },
    )
    return job


@router.post("/system/model-downloads/{job_id}/cancel", response_model=JobOut)
def cancel_model_download(job_id: str, db: Db) -> Job:
    job = db.get(Job, job_id)
    if not job or job.kind != MODEL_DOWNLOAD_JOB_KIND:
        raise HTTPException(404, "Model download job not found")
    try:
        return job_runner.request_cancel(db, job_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
