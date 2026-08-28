import hashlib
import math
import re
import uuid
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from functools import lru_cache
from pathlib import Path
from time import perf_counter
from typing import Any

import httpx
from openai import OpenAI
from sklearn.feature_extraction.text import HashingVectorizer
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT, get_settings
from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id
from app.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEmbedding,
    ModelProfile,
)
from app.services.audit import record_model_egress
from app.services.model_profiles import (
    get_active_model_profile,
    get_profile_api_key,
    validate_model_endpoint,
)


HASHING_VECTOR_SIZE = 384
_hashing_vectorizer = HashingVectorizer(
    n_features=HASHING_VECTOR_SIZE,
    analyzer="char_wb",
    ngram_range=(3, 5),
    alternate_sign=False,
    norm="l2",
)


class RetrievalModelError(RuntimeError):
    pass


def resolve_local_model_reference(model_name: str) -> str:
    """Resolve repository-relative model directories without changing Hub IDs."""
    value = model_name.strip()
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    candidate = (PROJECT_ROOT / path).resolve()
    if candidate.exists() or value.replace("\\", "/").startswith("models/"):
        return str(candidate)
    return value


@lru_cache
def _qdrant_client():
    settings = get_settings()
    if not settings.qdrant_url:
        return None
    try:
        from qdrant_client import QdrantClient

        return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)
    except Exception:
        return None


def _qdrant_collection(
    profile: ModelProfile,
    generation_id: str | None = None,
) -> str:
    suffix = re.sub(r"[^a-zA-Z0-9_-]+", "_", profile.id).strip("_").lower()
    fingerprint = hashlib.sha256(
        f"{profile.model_name}\n{profile.config_json}".encode("utf-8")
    ).hexdigest()[:10]
    base = f"{get_settings().qdrant_collection}_{suffix}_{fingerprint}"
    if not generation_id or generation_id == "legacy":
        return base[:200]
    generation_suffix = re.sub(
        r"[^a-zA-Z0-9_-]+",
        "_",
        generation_id,
    ).strip("_").lower()
    return f"{base}_{generation_suffix}"[:200]


def _mirror_vectors_to_qdrant(
    profile: ModelProfile,
    chunks: Sequence[KnowledgeChunk],
    vectors: Sequence[Sequence[float]],
    generation_id: str,
) -> None:
    client = _qdrant_client()
    if not client or not chunks or not vectors:
        return
    try:
        from qdrant_client.models import Distance, PointStruct, VectorParams

        collection = _qdrant_collection(profile, generation_id)
        try:
            exists = client.collection_exists(collection)
        except AttributeError:
            exists = any(item.name == collection for item in client.get_collections().collections)
        if not exists:
            client.create_collection(
                collection,
                vectors_config=VectorParams(size=len(vectors[0]), distance=Distance.COSINE),
            )
        points = [
            PointStruct(
                id=str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    (
                        f"{profile.id}:{chunk.id}"
                        if generation_id == "legacy"
                        else f"{profile.id}:{generation_id}:{chunk.id}"
                    ),
                )),
                vector=list(vector),
                payload={
                    "chunk_id": chunk.id,
                    "document_id": chunk.document_id,
                    "generation_id": generation_id,
                },
            )
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        client.upsert(collection_name=collection, points=points, wait=True)
    except Exception:
        # SQLite vectors remain authoritative; an unavailable optional Qdrant must not block indexing.
        return


def _delete_qdrant_generation(
    profile: ModelProfile,
    generation_id: str | None,
) -> None:
    if not generation_id:
        return
    client = _qdrant_client()
    if not client:
        return
    try:
        collection = _qdrant_collection(profile, generation_id)
        if client.collection_exists(collection):
            client.delete_collection(collection)
    except Exception:
        return


def _qdrant_scores(
    profile: ModelProfile,
    query_vector: list[float],
    limit: int,
    *,
    generation_id: str,
    chunk_ids: set[str] | None = None,
) -> dict[str, float]:
    client = _qdrant_client()
    if not client:
        return {}
    try:
        collection = _qdrant_collection(profile, generation_id)
        query_filter = None
        if chunk_ids:
            from qdrant_client.models import FieldCondition, Filter, MatchAny

            query_filter = Filter(must=[
                FieldCondition(
                    key="chunk_id",
                    match=MatchAny(any=sorted(chunk_ids)),
                )
            ])
        try:
            response = client.query_points(
                collection_name=collection,
                query=query_vector,
                limit=limit,
                with_payload=True,
                query_filter=query_filter,
            )
            points = response.points
        except AttributeError:
            points = client.search(
                collection_name=collection,
                query_vector=query_vector,
                limit=limit,
                with_payload=True,
                query_filter=query_filter,
            )
        return {
            str((point.payload or {}).get("chunk_id", point.id)): float(point.score)
            for point in points
        }
    except Exception:
        return {}


def _normalize(vector: Sequence[float]) -> list[float]:
    values = [float(value) for value in vector]
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0:
        return values
    return [value / norm for value in values]


def _local_model_runtime_error(kind: str, model_name: str, exc: Exception) -> RetrievalModelError:
    detail = str(exc)
    if getattr(exc, "winerror", None) == 1114 or "WinError 1114" in detail:
        return RetrievalModelError(
            f"Local {kind} native runtime failed to initialize (WinError 1114). "
            "The standard and portable platform runtimes intentionally exclude Torch and "
            "sentence-transformers. Keep native model execution in a separate model service "
            "and connect it through an API profile; do not install it into the platform runtime."
        )
    return RetrievalModelError(f"Local {kind} model {model_name!r} failed: {detail}")


@lru_cache(maxsize=3)
def _load_sentence_transformer(model_name: str, device: str):
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RetrievalModelError(
            "In-process local Embedding is not included in the standard or portable runtime. "
            "Use the built-in Hashing Embedding or configure an approved Embedding API profile."
        ) from exc
    try:
        return SentenceTransformer(model_name, device=device)
    except Exception as exc:
        raise _local_model_runtime_error("embedding", model_name, exc) from exc


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str, device: str, instruction: str):
    try:
        from sentence_transformers import CrossEncoder
    except ImportError as exc:
        raise RetrievalModelError(
            "In-process local Reranker is not included in the standard or portable runtime. "
            "Keep Reranker disabled or configure an approved Reranker API profile."
        ) from exc
    try:
        prompt_options = (
            {"prompts": {"diagnosis": instruction}, "default_prompt_name": "diagnosis"}
            if instruction
            else {}
        )
        return CrossEncoder(model_name, device=device, **prompt_options)
    except Exception as exc:
        raise _local_model_runtime_error("reranker", model_name, exc) from exc


def embed_texts(
    profile: ModelProfile,
    texts: list[str],
    purpose: str = "embedding",
) -> list[list[float]]:
    if not texts:
        return []
    config = json_loads(profile.config_json, {})
    if profile.provider == "hashing":
        return _hashing_vectorizer.transform(texts).toarray().astype(float).tolist()
    if profile.provider == "sentence_transformers":
        device = str(config.get("device") or "cpu")
        model_name = resolve_local_model_reference(profile.model_name)
        model = _load_sentence_transformer(model_name, device)
        model_inputs = texts
        query_instruction = str(config.get("query_instruction") or "").strip()
        if query_instruction and purpose.endswith("_query"):
            model_inputs = [f"{query_instruction}{text}" for text in texts]
        try:
            vectors = model.encode(
                model_inputs,
                batch_size=max(1, min(int(config.get("batch_size") or 16), 100)),
                normalize_embeddings=bool(config.get("normalize", True)),
                show_progress_bar=False,
            )
        except Exception as exc:
            raise _local_model_runtime_error("embedding", model_name, exc) from exc
        raw_vectors = vectors.tolist() if hasattr(vectors, "tolist") else vectors
        return [_normalize(vector) for vector in raw_vectors]
    if profile.provider == "openai_compatible":
        started = perf_counter()
        try:
            validate_model_endpoint(profile.base_url or "")
            client = OpenAI(
                api_key=get_profile_api_key(profile),
                base_url=profile.base_url,
                timeout=float(config.get("timeout_seconds") or 120),
                max_retries=int(config.get("max_retries") or 2),
            )
            request: dict[str, Any] = {"model": profile.model_name, "input": texts}
            if config.get("dimension"):
                request["dimensions"] = int(config["dimension"])
            response = client.embeddings.create(**request)
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors = [_normalize(item.embedding) for item in ordered]
        except Exception as exc:
            record_model_egress(
                profile,
                task_type="embedding",
                purpose=purpose,
                request_items=len(texts),
                request_chars=sum(len(text) for text in texts),
                duration_ms=int((perf_counter() - started) * 1000),
                outcome="FAILED",
                error_type=type(exc).__name__,
            )
            raise RetrievalModelError(f"Embedding API request failed: {exc}") from exc
        usage_object = getattr(response, "usage", None)
        record_model_egress(
            profile,
            task_type="embedding",
            purpose=purpose,
            request_items=len(texts),
            request_chars=sum(len(text) for text in texts),
            duration_ms=int((perf_counter() - started) * 1000),
            outcome="SUCCESS",
            usage={
                "prompt_tokens": getattr(usage_object, "prompt_tokens", None),
                "total_tokens": getattr(usage_object, "total_tokens", None),
            },
        )
        return vectors
    raise RetrievalModelError(f"Unsupported embedding provider: {profile.provider}")


def index_embeddings(
    db: Session,
    profile: ModelProfile,
    chunks: Sequence[KnowledgeChunk],
    progress: Callable[[int, int], None] | None = None,
    *,
    generation_id: str | None = None,
    activate_if_missing: bool = True,
) -> int:
    generation_id = (
        generation_id
        or profile.active_embedding_generation_id
        or new_id("EGEN")
    )
    should_activate = (
        activate_if_missing
        and profile.active_embedding_generation_id is None
    )
    config = json_loads(profile.config_json, {})
    batch_size = max(1, min(int(config.get("batch_size") or 16), 100))
    completed = 0
    try:
        for start in range(0, len(chunks), batch_size):
            batch = list(chunks[start:start + batch_size])
            vectors = embed_texts(
                profile,
                [chunk.content for chunk in batch],
                purpose="knowledge_index",
            )
            if len(vectors) != len(batch):
                raise RetrievalModelError(
                    "Embedding model returned an unexpected number of vectors"
                )
            chunk_ids = [chunk.id for chunk in batch]
            db.execute(delete(KnowledgeEmbedding).where(
                KnowledgeEmbedding.profile_id == profile.id,
                KnowledgeEmbedding.generation_id == generation_id,
                KnowledgeEmbedding.chunk_id.in_(chunk_ids),
            ))
            for chunk, vector in zip(batch, vectors, strict=True):
                db.add(KnowledgeEmbedding(
                    id=new_id("VEC"),
                    chunk_id=chunk.id,
                    profile_id=profile.id,
                    generation_id=generation_id,
                    dimension=len(vector),
                    vector_json=json_dumps(vector),
                ))
            # The model work happens before this short write transaction, so a
            # failed batch never deletes its last usable vector.
            db.commit()
            _mirror_vectors_to_qdrant(
                profile,
                batch,
                vectors,
                generation_id,
            )
            completed += len(batch)
            if progress:
                progress(completed, len(chunks))
        if should_activate:
            profile.active_embedding_generation_id = generation_id
            db.commit()
            db.execute(delete(KnowledgeEmbedding).where(
                KnowledgeEmbedding.profile_id == profile.id,
                KnowledgeEmbedding.generation_id != generation_id,
            ))
            db.commit()
    except Exception:
        db.rollback()
        if should_activate:
            db.execute(delete(KnowledgeEmbedding).where(
                KnowledgeEmbedding.profile_id == profile.id,
                KnowledgeEmbedding.generation_id == generation_id,
            ))
            db.commit()
            _delete_qdrant_generation(profile, generation_id)
        raise
    return completed


def index_active_embeddings(db: Session, chunks: Sequence[KnowledgeChunk]) -> int:
    profile = get_active_model_profile("embedding", db)
    if not profile:
        return 0
    return index_embeddings(db, profile, chunks)


def reindex_all_embeddings(
    db: Session,
    profile: ModelProfile,
    progress: Callable[[int, int], None] | None = None,
    on_publish: Callable[[str, int], None] | None = None,
) -> int:
    chunks = list(db.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.document_id, KnowledgeChunk.chunk_index)).all())
    previous_generation = profile.active_embedding_generation_id
    generation_id = new_id("EGEN")
    try:
        count = index_embeddings(
            db,
            profile,
            chunks,
            progress,
            generation_id=generation_id,
            activate_if_missing=False,
        )
        expected_generation = (
            ModelProfile.active_embedding_generation_id.is_(None)
            if previous_generation is None
            else ModelProfile.active_embedding_generation_id
            == previous_generation
        )
        published = db.execute(
            update(ModelProfile)
            .where(
                ModelProfile.id == profile.id,
                expected_generation,
            )
            .values(active_embedding_generation_id=generation_id)
        )
        if published.rowcount != 1:
            raise RetrievalModelError(
                "Embedding rebuild was superseded by another generation"
            )
        if on_publish:
            on_publish(generation_id, count)
        db.commit()
    except Exception:
        db.rollback()
        db.execute(delete(KnowledgeEmbedding).where(
            KnowledgeEmbedding.profile_id == profile.id,
            KnowledgeEmbedding.generation_id == generation_id,
        ))
        db.commit()
        _delete_qdrant_generation(profile, generation_id)
        raise

    try:
        db.execute(delete(KnowledgeEmbedding).where(
            KnowledgeEmbedding.profile_id == profile.id,
            KnowledgeEmbedding.generation_id != generation_id,
        ))
        db.commit()
    except Exception:
        # Publication is already durable. Stale generations are invisible and
        # can be reclaimed by the next successful rebuild.
        db.rollback()
    if previous_generation and previous_generation != generation_id:
        _delete_qdrant_generation(profile, previous_generation)
    return count


def ensure_builtin_embedding_index(db: Session) -> int:
    profile = get_active_model_profile("embedding", db)
    if not profile or profile.provider != "hashing":
        return 0
    indexed_chunk_ids = select(KnowledgeEmbedding.chunk_id).where(
        KnowledgeEmbedding.profile_id == profile.id,
        KnowledgeEmbedding.generation_id == profile.active_embedding_generation_id,
    )
    missing = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.id.not_in(indexed_chunk_ids))).all())
    return index_embeddings(db, profile, missing) if missing else 0


def embedding_index_status(db: Session) -> dict[str, Any]:
    profile = get_active_model_profile("embedding", db)
    chunk_count = int(db.scalar(select(func.count(KnowledgeChunk.id))) or 0)
    vector_count = 0
    if profile:
        vector_count = int(db.scalar(
            select(func.count(KnowledgeEmbedding.id)).where(
                KnowledgeEmbedding.profile_id == profile.id,
                KnowledgeEmbedding.generation_id
                == profile.active_embedding_generation_id,
            )
        ) or 0)
    return {
        "profile_id": profile.id if profile else None,
        "profile_name": profile.name if profile else None,
        "provider": profile.provider if profile else None,
        "generation_id": (
            profile.active_embedding_generation_id if profile else None
        ),
        "chunk_count": chunk_count,
        "vector_count": vector_count,
        "complete": vector_count >= chunk_count,
    }


def embedding_scores(
    query: str,
    chunk_ids: set[str],
    *,
    db: Session | None = None,
) -> dict[str, float]:
    if not chunk_ids:
        return {}
    session_context = nullcontext(db) if db is not None else SessionLocal()
    with session_context as active_db:
        profile = get_active_model_profile("embedding", active_db)
        if not profile or not profile.active_embedding_generation_id:
            return {}
        query_vector = embed_texts(
            profile,
            [query],
            purpose="case_retrieval_query",
        )[0]
        qdrant_limit = min(len(chunk_ids), 100)
        qdrant = _qdrant_scores(
            profile,
            query_vector,
            max(qdrant_limit, 1),
            generation_id=profile.active_embedding_generation_id,
            chunk_ids=chunk_ids,
        )
        if qdrant:
            return qdrant
        rows = active_db.execute(
            select(KnowledgeEmbedding.chunk_id, KnowledgeEmbedding.dimension, KnowledgeEmbedding.vector_json)
            .where(
                KnowledgeEmbedding.profile_id == profile.id,
                KnowledgeEmbedding.generation_id
                == profile.active_embedding_generation_id,
                KnowledgeEmbedding.chunk_id.in_(chunk_ids),
            )
        ).all()
        if not rows:
            return {}
    scores: dict[str, float] = {}
    for chunk_id, dimension, vector_json in rows:
        if dimension != len(query_vector):
            continue
        vector = json_loads(vector_json, [])
        if len(vector) != len(query_vector):
            continue
        scores[chunk_id] = sum(left * float(right) for left, right in zip(query_vector, vector, strict=True))
    return scores


def embedding_search(
    query: str,
    limit: int,
    *,
    db: Session | None = None,
) -> dict[str, float]:
    """Return dense top-K without loading every knowledge document body.

    Qdrant performs the bounded nearest-neighbour query when configured. The
    SQLite fallback scans only compact vector rows and keeps the top results in
    memory, so retrieval no longer needs to materialize every document first.
    """
    if limit <= 0:
        return {}
    session_context = nullcontext(db) if db is not None else SessionLocal()
    with session_context as active_db:
        profile = get_active_model_profile("embedding", active_db)
        if not profile or not profile.active_embedding_generation_id:
            return {}
        query_vector = embed_texts(
            profile,
            [query],
            purpose="case_retrieval_query",
        )[0]
        qdrant = _qdrant_scores(
            profile,
            query_vector,
            min(max(limit * 8, limit), 1000),
            generation_id=profile.active_embedding_generation_id,
        )
        if qdrant:
            searchable_ids = set(active_db.scalars(
                select(KnowledgeChunk.id)
                .join(
                    KnowledgeDocument,
                    KnowledgeChunk.document_id == KnowledgeDocument.id,
                )
                .where(
                    KnowledgeChunk.id.in_(set(qdrant)),
                    KnowledgeDocument.active.is_(True),
                    KnowledgeDocument.review_status == "ACTIVE",
                )
            ).all())
            filtered_qdrant = {
                chunk_id: score
                for chunk_id, score in qdrant.items()
                if chunk_id in searchable_ids
            }
            if filtered_qdrant:
                return dict(list(filtered_qdrant.items())[:limit])
        rows = active_db.execute(
            select(
                KnowledgeEmbedding.chunk_id,
                KnowledgeEmbedding.dimension,
                KnowledgeEmbedding.vector_json,
            )
            .join(
                KnowledgeChunk,
                KnowledgeEmbedding.chunk_id == KnowledgeChunk.id,
            )
            .join(
                KnowledgeDocument,
                KnowledgeChunk.document_id == KnowledgeDocument.id,
            )
            .where(
                KnowledgeEmbedding.profile_id == profile.id,
                KnowledgeEmbedding.generation_id
                == profile.active_embedding_generation_id,
                KnowledgeDocument.active.is_(True),
                KnowledgeDocument.review_status == "ACTIVE",
            )
        ).all()
    ranked: list[tuple[str, float]] = []
    for chunk_id, dimension, vector_json in rows:
        if dimension != len(query_vector):
            continue
        vector = json_loads(vector_json, [])
        if len(vector) != len(query_vector):
            continue
        score = sum(
            left * float(right)
            for left, right in zip(query_vector, vector, strict=True)
        )
        ranked.append((chunk_id, score))
    ranked.sort(key=lambda item: item[1], reverse=True)
    return dict(ranked[:limit])


def rerank_documents(
    query: str,
    documents: list[str],
    top_n: int,
    profile: ModelProfile | None = None,
    purpose: str = "retrieval_candidates",
) -> list[tuple[int, float]] | None:
    if not documents:
        return []
    selected = profile or get_active_model_profile("reranker")
    if not selected or selected.provider == "disabled":
        return None
    profile = selected
    config = json_loads(profile.config_json, {})
    top_n = max(1, min(top_n, len(documents)))
    if profile.provider == "sentence_transformers":
        device = str(config.get("device") or "cpu")
        instruction = str(config.get("instruction") or "").strip()[:2000]
        model_name = resolve_local_model_reference(profile.model_name)
        model = _load_cross_encoder(model_name, device, instruction)
        try:
            scores = model.predict(
                [(query, document) for document in documents],
                batch_size=max(1, min(int(config.get("batch_size") or 8), 100)),
                show_progress_bar=False,
            )
            raw_scores = scores.tolist() if hasattr(scores, "tolist") else scores
            values = [float(value) for value in raw_scores]
        except Exception as exc:
            raise _local_model_runtime_error("reranker", model_name, exc) from exc
        return sorted(enumerate(values), key=lambda item: item[1], reverse=True)[:top_n]
    if profile.provider == "qwen_rerank_api":
        started = perf_counter()
        try:
            validate_model_endpoint(profile.base_url or "")
        except ValueError as exc:
            raise RetrievalModelError(str(exc)) from exc
        endpoint = (profile.base_url or "").rstrip("/")
        if not endpoint.endswith("/reranks"):
            endpoint += "/reranks"
        payload = {
            "model": profile.model_name,
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "instruct": config.get(
                "instruction",
                "Given a network troubleshooting query, retrieve passages that help diagnose and solve it.",
            ),
        }
        try:
            response = httpx.post(
                endpoint,
                headers={"Authorization": f"Bearer {get_profile_api_key(profile)}"},
                json=payload,
                timeout=float(config.get("timeout_seconds") or 120),
            )
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            ranking = [
                (int(item["index"]), float(item.get("relevance_score", item.get("score", 0.0))))
                for item in results[:top_n]
            ]
        except Exception as exc:
            record_model_egress(
                profile,
                task_type="reranker",
                purpose=purpose,
                request_items=len(documents) + 1,
                request_chars=len(query) + sum(len(document) for document in documents),
                duration_ms=int((perf_counter() - started) * 1000),
                outcome="FAILED",
                error_type=type(exc).__name__,
            )
            raise RetrievalModelError(f"Reranker API request failed: {exc}") from exc
        record_model_egress(
            profile,
            task_type="reranker",
            purpose=purpose,
            request_items=len(documents) + 1,
            request_chars=len(query) + sum(len(document) for document in documents),
            duration_ms=int((perf_counter() - started) * 1000),
            outcome="SUCCESS",
        )
        return ranking
    raise RetrievalModelError(f"Unsupported reranker provider: {profile.provider}")


def candidate_count_for_reranker(
    default: int,
    profile: ModelProfile | None = None,
) -> int:
    profile = profile or get_active_model_profile("reranker")
    if not profile or profile.provider == "disabled":
        return default
    config = json_loads(profile.config_json, {})
    return max(default, min(int(config.get("candidate_count") or 30), 100))
