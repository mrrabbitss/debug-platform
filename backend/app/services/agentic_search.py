from collections import defaultdict
import math
from time import perf_counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CodeSymbol, Repository
from app.services.code_graph import search_code_graph
from app.services.commit_graph import search_commits, symbols_for_commit_paths
from app.services.memory import (
    extract_memories_from_search,
    mark_memories_reused,
    memory_to_dict,
    search_memories,
)
from app.services.model_profiles import get_active_model_profile
from app.services.rag import retriever, tokenize
from app.services.retrieval_models import (
    RetrievalModelError,
    candidate_count_for_reranker,
    embed_texts,
    rerank_documents,
)


SEARCH_MODULES = {"knowledge", "code", "commit", "memory"}
CODE_INTENT_TERMS = {
    "代码", "函数", "方法", "调用", "引用", "继承", "实现", "接口", "类", "宏",
    "文件", "源码", "堆栈", "崩溃", "定位", "symbol", "function", "call", "reference",
    "inherit", "implement", "class", "interface", "source", "stack", "crash",
}
COMMIT_INTENT_TERMS = {
    "commit", "提交", "修改", "变更", "引入", "回归", "版本", "历史", "修复记录",
    "何时", "谁改", "regression", "change", "introduced", "history", "blame", "fix",
}
MEMORY_INTENT_TERMS = {
    "以前", "之前", "类似", "经验", "历史案例", "失败", "复用", "曾经",
    "previous", "similar", "memory", "experience", "failed",
}


def _query_signals(query: str) -> set[str]:
    lower = query.lower()
    signals = set(tokenize(query))
    for term in CODE_INTENT_TERMS | COMMIT_INTENT_TERMS | MEMORY_INTENT_TERMS:
        if term in lower:
            signals.add(term)
    return signals


def build_search_plan(
    query: str,
    *,
    repository_count: int,
    requested_modules: list[str] | None,
    max_hops: int,
) -> dict[str, Any]:
    signals = _query_signals(query)
    if requested_modules is not None:
        modules = [
            module for module in requested_modules
            if module in SEARCH_MODULES
        ]
        if modules:
            rationale = ["使用调用方明确指定的检索模块"]
        else:
            modules = ["knowledge", "memory"]
            rationale = ["未选择有效模块，回退到知识库和记忆检索"]
    else:
        modules = ["knowledge", "memory"]
        rationale = ["知识与经验是诊断检索的默认第一跳"]
        if repository_count and signals.intersection(CODE_INTENT_TERMS):
            modules.append("code")
            rationale.append("检测到代码定位/调用关系意图，启用代码图谱")
        if repository_count and signals.intersection(COMMIT_INTENT_TERMS):
            if "code" not in modules:
                modules.append("code")
            modules.append("commit")
            rationale.append("检测到变更、回归或历史意图，启用 Commit → 文件 → 代码路径")
        if signals.intersection(MEMORY_INTENT_TERMS):
            rationale.append("检测到历史经验意图，提高记忆模块优先级")
    modules = list(dict.fromkeys(modules))
    return {
        "selected_modules": modules,
        "intent_signals": sorted(signals.intersection(
            CODE_INTENT_TERMS | COMMIT_INTENT_TERMS | MEMORY_INTENT_TERMS
        )),
        "max_hops": max_hops,
        "algorithms": [
            "BM25",
            "dense_embedding",
            "reciprocal_rank_fusion",
            "reranker",
            "graph_multi_hop",
        ],
        "rationale": rationale,
    }


def _knowledge_candidates(
    db: Session,
    query: str,
    *,
    case_id: str,
    device_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    hits = retriever.search(
        query,
        db=db,
        case_id=case_id,
        device_type=device_type,
        top_k=limit,
        include_code_symbols=False,
    )
    return [
        {
            "evidence_id": hit.evidence_id,
            "source_type": hit.source_type,
            "title": hit.title,
            "content": hit.content,
            "source_score": hit.score,
            "metadata": hit.metadata,
            "paths": [],
        }
        for hit in hits
    ]


def _memory_candidates(
    db: Session,
    query: str,
    *,
    case_id: str,
    limit: int,
) -> list[dict[str, Any]]:
    results = search_memories(db, query, case_id=case_id, limit=limit)
    candidates: list[dict[str, Any]] = []
    for memory, score in results:
        payload = memory_to_dict(memory, score)
        candidates.append({
            "evidence_id": memory.id,
            "source_type": f"memory_{memory.memory_type.lower()}",
            "title": memory.title,
            "content": memory.content,
            "source_score": score,
            "metadata": payload,
            "paths": [],
        })
    return candidates


def _code_candidates(
    db: Session,
    query: str,
    repositories: list[Repository],
    *,
    max_hops: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    all_paths: list[dict[str, Any]] = []
    for repository in repositories:
        result = search_code_graph(
            db,
            repository.id,
            query,
            max_hops=max_hops,
            limit=limit,
        )
        node_ids = {node["id"] for node in result["nodes"]}
        symbol_map = {
            symbol.id: symbol
            for symbol in db.scalars(select(CodeSymbol).where(
                CodeSymbol.id.in_(node_ids)
            ))
        } if node_ids else {}
        path_by_target: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for path in result["paths"]:
            path_by_target[str(path["to"])].append(path)
            all_paths.append({
                "path_type": "code_graph",
                "repository_id": repository.id,
                **path,
            })
        for node in result["nodes"][:limit]:
            symbol = symbol_map.get(node["id"])
            content = (
                symbol.code[:8000]
                if symbol
                else f"{node.get('signature') or ''}\n{node['file_path']}"
            )
            candidates.append({
                "evidence_id": node["id"],
                "source_type": "code_symbol",
                "title": (
                    f"{node['kind']} {node['name']} — "
                    f"{node['file_path']}:{node['line_start']}"
                ),
                "content": content,
                "source_score": float(node["score"]),
                "metadata": {
                    **node,
                    "repository_id": repository.id,
                    "repository_name": repository.name,
                },
                "paths": path_by_target.get(node["id"], []),
            })
    return candidates, all_paths


def _commit_candidates(
    db: Session,
    query: str,
    repositories: list[Repository],
    *,
    limit: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates: list[dict[str, Any]] = []
    paths: list[dict[str, Any]] = []
    for repository in repositories:
        commits = search_commits(db, repository.id, query, limit=limit)
        for commit in commits:
            file_paths = {item["file_path"] for item in commit["files"]}
            symbols = symbols_for_commit_paths(
                db,
                repository.id,
                file_paths,
                limit=100,
            )
            symbol_summaries = [
                {
                    "symbol_id": symbol.id,
                    "name": symbol.name,
                    "kind": symbol.kind,
                    "file_path": symbol.file_path,
                    "line_start": symbol.line_start,
                }
                for symbol in symbols
            ]
            file_paths_explained = [
                {
                    "path_type": "query_commit_file",
                    "query": query,
                    "commit_id": commit["evidence_id"],
                    "commit_hash": commit["commit_hash"],
                    "change_id": item["change_id"],
                    "change_type": item["change_type"],
                    "file_path": item["file_path"],
                }
                for item in commit["files"][:100]
            ]
            symbol_paths = [
                {
                    "path_type": "query_commit_file_code",
                    "query": query,
                    "commit_id": commit["evidence_id"],
                    "commit_hash": commit["commit_hash"],
                    "file_path": symbol.file_path,
                    "symbol_id": symbol.id,
                }
                for symbol in symbols[:50]
            ]
            commit_paths = [*file_paths_explained, *symbol_paths]
            paths.extend(commit_paths)
            candidates.append({
                "evidence_id": commit["evidence_id"],
                "source_type": "commit",
                "title": f"{commit['commit_hash'][:12]} {commit['subject']}",
                "content": (
                    f"{commit['subject']}\n{commit['body']}\n"
                    + "\n".join(item["file_path"] for item in commit["files"][:100])
                )[:10000],
                "source_score": float(commit["score"]),
                "metadata": {
                    **commit,
                    "repository_id": repository.id,
                    "repository_name": repository.name,
                    "symbols": symbol_summaries,
                },
                "paths": commit_paths,
            })
    return candidates, paths


def _fuse_module_results(
    module_results: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    fused: dict[tuple[str, str], dict[str, Any]] = {}
    for module, candidates in module_results.items():
        for rank, candidate in enumerate(candidates, start=1):
            key = (str(candidate["source_type"]), str(candidate["evidence_id"]))
            existing = fused.get(key)
            if existing is None:
                existing = {
                    **candidate,
                    "modules": [],
                    "module_ranks": {},
                    "fusion_score": 0.0,
                }
                fused[key] = existing
            existing["modules"].append(module)
            existing["module_ranks"][module] = rank
            existing["fusion_score"] += 1.0 / (60 + rank)
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
        key=lambda item: (item["fusion_score"], item["source_score"]),
        reverse=True,
    )


def _apply_dense_scores(
    db: Session,
    query: str,
    candidates: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    profile = get_active_model_profile("embedding", db)
    if not profile or not candidates:
        return candidates, {"status": "SKIPPED", "reason": "No active embedding profile"}
    selected = candidates[:80]
    for item in candidates:
        item["dense_score"] = None
        item["combined_score"] = item["fusion_score"] * 20
    try:
        query_vectors = embed_texts(
            profile,
            [query],
            purpose="agentic_search_query",
        )
        if len(query_vectors) != 1 or not query_vectors[0]:
            raise ValueError("Embedding model returned no query vector")
        query_vector = query_vectors[0]
        document_vectors = embed_texts(
            profile,
            [
                f"{item['title']}\n{item['content'][:8000]}"
                for item in selected
            ],
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


def _apply_reranker(
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


def agentic_search(
    db: Session,
    *,
    case_id: str,
    query: str,
    top_k: int = 12,
    max_hops: int = 2,
    requested_modules: list[str] | None = None,
) -> dict[str, Any]:
    from app.models import Case

    case = db.get(Case, case_id)
    if not case:
        raise ValueError("Case not found")
    repositories = list(db.scalars(select(Repository).where(
        Repository.case_id == case_id
    )).all())
    plan = build_search_plan(
        query,
        repository_count=len(repositories),
        requested_modules=requested_modules,
        max_hops=max_hops,
    )
    traces: list[dict[str, Any]] = []
    module_results: dict[str, list[dict[str, Any]]] = {}
    all_paths: list[dict[str, Any]] = []
    per_module_limit = max(top_k * 3, 20)
    indexed_code_repositories = [
        repository for repository in repositories
        if repository.graph_status == "INDEXED"
    ]
    indexed_commit_repositories = [
        repository for repository in repositories
        if repository.commit_graph_status == "INDEXED"
    ]

    for module in plan["selected_modules"]:
        started = perf_counter()
        try:
            stage_status = "COMPLETED"
            stage_reason = None
            if module == "knowledge":
                candidates = _knowledge_candidates(
                    db,
                    query,
                    case_id=case_id,
                    device_type=case.device_type,
                    limit=per_module_limit,
                )
                paths: list[dict[str, Any]] = []
            elif module == "memory":
                candidates = _memory_candidates(
                    db,
                    query,
                    case_id=case_id,
                    limit=per_module_limit,
                )
                paths = []
            elif module == "code":
                if indexed_code_repositories:
                    candidates, paths = _code_candidates(
                        db,
                        query,
                        indexed_code_repositories,
                        max_hops=max_hops,
                        limit=per_module_limit,
                    )
                else:
                    candidates, paths = [], []
                    stage_status = "SKIPPED"
                    stage_reason = "当前案例没有已完成索引的代码图谱"
            elif module == "commit":
                if indexed_commit_repositories:
                    candidates, paths = _commit_candidates(
                        db,
                        query,
                        indexed_commit_repositories,
                        limit=per_module_limit,
                    )
                else:
                    candidates, paths = [], []
                    stage_status = "SKIPPED"
                    stage_reason = "当前案例没有可用的 Commit 图谱"
            else:
                continue
            module_results[module] = candidates
            all_paths.extend(paths)
            traces.append({
                "stage": module,
                "status": stage_status,
                "candidate_count": len(candidates),
                "duration_ms": int((perf_counter() - started) * 1000),
                **({"reason": stage_reason} if stage_reason else {}),
            })
        except Exception as exc:
            module_results[module] = []
            traces.append({
                "stage": module,
                "status": "FAILED",
                "error": str(exc)[:2000],
                "duration_ms": int((perf_counter() - started) * 1000),
            })

    graph_stage_available = (
        "code" in plan["selected_modules"] and bool(indexed_code_repositories)
    ) or (
        "commit" in plan["selected_modules"] and bool(indexed_commit_repositories)
    )
    traces.append({
        "stage": "graph_multi_hop",
        "status": "COMPLETED" if graph_stage_available else "SKIPPED",
        "candidate_count": len(all_paths),
        "duration_ms": 0,
        **(
            {}
            if graph_stage_available
            else {"reason": "未选择图谱模块，或没有已完成索引的图谱"}
        ),
    })
    fusion_started = perf_counter()
    fused = _fuse_module_results(module_results)
    traces.append({
        "stage": "reciprocal_rank_fusion",
        "status": "COMPLETED",
        "candidate_count": len(fused),
        "duration_ms": int((perf_counter() - fusion_started) * 1000),
    })
    dense_started = perf_counter()
    fused, dense_trace = _apply_dense_scores(db, query, fused)
    traces.append({
        "stage": "dense_embedding",
        **dense_trace,
        "duration_ms": int((perf_counter() - dense_started) * 1000),
    })
    rerank_started = perf_counter()
    final_results, reranker_trace = _apply_reranker(db, query, fused, top_k)
    traces.append({
        "stage": "reranker",
        **reranker_trace,
        "duration_ms": int((perf_counter() - rerank_started) * 1000),
    })
    reused_memory_ids = {
        str(item["evidence_id"])
        for item in final_results
        if str(item["source_type"]).startswith("memory_")
    }
    mark_memories_reused(db, reused_memory_ids)
    extract_memories_from_search(
        db,
        case,
        query=query,
        plan=plan,
        traces=traces,
        results=final_results,
        path_count=len(all_paths),
    )
    db.commit()
    return {
        "case_id": case_id,
        "query": query,
        "plan": plan,
        "trace": traces,
        "results": final_results,
        "paths": all_paths[:500],
        "summary": {
            "repositories": len(repositories),
            "module_candidates": {
                module: len(items) for module, items in module_results.items()
            },
            "fused_candidates": len(fused),
            "returned": len(final_results),
        },
    }
