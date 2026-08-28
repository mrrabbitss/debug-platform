from __future__ import annotations

from collections import defaultdict
import math
from typing import Any

from sqlalchemy.orm import Session

from app.services.model_profiles import get_active_model_profile
from app.services.retrieval_models import (
    RetrievalModelError,
    candidate_count_for_reranker,
    embed_texts,
    rerank_documents,
)


MODULE_WEIGHTS = {
    "knowledge": 1.0,
    "domain_graph": 0.95,
    "code": 1.0,
    "commit": 0.9,
    "memory": 0.9,
}


def fuse_module_results(
    module_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    fused: dict[tuple[str, str], dict[str, Any]] = {}
    for module, candidates in module_results.items():
        module_size = max(len(candidates), 1)
        module_weight = MODULE_WEIGHTS.get(module, 1.0)
        for rank, candidate in enumerate(candidates, start=1):
            key = (str(candidate["source_type"]), str(candidate["evidence_id"]))
            existing = fused.get(key)
            if existing is None:
                existing = {
                    **candidate,
                    "modules": [],
                    "module_ranks": {},
                    "module_score": 0.0,
                    "fusion_score": 0.0,
                }
                fused[key] = existing
            existing["modules"].append(module)
            existing["module_ranks"][module] = rank
            existing["fusion_score"] += module_weight / (60 + rank)
            existing["module_score"] = max(
                float(existing["module_score"]),
                module_weight * (module_size - rank + 1) / module_size,
            )
            existing["source_score"] = max(
                float(existing.get("source_score", 0.0)),
                float(candidate.get("source_score", 0.0)),
            )
            if candidate.get("paths"):
                existing["paths"] = [
                    *existing.get("paths", []),
                    *candidate["paths"],
                ]
    return sorted(
        fused.values(),
        key=lambda item: (item["fusion_score"], item["module_score"]),
        reverse=True,
    )


def _balanced_candidate_pool(
    candidates: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if len(candidates) <= limit:
        return candidates
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        modules = candidate.get("modules") or ["unknown"]
        by_module[str(modules[0])].append(candidate)
    ordered_modules = [module for module in MODULE_WEIGHTS if by_module.get(module)]
    ordered_modules.extend(
        module for module in by_module if module not in ordered_modules
    )
    selected: list[dict[str, Any]] = []
    offset = 0
    while len(selected) < limit:
        added = False
        for module in ordered_modules:
            module_candidates = by_module[module]
            if offset < len(module_candidates):
                selected.append(module_candidates[offset])
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
        offset += 1
    return selected


def apply_dense_scores(
    db: Session,
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile = get_active_model_profile("embedding", db)
    if not profile or not candidates:
        return candidates, {"status": "SKIPPED", "reason": "No active embedding profile"}
    selected = _balanced_candidate_pool(candidates, 80)
    for item in candidates:
        item["dense_score"] = None
        item["combined_score"] = item["fusion_score"] * 20
    try:
        query_vectors = embed_texts(profile, [query], purpose="agentic_search_query")
        if len(query_vectors) != 1 or not query_vectors[0]:
            raise ValueError("Embedding model returned no query vector")
        query_vector = query_vectors[0]
        document_vectors = embed_texts(
            profile,
            [f"{item['title']}\n{item['content'][:8000]}" for item in selected],
            purpose="agentic_search_candidates",
        )
        if len(document_vectors) != len(selected):
            raise ValueError("Embedding model returned an unexpected number of vectors")
        query_norm = math.sqrt(sum(float(value) ** 2 for value in query_vector))
        if query_norm == 0:
            raise ValueError("Embedding model returned a zero query vector")
        for item, vector in zip(selected, document_vectors, strict=True):
            if len(vector) != len(query_vector):
                continue
            vector_norm = math.sqrt(sum(float(value) ** 2 for value in vector))
            if vector_norm == 0:
                continue
            dot_product = sum(
                float(left) * float(right)
                for left, right in zip(query_vector, vector, strict=True)
            )
            dense_score = dot_product / (query_norm * vector_norm)
            item["dense_score"] = round(dense_score, 6)
            item["combined_score"] = item["fusion_score"] * 20 + dense_score * 2
        return sorted(
            candidates,
            key=lambda item: item["combined_score"],
            reverse=True,
        ), {
            "status": "COMPLETED",
            "profile_id": profile.id,
            "provider": profile.provider,
            "candidate_count": len(selected),
        }
    except (RetrievalModelError, ValueError, TypeError, IndexError) as exc:
        return candidates, {"status": "FAILED", "error": str(exc)}


def apply_reranker(
    db: Session,
    query: str,
    candidates: list[dict[str, Any]],
    top_k: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not candidates:
        return [], {"status": "SKIPPED", "reason": "No candidates"}
    profile = get_active_model_profile("reranker", db)
    if not profile or profile.provider == "disabled":
        return candidates[:top_k], {
            "status": "SKIPPED",
            "reason": "Reranker is disabled",
        }
    candidate_count = min(
        len(candidates),
        candidate_count_for_reranker(max(top_k * 3, 20), profile=profile),
    )
    selected = candidates[:candidate_count]
    try:
        ranking = rerank_documents(
            query,
            [f"{item['title']}\n{item['content'][:10000]}" for item in selected],
            top_k,
            profile=profile,
            purpose="agentic_search_candidates",
        )
    except (RetrievalModelError, ValueError, TypeError, IndexError) as exc:
        ranking = None
        status = {"status": "FAILED", "error": str(exc)}
    else:
        status = {
            "status": "COMPLETED" if ranking is not None else "SKIPPED",
            "candidate_count": candidate_count,
            "reason": "Reranker is disabled" if ranking is None else None,
        }
    if ranking is None:
        return selected[:top_k], status
    reranked: list[dict[str, Any]] = []
    for index, score in ranking:
        if 0 <= index < len(selected):
            selected[index]["reranker_score"] = round(float(score), 6)
            reranked.append(selected[index])
    if not reranked:
        return selected[:top_k], {
            "status": "FAILED",
            "error": "Reranker returned no valid candidate indexes",
        }
    return reranked[:top_k], status
