import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.utils import json_loads
from app.models import CodeSymbol, KnowledgeChunk, KnowledgeDocument, Repository
from app.services.retrieval_models import (
    RetrievalModelError,
    candidate_count_for_reranker,
    embedding_scores,
    rerank_documents,
)


TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]*|[\u4e00-\u9fff]{1,4}|-?\d+")


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_PATTERN.findall(text)]


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
        case_id: str | None = None,
        device_type: str | None = None,
        module: str | None = None,
        top_k: int | None = None,
    ) -> list[RetrievalHit]:
        top_k = top_k or get_settings().retrieval_top_k
        with SessionLocal() as db:
            rows = db.execute(
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
                .where(KnowledgeDocument.active.is_(True))
            ).all()
            symbol_query = select(CodeSymbol)
            if case_id:
                symbol_query = (
                    symbol_query
                    .join(Repository, CodeSymbol.repository_id == Repository.id)
                    .where(Repository.case_id == case_id)
                )
            else:
                symbol_query = symbol_query.where(False)
            symbols = db.scalars(symbol_query.limit(5000)).all()

        docs: list[dict[str, Any]] = []
        for chunk, document in rows:
            if device_type and document.device_type and document.device_type not in {device_type, "OTHER"}:
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
                "id": symbol.id,
                "source_type": "code_symbol",
                "title": f"{symbol.kind} {symbol.name} — {symbol.file_path}:{symbol.line_start}",
                "content": symbol.code,
                "metadata": {
                    "repository_id": symbol.repository_id,
                    "file_path": symbol.file_path,
                    "line_start": symbol.line_start,
                    "line_end": symbol.line_end,
                    "module": symbol.module,
                },
            })
        if not docs:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        doc_tokens = [tokenize(doc["title"] + "\n" + doc["content"]) for doc in docs]
        n_docs = len(docs)
        avg_len = sum(len(tokens) for tokens in doc_tokens) / max(n_docs, 1)
        df = Counter()
        for tokens in doc_tokens:
            df.update(set(tokens))

        exact_terms = set(query_tokens)
        try:
            vector_scores = embedding_scores(
                query,
                {str(doc["id"]) for doc in docs if doc["source_type"] != "code_symbol"},
            )
        except RetrievalModelError:
            vector_scores = {}
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
            overlap = len(exact_terms.intersection(tokens)) / max(len(exact_terms), 1)
            title_tokens = set(tokenize(doc["title"]))
            title_bonus = len(exact_terms.intersection(title_tokens)) / max(len(exact_terms), 1)
            trust = str(doc["metadata"].get("trust_level", "MEDIUM")).upper()
            trust_bonus = {"HIGH": 0.25, "MEDIUM": 0.1, "LOW": 0.0}.get(trust, 0.05)
            vector_bonus = vector_scores.get(doc["id"], 0.0) * 2.0
            score = bm25 + overlap * 2.0 + title_bonus * 1.5 + trust_bonus + vector_bonus
            if score > 0:
                results.append(RetrievalHit(
                    evidence_id=doc["id"], source_type=doc["source_type"], title=doc["title"],
                    content=doc["content"], score=round(score, 6), metadata=doc["metadata"],
                ))
        candidate_count = candidate_count_for_reranker(max(top_k * 3, 20))
        candidates = sorted(results, key=lambda item: item.score, reverse=True)[:candidate_count]
        try:
            ranking = rerank_documents(
                query,
                [f"{item.title}\n{item.content}" for item in candidates],
                top_k,
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
