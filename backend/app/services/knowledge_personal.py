"""Explicit, immutable personal knowledge views; never modify shared retrieval indexes."""
from sqlalchemy import select

from app.core.utils import json_dumps, json_loads, new_id
from app.models import KnowledgeDocument, KnowledgeDraft, KnowledgeWorkingRevision

PERSONAL_STATES = {"DRAFT", "IN_REVIEW", "REJECTED", "FAILED", "BUILDING"}


def preserve_working_revision(db, draft):
    row = db.scalar(select(KnowledgeWorkingRevision).where(
        KnowledgeWorkingRevision.draft_id == draft.id,
        KnowledgeWorkingRevision.draft_version == draft.version))
    if row is None:
        row = KnowledgeWorkingRevision(id=new_id("KWREV"), draft_id=draft.id,
            document_id=draft.document_id, owner_key=draft.owner_key,
            draft_version=draft.version, base_version=draft.base_version, snapshot_json=draft.snapshot_json)
        db.add(row)
        db.flush()
    return row


def personal_view(db, actor: str | None) -> list[str]:
    if not actor:
        return []
    drafts = db.scalars(select(KnowledgeDraft).join(KnowledgeDocument).where(
        KnowledgeDraft.owner_key == actor, KnowledgeDraft.status.in_(PERSONAL_STATES),
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE"))
    selected = list(drafts)
    if len(selected) > 50:
        raise ValueError("More than 50 personal knowledge overrides; publish or withdraw older proposals first")
    return [preserve_working_revision(db, draft).id for draft in selected]


def working_documents(db, revision_ids: list[str] | None) -> list[KnowledgeDocument]:
    """IDs are selected by trusted run creation, never accepted as caller-chosen scopes."""
    result = []
    for revision_id in revision_ids or []:
        revision = db.get(KnowledgeWorkingRevision, revision_id)
        if revision is None:
            raise ValueError("Pinned personal knowledge revision is unavailable")
        source = db.get(KnowledgeDocument, revision.document_id)
        if source is None or not source.active:
            continue  # Explicit withdrawal stops fresh retrieval, including personal projections.
        values = json_loads(revision.snapshot_json, {})
        metadata = {**values.get("metadata", {}), "personal_revision_id": revision.id,
                    "personal_draft_id": revision.draft_id, "personal_draft_version": revision.draft_version,
                    "publication_status": "PERSONAL_UNREVIEWED", "base_document_version": revision.base_version}
        result.append(KnowledgeDocument(id=source.id, version=revision.base_version,
            active=True, review_status="ACTIVE", metadata_json=json_dumps(metadata),
            **{key: value for key, value in values.items() if key not in {"metadata", "category_id", "active", "review_status"}}))
    return result


def overlay_chunks(db, rows, revision_ids):
    from app.models import KnowledgeChunk
    from app.services.knowledge import chunk_document
    working = working_documents(db, revision_ids)
    replaced = {document.id for document in working}
    result = [(chunk, document) for chunk, document in rows if document.id not in replaced]
    for document in working:
        revision_id = json_loads(document.metadata_json, {})["personal_revision_id"]
        result.extend((KnowledgeChunk(id=f"{revision_id}:{index}", document_id=document.id,
            document_version=document.version, heading=heading, content=content), document)
            for index, (heading, content) in enumerate(chunk_document(document.content)))
    return result
