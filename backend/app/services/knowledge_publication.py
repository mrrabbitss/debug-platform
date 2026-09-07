"""Prepare private chunks/vectors/graph, then atomically publish the reviewed proposal."""
from collections import defaultdict
import hashlib

from sqlalchemy import delete, select, update

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (KnowledgeChunk, KnowledgeDocument, KnowledgeDraft, KnowledgeEmbedding,
                        KnowledgeGraphState, KnowledgePublication, ModelProfile)
from app.services.jobs import JobContext
from app.services.knowledge import chunk_document
from app.services.knowledge_governance import create_document_revision
from app.services.knowledge_graph import _source_signature
from app.services.knowledge_release_graph import stage_graph
from app.services.knowledge_taxonomy import set_document_category
from app.services.knowledge_visibility import current_chunk_clause
from app.services.model_profiles import get_active_model_profile
from app.services.retrieval_models import index_embeddings


def active_documents(db):
    return list(db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE",
    ).order_by(KnowledgeDocument.id)).all())


def require_publishable_proposal(draft, document, document_id, draft_version):
    if not draft or not document or draft.document_id != document_id:
        raise ValueError("Knowledge draft or source not found")
    if draft.version != draft_version or draft.status != "IN_REVIEW" or document.version != draft.base_version:
        raise ValueError("Reviewed proposal is stale; refresh and submit again")
    first_publication = not document.active and document.review_status == "IN_REVIEW"
    if not first_publication and (not document.active or document.review_status != "ACTIVE"):
        raise ValueError("Source publication was withdrawn")
    return first_publication


def publication_job(ctx: JobContext, document_id: str, draft_id: str, draft_version: int, reviewer: str) -> dict:
    graph_id = new_id("KGEN")
    build_version = None
    try:
        with SessionLocal() as db:
            draft = db.get(KnowledgeDraft, draft_id)
            document = db.get(KnowledgeDocument, document_id)
            first_publication = require_publishable_proposal(draft, document, document_id, draft_version)
            documents = active_documents(db)
            signature = _source_signature(documents)
            old_version, old_lock = document.version, document.lock_version
            profile = get_active_model_profile("embedding", db)
            if profile is None:
                raise ValueError("An active embedding profile is required before publication")
            profile_id, old_vector_id = profile.id, profile.active_embedding_generation_id
            vector_id = new_id("EGEN")
            graph_state = db.get(KnowledgeGraphState, "domain")
            if graph_state is None:
                graph_state = KnowledgeGraphState(id="domain", status="NOT_BUILT")
                db.add(graph_state)
                db.flush()
            if graph_state.building_generation_id:
                raise ValueError("Another graph or knowledge publication is building; retry after it completes")
            old_graph_id = graph_state.active_generation_id
            claimed = db.execute(update(KnowledgeGraphState).where(
                KnowledgeGraphState.id == "domain", KnowledgeGraphState.building_generation_id.is_(None),
            ).values(building_generation_id=graph_id, status="BUILDING"))
            if claimed.rowcount != 1:
                raise ValueError("Another publication claimed the graph builder")
            draft.status = "BUILDING"
            draft.publication_job_id = getattr(ctx, "job_id", None)
            draft.building_generation_id = graph_id
            draft.reviewed_by = reviewer
            snapshot = json_loads(draft.snapshot_json, {})
            db.commit()
            build_version = draft.version
        ctx.update(10, "Preparing a private knowledge revision; current publication remains online")
        replacement = KnowledgeDocument(
            id=document_id, version=old_version + 1, lock_version=old_lock + 1,
            active=True, review_status="ACTIVE", metadata_json=json_dumps(snapshot.get("metadata", {})),
            **{key: value for key, value in snapshot.items() if key not in {"metadata", "category_id"}},
        )
        with SessionLocal() as db:
            # Retry remnants belong only to the not-yet-published next version.
            staged = (KnowledgeChunk.document_id == document_id) & (KnowledgeChunk.document_version == old_version + 1)
            ids = select(KnowledgeChunk.id).where(staged)
            db.execute(delete(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id.in_(ids)))
            db.execute(delete(KnowledgeChunk).where(staged))
            chunks = [KnowledgeChunk(id=new_id("CHK"), document_id=document_id, document_version=old_version + 1,
                                     chunk_index=index, heading=heading, content=content, token_estimate=max(1, len(content) // 3),
                                     metadata_json=json_dumps({"title": replacement.title, "source_type": replacement.source_type}))
                      for index, (heading, content) in enumerate(chunk_document(replacement.content))]
            if not chunks:
                raise ValueError("Cannot publish empty knowledge")
            db.add_all(chunks)
            db.commit()
            profile = db.get(ModelProfile, profile_id)
            # A manifest owns an immutable vector generation. Never append staged
            # vectors to the currently active generation (including remote stores).
            to_index = [*db.scalars(select(KnowledgeChunk).join(KnowledgeDocument).where(
                current_chunk_clause(), KnowledgeDocument.active.is_(True),
                KnowledgeDocument.review_status == "ACTIVE", KnowledgeDocument.id != document_id,
            )).all(), *chunks]
            index_embeddings(db, profile, to_index, generation_id=vector_id, activate_if_missing=False)
            chunks_by_document = defaultdict(list)
            for chunk in db.scalars(select(KnowledgeChunk).join(KnowledgeDocument).where(
                current_chunk_clause(), KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE",
            )):
                chunks_by_document[chunk.document_id].append(chunk)
            chunks_by_document[document_id] = chunks
        ctx.update(50, "Embedding ready; compiling the matching method and domain graph snapshot")
        replacements = [replacement if item.id == document_id else item for item in documents]
        if first_publication:
            replacements.append(replacement)
        graph_documents = [item for item in replacements if item.confidentiality in {"PUBLIC", "INTERNAL"}]
        graph = stage_graph(SessionLocal, graph_documents, chunks_by_document, graph_id, ctx)
        ctx.raise_if_cancelled()
        with SessionLocal() as db:
            # Serialize pointer publication before reading the source snapshots.
            claimed = db.execute(update(KnowledgeGraphState).where(
                KnowledgeGraphState.id == "domain", KnowledgeGraphState.building_generation_id == graph_id,
                KnowledgeGraphState.active_generation_id == old_graph_id,
            ).values(status="BUILDING"))
            if claimed.rowcount != 1:
                raise ValueError("Graph generation changed before publication")
            list(db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.id).with_for_update()))
            draft = db.get(KnowledgeDraft, draft_id)
            document = db.get(KnowledgeDocument, document_id)
            profile = get_active_model_profile("embedding", db)
            graph_state = db.get(KnowledgeGraphState, "domain")
            if (not draft or draft.version != build_version or draft.status != "BUILDING"
                    or not document or document.version != old_version or document.lock_version != old_lock
                    or _source_signature(active_documents(db)) != signature):
                raise ValueError("Knowledge changed during preparation; current publication was preserved")
            if (not profile or profile.id != profile_id or profile.active_embedding_generation_id != old_vector_id
                    or not graph_state or graph_state.active_generation_id != old_graph_id
                    or graph_state.building_generation_id != graph_id):
                raise ValueError("Index generation changed during preparation; retry publication")
            for key, value in snapshot.items():
                if key not in {"metadata", "category_id"}:
                    setattr(document, key, value)
            metadata = {**snapshot.get("metadata", {}), "embedding_status": "INDEXED", "embedding_vector_count": len(chunks),
                        "embedding_generation_id": vector_id, "embedding_profile_id": profile_id}
            document.metadata_json = json_dumps(metadata)
            document.active = True
            document.review_status = "ACTIVE"
            document.version += 1
            document.lock_version += 1
            document.reviewed_by = reviewer
            document.reviewed_at = document.published_at = utcnow()
            document.review_comment = draft.review_comment
            from app.services.knowledge_access import bind_owner
            from app.models import KnowledgeAccess
            bind_owner(db, document.id, draft.created_by)
            access = db.get(KnowledgeAccess, document.id)
            if not access.publisher_id:
                access.publisher_id = access.owner_id or reviewer
            set_document_category(db, document_id, snapshot.get("category_id"))
            db.flush()
            revision = create_document_revision(db, document, created_by=draft.created_by, change_summary="Published reviewed proposal atomically")
            publication_id = new_id("KPUB")
            manifest = {
                "schema_version": 1, "publication_id": publication_id, "document_id": document_id,
                "revision_id": revision.id, "document_version": document.version,
                "content_hash": hashlib.sha256(document.content.encode()).hexdigest(),
                "embedding_profile_id": profile_id, "embedding_generation_id": vector_id,
                "graph_generation_id": graph_id,
                "document_versions": {item.id: item.version for item in replacements},
                "chunk_ids": {key: [chunk.id for chunk in value] for key, value in chunks_by_document.items()},
                "document_metadata_snapshot_hash": hashlib.sha256(json_dumps({item.id: json_loads(item.metadata_json, {}) for item in replacements}).encode()).hexdigest(),
            }
            db.add(KnowledgePublication(id=publication_id, document_id=document_id, document_version=document.version,
                                        manifest_json=json_dumps(manifest), published_by=reviewer))
            switched = db.execute(update(ModelProfile).where(
                ModelProfile.id == profile_id, ModelProfile.is_active.is_(True),
                ModelProfile.active_embedding_generation_id == old_vector_id,
            ).values(active_embedding_generation_id=vector_id))
            if switched.rowcount != 1:
                raise ValueError("Embedding generation changed before publication")
            graph_state.active_generation_id = graph_id
            graph_state.building_generation_id = None
            graph_state.status = "READY"
            graph_state.metadata_json = json_dumps({**graph, "published_at": utcnow().isoformat(), "publication_id": publication_id})
            graph_state.error_message = None
            draft.status = "PUBLISHED"
            draft.building_generation_id = None
            result = {"publication_id": publication_id, "document_id": document_id, "version": document.version,
                      "revision_id": revision.id, "graph_generation_id": graph_id, "chunks": len(chunks)}
            ctx.complete_in_transaction(db, result, "Knowledge revision, chunks, vectors, methods and graph published")
            db.commit()
            return result
    except Exception as error:
        with SessionLocal() as db:
            draft = db.get(KnowledgeDraft, draft_id)
            if draft and draft.status == "BUILDING" and draft.version == build_version:
                draft.status = "FAILED"
                draft.building_generation_id = None
                draft.review_comment = f"Build failed ({type(error).__name__}); previous publication remains active"
            state = db.get(KnowledgeGraphState, "domain")
            if state and state.building_generation_id == graph_id:
                state.building_generation_id = None
                state.status = "STALE" if state.active_generation_id else "NOT_BUILT"
                state.error_message = f"Knowledge publication failed: {type(error).__name__}"
            db.commit()
        raise


def recover_abandoned_publications(db) -> int:
    """Expired workers cannot leave a permanent BUILDING latch or publish partial data."""
    from app.models import Job
    recovered = 0
    drafts = list(db.scalars(select(KnowledgeDraft).where(KnowledgeDraft.status == "BUILDING")))
    for draft in drafts:
        job = db.get(Job, draft.publication_job_id) if draft.publication_job_id else None
        if job and job.status in {"RUNNING", "CANCEL_REQUESTED"}:
            continue
        # Pre-upgrade BUILDING proposals have no job link: retain them while an
        # active publication job still explicitly targets this exact draft.
        if not draft.publication_job_id:
            active = db.scalars(select(Job).where(Job.kind == "publish_knowledge_revision",
                                                Job.status.in_(["RUNNING", "CANCEL_REQUESTED"])))
            if any(json_loads(item.input_json, {}).get("draft_id") == draft.id for item in active):
                continue
        generation = draft.building_generation_id
        changed = db.execute(update(KnowledgeDraft).where(KnowledgeDraft.id == draft.id,
            KnowledgeDraft.status == "BUILDING", KnowledgeDraft.version == draft.version).values(
                status="FAILED", version=draft.version + 1, building_generation_id=None,
                review_comment="Publication interrupted; online knowledge retained. Submit again to rebuild."))
        if changed.rowcount != 1:
            continue
        if generation:
            state = db.get(KnowledgeGraphState, "domain")
            if state and state.building_generation_id == generation:
                state.building_generation_id = None
                state.status = "STALE" if state.active_generation_id else "NOT_BUILT"
        recovered += 1
    return recovered
