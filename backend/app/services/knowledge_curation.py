from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from time import perf_counter
from typing import Any

from fastapi import UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import (
    json_dumps,
    json_loads,
    mask_sensitive,
    new_id,
    sha256_file,
    utcnow,
)
from app.models import (
    KnowledgeCurationMessage,
    KnowledgeCurationRevision,
    KnowledgeCurationSession,
    KnowledgeCurationSourceFile,
    KnowledgeDocument,
    ModelProfile,
)
from app.services.agent_trace import record_agent_run, update_resource_approval
from app.services.curation_refinement_state import (
    prepare_refinement_session,
    report_refinement_stage,
    revalidate_refinement_snapshot,
)
from app.services.jobs import JobCancelledError, JobContext
from app.services.job_progress import report_progress
from app.services.knowledge_governance import create_document_revision
from app.services.knowledge_curation_common import CurationConflict, CurationError
from app.services.knowledge_curation_prompts import (
    _initial_system_prompt, _initial_user_prompt, _refinement_system_prompt)
from app.services.knowledge_curation_evidence import (
    build_evidence_bundle as _build_evidence_bundle,
    source_refs as _source_refs,
    validate_curation_markdown,
)
from app.services.knowledge_curation_serialization import (
    message_to_dict as message_to_dict,
    revision_to_dict as revision_to_dict,
    session_to_dict as session_to_dict,
    source_to_dict as source_to_dict,
)
from app.services.knowledge_curation_uploads import (
    normalize_relative_path as normalize_relative_path,
    persist_curation_uploads as _persist_curation_uploads,
)
from app.services.knowledge_taxonomy import get_default_category_id, set_document_category
from app.services.llm import LLMError, get_llm_provider
from app.services.model_access import (ModelAccessError, chat_model_snapshot, principal_for_model_user,
    resolve_chat_model_snapshot, resolve_user_chat_profile)
from app.services.storage import storage
from app.services.text_files import read_text_range


PROMPT_VERSION = "knowledge-case-curation-v1"
logger = logging.getLogger(__name__)


def build_evidence_bundle(
    db: Session,
    session: KnowledgeCurationSession,
) -> tuple[str, dict[str, Any]]:
    """Build evidence with the runtime storage dependency used by this service.

    Keeping this small boundary also lets isolated tests and deployments replace
    the storage service without mutating the evidence module's process global.
    """
    return _build_evidence_bundle(db, session, storage_service=storage)


async def persist_curation_uploads(
    session_id: str,
    uploads: list[UploadFile],
    relative_paths: list[str],
) -> list[dict[str, Any]]:
    """Persist uploads through the storage dependency selected by this service."""
    return await _persist_curation_uploads(
        session_id,
        uploads,
        relative_paths,
        storage_service=storage,
    )


class GeneratedCaseDraft(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    markdown: str = Field(min_length=20)
    change_summary: str = Field(default="模型生成案例初稿", max_length=512)
    open_questions: list[str] = Field(default_factory=list, max_length=50)
    citations: list[str] = Field(default_factory=list, max_length=500)
    device_type: str | None = Field(default=None, max_length=32)
    device_model: str | None = Field(default=None, max_length=128)
    firmware_range: str | None = Field(default=None, max_length=255)
    module: str | None = Field(default=None, max_length=64)


class RefinedCaseDraft(BaseModel):
    assistant_message: str = Field(min_length=1, max_length=20_000)
    revised_markdown: str = Field(min_length=20)
    change_summary: str = Field(default="根据对话修订案例", max_length=512)
    open_questions: list[str] = Field(default_factory=list, max_length=50)
    citations: list[str] = Field(default_factory=list, max_length=500)


def resolve_curation_model(
    db: Session,
    model_profile_id: str | None,
    principal: dict | None = None,
) -> tuple[ModelProfile, dict[str, Any]]:
    if principal is None:
        raise CurationError("An authenticated model owner is required")
    try:
        profile = resolve_user_chat_profile(db, principal, model_profile_id)
    except ModelAccessError as exc:
        raise CurationError(str(exc)) from exc
    if not profile or profile.task_type != "chat" or not profile.enabled:
        raise CurationError("Select an enabled diagnostic chat model")
    if profile.provider == "mock":
        raise CurationError(
            "The built-in mock model cannot extract a case. Configure and select an API chat model."
        )
    try:
        provider = get_llm_provider(profile)
    except LLMError as exc:
        raise CurationError(str(exc)) from exc
    snapshot = {
        "profile_id": profile.id,
        "profile_name": profile.name,
        "provider": profile.provider,
        "mode": profile.mode,
        "model": provider.model_name,
        "base_url": profile.base_url,
        "proxy_url_configured": bool(profile.proxy_url_ciphertext),
        "certificate_revocation_check_skipped": bool(profile.proxy_url_ciphertext),
        "prompt_version": PROMPT_VERSION,
        **chat_model_snapshot(db, principal, profile),
    }
    return profile, snapshot


def resolve_session_model(db, session):
    """Refresh owner privileges and preserve the queued request's exact API choice."""
    try:
        principal = principal_for_model_user(db, session.created_by)
        if principal["role"] not in {"ADMIN", "EXPERT", "ENGINEER"}:
            raise CurationError("The extraction owner can no longer contribute knowledge")
        snapshot = json_loads(session.model_snapshot_json, {})
        if snapshot.get("selected_chat_profile_id"):
            profile = resolve_chat_model_snapshot(db, snapshot)
        else:
            profile, snapshot = resolve_curation_model(db, session.model_profile_id, principal)
        if profile.mode == "api" and json_loads(session.source_manifest_json, {}).get("model_egress_consent") is not True:
            raise CurationError("Model API egress consent is disabled for this session")
        return profile, snapshot
    except ModelAccessError as exc:
        raise CurationError(str(exc)) from exc


def _normalize_markdown(title: str, markdown: str) -> str:
    value = markdown.strip()
    if not re.search(r"(?m)^#\s+", value):
        value = f"# {title.strip()}\n\n{value}"
    if len(value) > get_settings().curation_max_draft_chars:
        raise CurationError(
            "Generated Markdown exceeds the configured draft size limit"
        )
    return value + "\n"


def _create_curation_revision(
    db: Session,
    session: KnowledgeCurationSession,
    *,
    markdown: str,
    version: int,
    change_summary: str,
    validation: dict[str, Any],
    created_by: str | None,
    source_message_id: str | None = None,
) -> KnowledgeCurationRevision:
    revision = KnowledgeCurationRevision(
        id=new_id("KCURV"),
        session_id=session.id,
        version=version,
        markdown=markdown,
        content_hash=hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        change_summary=change_summary[:512],
        validation_json=json_dumps(validation),
        source_message_id=source_message_id,
        created_by=created_by,
    )
    db.add(revision)
    return revision


def curate_knowledge_folder_job(ctx: JobContext, session_id: str) -> dict[str, Any]:
    trace_started = perf_counter()
    extraction_started = trace_started
    extraction_duration_ms = 0
    profile: ModelProfile | None = None
    provider: Any = None
    snapshot: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    try:
        with SessionLocal() as db:
            session = db.get(KnowledgeCurationSession, session_id)
            if not session:
                raise CurationError("Knowledge curation session not found")
            if session.draft_version > 0:
                raise CurationConflict("This session already has a generated draft")
            session.status = "EXTRACTING"
            session.error_message = None
            db.commit()
            profile, snapshot = resolve_session_model(db, session)
            session.model_profile_id = profile.id
            session.model_snapshot_json = json_dumps(snapshot)
            db.commit()
            from app.services.job_progress import report_progress
            report_progress(ctx, 10, "正在检查上传文件并提取案例证据", stage="提取来源证据", stage_index=1, stage_count=3)
            evidence, manifest = build_evidence_bundle(db, session)
            extraction_duration_ms = int((perf_counter() - extraction_started) * 1000)
            user_prompt = _initial_user_prompt(session, evidence)
            ctx.raise_if_cancelled()
            provider = get_llm_provider(profile)

        report_progress(ctx, 40, "正在根据来源证据生成案例知识初稿", stage="生成提炼草稿", stage_index=2, stage_count=3)
        generated_data = asyncio.run(provider.generate_json(
            _initial_system_prompt(),
            user_prompt,
            schema_name="knowledge_case_curation",
            purpose="knowledge_case_curation",
        ))
        try:
            generated = GeneratedCaseDraft.model_validate(generated_data)
        except ValidationError as exc:
            raise CurationError("Model returned an invalid case draft structure") from exc
        markdown = _normalize_markdown(generated.title, generated.markdown)

        report_progress(ctx, 90, "正在核对来源引用并保存初稿，随后进入人工审核", stage="校验与保存草稿", stage_index=3, stage_count=3)
        with SessionLocal() as db:
            session = db.get(KnowledgeCurationSession, session_id)
            if not session:
                raise CurationError("Knowledge curation session was deleted")
            source_refs = _source_refs(db, session.id)
            validation = validate_curation_markdown(markdown, source_refs)
            version = 1
            published = db.execute(
                update(KnowledgeCurationSession)
                .where(
                    KnowledgeCurationSession.id == session.id,
                    KnowledgeCurationSession.draft_version == 0,
                    KnowledgeCurationSession.status == "EXTRACTING",
                )
                .values(
                    status="REVIEWING",
                    draft_title=generated.title,
                    draft_markdown=markdown,
                    draft_version=version,
                    validation_json=json_dumps(validation),
                    open_questions_json=json_dumps(generated.open_questions),
                    source_manifest_json=json_dumps(manifest),
                    device_type=generated.device_type or session.device_type,
                    device_model=generated.device_model or session.device_model,
                    firmware_range=generated.firmware_range or session.firmware_range,
                    module=generated.module or session.module,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )
            if published.rowcount != 1:
                db.rollback()
                raise CurationConflict("Curation draft changed while extraction was running")
            db.expire_all()
            session = db.get(KnowledgeCurationSession, session.id)
            assistant_message = KnowledgeCurationMessage(
                id=new_id("KCURM"),
                session_id=session.id,
                role="assistant",
                content=(
                    "已根据文件夹证据生成案例初稿。请逐项核对来源引用和待确认问题；"
                    "当前内容尚未进入知识库。"
                ),
                citations_json=json_dumps(validation["cited_source_refs"]),
                draft_version=version,
                model_profile_id=profile.id,
                created_by="curation-model",
            )
            db.add(assistant_message)
            _create_curation_revision(
                db,
                session,
                markdown=markdown,
                version=version,
                change_summary=generated.change_summary,
                validation=validation,
                created_by="curation-model",
                source_message_id=assistant_message.id,
            )
            result = {
                "session_id": session.id,
                "draft_version": version,
                "confirmable": validation["confirmable"],
                "source_refs": validation["cited_source_refs"],
            }
            ctx.complete_in_transaction(
                db,
                result,
                message="Knowledge case draft generated",
            )
            db.commit()
            trace_events = [
                {
                    "stage": "source_extraction",
                    "tool_name": "curation_document_extractor",
                    "status": "COMPLETED",
                    "duration_ms": extraction_duration_ms,
                    "candidate_count": len(manifest.get("selected_source_refs", [])),
                    "evidence_ids": validation["cited_source_refs"],
                },
                {
                    "stage": "model_generate",
                    "tool_name": "openai_compatible_chat",
                    "status": "COMPLETED",
                    "duration_ms": int(getattr(provider, "last_duration_ms", 0) or 0),
                    "input_tokens": int(
                        (getattr(provider, "last_usage", {}) or {}).get("prompt_tokens") or 0
                    ),
                    "output_tokens": int(
                        (getattr(provider, "last_usage", {}) or {}).get("completion_tokens") or 0
                    ),
                    "model_profile_id": profile.id,
                    "provider": profile.provider,
                    "model": profile.model_name,
                },
                {
                    "stage": "evidence_validate",
                    "tool_name": "curation_citation_gate",
                    "status": "COMPLETED" if validation["confirmable"] else "FAILED",
                    "duration_ms": 0,
                    "candidate_count": validation["line_citation_count"],
                    "evidence_ids": validation["cited_source_refs"],
                    "reason": "human review required",
                },
            ]
            try:
                record_agent_run(
                    db,
                    resource_type="knowledge_curation",
                    resource_id=session.id,
                    operation="knowledge_curation",
                    execution_mode="model_assisted",
                    input_summary={
                        "session_id": session.id,
                        "evidence_sha256": manifest.get("evidence_sha256"),
                        "source_refs": manifest.get("selected_source_refs", []),
                    },
                    output_summary={
                        "draft_version": version,
                        "markdown": markdown,
                        "confirmable": validation["confirmable"],
                    },
                    events=trace_events,
                    evidence_ids=validation["cited_source_refs"],
                    stop_reason="HUMAN_REVIEW_REQUIRED",
                    approval_status="PENDING_HUMAN_REVIEW",
                    duration_ms=int((perf_counter() - trace_started) * 1000),
                    model_profile_id=profile.id,
                    model_name=profile.model_name,
                    model_config=snapshot,
                    prompt_version=PROMPT_VERSION,
                    usage=getattr(provider, "last_usage", {}) or {},
                    created_by=session.created_by,
                    budget_ms=get_settings().llm_timeout_seconds * 1000,
                )
            except Exception:  # noqa: BLE001 - observability must not invalidate a published draft
                logger.exception("Unable to persist curation agent trace for %s", session.id)
            return result
    except Exception as exc:
        with SessionLocal() as db:
            session = db.get(KnowledgeCurationSession, session_id)
            if session and session.status not in {"REVIEWING", "CONFIRMED"}:
                session.status = (
                    "CANCELLED" if isinstance(exc, JobCancelledError) else "FAILED"
                )
                session.error_message = str(exc)[:4000]
                db.commit()
            try:
                failure_status = "CANCELLED" if isinstance(exc, JobCancelledError) else "FAILED"
                record_agent_run(
                    db,
                    resource_type="knowledge_curation",
                    resource_id=session_id,
                    operation="knowledge_curation",
                    execution_mode="model_assisted",
                    input_summary={
                        "session_id": session_id,
                        "evidence_sha256": manifest.get("evidence_sha256"),
                    },
                    output_summary={"error_type": type(exc).__name__},
                    events=[{
                        "stage": "knowledge_curation",
                        "tool_name": "curation_pipeline",
                        "status": failure_status,
                        "duration_ms": int((perf_counter() - trace_started) * 1000),
                        "stop_reason": failure_status,
                    }],
                    evidence_ids=list(manifest.get("selected_source_refs", [])),
                    stop_reason=failure_status,
                    approval_status="NOT_APPROVED",
                    duration_ms=int((perf_counter() - trace_started) * 1000),
                    model_profile_id=profile.id if profile else None,
                    model_name=profile.model_name if profile else None,
                    model_config=snapshot,
                    prompt_version=PROMPT_VERSION,
                    usage=getattr(provider, "last_usage", {}) if provider else {},
                    created_by=session.created_by if session else None,
                    status=failure_status,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Unable to persist failed curation trace for %s", session_id)
        raise


def _evidence_for_session(session: KnowledgeCurationSession) -> str:
    manifest = json_loads(session.source_manifest_json, {})
    storage_key = manifest.get("evidence_storage_key")
    if not storage_key:
        raise CurationError("Model evidence bundle is missing; retry initial extraction")
    evidence_path = storage.resolve_path(storage_key)
    if not evidence_path.is_file():
        raise CurationError("Model evidence bundle file is missing")
    evidence = evidence_path.read_text(encoding="utf-8")
    expected_hash = str(manifest.get("evidence_sha256") or "")
    actual_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
    if not expected_hash or expected_hash != actual_hash:
        raise CurationError("Model evidence bundle failed its integrity check")
    return evidence


def _conversation_history(db: Session, session_id: str) -> str:
    messages = list(db.scalars(
        select(KnowledgeCurationMessage)
        .where(KnowledgeCurationMessage.session_id == session_id)
        .order_by(KnowledgeCurationMessage.created_at.desc())
        .limit(16)
    ).all())
    messages.reverse()
    rendered: list[str] = []
    total = 0
    for message in messages:
        entry = f"{message.role}: {message.content}"
        if total + len(entry) > 20_000:
            continue
        rendered.append(entry)
        total += len(entry)
    return "\n".join(rendered)


async def refine_curation_session(
    db: Session,
    session: KnowledgeCurationSession,
    *,
    instruction: str,
    expected_draft_version: int,
    actor: str | None,
    model_snapshot: dict[str, Any] | None = None,
    ctx: JobContext | None = None,
    commit: bool = True,
    record_trace: bool = True,
) -> KnowledgeCurationSession:
    trace_started = perf_counter()
    profile, snapshot = prepare_refinement_session(
        db, session, actor, expected_draft_version, model_snapshot,
        resolve_session=resolve_session_model, resolve_model=resolve_chat_model_snapshot,
    )
    evidence = _evidence_for_session(session)
    history = _conversation_history(db, session.id)
    user_prompt = f"""当前草稿版本：v{session.draft_version}

当前完整 Markdown：
{mask_sensitive(session.draft_markdown)}

此前对话：
{mask_sensitive(history) or '无'}

工程师本轮说明：
{mask_sensitive(instruction)}

可引用的脱敏来源证据：
<SOURCE_EVIDENCE>
{evidence}
</SOURCE_EVIDENCE>
"""
    provider = get_llm_provider(profile)
    session_id = session.id
    profile_id = profile.id
    draft_title = session.draft_title or "故障案例"
    report_refinement_stage(ctx, 2, reporter=report_progress)
    db.rollback()
    refined_data = await provider.generate_json(
        _refinement_system_prompt(),
        user_prompt,
        schema_name="knowledge_case_refinement",
        purpose="knowledge_case_refinement",
    )
    try:
        refined = RefinedCaseDraft.model_validate(refined_data)
    except ValidationError as exc:
        raise CurationError("Model returned an invalid refinement structure") from exc
    markdown = _normalize_markdown(draft_title, refined.revised_markdown)
    report_refinement_stage(ctx, 3, reporter=report_progress)
    db.expire_all()
    session = db.get(KnowledgeCurationSession, session_id)
    if not session:
        raise CurationError("Knowledge curation session was deleted")
    if ctx and model_snapshot is not None:
        profile = revalidate_refinement_snapshot(
            db, session, actor, model_snapshot,
            resolve_model=resolve_chat_model_snapshot, resolve_actor=principal_for_model_user,
        )
    source_refs = _source_refs(db, session.id)
    validation = validate_curation_markdown(markdown, source_refs)
    new_version = expected_draft_version + 1
    user_message = KnowledgeCurationMessage(
        id=new_id("KCURM"),
        session_id=session.id,
        role="user",
        content=instruction,
        citations_json="[]",
        draft_version=expected_draft_version,
        created_by=actor,
    )
    assistant_message = KnowledgeCurationMessage(
        id=new_id("KCURM"),
        session_id=session.id,
        role="assistant",
        content=refined.assistant_message,
        citations_json=json_dumps(validation["cited_source_refs"]),
        draft_version=new_version,
        model_profile_id=profile_id,
        created_by="curation-model",
    )
    # Report before acquiring the persistence write lock for the draft and Job.
    report_refinement_stage(ctx, 4, reporter=report_progress)
    changed = db.execute(
        update(KnowledgeCurationSession)
        .where(
            KnowledgeCurationSession.id == session.id,
            KnowledgeCurationSession.status == "REVIEWING",
            KnowledgeCurationSession.draft_version == expected_draft_version,
        )
        .values(
            draft_markdown=markdown,
            draft_version=new_version,
            validation_json=json_dumps(validation),
            open_questions_json=json_dumps(refined.open_questions),
            model_profile_id=profile_id,
            model_snapshot_json=json_dumps(snapshot),
            error_message=None,
            updated_at=utcnow(),
        )
    )
    if changed.rowcount != 1:
        db.rollback()
        raise CurationConflict("Draft changed while the model was responding; refresh and retry")
    db.add_all([user_message, assistant_message])
    db.expire_all()
    session = db.get(KnowledgeCurationSession, session.id)
    _create_curation_revision(
        db,
        session,
        markdown=markdown,
        version=new_version,
        change_summary=refined.change_summary,
        validation=validation,
        created_by=actor or "curation-model",
        source_message_id=assistant_message.id,
    )
    if not commit:
        return session
    db.commit()
    db.refresh(session)
    if not record_trace:
        return session
    try:
        record_agent_run(
            db,
            resource_type="knowledge_curation",
            resource_id=session.id,
            operation="knowledge_curation_refinement",
            execution_mode="model_assisted",
            input_summary={
                "session_id": session.id,
                "instruction": instruction,
                "expected_draft_version": expected_draft_version,
            },
            output_summary={
                "draft_version": new_version,
                "markdown": markdown,
                "confirmable": validation["confirmable"],
            },
            events=[
                {
                    "stage": "model_refine",
                    "tool_name": "openai_compatible_chat",
                    "status": "COMPLETED",
                    "duration_ms": int(getattr(provider, "last_duration_ms", 0) or 0),
                    "input_tokens": int(
                        (getattr(provider, "last_usage", {}) or {}).get("prompt_tokens") or 0
                    ),
                    "output_tokens": int(
                        (getattr(provider, "last_usage", {}) or {}).get("completion_tokens") or 0
                    ),
                    "model_profile_id": profile.id,
                    "provider": profile.provider,
                    "model": profile.model_name,
                },
                {
                    "stage": "evidence_validate",
                    "tool_name": "curation_citation_gate",
                    "status": "COMPLETED" if validation["confirmable"] else "FAILED",
                    "duration_ms": 0,
                    "candidate_count": validation["line_citation_count"],
                    "evidence_ids": validation["cited_source_refs"],
                },
            ],
            evidence_ids=validation["cited_source_refs"],
            stop_reason="HUMAN_REVIEW_REQUIRED",
            approval_status="PENDING_HUMAN_REVIEW",
            duration_ms=int((perf_counter() - trace_started) * 1000),
            model_profile_id=profile.id,
            model_name=profile.model_name,
            model_config=snapshot,
            prompt_version=PROMPT_VERSION,
            usage=getattr(provider, "last_usage", {}) or {},
            created_by=actor,
            budget_ms=get_settings().llm_timeout_seconds * 1000,
        )
        db.refresh(session)
    except Exception:  # noqa: BLE001
        logger.exception("Unable to persist curation refinement trace for %s", session.id)
    return session


def save_manual_curation_draft(
    db: Session,
    session: KnowledgeCurationSession,
    *,
    markdown: str,
    title: str | None,
    expected_draft_version: int,
    change_summary: str,
    actor: str | None,
) -> KnowledgeCurationSession:
    if actor != session.created_by:
        raise CurationError("Only the session owner may edit its private extraction")
    if session.status != "REVIEWING":
        raise CurationConflict("Only a reviewing session can be edited")
    if session.draft_version != expected_draft_version:
        raise CurationConflict("Draft changed; refresh before saving")
    normalized = _normalize_markdown(title or session.draft_title or "故障案例", markdown)
    validation = validate_curation_markdown(normalized, _source_refs(db, session.id))
    new_version = expected_draft_version + 1
    new_title = (title or session.draft_title).strip()[:512]
    changed = db.execute(
        update(KnowledgeCurationSession)
        .where(
            KnowledgeCurationSession.id == session.id,
            KnowledgeCurationSession.status == "REVIEWING",
            KnowledgeCurationSession.draft_version == expected_draft_version,
        )
        .values(
            draft_title=new_title,
            draft_markdown=normalized,
            draft_version=new_version,
            validation_json=json_dumps(validation),
            updated_at=utcnow(),
        )
    )
    if changed.rowcount != 1:
        db.rollback()
        raise CurationConflict("Draft changed while it was being saved; refresh and retry")
    db.expire_all()
    session = db.get(KnowledgeCurationSession, session.id)
    message = KnowledgeCurationMessage(
        id=new_id("KCURM"),
        session_id=session.id,
        role="system",
        content=f"人工保存：{change_summary or '手工修订案例草稿'}",
        citations_json=json_dumps(validation["cited_source_refs"]),
        draft_version=new_version,
        created_by=actor,
    )
    db.add(message)
    _create_curation_revision(
        db,
        session,
        markdown=normalized,
        version=new_version,
        change_summary=change_summary or "Manual curation edit",
        validation=validation,
        created_by=actor,
        source_message_id=message.id,
    )
    db.commit()
    db.refresh(session)
    return session


def confirm_curation_session(
    db: Session,
    session: KnowledgeCurationSession,
    *,
    expected_draft_version: int,
    actor: str | None,
) -> KnowledgeDocument:
    if actor != session.created_by:
        raise CurationError("Only the session owner may confirm its private extraction")
    if session.knowledge_document_id:
        existing = db.get(KnowledgeDocument, session.knowledge_document_id)
        if existing:
            return existing
    if session.status != "REVIEWING":
        raise CurationConflict("Only a reviewing session can be confirmed")
    if session.draft_version != expected_draft_version:
        raise CurationConflict("Draft changed; refresh before confirmation")
    validation = validate_curation_markdown(
        session.draft_markdown,
        _source_refs(db, session.id),
    )
    if not validation["confirmable"]:
        raise CurationError(
            "Draft cannot be confirmed: " + "; ".join(validation["warnings"])
        )
    claimed = db.execute(
        update(KnowledgeCurationSession)
        .where(
            KnowledgeCurationSession.id == session.id,
            KnowledgeCurationSession.status == "REVIEWING",
            KnowledgeCurationSession.draft_version == expected_draft_version,
            KnowledgeCurationSession.knowledge_document_id.is_(None),
        )
        .values(status="CONFIRMING", updated_at=utcnow())
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise CurationConflict("Curation session changed before confirmation")
    try:
        db.expire_all()
        session = db.get(KnowledgeCurationSession, session.id)
        metadata = {
            "content_kind": "KNOWLEDGE",
            "curation_session_id": session.id,
            "curation_draft_version": session.draft_version,
            "source_manifest": json_loads(session.source_manifest_json, {}),
            "source_refs": validation["cited_source_refs"],
            "human_confirmed": True,
            "human_confirmed_at": utcnow().isoformat(),
            "prompt_version": PROMPT_VERSION,
        }
        document = KnowledgeDocument(
            id=new_id("DOC"),
            title=session.draft_title or "提炼故障案例",
            source_type="fault_case",
            device_type=session.device_type,
            device_model=session.device_model,
            firmware_range=session.firmware_range,
            module=session.module,
            trust_level=session.trust_level,
            confidentiality=session.confidentiality,
            content=session.draft_markdown,
            metadata_json=json_dumps(metadata),
            active=False,
            review_status="DRAFT",
        )
        db.add(document)
        db.flush()
        from app.services.knowledge_access import bind_owner
        bind_owner(db, document.id, session.created_by)
        set_document_category(
            db,
            document.id,
            session.category_id or get_default_category_id(db, "fault_case"),
        )
        create_document_revision(
            db,
            document,
            created_by=actor,
            change_summary=f"Confirmed from curation session {session.id}",
        )
        session.status = "CONFIRMED"
        session.knowledge_document_id = document.id
        session.validation_json = json_dumps(validation)
        session.confirmed_at = utcnow()
        session.error_message = None
        db.add(KnowledgeCurationMessage(
            id=new_id("KCURM"),
            session_id=session.id,
            role="system",
            content=(
                f"人工确认 v{session.draft_version}，已创建知识草稿 {document.id}。"
                "仍需通过现有知识审核后才会参与检索。"
            ),
            citations_json=json_dumps(validation["cited_source_refs"]),
            draft_version=session.draft_version,
            created_by=actor,
        ))
        # Draft confirmation and its owner contribution commit together. Indexing
        # waits for expert approval and the private-generation publication job.
        db.flush()
        from app.services.knowledge_contributions import create_contribution
        create_contribution(db, {"id": session.created_by, "role": "ENGINEER", "type": "user"},
                            {"source_curation_id": session.id}, existing_document=document)
        db.commit()
        db.refresh(document)
        update_resource_approval(
            db,
            resource_type="knowledge_curation",
            resource_id=session.id,
            approval_status="HUMAN_CONFIRMED_DRAFT",
            stop_reason="HUMAN_CONFIRMED_DRAFT",
        )
        db.refresh(document)
        return document
    except Exception as exc:
        db.rollback()
        current = db.get(KnowledgeCurationSession, session.id)
        if current and current.status == "CONFIRMED" and current.knowledge_document_id:
            document = db.get(KnowledgeDocument, current.knowledge_document_id)
            if document:
                return document
        if current and current.status == "CONFIRMING":
            current.status = "REVIEWING"
            current.error_message = str(exc)[:4000]
            db.commit()
        raise


def preview_curation_source(
    source: KnowledgeCurationSourceFile,
    *,
    start_line: int,
    line_count: int,
) -> dict[str, Any]:
    raw_path = storage.resolve_path(source.stored_path)
    if not raw_path.is_file():
        raise CurationError("Curation source file is missing from local storage")
    if not hmac.compare_digest(sha256_file(raw_path), source.sha256):
        raise CurationError("Curation source file failed its integrity check")
    path = storage.resolve_path(source.extracted_text_path or source.stored_path)
    if not path.is_file():
        raise CurationError("Curation source file is missing from local storage")
    if source.extracted_text_path:
        if not source.extracted_text_sha256:
            raise CurationError("Extracted document integrity metadata is missing")
        actual_hash = sha256_file(path)
        if not hmac.compare_digest(actual_hash, source.extracted_text_sha256):
            raise CurationError("Extracted document text failed its integrity check")
    result = read_text_range(path, start_line, line_count)
    if result is None:
        raise CurationError("Source file is not a supported text file")
    return {
        "source_ref": source.source_ref,
        "relative_path": source.relative_path,
        "start_line": start_line,
        "returned_lines": result.returned_lines,
        "has_more": result.has_more,
        "encoding": result.encoding,
        "extraction_method": source.extraction_method,
        "extraction_truncated": source.extraction_truncated,
        "page_count": source.page_count,
        "text": result.text,
    }
