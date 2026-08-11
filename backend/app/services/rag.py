import math
import re
from collections import Counter
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_loads
from app.models import CodeSymbol, KnowledgeChunk, KnowledgeDocument, Repository
from app.services.model_profiles import get_active_model_profile
from app.services.retrieval_models import (
    RetrievalModelError,
    candidate_count_for_reranker,
    embedding_search,
    rerank_documents,
)


TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]*|[\u4e00-\u9fff]{1,4}|-?\d+")
GENERAL_KNOWLEDGE_DEVICE_TYPES = frozenset({"GENERAL", "OTHER"})


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


def knowledge_matches_device_type(
    document_device_type: str | None,
    requested_device_type: str | None,
) -> bool:
    """Keep shared knowledge available to every concrete device type."""
    if not requested_device_type or not document_device_type:
        return True
    document_scope = document_device_type.strip().upper()
    requested_scope = requested_device_type.strip().upper()
    return document_scope == requested_scope or document_scope in GENERAL_KNOWLEDGE_DEVICE_TYPES


@dataclass
class RetrievalHit:
    evidence_id: str
    source_type: str
    title: str
    content: str
    score: float
    metadata: dict[str, Any]


class LocalHybridRetriever:
    """Dependency-light BM25 + character/token overlap retrieval.

    It intentionally keeps exact error codes, paths and symbols competitive with natural-language matches.
    """

    def search(
        self,
        query: str,
        *,
        db: Session | None = None,
        case_id: str | None = None,
        device_type: str | None = None,
        module: str | None = None,
        top_k: int | None = None,
        include_code_symbols: bool = True,
        apply_models: bool = True,
    ) -> list[RetrievalHit]:
        top_k = top_k or get_settings().retrieval_top_k
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        unique_query = set(query_tokens)
        search_terms = sorted(
            {token for token in query_tokens if len(token) >= 2},
            key=len,
            reverse=True,
        )[:12]
        candidate_pool = max(top_k * 8, 80)
        dense_scores: dict[str, float] = {}
        session_context = nullcontext(db) if db is not None else SessionLocal()
        with session_context as active_db:
            if apply_models:
                try:
                    dense_scores = embedding_search(
                        query,
                        candidate_pool,
                        db=active_db,
                    )
                except RetrievalModelError:
                    dense_scores = {}

            knowledge_query = (
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .where(
                    KnowledgeDocument.active.is_(True),
                    KnowledgeDocument.review_status == "ACTIVE",
                )
            )
            if search_terms:
                knowledge_conditions = []
                for term in search_terms:
                    pattern = f"%{term}%"
                    knowledge_conditions.extend([
                        KnowledgeDocument.title.ilike(pattern),
                        KnowledgeChunk.heading.ilike(pattern),
                        KnowledgeChunk.content.ilike(pattern),
                    ])
                knowledge_query = knowledge_query.where(
                    or_(*knowledge_conditions)
                )
            rows = list(active_db.execute(
                knowledge_query.limit(min(candidate_pool * 20, 5_000))
            ).all())
            row_map = {chunk.id: (chunk, document) for chunk, document in rows}
            missing_dense_ids = set(dense_scores).difference(row_map)
            if missing_dense_ids:
                dense_rows = active_db.execute(
                    select(KnowledgeChunk, KnowledgeDocument)
                    .join(
                        KnowledgeDocument,
                        KnowledgeChunk.document_id == KnowledgeDocument.id,
                    )
                    .where(
                        KnowledgeDocument.active.is_(True),
                        KnowledgeDocument.review_status == "ACTIVE",
                        KnowledgeChunk.id.in_(missing_dense_ids),
                    )
                ).all()
                for chunk, document in dense_rows:
                    row_map[chunk.id] = (chunk, document)
            rows = list(row_map.values())

            symbol_query = select(CodeSymbol)
            if case_id and include_code_symbols:
                symbol_query = (
                    symbol_query
                    .join(Repository, CodeSymbol.repository_id == Repository.id)
                    .where(
                        Repository.case_id == case_id,
                        CodeSymbol.generation_id
                        == Repository.active_graph_generation_id,
                    )
                )
                if search_terms:
                    symbol_conditions = []
                    for term in search_terms:
                        pattern = f"%{term}%"
                        symbol_conditions.extend([
                            CodeSymbol.name.ilike(pattern),
                            CodeSymbol.file_path.ilike(pattern),
                            CodeSymbol.signature.ilike(pattern),
                            CodeSymbol.code.ilike(pattern),
                        ])
                    symbol_query = symbol_query.where(
                        or_(*symbol_conditions)
                    )
            else:
                symbol_query = symbol_query.where(False)
            symbols = active_db.scalars(
                symbol_query.limit(min(candidate_pool * 10, 2_000))
            ).all()

        docs: list[dict[str, Any]] = []
        for chunk, document in rows:
            if not knowledge_matches_device_type(document.device_type, device_type):
                continue
            if module and document.module and document.module.upper() != module.upper():
                # Soft filter: retain protocol/history documents without a module restriction.
                if document.source_type not in {"historical_bug", "protocol", "builtin_rule"}:
                    continue
            docs.append({
                "id": chunk.id,
                "source_type": document.source_type,
                "title": f"{document.title}{' / ' + chunk.heading if chunk.heading else ''}",
                "content": chunk.content,
                "metadata": {
                    "document_id": document.id,
                    "device_type": document.device_type,
                    "module": document.module,
                    "trust_level": document.trust_level,
                    **json_loads(document.metadata_json, {}),
                },
            })
        for symbol in symbols:
            docs.append({
                "id": symbol.logical_id or symbol.id,
                "source_type": "code_symbol",
                "title": f"{symbol.kind} {symbol.name} — {symbol.file_path}:{symbol.line_start}",
                "content": symbol.code,
                "metadata": {
                    "revision_id": symbol.id,
                    "repository_id": symbol.repository_id,
                    "file_path": symbol.file_path,
                    "line_start": symbol.line_start,
                    "line_end": symbol.line_end,
                    "module": symbol.module,
                },
            })
        if not docs:
            return []

        doc_tokens = [tokenize(doc["title"] + "\n" + doc["content"]) for doc in docs]
        n_docs = len(docs)
        avg_len = sum(len(tokens) for tokens in doc_tokens) / max(n_docs, 1)
        df = Counter()
        for tokens in doc_tokens:
            df.update(set(tokens))

        results: list[RetrievalHit] = []
        k1, b = 1.5, 0.75
        for doc, tokens in zip(docs, doc_tokens, strict=True):
            tf = Counter(tokens)
            bm25 = 0.0
            for term in query_tokens:
                freq = tf.get(term, 0)
                if not freq:
                    continue
                idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
                denominator = freq + k1 * (1 - b + b * len(tokens) / max(avg_len, 1))
                bm25 += idf * (freq * (k1 + 1) / denominator)
            overlap = len(unique_query.intersection(tokens)) / max(len(unique_query), 1)
            title_tokens = set(tokenize(doc["title"]))
            title_bonus = len(unique_query.intersection(title_tokens)) / max(len(unique_query), 1)
            trust = str(doc["metadata"].get("trust_level", "MEDIUM")).upper()
            trust_bonus = {"HIGH": 0.25, "MEDIUM": 0.1, "LOW": 0.0}.get(trust, 0.05)
            vector_bonus = dense_scores.get(doc["id"], 0.0) * 2.0
            relevance = bm25 + overlap * 2.0 + title_bonus * 1.5 + vector_bonus
            if relevance > 0:
                score = relevance + trust_bonus
                results.append(RetrievalHit(
                    evidence_id=doc["id"], source_type=doc["source_type"], title=doc["title"],
                    content=doc["content"], score=round(score, 6), metadata=doc["metadata"],
                ))
        candidates = sorted(
            results,
            key=lambda item: item.score,
            reverse=True,
        )[:candidate_pool]
        if not apply_models:
            return candidates[:top_k]
        reranker_profile = (
            get_active_model_profile("reranker", db)
            if db is not None
            else None
        )
        candidate_count = candidate_count_for_reranker(
            max(top_k * 3, 20),
            profile=reranker_profile,
        )
        candidates = candidates[:candidate_count]
        try:
            if db is None:
                ranking = rerank_documents(
                    query,
                    [f"{item.title}\n{item.content}" for item in candidates],
                    top_k,
                )
            else:
                ranking = (
                    rerank_documents(
                        query,
                        [f"{item.title}\n{item.content}" for item in candidates],
                        top_k,
                        profile=reranker_profile,
                    )
                    if reranker_profile is not None
                    else None
                )
        except RetrievalModelError:
            ranking = None
        if ranking is None:
            return candidates[:top_k]
        reranked: list[RetrievalHit] = []
        for index, reranker_score in ranking:
            if index < 0 or index >= len(candidates):
                continue
            item = candidates[index]
            item.metadata = {
                **item.metadata,
                "hybrid_score": item.score,
                "reranker_score": reranker_score,
            }
            item.score = round(reranker_score, 6)
            reranked.append(item)
        return reranked[:top_k]


retriever = LocalHybridRetriever()
