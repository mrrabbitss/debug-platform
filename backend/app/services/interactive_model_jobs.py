"""Durable wrappers for interactive Chat work.

The job input deliberately contains only stable identifiers, the user instruction
and a non-secret Chat profile fingerprint.  Prompts, candidate documents and
provider responses remain outside the durable job payload.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.timeouts import AI_JOB_TIMEOUT_SECONDS
from app.core.utils import json_loads
from app.services.jobs import JobCancelledError, JobLeaseLostError, JobTimeoutError, job_runner
from app.services.job_progress import report_progress
from app.services.knowledge_access import require_curation_access, require_knowledge_admin
from app.services.knowledge_contributions import require_contribution, require_version
from app.services.model_access import (
    ModelAccessError,
    chat_connection_test_snapshot,
    principal_for_model_user,
    require_model_profile,
    resolve_chat_connection_test_snapshot,
    resolve_chat_model_snapshot,
)
from app.services.model_profiles import profile_uses_proxy, test_profile_connection
from app.services.model_transport import safe_model_connection_error


def _job_principal(db: Session, owner_id: str) -> dict:
    try:
        return principal_for_model_user(db, owner_id)
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _model_from_snapshot(db: Session, snapshot: dict):
    try:
        return resolve_chat_model_snapshot(db, snapshot)
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def _connection_test_model_from_snapshot(db: Session, snapshot: dict):
    try:
        return resolve_chat_connection_test_snapshot(db, snapshot)
    except ModelAccessError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


def submit_chat_model_connection_test(db: Session, principal: dict, profile_id: str):
    """Authorize and persist a single probe of the caller's exact Chat profile."""
    profile = require_model_profile(db, principal, profile_id)
    model_snapshot = chat_connection_test_snapshot(db, principal, profile)
    return job_runner.submit(
        db,
        "test_chat_model_connection",
        chat_model_connection_test_job,
        input_data={
            "profile_id": profile.id,
            "owner_id": principal["id"],
            "model_snapshot": model_snapshot,
        },
        max_attempts=1,
        timeout_seconds=AI_JOB_TIMEOUT_SECONDS,
    )


def contribution_review_job(
    ctx,
    contribution_id: str,
    expected_version: int,
    instruction: str,
    owner_id: str,
    model_snapshot: dict,
    consent_model_egress: bool,
) -> dict[str, Any]:
    """Run a reviewer correction and atomically save its draft and Job result."""
    from app.services.knowledge_contribution_review import refine_contribution

    report_progress(ctx, 15, "正在准备审核依据", stage="准备审核依据",
                    stage_index=1, stage_count=4, waiting_for_model=False)
    with SessionLocal() as db:
        principal = _job_principal(db, owner_id)
        require_knowledge_admin(principal)
        row = require_contribution(db, contribution_id, principal)
        require_version(row, expected_version)
        _model_from_snapshot(db, model_snapshot)
        ctx.raise_if_cancelled()
        try:
            refined = asyncio.run(refine_contribution(
                db,
                row,
                principal,
                {
                    "expected_version": expected_version,
                    "instruction": instruction,
                    "consent_model_egress": consent_model_egress,
                },
                model_snapshot=model_snapshot,
                ctx=ctx,
            ))
        except (HTTPException, JobCancelledError, JobLeaseLostError, JobTimeoutError):
            raise
        except Exception:
            raise RuntimeError("AI review request failed; the pending candidate was preserved") from None
        # The row, revision/audit records and job completion marker share one
        # transaction.  A cancellation or a lost lease rolls every candidate
        # write back before it becomes observable.
        ctx.raise_if_cancelled()
        result = {"contribution_id": refined.id, "version": refined.version}
        ctx.complete_in_transaction(db, result, "AI review correction saved")
        db.commit()
    return result


def chat_model_connection_test_job(
    ctx,
    profile_id: str,
    owner_id: str,
    model_snapshot: dict,
) -> dict[str, Any]:
    """Probe one pinned Chat profile without persisting provider request data."""
    report_progress(ctx, 15, "正在核对模型配置", stage="核对模型配置",
                    stage_index=1, stage_count=4, waiting_for_model=False)
    with SessionLocal() as db:
        principal = _job_principal(db, owner_id)
        profile = _connection_test_model_from_snapshot(db, model_snapshot)
        if profile.id != profile_id:
            raise HTTPException(409, "Saved model selection does not match this connection test")
        # Preserve the synchronous endpoint's disabled-profile rule: its owner
        # or a manager may still verify a disabled draft configuration.
        require_model_profile(db, principal, profile_id)
        ctx.raise_if_cancelled()
        report_progress(ctx, 35, "正在测试模型响应", stage="测试模型响应",
                        stage_index=2, stage_count=4, waiting_for_model=True)
        try:
            result = asyncio.run(test_profile_connection(profile))
        except (JobCancelledError, JobLeaseLostError, JobTimeoutError):
            raise
        except Exception as exc:
            raise HTTPException(
                502,
                safe_model_connection_error(exc, proxy_configured=profile_uses_proxy(profile)),
            ) from None
        ctx.raise_if_cancelled()
        report_progress(ctx, 80, "正在核对返回结果", stage="核对返回",
                        stage_index=3, stage_count=4, waiting_for_model=False)
        # A slow probe must not report success for an account, visibility or
        # credential fingerprint that changed while the provider was running.
        db.expire_all()
        principal = _job_principal(db, owner_id)
        current = _connection_test_model_from_snapshot(db, model_snapshot)
        if current.id != profile_id:
            raise HTTPException(409, "Saved model selection does not match this connection test")
        require_model_profile(db, principal, profile_id)
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise RuntimeError("Chat model connection test did not receive the expected response")
        safe_result = {"profile_id": profile.id, "test": result}
        report_progress(ctx, 95, "正在保存测试结果", stage="保存测试结果",
                        stage_index=4, stage_count=4, waiting_for_model=False)
        ctx.complete_in_transaction(db, safe_result, "Chat model connection test completed")
        db.commit()
    return safe_result


def curation_refinement_job(
    ctx,
    session_id: str,
    expected_draft_version: int,
    instruction: str,
    owner_id: str,
    model_snapshot: dict,
) -> dict[str, Any]:
    """Run one pinned curation refinement and atomically publish its revision."""
    from app.services.knowledge_curation import refine_curation_session

    report_progress(ctx, 15, "正在准备审核依据", stage="准备审核依据",
                    stage_index=1, stage_count=4, waiting_for_model=False)
    with SessionLocal() as db:
        principal = _job_principal(db, owner_id)
        session = require_curation_access(db, session_id, principal, write=True)
        if session.created_by != owner_id:
            raise HTTPException(404, "Knowledge curation session not found")
        if session.draft_version != expected_draft_version:
            raise HTTPException(409, "Draft changed; refresh before sending another correction")
        saved_snapshot = json_loads(session.model_snapshot_json, {})
        if not isinstance(saved_snapshot, dict) or saved_snapshot.get("model_profile_fingerprint") != model_snapshot.get("model_profile_fingerprint"):
            raise HTTPException(409, "The saved curation model changed; start a fresh request")
        _model_from_snapshot(db, model_snapshot)
        ctx.raise_if_cancelled()
        try:
            refined = asyncio.run(refine_curation_session(
                db,
                session,
                instruction=instruction,
                expected_draft_version=expected_draft_version,
                actor=owner_id,
                model_snapshot=model_snapshot,
                ctx=ctx,
                commit=False,
                record_trace=False,
            ))
        except (HTTPException, JobCancelledError, JobLeaseLostError, JobTimeoutError):
            raise
        except Exception:
            raise RuntimeError("AI curation refinement failed; the existing draft was preserved") from None
        ctx.raise_if_cancelled()
        result = {"session_id": refined.id, "draft_version": refined.draft_version}
        ctx.complete_in_transaction(db, result, "AI curation refinement saved")
        db.commit()
    return result


job_runner.register(
    "test_chat_model_connection",
    chat_model_connection_test_job,
    ("profile_id", "owner_id", "model_snapshot"),
    cancellable=True,
    max_attempts=1,
    timeout_seconds=AI_JOB_TIMEOUT_SECONDS,
)
