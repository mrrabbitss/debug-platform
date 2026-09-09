"""Publish approved contributions through private vector/graph generations and a lease fence."""
from collections import defaultdict
import hashlib

from sqlalchemy import case, select, update

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.knowledge_contribution_models import KnowledgeContribution
from app.models import (Job, KnowledgeAccess, KnowledgeChunk, KnowledgeDocument, KnowledgeGraphState,
                        KnowledgePublication, ModelProfile)
from app.services.assistant_state import require_admin_actor
from app.services.jobs import JobCancelledError, JobLeaseLostError
from app.services.knowledge import chunk_document
from app.services.knowledge_access import bind_owner
from app.services.knowledge_contributions import content_digest, normalize_candidate, validate_candidate_evidence
from app.services.knowledge_governance import create_document_revision
from app.services.knowledge_publication import active_documents
from app.services.knowledge_release_graph import stage_graph
from app.services.knowledge_taxonomy import set_document_category
from app.services.knowledge_visibility import current_chunk_clause
from app.services.model_profiles import get_active_model_profile
from app.services.retrieval_models import index_embeddings


def fingerprint(document):
    return hashlib.sha256(json_dumps({key: getattr(document, key) for key in ("id", "version", "lock_version",
        "title", "content", "source_type", "metadata_json", "active", "review_status", "confidentiality",
        "device_type", "device_model", "firmware_range", "module", "trust_level")}).encode()).hexdigest()


def profile_fingerprint(profile):
    return hashlib.sha256(json_dumps({key: getattr(profile, key) for key in ("id", "enabled", "is_active",
        "config_json", "provider", "mode", "base_url", "model_name", "api_key_ciphertext",
        "proxy_url_ciphertext", "active_embedding_generation_id")}).encode()).hexdigest()


def corpus(db):
    documents = active_documents(db)
    chunks = list(db.scalars(select(KnowledgeChunk).join(KnowledgeDocument).where(current_chunk_clause(),
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE").order_by(KnowledgeChunk.id)))
    signature = hashlib.sha256(json_dumps({"documents": [(item.id, fingerprint(item)) for item in documents],
        "chunks": [(item.id, item.document_version, item.content, item.metadata_json) for item in chunks]}).encode()).hexdigest()
    return documents, chunks, signature


class ContributionFence:
    def __init__(self, ctx, contribution_id, approved_version, approved_hash, reviewer):
        self.ctx, self.id, self.version, self.digest, self.reviewer = ctx, contribution_id, approved_version, approved_hash, reviewer
        self.graph_id, self.token = new_id("KGEN"), new_id("KWORK")

    def check(self, db, *, building=True):
        row = db.get(KnowledgeContribution, self.id)
        job = db.get(Job, self.ctx.job_id)
        if (not job or job.status != "RUNNING" or (self.ctx.lease_owner and job.lease_owner != self.ctx.lease_owner)):
            raise JobLeaseLostError("Contribution publisher no longer owns its job lease")
        if (not row or row.publication_job_id != job.id or row.status not in {"APPROVED", "PUBLISHING"}
                or row.approved_version != self.version or row.approved_hash != self.digest
                or row.reviewed_by != self.reviewer or row.content_hash != self.digest or content_digest(row) != self.digest):
            raise ValueError("Publication no longer matches the approved contribution")
        require_admin_actor(db, self.reviewer)
        if building:
            state = db.get(KnowledgeGraphState, "domain")
            if (row.worker_token != self.token or row.building_generation_id != self.graph_id or not state
                    or state.building_generation_id != self.graph_id):
                raise JobLeaseLostError("Contribution publication was superseded")
        return row

    def raise_if_cancelled(self):
        self.ctx.raise_if_cancelled()
        with SessionLocal() as db:
            self.check(db)

    def update(self, progress, message):
        self.raise_if_cancelled()
        from app.services.job_progress import report_progress
        report_progress(self.ctx, progress, "正在构建已审批知识的索引" if progress < 60 else "正在构建知识关联",
            stage="构建知识索引" if progress < 60 else "构建知识关联", stage_index=2 if progress < 60 else 3, stage_count=4)

    def report_progress(self, progress, message, details):
        self.raise_if_cancelled()
        if hasattr(self.ctx, "report_progress"):
            self.ctx.report_progress(progress, message, details)
        else:
            self.ctx.update(progress, message)


def prepare(fence):
    with SessionLocal() as db:
        # SQL UPDATE also serializes on SQLite, whose FOR UPDATE is a no-op.
        db.execute(update(KnowledgeContribution).where(KnowledgeContribution.id == fence.id).values(updated_at=utcnow()))
        current = db.get(KnowledgeContribution, fence.id)
        job = db.get(Job, fence.ctx.job_id)
        retry_of = json_loads(job.input_json, {}).get("_retry_of_job_id") if job else None
        if (current and retry_of and current.publication_job_id == retry_of
                and current.status in {"APPROVED", "PUBLISHING"}):
            previous = db.get(Job, retry_of)
            if previous and previous.status in {"FAILED", "CANCELLED", "DEAD_LETTER"}:
                current.publication_job_id = job.id
                db.flush()
        row = fence.check(db, building=False)
        state = db.get(KnowledgeGraphState, "domain")
        if not state:
            state = KnowledgeGraphState(id="domain", status="NOT_BUILT")
            db.add(state)
            db.flush()
        if state.building_generation_id:
            if state.building_generation_id != row.building_generation_id:
                raise ValueError("Another index publication is building; retry when it finishes")
            # An expired worker's exact same approved job may resume without another approval.
            state.building_generation_id = None
            db.flush()
        claimed = db.execute(update(KnowledgeGraphState).where(KnowledgeGraphState.id == "domain",
            KnowledgeGraphState.building_generation_id.is_(None)).values(building_generation_id=fence.graph_id, status="BUILDING"))
        if claimed.rowcount != 1:
            raise ValueError("Another publication acquired the graph builder")
        row.status, row.worker_token, row.building_generation_id = "PUBLISHING", fence.token, fence.graph_id
        row.error_message = None
        profile = get_active_model_profile("embedding", db)
        if not profile or not profile.enabled:
            raise ValueError("An enabled embedding profile is required for publication")
        document_id = row.target_document_id or row.published_document_id
        document = db.get(KnowledgeDocument, document_id)
        if not document:
            raise ValueError("Reserved knowledge document is missing")
        if row.operation in {"UPDATE", "DELETE"}:
            if (not document.active or document.review_status != "ACTIVE" or document.version != row.base_version
                    or document.lock_version != row.base_lock_version):
                raise ValueError("Target knowledge changed after approval")
        elif document.active:
            raise ValueError("The reserved document has already been published")
        candidate = normalize_candidate(db, json_loads(row.candidate_json, {}), row.content_kind)
        validate_candidate_evidence(db, row, candidate)
        if content_digest(row, candidate) != fence.digest:
            raise ValueError("Validated candidate differs from the approved bytes")
        documents, chunks, signature = corpus(db)
        snapshot = dict(document_id=document_id, before=fingerprint(document), candidate=candidate,
            documents=documents, chunks=chunks, signature=signature, operation=row.operation,
            old_version=document.version, old_lock=document.lock_version, profile_id=profile.id,
            profile_hash=profile_fingerprint(profile), old_vector=profile.active_embedding_generation_id,
            old_graph=state.active_generation_id, vector_id=new_id("EGEN"))
        db.commit()
        return snapshot


def build(fence, snapshot):
    candidate, document_id = snapshot["candidate"], snapshot["document_id"]
    replacements = [doc for doc in snapshot["documents"] if doc.id != document_id]
    by_document = defaultdict(list)
    for chunk in snapshot["chunks"]:
        if chunk.document_id != document_id:
            by_document[chunk.document_id].append(chunk)
    staged = []
    if snapshot["operation"] != "DELETE":
        replacement = KnowledgeDocument(id=document_id, active=True, review_status="ACTIVE",
            version=snapshot["old_version"] + 1, lock_version=snapshot["old_lock"] + 1,
            metadata_json=json_dumps(candidate["metadata"]),
            **{k: v for k, v in candidate.items() if k not in {"category_id", "metadata"}})
        replacements.append(replacement)
        with SessionLocal() as db:
            fence.check(db)
            staged = [KnowledgeChunk(id=new_id("CHK"), document_id=document_id, document_version=0,
                chunk_index=index, heading=heading, content=content, token_estimate=max(1, len(content) // 3),
                metadata_json=json_dumps({"title": replacement.title, "source_type": replacement.source_type,
                    "contribution_id": fence.id, "publication_generation": fence.graph_id}))
                for index, (heading, content) in enumerate(chunk_document(replacement.content))]
            if not staged:
                raise ValueError("Cannot publish empty knowledge")
            db.add_all(staged)
            db.commit()
        by_document[document_id] = staged
    fence.update(20, "Building private vectors for the exact approved contribution")
    with SessionLocal() as db:
        fence.check(db)
        profile = db.get(ModelProfile, snapshot["profile_id"])
        chunks = [chunk for group in by_document.values() for chunk in group]
        index_embeddings(db, profile, chunks, generation_id=snapshot["vector_id"], activate_if_missing=False,
            progress=lambda done, total: fence.update(20 + int(35 * done / max(1, total)), "Preparing private knowledge vectors"))
    fence.update(60, "Building the matching private knowledge graph")
    graph = stage_graph(SessionLocal, [doc for doc in replacements if doc.confidentiality in {"PUBLIC", "INTERNAL"}],
                        by_document, fence.graph_id, fence)
    return replacements, by_document, graph, staged


def publish(fence, snapshot, replacements, by_document, graph, staged):
    fence.raise_if_cancelled()
    with SessionLocal() as db:
        db.execute(update(KnowledgeContribution).where(KnowledgeContribution.id == fence.id).values(updated_at=utcnow()))
        row = fence.check(db)
        claimed = db.execute(update(KnowledgeGraphState).where(KnowledgeGraphState.id == "domain",
            KnowledgeGraphState.building_generation_id == fence.graph_id,
            KnowledgeGraphState.active_generation_id == snapshot["old_graph"]).values(status="BUILDING"))
        if claimed.rowcount != 1:
            raise ValueError("Knowledge graph publication changed")
        list(db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.id).with_for_update()))
        if corpus(db)[2] != snapshot["signature"]:
            raise ValueError("Published knowledge changed while preparing the contribution")
        document = db.get(KnowledgeDocument, snapshot["document_id"])
        if not document or fingerprint(document) != snapshot["before"]:
            raise ValueError("Target knowledge changed during publication")
        profile = get_active_model_profile("embedding", db)
        if not profile or profile_fingerprint(profile) != snapshot["profile_hash"]:
            raise ValueError("Embedding configuration changed during publication")
        candidate = snapshot["candidate"]
        if snapshot["operation"] != "DELETE":
            ids = [chunk.id for chunk in staged]
            moved = db.execute(update(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids),
                KnowledgeChunk.document_version == 0).values(document_version=snapshot["old_version"] + 1))
            if moved.rowcount != len(ids):
                raise ValueError("Staged knowledge chunks changed")
            for key, value in candidate.items():
                if key not in {"metadata", "category_id"}:
                    setattr(document, key, value)
            document.metadata_json = json_dumps({**candidate["metadata"], "contribution_id": row.id,
                "embedding_status": "INDEXED", "embedding_profile_id": profile.id,
                "embedding_generation_id": snapshot["vector_id"], "embedding_vector_count": len(staged)})
            set_document_category(db, document.id, candidate.get("category_id"))
            document.active, document.review_status = True, "ACTIVE"
        else:
            document.active, document.review_status = False, "ARCHIVED"
        document.version += 1
        document.lock_version += 1
        document.reviewed_by, document.reviewed_at = fence.reviewer, utcnow()
        document.review_comment = row.review_comment
        document.published_at = utcnow()
        bind_owner(db, document.id, row.owner_id)
        db.get(KnowledgeAccess, document.id).publisher_id = fence.reviewer
        db.flush()
        revision = create_document_revision(db, document, created_by=fence.reviewer,
                                            change_summary=f"Reviewed contribution {row.id}: {row.operation}")
        publication_id = new_id("KPUB")
        manifest = {"schema_version": 1, "publication_id": publication_id, "document_id": document.id,
            "document_version": document.version, "revision_id": revision.id, "operation": row.operation,
            "contribution_id": row.id, "approved_version": row.approved_version, "approved_hash": row.approved_hash,
            "content_hash": hashlib.sha256(document.content.encode()).hexdigest(),
            "embedding_profile_id": profile.id, "embedding_generation_id": snapshot["vector_id"],
            "graph_generation_id": fence.graph_id, "document_versions": {item.id: item.version for item in replacements},
            "chunk_ids": {key: [chunk.id for chunk in group] for key, group in by_document.items()}}
        db.add(KnowledgePublication(id=publication_id, document_id=document.id, document_version=document.version,
            published_by=fence.reviewer, manifest_json=json_dumps(manifest)))
        switched = db.execute(update(ModelProfile).where(ModelProfile.id == profile.id,
            ModelProfile.enabled.is_(True), ModelProfile.is_active.is_(True),
            ModelProfile.active_embedding_generation_id == snapshot["old_vector"]).values(
                active_embedding_generation_id=snapshot["vector_id"]))
        if switched.rowcount != 1:
            raise ValueError("Embedding generation changed before publication")
        state = db.get(KnowledgeGraphState, "domain")
        state.active_generation_id, state.building_generation_id, state.status = fence.graph_id, None, "READY"
        state.metadata_json, state.error_message = json_dumps(graph), None
        row.status, row.building_generation_id, row.worker_token = "PUBLISHED", None, None
        row.published_document_id = document.id
        if row.source_library_id:
            from app.services.workbench_library import publish_reviewed_conclusion
            publish_reviewed_conclusion(db, row)
        result = {"contribution_id": row.id, "document_id": document.id, "publication_id": publication_id,
            "version": document.version, "graph_generation_id": fence.graph_id, "embedding_generation_id": snapshot["vector_id"]}
        fence.ctx.complete_in_transaction(db, result, "Reviewed knowledge and both indexes published atomically")
        db.commit()
        return result


def publication_job(ctx, contribution_id, approved_version, approved_hash, reviewer):
    fence = ContributionFence(ctx, contribution_id, approved_version, approved_hash, reviewer)
    try:
        from app.services.job_progress import report_progress
        report_progress(ctx, 5, "正在核对已审批版本并准备发布", stage="核对发布审批", stage_index=1, stage_count=4)
        snapshot = prepare(fence)
        replacements, chunks, graph, staged = build(fence, snapshot)
        report_progress(ctx, 95, "正在原子发布已审批内容与索引", stage="发布并保存", stage_index=4, stage_count=4)
        return publish(fence, snapshot, replacements, chunks, graph, staged)
    except JobLeaseLostError:
        raise
    except Exception as error:
        with SessionLocal() as db:
            row = db.get(KnowledgeContribution, contribution_id)
            if row and row.worker_token == fence.token and row.publication_job_id == ctx.job_id:
                state = db.get(KnowledgeGraphState, "domain")
                if state and state.building_generation_id == fence.graph_id:
                    state.building_generation_id = None
                    state.status = "STALE" if state.active_generation_id else "NOT_BUILT"
                row.status, row.building_generation_id, row.worker_token = "APPROVED", None, None
                row.error_message = "Publication incomplete; approved content retained and previous indexes remain active"
                db.commit()
        if isinstance(error, JobCancelledError):
            raise
        raise ValueError("Knowledge contribution publication incomplete; previous generation retained") from None


def recover_abandoned_contributions(db) -> int:
    """Release only terminal publishers' own builds, preserving exact approval for retry.

    Called after the dispatcher reconciles abandoned leases, in its transaction.
    QUEUED/RUNNING jobs keep their generation and may resume with a fresh fence.
    """
    terminal = select(Job.id).where(Job.kind == "publish_knowledge_contribution",
                                   Job.status.in_(["FAILED", "CANCELLED", "DEAD_LETTER"]))
    rows = list(db.scalars(select(KnowledgeContribution).where(KnowledgeContribution.status == "PUBLISHING",
        KnowledgeContribution.publication_job_id.in_(terminal))))
    recovered = 0
    for row in rows:
        generation = row.building_generation_id
        changed = db.execute(update(KnowledgeContribution).where(
            KnowledgeContribution.id == row.id, KnowledgeContribution.status == "PUBLISHING",
            KnowledgeContribution.version == row.version,
            KnowledgeContribution.publication_job_id == row.publication_job_id,
            KnowledgeContribution.publication_job_id.in_(terminal),
            KnowledgeContribution.building_generation_id == generation,
            KnowledgeContribution.worker_token == row.worker_token).values(
                status="APPROVED", building_generation_id=None, worker_token=None, updated_at=utcnow(),
                error_message="Publication worker stopped; approved content retained for an explicit job retry"))
        if changed.rowcount != 1:
            continue
        if generation:
            db.execute(update(KnowledgeGraphState).where(KnowledgeGraphState.id == "domain",
                KnowledgeGraphState.building_generation_id == generation).values(building_generation_id=None,
                    status=case((KnowledgeGraphState.active_generation_id.is_not(None), "STALE"), else_="NOT_BUILT")))
        recovered += 1
    return recovered
