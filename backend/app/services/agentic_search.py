from collections import defaultdict
from time import perf_counter
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CodeSymbol, Repository
from app.services.agentic.fusion import (
    _balanced_candidate_pool as _balanced_candidate_pool,
    apply_dense_scores as _apply_dense_scores,
    apply_reranker as _apply_reranker,
    fuse_module_results as _fuse_module_results,
)
from app.services.agentic.planner import build_search_plan
from app.services.agent_trace import record_agent_run
from app.services.code_graph import search_code_graph
from app.services.commit_graph import search_commits, symbols_for_commit_paths
from app.services.knowledge_graph import (
    domain_graph_candidates,
    domain_graph_status,
)
from app.services.memory import (
    extract_memories_from_search,
    mark_memories_reused,
    memory_to_dict,
    search_memories,
)
from app.services.model_profiles import get_active_model_profile
from app.services.agentic.knowledge_candidates import knowledge_candidates as _knowledge_candidates


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
            (symbol.logical_id or symbol.id): symbol
            for symbol in db.scalars(select(CodeSymbol).where(
                CodeSymbol.repository_id == repository.id,
                CodeSymbol.generation_id
                == repository.active_graph_generation_id,
                or_(
                    CodeSymbol.logical_id.in_(node_ids),
                    CodeSymbol.id.in_(node_ids),
                ),
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
                    "symbol_id": symbol.logical_id or symbol.id,
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
                    "symbol_id": symbol.logical_id or symbol.id,
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


def agentic_search(
    db: Session,
    *,
    case_id: str,
    query: str,
    top_k: int = 12,
    max_hops: int = 2,
    requested_modules: list[str] | None = None,
    record_memory: bool = True,
    execution_mode: str = "deterministic",
    replay_of_run_id: str | None = None,
    created_by: str | None = None,
    joint_diagnostic_scope: bool = False,
    knowledge_view: list[str] | None = None,
) -> dict[str, Any]:
    from app.models import Case

    overall_started = perf_counter()
    case = db.get(Case, case_id)
    if not case:
        raise ValueError("Case not found")
    repositories = list(db.scalars(select(Repository).where(
        Repository.case_id == case_id
    )).all())
    graph_status = domain_graph_status(db, include_counts=False)
    from app.services.workbench import case_knowledge, case_graph_generation, case_category, matches_category
    pinned_knowledge = case_knowledge.get() is not None
    graph_compatible = not pinned_knowledge or case_graph_generation.get() == graph_status.get("active_generation_id")
    plan = build_search_plan(
        query,
        repository_count=len(repositories),
        requested_modules=requested_modules,
        max_hops=max_hops,
        domain_graph_available=bool(
            graph_status.get("active_generation_id") and graph_compatible
        ),
    )
    traces: list[dict[str, Any]] = []
    module_results: dict[str, list[dict[str, Any]]] = {}
    all_paths: list[dict[str, Any]] = []
    per_module_limit = max(top_k * 3, 20)
    indexed_code_repositories = [
        repository for repository in repositories
        if repository.active_graph_generation_id
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
                    device_type=None if joint_diagnostic_scope else case.device_type,
                    limit=per_module_limit,
                    joint_diagnostic_scope=joint_diagnostic_scope,
                    knowledge_view=knowledge_view,
                )
                paths: list[dict[str, Any]] = []
            elif module == "domain_graph":
                if graph_status.get("active_generation_id") and not knowledge_view and graph_compatible:
                    candidates, paths = domain_graph_candidates(
                        db,
                        query,
                        top_k=per_module_limit,
                        max_hops=max_hops,
                    )
                    from app.services.workbench_retrieval import scope_graph
                    candidates, paths = scope_graph(db, candidates, case)
                else:
                    candidates, paths = [], []
                    stage_status = "SKIPPED"
                    stage_reason = "固定知识版本使用原文检索与重排，避免混入后续发布的图谱" if pinned_knowledge else ("Personal revisions use lexical retrieval and reranking; shared graph is bypassed" if knowledge_view else "No active domain knowledge graph")
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
    ) or (
        "domain_graph" in plan["selected_modules"]
        and bool(graph_status.get("active_generation_id"))
        and graph_compatible and not knowledge_view
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
    if record_memory:
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
    stop_reason = (
        "NO_RESULTS"
        if not final_results
        else "COMPLETED_WITH_FALLBACK"
        if any(item.get("status") == "FAILED" for item in traces)
        else "COMPLETED"
    )
    result = {
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
            "knowledge_scope": (
                "GW_AP_JOINT" if joint_diagnostic_scope else case.device_type
            ),
        },
    }
    active_profiles = [
        profile
        for task_type in ("embedding", "reranker")
        if (profile := get_active_model_profile(task_type, db)) is not None
    ]
    evidence_ids = [str(item["evidence_id"]) for item in final_results]
    duration_ms = int((perf_counter() - overall_started) * 1000)
    run = record_agent_run(
        db,
        case_id=case_id,
        operation="agentic_search",
        execution_mode=execution_mode,
        input_summary={
            "case_id": case_id,
            "query": query,
            "top_k": top_k,
            "max_hops": max_hops,
            "modules": requested_modules,
            "joint_diagnostic_scope": joint_diagnostic_scope,
        },
        output_summary={
            "evidence_ids": evidence_ids,
            "returned": len(final_results),
            "path_count": len(all_paths),
            "stop_reason": stop_reason,
        },
        events=traces,
        evidence_ids=evidence_ids,
        stop_reason=stop_reason,
        approval_status="READ_ONLY_AUTO",
        duration_ms=duration_ms,
        resource_type="case",
        resource_id=case_id,
        model_name=", ".join(
            f"{profile.task_type}:{profile.model_name}" for profile in active_profiles
        ) or "deterministic-retrieval",
        model_config={
            "profiles": [
                {
                    "profile_id": profile.id,
                    "task_type": profile.task_type,
                    "mode": profile.mode,
                    "provider": profile.provider,
                    "model_name": profile.model_name,
                }
                for profile in active_profiles
            ],
            "top_k": top_k,
            "max_hops": max_hops,
            "joint_diagnostic_scope": joint_diagnostic_scope,
        },
        prompt_version="agentic-search-v2",
        replay_of_run_id=replay_of_run_id,
        replay_payload={
            "case_id": case_id,
            "query": query,
            "top_k": top_k,
            "max_hops": max_hops,
            "modules": requested_modules,
            "execution_mode": execution_mode,
            "joint_diagnostic_scope": joint_diagnostic_scope,
        },
        created_by=created_by,
        budget_ms=30_000,
    )
    result.update(run_id=run.id, stop_reason=stop_reason)
    return result
