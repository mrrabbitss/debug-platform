"""Explicit knowledge ownership; shared retrieval never includes restricted knowledge."""
from fastapi import HTTPException
from sqlalchemy import and_, or_, select

from app.core.utils import json_loads
from app.knowledge_acl_models import KnowledgeAccess
from app.models import Artifact, Job, KnowledgeDocument


def shared_knowledge_clause():
    return KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"])


def visible_knowledge_clause(principal):
    if principal.get("role") == "ADMIN":
        return True
    owned = select(KnowledgeAccess.document_id).where(KnowledgeAccess.owner_id == principal.get("id", ""))
    return or_(KnowledgeDocument.id.in_(owned), and_(KnowledgeDocument.active.is_(True),
                KnowledgeDocument.review_status == "ACTIVE", shared_knowledge_clause()))


def bind_owner(db, document_id: str, owner_id: str | None):
    if db.get(KnowledgeAccess, document_id) is None:
        db.add(KnowledgeAccess(document_id=document_id, owner_id=owner_id))
        db.flush()


def require_knowledge_access(db, document_id: str, principal: dict, *, write=False):
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    if principal.get("role") == "ADMIN":
        return document
    owner = db.get(KnowledgeAccess, document_id)
    owned = bool(principal.get("id")) and owner is not None and owner.owner_id == principal.get("id")
    if write:
        allowed = principal.get("role") == "ENGINEER" and (owned or (
            document.active and document.review_status == "ACTIVE" and document.confidentiality in {"PUBLIC", "INTERNAL"}))
    else:
        allowed = owned or (document.active and document.review_status == "ACTIVE" and document.confidentiality in {"PUBLIC", "INTERNAL"})
    if not allowed:
        raise HTTPException(403, "No access to this knowledge scope")
    return document


def can_publish(db, document, principal: dict) -> bool:
    if principal.get("role") == "ADMIN":
        return True
    access = db.get(KnowledgeAccess, document.id)
    publisher = (access.publisher_id if access else None) or document.reviewed_by
    return bool(principal.get("id")) and principal.get("role") == "ENGINEER" and publisher == principal["id"]


def require_publisher(db, document, principal: dict):
    if not can_publish(db, document, principal):
        raise HTTPException(403, "Administrator or knowledge publisher approval required")


def authorize_knowledge_request(db, parts, method, principal):
    """Transport-level permissions; draft actions additionally authorize the selected proposal."""
    if not parts or parts[0] not in {"knowledge", "knowledge-routing"}:
        return
    role = principal.get("role", "VIEWER")
    special = {"categories", "templates", "reindex", "graph", "import"}
    is_document = parts[0] == "knowledge" and len(parts) >= 2 and parts[1] not in special
    document = require_knowledge_access(db, parts[1], principal) if is_document else None
    if method == "GET":
        return
    if role != "ENGINEER":
        raise HTTPException(403, "Engineer role required to contribute knowledge")
    if parts in (["knowledge"], ["knowledge-routing", "import"]) and method == "POST":
        return
    if document and method == "PATCH" and len(parts) == 2:
        require_knowledge_access(db, document.id, principal, write=True)
        return
    if document and parts[2:] == ["draft", "review"] and method == "POST":
        return  # Per-proposal author/publisher check in the typed boundary.
    if document and len(parts) == 4 and parts[2] == "review" and method == "POST":
        if parts[3] == "submit":
            require_knowledge_access(db, document.id, principal, write=True)
        else:
            require_publisher(db, document, principal)
        return
    raise HTTPException(403, "Administrator role required for this knowledge operation")


def authorize_routing_job(db, job_id: str, principal: dict) -> bool:
    job = db.get(Job, job_id)
    if job and job.kind == "publish_knowledge_revision":
        document = db.get(KnowledgeDocument, json_loads(job.input_json, {}).get("document_id"))
        if not document or not can_publish(db, document, principal):
            raise HTTPException(403, "Only the administrator or knowledge publisher may manage this publication job")
        return True
    if not job or job.kind != "route_markdown_knowledge":
        return False
    artifact_id = json_loads(job.input_json, {}).get("artifact_id")
    artifact = db.get(Artifact, artifact_id) if artifact_id else None
    owner = json_loads(artifact.metadata_json, {}).get("created_by") if artifact else None
    if principal.get("role") != "ADMIN" and (principal.get("role") != "ENGINEER" or not owner or owner != principal.get("id")):
        raise HTTPException(403, "No access to this knowledge import job")
    return True


def can_read_revision(db, document, revision, principal):
    if principal.get("role") == "ADMIN":
        return True
    owner = db.get(KnowledgeAccess, document.id)
    if principal.get("id") and owner and owner.owner_id == principal["id"]:
        return True
    from app.models import KnowledgePublication
    snapshot = json_loads(revision.snapshot_json, {})
    if snapshot.get("confidentiality", "RESTRICTED") not in {"PUBLIC", "INTERNAL"}:
        return False
    return document.active and (revision.version == document.version or bool(db.scalar(select(KnowledgePublication.id).where(
        KnowledgePublication.document_id == document.id, KnowledgePublication.document_version == revision.version))))
