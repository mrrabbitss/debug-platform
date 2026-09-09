"""Publish a confirmed bundle, documents and both index pointers in one transaction."""
from collections import defaultdict
from sqlalchemy import select, update

from app.core.db import SessionLocal
from app.core.utils import json_loads, json_dumps, new_id, utcnow
from app.models import (KnowledgeDocument, KnowledgeChunk, KnowledgeEmbedding, KnowledgeGraphState,
    KnowledgePublication, ModelProfile, KnowledgeAccess)
from app.services.assistant_plan import validate_review, review_digest, validate_target
from app.services.assistant_sources import document_fingerprint
from app.services.assistant_state import (digest, locked_session, claim_worker, require_worker,
    require_admin_actor, release_generation)
from app.services.workbench import SOURCE_TYPES
from app.services.knowledge import chunk_document
from app.services.knowledge_publication import active_documents
from app.services.knowledge_release_graph import stage_graph
from app.services.knowledge_governance import create_document_revision
from app.services.knowledge_access import bind_owner
from app.services.knowledge_visibility import current_chunk_clause
from app.services.model_profiles import get_active_model_profile, MANAGED_LOCAL_PROVIDER
from app.services.retrieval_models import index_embeddings, _validate_profile_endpoint
from app.services.jobs import JobCancelledError, JobLeaseLostError


def profile_fingerprint(profile):
    return digest({key: getattr(profile, key) for key in ("id", "enabled", "is_active", "mode", "provider",
        "model_name", "base_url", "config_json", "api_key_ciphertext", "proxy_url_ciphertext",
        "active_embedding_generation_id")})


def corpus(db):
    documents = active_documents(db)
    chunks = list(db.scalars(select(KnowledgeChunk).join(KnowledgeDocument).where(current_chunk_clause(),
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE").order_by(KnowledgeChunk.id)))
    signature = digest({"documents": [(doc.id, document_fingerprint(doc)) for doc in documents],
        "chunks": [(c.id, c.document_id, c.document_version, digest(c.content), c.metadata_json) for c in chunks]})
    return documents, chunks, signature


def require_approval(db, row, value, ctx, version, reviewer, statuses):
    job = require_worker(db, row, value, ctx, version, statuses)
    require_admin_actor(db, reviewer)
    if (job.attempt != 1 or value.get("approved_by") != reviewer or not value.get("approved_at")
            or value.get("approved_digest") != review_digest(value)):
        raise ValueError("具体方案没有有效审批；请重新核对确认")


def metadata_for(document, operations, value, session_id):
    metadata = json_loads(document.metadata_json, {})
    paths = list(dict.fromkeys(path for op in operations for path in op["source_paths"]))
    uploaded = {item["path"] for item in value.get("files", [])}
    if any(path in uploaded for path in paths) or not document.content:
        old = metadata.get("bundle_manifest", [])
        manifest = value["bundle_manifest"]
        replaced = {item["path"] for item in manifest}
        metadata.update(bundle_id=session_id,
            source_paths=list(dict.fromkeys([*metadata.get("source_paths", []), *paths])),
            bundle_manifest=[item for item in old if item["path"] not in replaced] + manifest)
    last = operations[-1]
    metadata.update(problem_categories=last["categories"], knowledge_role=last["role"],
        assistant_session_id=session_id, assistant_operation_ids=[op["operation_id"] for op in operations])
    metadata.pop("assistant_reservation", None)
    metadata.pop("assistant_operation_id", None)
    return metadata


def make_candidates(db, operations, value, session_id):
    grouped = defaultdict(list)
    for op in operations:
        grouped[op.get("target_id") or op["new_id"]].append(op)
    candidates = []
    for key, items in grouped.items():
        last = items[-1]
        document = db.get(KnowledgeDocument, key)
        for op in items:
            validate_target(document, op)
        if not last.get("target_id"):
            if document is None:
                document = KnowledgeDocument(id=key, title=last["title"], content="", active=False,
                    review_status="DRAFT", confidentiality="INTERNAL", trust_level="MEDIUM",
                    metadata_json=json_dumps({"assistant_reservation": session_id,
                        "assistant_operation_id": last["operation_id"]}))
                db.add(document)
                db.flush()
            meta = json_loads(document.metadata_json, {})
            if (document.active or document.content or meta.get("assistant_reservation") != session_id
                    or meta.get("assistant_operation_id") != last["operation_id"]):
                raise ValueError("新增知识预留编号已被使用")
        metadata = metadata_for(document, items, value, session_id)
        replacement = KnowledgeDocument(id=document.id, title=last["title"], content=last["after"],
            source_type=SOURCE_TYPES[last["role"]], active=True, review_status="ACTIVE",
            version=document.version + 1, lock_version=document.lock_version + 1, trust_level="HIGH",
            confidentiality=document.confidentiality, device_type=document.device_type,
            device_model=document.device_model, firmware_range=document.firmware_range, module=document.module,
            metadata_json=json_dumps(metadata))
        candidates.append({"operations": items, "before_fingerprint": document_fingerprint(document),
                           "document": replacement})
    return candidates


class PublicationFence:
    def __init__(self, ctx, session_id, version, reviewer, graph_id):
        self.ctx, self.session_id, self.version = ctx, session_id, version
        self.reviewer, self.graph_id = reviewer, graph_id

    def check(self, db):
        row, value = locked_session(db, self.session_id)
        require_approval(db, row, value, self.ctx, self.version, self.reviewer, {"BUILDING"})
        state = db.get(KnowledgeGraphState, "domain")
        if not state or state.building_generation_id != self.graph_id or value.get("building_generation_id") != self.graph_id:
            raise JobLeaseLostError("发布构建已被取消或接管")
        profile = get_active_model_profile("embedding", db)
        if not profile:
            raise ValueError("Embedding模型已停用")
        if profile.mode == "api":
            if profile.provider != MANAGED_LOCAL_PROVIDER and value.get("model_egress_approved") is not True:
                raise JobCancelledError("模型授权已关闭")
            _validate_profile_endpoint(profile)
        return row, value

    def raise_if_cancelled(self):
        self.ctx.raise_if_cancelled()
        with SessionLocal() as db:
            self.check(db)

    def update(self, progress, message):
        self.raise_if_cancelled()
        self.ctx.update(progress, message)


def prepare(fence, vector_id):
    with SessionLocal() as db:
        row, value = locked_session(db, fence.session_id)
        claim_worker(db, row, value, fence.ctx, fence.version, {"APPROVED"})
        require_approval(db, row, value, fence.ctx, fence.version, fence.reviewer, {"APPROVED"})
        validate_review(db, row.id, value)
        documents, chunks, signature = corpus(db)
        profile = get_active_model_profile("embedding", db)
        if not profile:
            raise ValueError("请先启用Embedding模型")
        state = db.get(KnowledgeGraphState, "domain")
        if state is None:
            state = KnowledgeGraphState(id="domain", status="NOT_BUILT")
            db.add(state)
            db.flush()
        old_graph = state.active_generation_id
        claimed = db.execute(update(KnowledgeGraphState).where(KnowledgeGraphState.id == "domain",
            KnowledgeGraphState.building_generation_id.is_(None)).values(
                building_generation_id=fence.graph_id, status="BUILDING"))
        if claimed.rowcount != 1:
            raise ValueError("另一个发布任务正在构建")
        candidates = make_candidates(db, [op for op in value["plan"] if op["action"] != "skip"], value, row.id)
        value.update(status="BUILDING", building_generation_id=fence.graph_id)
        row.payload_json = json_dumps(value)
        db.flush()
        fence.check(db)
        snapshot = {"documents": documents, "chunks": chunks, "signature": signature, "candidates": candidates,
            "profile_id": profile.id, "profile_fingerprint": profile_fingerprint(profile),
            "old_vector": profile.active_embedding_generation_id, "old_graph": old_graph, "vector_id": vector_id}
        db.commit()
        return snapshot


def stage_chunks(fence, snapshot):
    candidates = snapshot["candidates"]
    candidate_ids = {item["document"].id for item in candidates}
    by_document = defaultdict(list)
    for chunk in snapshot["chunks"]:
        if chunk.document_id not in candidate_ids:
            by_document[chunk.document_id].append(chunk)
    with SessionLocal() as db:
        fence.check(db)
        for item in candidates:
            replacement = item["document"]
            pieces = chunk_document(replacement.content)
            if not pieces:
                raise ValueError("不能发布空白知识")
            for index, (heading, text) in enumerate(pieces):
                values = {"id": new_id("CHK"), "document_id": replacement.id, "chunk_index": index,
                    "heading": heading, "content": text, "token_estimate": max(1, len(text) // 3),
                    "metadata_json": json_dumps({"title": replacement.title, "source_type": replacement.source_type,
                        "assistant_generation_id": fence.graph_id})}
                # Version 0 is never visible. A stale worker's rows cannot later
                # appear when another publisher reuses the next document version.
                db.add(KnowledgeChunk(**values, document_version=0))
                # Remote vector payloads and graph metadata receive the final
                # version without prematurely changing the persisted chunk rows.
                by_document[replacement.id].append(KnowledgeChunk(**values, document_version=replacement.version))
        db.commit()
    return by_document


def stage_indexes(fence, snapshot, by_document):
    fence.update(25, "正在构建整组向量，现有知识继续可用")
    with SessionLocal() as db:
        profile = db.get(ModelProfile, snapshot["profile_id"])
        if profile_fingerprint(profile) != snapshot["profile_fingerprint"]:
            raise ValueError("索引配置已变化")
        chunks = [chunk for group in by_document.values() for chunk in group]
        index_embeddings(db, profile, chunks, generation_id=snapshot["vector_id"], activate_if_missing=False,
            progress=lambda done, total: fence.update(25 + int(30 * done / max(1, total)), "正在构建整组向量"))
    fence.raise_if_cancelled()
    changed = {item["document"].id: item["document"] for item in snapshot["candidates"]}
    replacements = [doc for doc in snapshot["documents"] if doc.id not in changed] + list(changed.values())
    graph = stage_graph(SessionLocal, [doc for doc in replacements if doc.confidentiality in {"PUBLIC", "INTERNAL"}],
        by_document, fence.graph_id, fence)
    return replacements, graph


def publish_documents(db, fence, snapshot, by_document, replacements):
    published = []
    for item in snapshot["candidates"]:
        replacement = item["document"]
        document = db.get(KnowledgeDocument, replacement.id)
        if not document or document_fingerprint(document) != item["before_fingerprint"]:
            raise ValueError("构建期间目标版本已变化")
        for op in item["operations"]:
            validate_target(document, op)
        chunk_ids = [chunk.id for chunk in by_document[document.id]]
        moved = db.execute(update(KnowledgeChunk).where(KnowledgeChunk.id.in_(chunk_ids),
            KnowledgeChunk.document_version == 0).values(document_version=replacement.version))
        if moved.rowcount != len(chunk_ids):
            raise ValueError("待发布分块已变化")
        for key in ("title", "content", "source_type", "version", "lock_version", "active", "review_status", "trust_level"):
            setattr(document, key, getattr(replacement, key))
        metadata = json_loads(replacement.metadata_json, {})
        metadata.update(embedding_status="INDEXED", embedding_generation_id=snapshot["vector_id"],
            embedding_profile_id=snapshot["profile_id"], embedding_vector_count=len(chunk_ids))
        document.metadata_json = json_dumps(metadata)
        document.reviewed_by = fence.reviewer
        document.published_at = document.reviewed_at = utcnow()
        bind_owner(db, document.id, fence.reviewer)
        access = db.get(KnowledgeAccess, document.id)
        if access and not access.publisher_id:
            access.publisher_id = fence.reviewer
        db.flush()
        revision = create_document_revision(db, document, created_by=fence.reviewer, change_summary="管理员确认知识助手具体变更清单")
        publication_id = new_id("KPUB")
        db.add(KnowledgePublication(id=publication_id, document_id=document.id, document_version=document.version,
            published_by=fence.reviewer, manifest_json=json_dumps({"schema_version": 1,
                "publication_id": publication_id, "document_id": document.id, "document_version": document.version,
                "content_hash": digest(document.content), "revision_id": revision.id,
                "embedding_profile_id": snapshot["profile_id"], "embedding_generation_id": snapshot["vector_id"],
                "graph_generation_id": fence.graph_id, "assistant_session_id": fence.session_id,
                "document_versions": {doc.id: doc.version for doc in replacements},
                "chunk_ids": {key: [chunk.id for chunk in chunks] for key, chunks in by_document.items()}})))
        published.append(document.id)
    return published


def publish(fence, snapshot, by_document, replacements, graph):
    fence.raise_if_cancelled()
    with SessionLocal() as db:
        row, value = fence.check(db)
        claimed = db.execute(update(KnowledgeGraphState).where(KnowledgeGraphState.id == "domain",
            KnowledgeGraphState.building_generation_id == fence.graph_id,
            KnowledgeGraphState.active_generation_id == snapshot["old_graph"]).values(status="BUILDING"))
        if claimed.rowcount != 1:
            raise ValueError("图谱切换冲突")
        list(db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.id).with_for_update()))
        if corpus(db)[2] != snapshot["signature"]:
            raise ValueError("构建期间已发布知识发生变化")
        validate_review(db, row.id, value)
        profile = get_active_model_profile("embedding", db)
        if profile:
            db.execute(select(ModelProfile).where(ModelProfile.id == profile.id).with_for_update())
            db.refresh(profile)
        if not profile or profile_fingerprint(profile) != snapshot["profile_fingerprint"]:
            raise ValueError("索引配置已变化")
        published = publish_documents(db, fence, snapshot, by_document, replacements)
        switched = db.execute(update(ModelProfile).where(ModelProfile.id == snapshot["profile_id"],
            ModelProfile.is_active.is_(True), ModelProfile.enabled.is_(True),
            ModelProfile.active_embedding_generation_id == snapshot["old_vector"]).values(
                active_embedding_generation_id=snapshot["vector_id"]))
        if switched.rowcount != 1:
            raise ValueError("向量索引切换冲突")
        state = db.get(KnowledgeGraphState, "domain")
        state.active_generation_id, state.building_generation_id, state.status = fence.graph_id, None, "READY"
        state.metadata_json, state.error_message = json_dumps(graph), None
        value.update(status="PUBLISHED", published_documents=published, building_generation_id=None)
        row.payload_json = json_dumps(value)
        result = {"session_id": fence.session_id, "documents": published, "graph_generation_id": fence.graph_id,
                  "embedding_generation_id": snapshot["vector_id"]}
        fence.ctx.complete_in_transaction(db, result, "知识变更、向量和图谱已同时发布")
        db.commit()
        return result


def failed(fence, error):
    with SessionLocal() as db:
        row, value = locked_session(db, fence.session_id)
        if (value.get("request_version") != fence.version or value.get("job_id") != fence.ctx.job_id
                or value.get("status") not in {"APPROVED", "BUILDING"}
                or (value.get("worker_token") and value.get("worker_token") != getattr(fence.ctx, "assistant_token", None))):
            return
        if isinstance(error, JobLeaseLostError):
            return
        release_generation(db, value)
        value.update(status="REVIEW", request_version=value["request_version"] + 1,
            error="发布未完成，原知识和索引保留；需要重新核对确认。")
        value["review_digest"] = review_digest(value)
        row.payload_json = json_dumps(value)
        db.commit()


def publication_job(ctx, session_id: str, request_version: int, reviewer: str):
    fence = PublicationFence(ctx, session_id, request_version, reviewer, new_id("KGEN"))
    try:
        snapshot = prepare(fence, new_id("EGEN"))
        by_document = stage_chunks(fence, snapshot)
        replacements, graph = stage_indexes(fence, snapshot, by_document)
        return publish(fence, snapshot, by_document, replacements, graph)
    except (JobCancelledError, JobLeaseLostError) as error:
        failed(fence, error)
        raise
    except Exception as error:
        failed(fence, error)
        raise ValueError("知识助手发布未完成；原版本保持可用，必须重新核对确认") from None
