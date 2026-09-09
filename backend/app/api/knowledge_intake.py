"""Explicit human attestation admits curated historical knowledge without model adjudication."""
from typing import Annotated, Literal
import hashlib
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import KnowledgeDocument, KnowledgeDraft
from app.services.knowledge_access import bind_owner
from app.services.knowledge_governance import document_snapshot, require_lock_version
from app.services.knowledge_publication import enqueue_publication
from app.services.jobs import job_runner
from app.schemas import JobOut

router = APIRouter(tags=["knowledge-intake"])
Db = Annotated[Session, Depends(get_db)]


class HumanVerifiedApproval(BaseModel):
    human_verified: Literal[True]
    expected_lock_version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/knowledge/{document_id}/adopt-human-verified")
def adopt_verified(document_id: str, payload: HumanVerifiedApproval, request: Request, db: Db):
    principal = getattr(request.state, "principal", {})
    if principal.get("role") not in {"ADMIN", "EXPERT"}:
        raise HTTPException(403, "Administrator must attest historical knowledge")
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge not found")
    try:
        require_lock_version(document, payload.expected_lock_version)
        if document.active or document.review_status not in {"DRAFT", "REJECTED"}:
            raise ValueError("Use a draft historical source; active publications use revision review")
        if hashlib.sha256(document.content.encode("utf-8")).hexdigest() != payload.content_sha256:
            raise ValueError("Historical source changed; review the current content")
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    from app.services.knowledge_drafts import draft_for_document
    actor = str(principal.get("id") or "local-user")
    draft = draft_for_document(db, document.id, author=actor)
    if draft and draft.status == "BUILDING":
        raise HTTPException(409, "Publication already building")
    document.trust_level = "HIGH"
    document.metadata_json = json_dumps({**json_loads(document.metadata_json, {}), "human_verified_source": {
        "confirmed_by": actor, "confirmed_at": utcnow().isoformat(), "content_sha256": payload.content_sha256}})
    document.review_status = "IN_REVIEW"
    document.lock_version += 1
    bind_owner(db, document.id, actor)
    snapshot = document_snapshot(db, document)
    snapshot.pop("active", None)
    snapshot.pop("review_status", None)
    if not draft:
        draft = KnowledgeDraft(id=new_id("KDRAFT"), document_id=document.id, base_version=document.version,
            created_by=actor, owner_key=actor, snapshot_json="{}")
        db.add(draft)
    draft.snapshot_json = json_dumps(snapshot)
    draft.status = "IN_REVIEW"
    draft.review_comment = "Human-verified historical source; no generated correctness claim"
    db.flush()
    job = enqueue_publication(db, document, draft, actor)
    db.commit()
    try:
        job_runner._schedule(job.id)
    except RuntimeError:
        pass
    return {"document_id": document.id, "trust_level": "HIGH", "publication_pending": True,
            "job": JobOut.model_validate(job).model_dump()}
