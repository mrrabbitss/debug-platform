"""Published knowledge is readable; management requires ADMIN regardless of ownership."""
from fastapi import HTTPException
from sqlalchemy import and_, select

from app.core.utils import json_loads
from app.knowledge_acl_models import KnowledgeAccess
from app.models import Job, KnowledgeDocument


KNOWLEDGE_MANAGEMENT_JOB_KINDS = frozenset({
    "publish_knowledge_revision", "route_markdown_knowledge", "import_knowledge",
    "curate_knowledge_folder", "reindex_knowledge", "rebuild_domain_graph",
    "assistant_plan", "assistant_publish",
})
KNOWLEDGE_READ_PATHS = {(), ("categories",), ("templates", "fault-case"), ("graph", "status")}
DOCUMENT_READ_PATHS = {(), ("sections",), ("revisions",), ("publications",)}


def require_knowledge_admin(principal: dict) -> None:
    """Shared domain gate for REST and MCP, including former personal contributions."""
    if principal.get("role") != "ADMIN":
        raise HTTPException(403, "Only administrators may manage knowledge")


def _published_visible(document) -> bool:
    return bool(document.active and document.review_status == "ACTIVE"
                and document.confidentiality in {"PUBLIC", "INTERNAL"})


def shared_knowledge_clause():
    return KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"])


def visible_knowledge_clause(principal):
    if principal.get("role") == "ADMIN":
        return True
    return and_(KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE", shared_knowledge_clause())


def bind_owner(db, document_id: str, owner_id: str | None):
    if db.get(KnowledgeAccess, document_id) is None:
        db.add(KnowledgeAccess(document_id=document_id, owner_id=owner_id))
        db.flush()


def require_knowledge_access(db, document_id: str, principal: dict, *, write=False):
    if write:
        require_knowledge_admin(principal)
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    if principal.get("role") == "ADMIN":
        return document
    if not _published_visible(document):
        raise HTTPException(403, "No access to this knowledge scope")
    return document


def can_publish(db, document, principal: dict) -> bool:
    return principal.get("role") == "ADMIN"


def require_publisher(db, document, principal: dict):
    require_knowledge_admin(principal)


def authorize_knowledge_request(db, parts, method, principal):
    """Only administrators manage knowledge, including all legacy contribution routes."""
    if not parts or parts[0] not in {"knowledge", "knowledge-routing", "knowledge-curations"}:
        return
    if principal.get("role") == "ADMIN":
        return
    if method.upper() != "GET":
        require_knowledge_admin(principal)
    if parts[0] != "knowledge":
        raise HTTPException(403, "Only administrators may access unpublished knowledge workflows")
    if tuple(parts[1:]) in KNOWLEDGE_READ_PATHS:
        return
    suffix = tuple(parts[2:])
    published_chunk = len(suffix) == 4 and suffix[0] == "versions" and suffix[2] == "chunks"
    if suffix not in DOCUMENT_READ_PATHS and not published_chunk:
        raise HTTPException(403, "Only administrators may access knowledge management views")
    require_knowledge_access(db, parts[1], principal)


def authorize_routing_job(db, job_id: str, principal: dict) -> bool:
    job = db.get(Job, job_id)
    if not job or job.kind not in KNOWLEDGE_MANAGEMENT_JOB_KINDS:
        return False
    require_knowledge_admin(principal)
    return True


def can_read_revision(db, document, revision, principal):
    if revision.document_id != document.id:
        return False
    if principal.get("role") == "ADMIN":
        return True
    if not _published_visible(document):
        return False
    from app.models import KnowledgePublication
    snapshot = json_loads(revision.snapshot_json, {})
    if not isinstance(snapshot, dict) or snapshot.get("confidentiality", "RESTRICTED") not in {"PUBLIC", "INTERNAL"}:
        return False
    return revision.version == document.version or bool(db.scalar(select(KnowledgePublication.id).where(
        KnowledgePublication.document_id == document.id, KnowledgePublication.document_version == revision.version)))
