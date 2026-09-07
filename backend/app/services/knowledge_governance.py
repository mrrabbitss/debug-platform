from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (
    AnalysisRun,
    Case,
    DiagnosisFeedback,
    KnowledgeDocument,
    KnowledgeDocumentCategory,
    KnowledgeGraphState,
    KnowledgeRevision,
)
from app.services.knowledge_taxonomy import (
    get_default_category_id,
    set_document_category,
)


REVIEW_STATES = {"DRAFT", "IN_REVIEW", "ACTIVE", "REJECTED", "ARCHIVED"}
MATERIAL_DOCUMENT_FIELDS = {
    "title",
    "source_type",
    "device_type",
    "device_model",
    "firmware_range",
    "module",
    "trust_level",
    "confidentiality",
    "content",
    "metadata",
    "category_id",
}


def actor_id(principal: dict[str, Any] | None) -> str | None:
    if not principal:
        return None
    value = principal.get("id") or principal.get("username")
    return str(value)[:128] if value else None


def current_category_id(db: Session, document_id: str) -> str | None:
    return db.scalar(
        select(KnowledgeDocumentCategory.category_id).where(
            KnowledgeDocumentCategory.document_id == document_id
        )
    )


def document_snapshot(db: Session, document: KnowledgeDocument) -> dict[str, Any]:
    return {
        "title": document.title,
        "source_type": document.source_type,
        "device_type": document.device_type,
        "device_model": document.device_model,
        "firmware_range": document.firmware_range,
        "module": document.module,
        "trust_level": document.trust_level,
        "confidentiality": document.confidentiality,
        "content": document.content,
        "metadata": json_loads(document.metadata_json, {}),
        "category_id": current_category_id(db, document.id),
        "active": document.active,
        "review_status": document.review_status,
    }


def create_document_revision(
    db: Session,
    document: KnowledgeDocument,
    *,
    created_by: str | None,
    change_summary: str,
) -> KnowledgeRevision:
    existing = db.scalar(
        select(KnowledgeRevision).where(
            KnowledgeRevision.document_id == document.id,
            KnowledgeRevision.version == document.version,
        )
    )
    if existing:
        return existing
    revision = KnowledgeRevision(
        id=new_id("KREV"),
        document_id=document.id,
        version=document.version,
        snapshot_json=json_dumps(document_snapshot(db, document)),
        content_hash=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
        change_summary=change_summary[:512],
        created_by=created_by,
    )
    db.add(revision)
    return revision


def advance_document_version(
    db: Session,
    document: KnowledgeDocument,
    *,
    created_by: str | None,
    change_summary: str,
) -> KnowledgeRevision:
    document.version += 1
    document.lock_version += 1
    document.review_status = "DRAFT"
    document.active = False
    document.reviewed_by = None
    document.reviewed_at = None
    document.review_comment = None
    document.published_at = None
    mark_domain_graph_stale(db, f"knowledge document {document.id} changed")
    try:
        db.flush()
    except StaleDataError as exc:
        db.rollback()
        raise ValueError(
            "Knowledge document changed since it was loaded; refresh and retry"
        ) from exc
    return create_document_revision(
        db,
        document,
        created_by=created_by,
        change_summary=change_summary,
    )


def require_lock_version(
    document: KnowledgeDocument,
    expected_lock_version: int | None,
) -> None:
    if (
        expected_lock_version is not None
        and expected_lock_version != document.lock_version
    ):
        raise ValueError(
            "Knowledge document changed since it was loaded; refresh and retry"
        )


def transition_document_review(
    db: Session,
    document: KnowledgeDocument,
    *,
    action: str,
    expected_lock_version: int | None,
    reviewer: str | None,
    comment: str | None,
) -> KnowledgeDocument:
    require_lock_version(document, expected_lock_version)
    action = action.upper()
    allowed_from = {
        "SUBMIT": {"DRAFT", "REJECTED"},
        "APPROVE": {"IN_REVIEW"},
        "REJECT": {"IN_REVIEW"},
        "ARCHIVE": {"DRAFT", "IN_REVIEW", "ACTIVE", "REJECTED"},
    }
    if action not in allowed_from:
        raise ValueError(f"Unsupported knowledge review action: {action}")
    if document.review_status not in allowed_from[action]:
        raise ValueError(
            f"Cannot {action.lower()} a document in {document.review_status} state"
        )

    now = utcnow()
    if action == "SUBMIT":
        document.review_status = "IN_REVIEW"
        document.active = False
        document.reviewed_by = None
        document.reviewed_at = None
        document.review_comment = comment
    elif action == "APPROVE":
        from app.services.knowledge_access import bind_owner
        from app.models import KnowledgeAccess
        bind_owner(db, document.id, reviewer)
        access = db.get(KnowledgeAccess, document.id)
        if not access.publisher_id:
            access.publisher_id = access.owner_id or reviewer
        document.review_status = "ACTIVE"
        document.active = True
        document.reviewed_by = reviewer
        document.reviewed_at = now
        document.review_comment = comment
        document.published_at = now
        mark_domain_graph_stale(db, f"knowledge document {document.id} published")
    elif action == "REJECT":
        document.review_status = "REJECTED"
        document.active = False
        document.reviewed_by = reviewer
        document.reviewed_at = now
        document.review_comment = comment
    else:
        document.review_status = "ARCHIVED"
        document.active = False
        document.reviewed_by = reviewer
        document.reviewed_at = now
        document.review_comment = comment
        mark_domain_graph_stale(db, f"knowledge document {document.id} archived")
    document.lock_version += 1
    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise ValueError(
            "Knowledge document changed since it was loaded; refresh and retry"
        ) from exc
    db.refresh(document)
    return document


def rollback_document(
    db: Session,
    document: KnowledgeDocument,
    revision: KnowledgeRevision,
    *,
    expected_lock_version: int | None,
    created_by: str | None,
    change_summary: str,
    expected_draft_version: int | None = None,
) -> KnowledgeDocument:
    require_lock_version(document, expected_lock_version)
    if revision.document_id != document.id:
        raise ValueError("Revision does not belong to this knowledge document")
    snapshot = json_loads(revision.snapshot_json, {})
    if document.active and document.review_status == "ACTIVE":
        from app.services.knowledge_drafts import save_draft
        save_draft(db, document, {key: value for key, value in snapshot.items() if key in MATERIAL_DOCUMENT_FIELDS},
                   expected_lock_version=expected_lock_version, expected_draft_version=expected_draft_version,
                   author=created_by)
        return document
    for field in (
        "title",
        "source_type",
        "device_type",
        "device_model",
        "firmware_range",
        "module",
        "trust_level",
        "confidentiality",
        "content",
    ):
        if field in snapshot:
            setattr(document, field, snapshot[field])
    if "metadata" in snapshot:
        document.metadata_json = json_dumps(snapshot["metadata"])
    if "category_id" in snapshot:
        set_document_category(db, document.id, snapshot["category_id"])
    advance_document_version(
        db,
        document,
        created_by=created_by,
        change_summary=(
            change_summary
            or f"Rolled back from version {revision.version}"
        ),
    )
    return document


def mark_domain_graph_stale(db: Session, reason: str) -> None:
    # Loading the graph state must not autoflush a concurrent knowledge edit;
    # the caller owns the explicit flush/commit and translates StaleDataError
    # into a user-facing optimistic-lock conflict.
    with db.no_autoflush:
        state = db.get(KnowledgeGraphState, "domain")
    if not state:
        return
    if not state.active_generation_id and state.status != "BUILDING":
        return
    metadata = json_loads(state.metadata_json, {})
    metadata["stale_reason"] = reason[:1000]
    metadata["stale_at"] = utcnow().isoformat()
    state.metadata_json = json_dumps(metadata)
    if state.status != "BUILDING":
        state.status = "STALE"


def feedback_to_dict(feedback: DiagnosisFeedback) -> dict[str, Any]:
    return {
        "id": feedback.id,
        "case_id": feedback.case_id,
        "analysis_run_id": feedback.analysis_run_id,
        "verdict": feedback.verdict,
        "root_cause_correct": feedback.root_cause_correct,
        "evidence_correct": feedback.evidence_correct,
        "comment": feedback.comment,
        "resolution_status": feedback.resolution_status,
        "resolution_notes": feedback.resolution_notes,
        "resolution_observed_at": feedback.resolution_observed_at,
        "corrections": json_loads(feedback.corrections_json, {}),
        "status": feedback.status,
        "submitted_by": feedback.submitted_by,
        "reviewed_by": feedback.reviewed_by,
        "reviewed_at": feedback.reviewed_at,
        "review_comment": feedback.review_comment,
        "incorporated_document_id": feedback.incorporated_document_id,
        "created_at": feedback.created_at,
        "updated_at": feedback.updated_at,
    }


def incorporate_feedback_as_draft(
    db: Session,
    feedback: DiagnosisFeedback,
    *,
    created_by: str | None,
) -> KnowledgeDocument:
    if feedback.status != "APPROVED":
        raise ValueError("Only approved feedback can be incorporated")
    if feedback.incorporated_document_id:
        existing = db.get(KnowledgeDocument, feedback.incorporated_document_id)
        if existing:
            return existing
    case = db.get(Case, feedback.case_id)
    analysis = db.get(AnalysisRun, feedback.analysis_run_id)
    if not case or not analysis:
        raise ValueError("Feedback source case or analysis no longer exists")
    corrections = json_loads(feedback.corrections_json, {})
    corrected_root_cause = str(corrections.get("root_cause") or "").strip()
    corrected_solution = str(corrections.get("solution") or "").strip()
    evidence_notes = str(corrections.get("evidence") or "").strip()
    content = f"""# 反馈提炼：{case.title}

> 来源案例 `{case.id}`，诊断运行 `{analysis.id}`。此文档由已审核人工反馈生成，
> 当前仍是草稿，必须再次经过知识审核后才会参与检索。

## 错误形式

- 案例设备：{case.device_type} / {case.device_model or "未填写"}
- 固件版本：{case.firmware_version or "未填写"}
- 人工结论：{feedback.verdict}

## 日志分析

{evidence_notes or feedback.comment or "请补充经人工确认的关键证据。"}

## 错误定位

{corrected_root_cause or "请补充经人工确认的根因。"}

## 解决方案

{corrected_solution or "请补充经人工确认的解决方案。"}

## 审核记录

- 反馈 ID：`{feedback.id}`
- 原始反馈：{feedback.comment or "无"}
- 根因判断正确：{feedback.root_cause_correct}
- 证据引用正确：{feedback.evidence_correct}
"""
    document = KnowledgeDocument(
        id=new_id("DOC"),
        title=f"{case.title}：已审核反馈提炼",
        source_type="fault_case",
        device_type=case.device_type,
        device_model=case.device_model,
        firmware_range=case.firmware_version,
        trust_level="MEDIUM",
        confidentiality="INTERNAL",
        content=content,
        metadata_json=json_dumps({
            "source_feedback_id": feedback.id,
            "source_case_id": case.id,
            "source_analysis_run_id": analysis.id,
            "human_approved_source": True,
        }),
        active=False,
        review_status="DRAFT",
    )
    db.add(document)
    db.flush()
    set_document_category(
        db,
        document.id,
        get_default_category_id(db, "fault_case"),
    )
    from app.services.knowledge import index_document

    index_document(db, document)
    create_document_revision(
        db,
        document,
        created_by=created_by,
        change_summary="Created from approved diagnosis feedback",
    )
    feedback.status = "INCORPORATED"
    feedback.incorporated_document_id = document.id
    db.commit()
    db.refresh(document)
    return document
