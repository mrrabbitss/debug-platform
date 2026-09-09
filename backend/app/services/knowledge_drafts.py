"""Draft mutations do not mutate the published document, chunks or method hashes."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, json_loads, new_id
from app.models import KnowledgeCategory, KnowledgeDocument, KnowledgeDraft
from app.schemas import KnowledgeCreate
from app.services.knowledge_governance import document_snapshot, require_lock_version
from app.services.knowledge_methods import enrich_knowledge_metadata


def draft_for_document(db: Session, document_id: str, *, author: str | None = None) -> KnowledgeDraft | None:
    query = select(KnowledgeDraft).where(KnowledgeDraft.document_id == document_id)
    if author is not None:
        query = query.where(KnowledgeDraft.owner_key == author)
    return db.scalar(query.order_by(KnowledgeDraft.updated_at.desc(), KnowledgeDraft.id).limit(1))


def attach_pending_drafts(db: Session, rows: list[dict], *, detail: bool = False, principal: dict | None = None) -> list[dict]:
    drafts = list(db.scalars(select(KnowledgeDraft).where(
        KnowledgeDraft.document_id.in_([row["id"] for row in rows]),
        KnowledgeDraft.status.not_in(["PUBLISHED", "ARCHIVED"]),
    )))
    from app.services.knowledge_access import can_publish
    result = []
    for row in rows:
        document = db.get(KnowledgeDocument, row["id"])
        reviewer = principal is None or can_publish(db, document, principal)
        proposals = [draft for draft in drafts if draft.document_id == row["id"]]
        own = next((draft for draft in proposals if draft.owner_key == str((principal or {}).get("id") or "")), None)
        if principal is None:
            own = proposals[0] if proposals else None
        result.append({**row, "pending_draft": draft_payload(own, include_content=detail) if own else None,
                       "can_publish": reviewer,
                       "can_attest_history": principal is None or principal.get("role") in {"ADMIN", "EXPERT"},
                       "review_drafts": [draft_payload(draft, include_content=detail) for draft in proposals
                                         if reviewer and draft.status == "IN_REVIEW"]})
    return result


def draft_payload(draft: KnowledgeDraft, *, include_content: bool = False) -> dict:
    result = {"id": draft.id, "document_id": draft.document_id, "base_version": draft.base_version,
              "version": draft.version, "status": draft.status, "created_by": draft.created_by,
              "reviewed_by": draft.reviewed_by, "review_comment": draft.review_comment, "updated_at": draft.updated_at}
    if include_content:
        result["snapshot"] = json_loads(draft.snapshot_json, {})
    return result


def save_draft(db: Session, document: KnowledgeDocument, values: dict, *, expected_lock_version: int,
               expected_draft_version: int | None, author: str | None) -> KnowledgeDraft:
    require_lock_version(document, expected_lock_version)
    if not document.active or document.review_status != "ACTIVE":
        raise ValueError("The publication-preserving draft workflow requires an active document")
    draft = draft_for_document(db, document.id, author=author or "")
    if draft and draft.status == "BUILDING":
        raise ValueError("Publication is building; wait for its result before editing")
    if draft and draft.status not in {"PUBLISHED", "ARCHIVED"} and expected_draft_version != draft.version:
        raise ValueError("Draft changed; refresh before editing")
    if not draft and expected_draft_version is not None:
        raise ValueError("Draft no longer exists; refresh before editing")
    snapshot = (json_loads(draft.snapshot_json, {}) if draft and draft.status not in {"PUBLISHED", "ARCHIVED"}
                else document_snapshot(db, document))
    candidate = KnowledgeCreate.model_validate({**snapshot, **values}).model_dump()
    if not candidate["title"] or not candidate["content"] or not candidate["source_type"]:
        raise ValueError("Draft title, source type and content are required")
    category_id = candidate.get("category_id")
    if category_id:
        category = db.get(KnowledgeCategory, category_id)
        if not category or not category.active:
            raise ValueError("Knowledge category not found or inactive")
    candidate["metadata"] = enrich_knowledge_metadata(candidate["source_type"], candidate["content"], candidate["metadata"])
    if draft is None:
        draft = KnowledgeDraft(id=new_id("KDRAFT"), document_id=document.id, base_version=document.version,
                               snapshot_json=json_dumps(candidate), created_by=author, owner_key=author or "")
        db.add(draft)
    else:
        draft.snapshot_json = json_dumps(candidate)
        draft.base_version = document.version
        draft.status = "DRAFT"
        draft.created_by = author
        draft.reviewed_by = None
        draft.review_comment = None
    db.flush()
    from app.services.knowledge_personal import preserve_working_revision
    preserve_working_revision(db, draft)
    return draft


def review_draft(db: Session, draft: KnowledgeDraft, *, action: str, expected_version: int,
                 reviewer: str | None, comment: str | None) -> KnowledgeDraft:
    if draft.version != expected_version:
        raise ValueError("Draft changed; refresh before review")
    states = {"SUBMIT": ({"DRAFT", "REJECTED", "FAILED"}, "IN_REVIEW"),
              "REJECT": ({"IN_REVIEW"}, "REJECTED"),
              "ARCHIVE": ({"DRAFT", "REJECTED", "FAILED", "IN_REVIEW"}, "ARCHIVED")}
    if action not in states or draft.status not in states[action][0]:
        raise ValueError("Invalid draft review transition")
    draft.status = states[action][1]
    draft.reviewed_by = reviewer
    draft.review_comment = comment
    db.flush()
    return draft
