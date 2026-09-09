"""Knowledge management capabilities and owner-scoped contribution evidence."""
from fastapi import HTTPException
from sqlalchemy import and_, exists, select

from app.core.utils import json_loads
from app.knowledge_acl_models import KnowledgeAccess
from app.models import Job, KnowledgeCurationSession, KnowledgeDocument


KNOWLEDGE_MANAGEMENT_JOB_KINDS = frozenset({
    "publish_knowledge_revision", "route_markdown_knowledge", "import_knowledge",
    "curate_knowledge_folder", "reindex_knowledge", "rebuild_domain_graph",
    "assistant_plan", "assistant_publish",
    "publish_knowledge_contribution",
    "refine_knowledge_contribution", "refine_knowledge_curation",
    "knowledge_reset",
})
INTERACTIVE_MODEL_JOB_KINDS = frozenset({
    "refine_knowledge_contribution",
    "refine_knowledge_curation",
    "test_chat_model_connection",
})
KNOWLEDGE_READ_PATHS = {(), ("categories",), ("templates", "fault-case"), ("graph", "status")}
DOCUMENT_READ_PATHS = {(), ("sections",), ("revisions",), ("publications",)}


def is_knowledge_manager(principal: dict) -> bool:
    return principal.get("role") in {"ADMIN", "EXPERT"}


def require_knowledge_admin(principal: dict) -> None:
    """Kept as the common REST/MCP management gate for backwards compatibility."""
    if not is_knowledge_manager(principal):
        raise HTTPException(403, "Only administrators and experts may manage knowledge")


def require_contributor(principal: dict) -> None:
    if principal.get("role") not in {"ADMIN", "EXPERT", "ENGINEER"} or not principal.get("id"):
        raise HTTPException(403, "An authenticated contributor account is required")


def knowledge_kind(document) -> str:
    """A persisted discriminator wins; a filename never grants Skill permissions."""
    metadata = json_loads(getattr(document, "metadata_json", "{}"), {})
    if not isinstance(metadata, dict):
        metadata = {}
    if metadata.get("content_kind") in {"KNOWLEDGE", "SKILL"}:
        return metadata["content_kind"]
    if (getattr(document, "source_type", "") in {"analysis_skill", "analysis_method", "fault_tree", "report_template"}
            or metadata.get("knowledge_role") in {"log_analysis", "diagnosis", "fault_tree", "report_template"}
            or metadata.get("bundle_manifest")):
        return "SKILL"
    return "KNOWLEDGE"


def require_curation_access(db, session_id: str, principal: dict, *, write=False):
    require_contributor(principal)
    session = db.get(KnowledgeCurationSession, session_id)
    if not session:
        raise HTTPException(404, "Knowledge curation session not found")
    if session.created_by == principal.get("id"):
        return session
    if not write and is_knowledge_manager(principal):
        from app.knowledge_contribution_models import KnowledgeContribution
        submitted = db.scalar(select(KnowledgeContribution.id).where(
            KnowledgeContribution.source_curation_id == session_id,
            KnowledgeContribution.status.not_in(["DRAFT", "DELETED"])))
        if submitted:
            return session
    raise HTTPException(404, "Knowledge curation session not found")


def _published_visible(document) -> bool:
    return bool(document.active and document.review_status == "ACTIVE"
                and document.confidentiality in {"PUBLIC", "INTERNAL"})


def shared_knowledge_clause():
    return KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"])


def visible_knowledge_clause(principal):
    if is_knowledge_manager(principal):
        from app.knowledge_contribution_models import KnowledgeContribution
        return ~exists(select(KnowledgeContribution.id).where(
            KnowledgeContribution.published_document_id == KnowledgeDocument.id,
            KnowledgeContribution.owner_id != str(principal.get("id") or ""),
            KnowledgeContribution.status.in_(["DRAFT", "DELETED"])))
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
    if is_knowledge_manager(principal):
        from app.knowledge_contribution_models import KnowledgeContribution
        private_draft = db.scalar(select(KnowledgeContribution.id).where(
            KnowledgeContribution.published_document_id == document.id,
            KnowledgeContribution.owner_id != str(principal.get("id") or ""),
            KnowledgeContribution.status.in_(["DRAFT", "DELETED"])))
        if private_draft:
            raise HTTPException(404, "Knowledge document not found")
        return document
    if not _published_visible(document):
        raise HTTPException(403, "No access to this knowledge scope")
    return document


def can_publish(db, document, principal: dict) -> bool:
    return is_knowledge_manager(principal)


def require_publisher(db, document, principal: dict):
    require_knowledge_admin(principal)


def authorize_knowledge_request(db, parts, method, principal):
    """Legacy writes remain privileged; curation is isolated at its own boundary."""
    if not parts or parts[0] not in {"knowledge", "knowledge-routing", "knowledge-curations"}:
        return
    if parts[0] == "knowledge-curations":
        require_contributor(principal)
        if len(parts) > 1:
            require_curation_access(db, parts[1], principal, write=method.upper() != "GET")
        return
    if is_knowledge_manager(principal):
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


def authorize_routing_job(db, job_id: str, principal: dict, *, method="GET") -> bool:
    job = db.get(Job, job_id)
    if not job:
        return False
    data = json_loads(job.input_json, {})
    if job.kind in INTERACTIVE_MODEL_JOB_KINDS:
        # Interactive model jobs may contain private prompts and profile
        # fingerprints.  They are never made visible to another manager or to
        # an otherwise authorised case user through the generic /jobs route.
        if data.get("owner_id") != principal.get("id"):
            raise HTTPException(404, "Job not found")
        if job.kind == "refine_knowledge_contribution":
            require_contributor(principal)
            require_knowledge_admin(principal)
            from app.services.knowledge_contributions import require_contribution
            require_contribution(db, data.get("contribution_id", ""), principal)
        elif job.kind == "refine_knowledge_curation":
            require_contributor(principal)
            require_curation_access(db, data.get("session_id", ""), principal,
                                    write=method.upper() != "GET")
        else:
            from app.services.model_access import ModelAccessError, require_model_profile
            try:
                require_model_profile(db, principal, data.get("profile_id", ""))
            except ModelAccessError as exc:
                raise HTTPException(exc.status_code, str(exc)) from exc
        return True
    if job.kind not in KNOWLEDGE_MANAGEMENT_JOB_KINDS:
        return False
    if job.kind == "curate_knowledge_folder":
        require_curation_access(db, data.get("session_id", ""), principal, write=method.upper() != "GET")
        return True
    if job.kind == "publish_knowledge_contribution":
        if method.upper() != "GET":
            require_knowledge_admin(principal)
        from app.services.knowledge_contributions import require_contribution
        require_contribution(db, data.get("contribution_id", ""), principal)
        return True
    require_knowledge_admin(principal)
    return True


def can_read_revision(db, document, revision, principal):
    if revision.document_id != document.id:
        return False
    if is_knowledge_manager(principal):
        return True
    if not _published_visible(document):
        return False
    from app.models import KnowledgePublication
    snapshot = json_loads(revision.snapshot_json, {})
    if not isinstance(snapshot, dict) or snapshot.get("confidentiality", "RESTRICTED") not in {"PUBLIC", "INTERNAL"}:
        return False
    return revision.version == document.version or bool(db.scalar(select(KnowledgePublication.id).where(
        KnowledgePublication.document_id == document.id, KnowledgePublication.document_version == revision.version)))
