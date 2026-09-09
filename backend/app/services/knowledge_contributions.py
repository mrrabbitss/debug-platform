"""Owner drafts and exact, auditable manager approval; never activates knowledge here."""
import hashlib
import json
from difflib import unified_diff

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select

from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.knowledge_contribution_models import KnowledgeContribution, KnowledgeContributionRevision
from app.models import AuditEvent, Job, KnowledgeCategory, KnowledgeDocument
from app.schemas import KnowledgeCreate
from app.services.knowledge_access import (is_knowledge_manager, knowledge_kind, require_contributor,
    require_curation_access, require_knowledge_access, require_knowledge_admin)
from app.services.knowledge_governance import document_snapshot

EDITABLE = {"DRAFT", "RETURNED", "REJECTED"}
APPROVED = {"APPROVED", "PUBLISHING", "PUBLISHED", "FAILED"}


def content_digest(row, candidate=None):
    value = {"operation": row.operation, "content_kind": row.content_kind,
        "target_document_id": row.target_document_id, "base_version": row.base_version,
        "base_lock_version": row.base_lock_version,
        "candidate": candidate if candidate is not None else json_loads(row.candidate_json, {})}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def diff_text(before, after):
    return "".join(unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
                                fromfile="original", tofile="candidate"))


def require_contribution(db, contribution_id, principal, *, owner=False):
    require_contributor(principal)
    row = db.get(KnowledgeContribution, contribution_id)
    if (not row or row.status == "DELETED" or (row.owner_id != principal["id"]
            and (owner or not is_knowledge_manager(principal) or row.status == "DRAFT"))):
        raise HTTPException(404, "Knowledge contribution not found")
    return row


def require_version(row, expected_version):
    if row.version != expected_version:
        raise HTTPException(409, "Contribution changed; refresh and review the current version")


def normalize_candidate(db, values, kind):
    try:
        candidate = KnowledgeCreate.model_validate(values).model_dump()
    except ValidationError as error:
        raise HTTPException(422, "Contribution fields are invalid") from error
    if not candidate["title"].strip() or len(candidate["title"]) > 512 or not candidate["content"].strip():
        raise HTTPException(422, "A nonempty title and Markdown content are required")
    if len(candidate["content"]) > 500_000 or len(json_dumps(candidate["metadata"])) > 100_000:
        raise HTTPException(413, "Contribution exceeds the document size limit")
    if candidate["category_id"]:
        category = db.get(KnowledgeCategory, candidate["category_id"])
        if not category or not category.active:
            raise HTTPException(422, "Knowledge category not found or inactive")
    # Content type is selected by a trusted workflow, never a model/file-name side effect.
    candidate["metadata"]["content_kind"] = kind
    candidate["metadata"].pop("curation_model", None)
    for key in ("embedding_generation_id", "embedding_profile_id", "embedding_status", "assistant_reservation"):
        candidate["metadata"].pop(key, None)
    return candidate


def audit_revision(db, row, principal, action, *, before=None, comment="", messages=None):
    candidate = json_loads(row.candidate_json, {})
    db.add(KnowledgeContributionRevision(id=new_id("KCR"), contribution_id=row.id,
        version=row.version, action=action, candidate_json=row.candidate_json, content_hash=row.content_hash,
        diff=diff_text((before or {}).get("content", ""), candidate.get("content", "")),
        actor_id=principal["id"], comment=comment, messages_json=json_dumps(messages or [])))
    db.add(AuditEvent(id=new_id("AUD"), actor_id=principal["id"], actor_type=principal.get("type", "user"),
        action="knowledge.contribution." + action.lower(), resource_type="knowledge_contribution",
        resource_id=row.id, outcome="SUCCESS", details_json=json_dumps({"version": row.version,
            "content_hash": row.content_hash, "content_kind": row.content_kind, "content_recorded": False})))
    db.flush()


def contribution_payload(db, row, *, detail=True):
    candidate, original = json_loads(row.candidate_json, {}), json_loads(row.original_json, {})
    status = row.status
    if status in {"APPROVED", "PUBLISHING"} and row.publication_job_id:
        job = db.get(Job, row.publication_job_id)
        if job and job.status in {"FAILED", "CANCELLED", "DEAD_LETTER"}:
            status = "FAILED"
    result = {key: getattr(row, key) for key in ("id", "owner_id", "operation", "content_kind",
        "target_document_id", "version", "content_hash", "publication_job_id", "published_document_id",
        "source_curation_id", "source_library_id", "reviewed_by", "reviewed_at", "review_comment",
        "approved_version", "approved_hash", "error_message", "created_at", "updated_at")}
    result.update(status=status, candidate=candidate, original=original,
        diff=diff_text(original.get("content", ""), "" if row.operation == "DELETE" else candidate.get("content", "")))
    if detail:
        revisions = list(db.scalars(select(KnowledgeContributionRevision).where(
            KnowledgeContributionRevision.contribution_id == row.id).order_by(KnowledgeContributionRevision.version)))
        result["revisions"] = [{"version": revision.version, "action": revision.action,
            "candidate": json_loads(revision.candidate_json, {}), "content_hash": revision.content_hash,
            "diff": revision.diff, "actor_id": revision.actor_id, "comment": revision.comment,
            "created_at": revision.created_at} for revision in revisions]
        result["messages"] = [message for revision in revisions for message in json_loads(revision.messages_json, [])]
    return result


def create_contribution(db, principal, values, *, existing_document=None):
    require_contributor(principal)
    source_id = values.get("source_curation_id")
    if source_id:
        session = require_curation_access(db, source_id, principal, write=True)
        if not session.knowledge_document_id or session.status != "CONFIRMED":
            raise HTTPException(409, "Confirm the extraction before creating a contribution")
        previous = db.scalar(select(KnowledgeContribution).where(KnowledgeContribution.source_curation_id == source_id))
        if previous:
            return previous
        existing_document = db.get(KnowledgeDocument, session.knowledge_document_id)
        # The source snapshot is server-owned; callers cannot substitute a document/id.
        values = {**document_snapshot(db, existing_document), "operation": "CREATE", "content_kind": "KNOWLEDGE",
                  "source_curation_id": source_id}
    operation, kind = values.get("operation", "CREATE"), values.get("content_kind", "KNOWLEDGE")
    target_id = values.get("target_document_id")
    target = None
    if operation in {"UPDATE", "DELETE"}:
        if not target_id:
            raise HTTPException(422, "A target document is required")
        target = require_knowledge_access(db, target_id, principal)
        if not target.active or target.review_status != "ACTIVE":
            raise HTTPException(409, "Only published knowledge can receive shared change proposals")
        kind = knowledge_kind(target)
    elif target_id:
        raise HTTPException(422, "CREATE cannot name an existing target")
    original = document_snapshot(db, target) if target else {}
    candidate = {**original, **{k: v for k, v in values.items() if v is not None}}
    if operation == "DELETE":
        candidate = original.copy()
    candidate = normalize_candidate(db, candidate, kind)
    row = KnowledgeContribution(id=new_id("KCON"), owner_id=principal["id"], operation=operation, content_kind=kind,
        target_document_id=target.id if target else None, base_version=target.version if target else None,
        base_lock_version=target.lock_version if target else None, source_curation_id=source_id,
        candidate_json=json_dumps(candidate), original_json=json_dumps(original), status="DRAFT", version=1,
        published_document_id=existing_document.id if existing_document else None, content_hash="")
    row.content_hash = content_digest(row)
    db.add(row)
    db.flush()
    audit_revision(db, row, principal, "CREATE", before=original)
    return row


def update_contribution(db, row, principal, values, *, review=False, messages=None):
    require_contributor(principal)
    require_version(row, values["expected_version"])
    if review:
        require_knowledge_admin(principal)
        if row.status != "SUBMITTED":
            raise HTTPException(409, "Only submitted contributions can be revised by a reviewer")
    elif row.owner_id != principal["id"] or row.status not in EDITABLE:
        raise HTTPException(409, "Only the owner can edit an unsubmitted draft")
    before = json_loads(row.candidate_json, {})
    updates = {key: values[key] for key in ("title", "content", "category_id", "metadata", "confidentiality") if key in values}
    if "metadata" in updates and isinstance(updates["metadata"], dict):
        protected = ("library_record_id", "library_record_version", "curation_session_id", "curation_draft_version",
                     "source_refs", "source_manifest", "case_id", "analysis_id")
        updates["metadata"] = {**updates["metadata"], **{key: before["metadata"][key] for key in protected if key in before["metadata"]}}
    if row.operation == "DELETE" and updates:
        raise HTTPException(409, "Deletion proposals preserve the exact target; edit the review comment only")
    candidate = normalize_candidate(db, {**before, **updates}, row.content_kind)
    row.candidate_json, row.content_hash = json_dumps(candidate), content_digest(row, candidate)
    row.version += 1
    row.approved_hash = row.approved_version = None
    audit_revision(db, row, principal, "REVIEW_EDIT" if review else "EDIT", before=before,
                   comment=values.get("comment", ""), messages=messages)
    return row


def submit_contribution(db, row, principal, expected_version):
    require_contributor(principal)
    require_version(row, expected_version)
    if row.owner_id != principal["id"] or row.status not in EDITABLE:
        raise HTTPException(409, "Only the owner can submit an editable draft")
    candidate = json_loads(row.candidate_json, {})
    validate_candidate_evidence(db, row, candidate)
    if not row.submitted_at and row.operation == "CREATE":
        row.original_json = row.candidate_json
    row.status, row.submitted_at = "SUBMITTED", utcnow()
    row.version += 1
    audit_revision(db, row, principal, "SUBMIT", before=candidate)
    return row


def validate_candidate_evidence(db, row, candidate):
    if row.source_curation_id:
        from app.services.knowledge_curation_evidence import source_refs, validate_curation_markdown
        validation = validate_curation_markdown(candidate["content"], source_refs(db, row.source_curation_id))
        if not validation["confirmable"]:
            raise HTTPException(422, "Extraction still has invalid source citations or required sections")


def review_contribution(db, row, principal, values):
    require_knowledge_admin(principal)
    expected_version, expected_hash = values["expected_version"], values["expected_content_hash"]
    if (values["action"] == "APPROVE" and row.status in APPROVED
            and row.approved_version == expected_version and row.approved_hash == expected_hash):
        return row
    require_version(row, expected_version)
    if row.status != "SUBMITTED" or row.content_hash != expected_hash or content_digest(row) != expected_hash:
        raise HTTPException(409, "Review must match the exact current submitted content")
    if values["action"] != "APPROVE":
        row.status = "RETURNED" if values["action"] == "RETURN" else "REJECTED"
        row.version += 1
        row.reviewed_by, row.reviewed_at = principal["id"], utcnow()
        row.review_comment = values.get("comment", "")
        audit_revision(db, row, principal, values["action"], comment=row.review_comment)
        return row
    validate_candidate_evidence(db, row, json_loads(row.candidate_json, {}))
    if row.target_document_id:
        target = db.get(KnowledgeDocument, row.target_document_id)
        if (not target or not target.active or target.review_status != "ACTIVE"
                or target.version != row.base_version or target.lock_version != row.base_lock_version):
            raise HTTPException(409, "Shared knowledge changed; create a proposal against its current version")
    row.approved_version, row.approved_hash = row.version, row.content_hash
    row.reviewed_by, row.reviewed_at = principal["id"], utcnow()
    row.review_comment, row.status = values.get("comment", ""), "APPROVED"
    row.version += 1
    if row.operation == "CREATE" and not row.published_document_id:
        from app.services.knowledge_access import bind_owner
        candidate = json_loads(row.candidate_json, {})
        document = KnowledgeDocument(id=new_id("DOC"), title=candidate["title"], content="",
            active=False, review_status="DRAFT", metadata_json=json_dumps({"contribution_reservation": row.id,
            "content_kind": row.content_kind}))
        db.add(document)
        db.flush()
        bind_owner(db, document.id, row.owner_id)
        row.published_document_id = document.id
    data = {"contribution_id": row.id, "approved_version": row.approved_version,
            "approved_hash": row.approved_hash, "reviewer": principal["id"]}
    job = Job(id=new_id("JOB"), kind="publish_knowledge_contribution", input_json=json_dumps(data),
        idempotency_key=f"contribution:{row.id}:{row.approved_version}:{row.approved_hash}",
        status="QUEUED", max_attempts=3, timeout_seconds=1800, available_at=utcnow())
    db.add(job)
    db.flush()
    row.publication_job_id = job.id
    audit_revision(db, row, principal, "APPROVE", comment=row.review_comment)
    # The caller commits contribution, exact approval, audit and queued Job together.
    # The existing persistent dispatcher discovers it, including after a restart.
    return row


def delete_contribution(db, row, principal, expected_version):
    require_contributor(principal)
    require_version(row, expected_version)
    if row.owner_id != principal["id"] or row.status not in EDITABLE:
        raise HTTPException(409, "Only unsubmitted owner drafts can be deleted")
    row.status = "DELETED"
    row.version += 1
    audit_revision(db, row, principal, "DELETE")
