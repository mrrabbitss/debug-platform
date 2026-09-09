from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.utils import json_loads
from app.models import (
    KnowledgeCurationMessage,
    KnowledgeCurationRevision,
    KnowledgeCurationSession,
    KnowledgeCurationSourceFile,
)


def source_to_dict(source: KnowledgeCurationSourceFile) -> dict[str, Any]:
    return {
        "id": source.id,
        "source_ref": source.source_ref,
        "relative_path": source.relative_path,
        "extraction_method": source.extraction_method,
        "extraction_truncated": source.extraction_truncated,
        "page_count": source.page_count,
        "sha256": source.sha256,
        "size_bytes": source.size_bytes,
        "media_type": source.media_type,
        "text_encoding": source.text_encoding,
        "line_count": source.line_count,
        "source_role": source.source_role,
        "included": source.included,
        "skip_reason": source.skip_reason,
        "created_at": source.created_at,
    }


def message_to_dict(message: KnowledgeCurationMessage) -> dict[str, Any]:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "citations": json_loads(message.citations_json, []),
        "draft_version": message.draft_version,
        "model_profile_id": message.model_profile_id,
        "created_by": message.created_by,
        "created_at": message.created_at,
    }


def revision_to_dict(revision: KnowledgeCurationRevision) -> dict[str, Any]:
    return {
        "id": revision.id,
        "version": revision.version,
        "content_hash": revision.content_hash,
        "change_summary": revision.change_summary,
        "validation": json_loads(revision.validation_json, {}),
        "source_message_id": revision.source_message_id,
        "created_by": revision.created_by,
        "created_at": revision.created_at,
    }


def session_to_dict(
    db: Session,
    session: KnowledgeCurationSession,
    *,
    detail: bool,
    principal: dict | None = None,
) -> dict[str, Any]:
    source_count = db.scalar(
        select(func.count(KnowledgeCurationSourceFile.id)).where(
            KnowledgeCurationSourceFile.session_id == session.id
        )
    ) or 0
    result: dict[str, Any] = {
        "id": session.id,
        "status": session.status,
        "title_hint": session.title_hint,
        "category_id": session.category_id,
        "device_type": session.device_type,
        "device_model": session.device_model,
        "firmware_range": session.firmware_range,
        "module": session.module,
        "trust_level": session.trust_level,
        "confidentiality": session.confidentiality,
        "model_profile_id": session.model_profile_id,
        "model_snapshot": {key: value for key, value in json_loads(session.model_snapshot_json, {}).items()
            if key in {"profile_id", "profile_name", "provider", "mode", "model", "base_url", "prompt_version"}},
        "source_manifest": json_loads(session.source_manifest_json, {}),
        "source_count": int(source_count),
        "draft_title": session.draft_title,
        "draft_version": session.draft_version,
        "validation": json_loads(session.validation_json, {}),
        "open_questions": json_loads(session.open_questions_json, []),
        "knowledge_document_id": session.knowledge_document_id,
        "job_id": session.job_id,
        "error_message": session.error_message,
        "created_by": session.created_by,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "confirmed_at": session.confirmed_at,
    }
    foreign_reviewer = principal is not None and session.created_by != principal.get("id")
    if foreign_reviewer:
        result["model_profile_id"] = None
        result["model_snapshot"] = {}
    if not detail:
        return result
    result["draft_markdown"] = session.draft_markdown
    sources = list(db.scalars(
        select(KnowledgeCurationSourceFile)
        .where(KnowledgeCurationSourceFile.session_id == session.id)
        .order_by(KnowledgeCurationSourceFile.source_ref)
    ).all())
    messages = list(db.scalars(
        select(KnowledgeCurationMessage)
        .where(KnowledgeCurationMessage.session_id == session.id)
        .order_by(KnowledgeCurationMessage.created_at, KnowledgeCurationMessage.id)
    ).all())
    revisions = list(db.scalars(
        select(KnowledgeCurationRevision)
        .where(KnowledgeCurationRevision.session_id == session.id)
        .order_by(KnowledgeCurationRevision.version.desc())
    ).all())
    result["sources"] = [source_to_dict(source) for source in sources]
    result["messages"] = [message_to_dict(message) for message in messages]
    if foreign_reviewer:
        for message in result["messages"]:
            message["model_profile_id"] = None
    result["revisions"] = [revision_to_dict(revision) for revision in revisions]
    return result
