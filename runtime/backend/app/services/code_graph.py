from collections import Counter, deque
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.utils import json_loads
from app.models import CodeRelation, CodeSymbol, Repository
from app.services.rag import tokenize


def _symbol_to_node(
    symbol: CodeSymbol,
    *,
    score: float = 0.0,
    hop: int = 0,
) -> dict[str, Any]:
    return {
        "id": symbol.logical_id or symbol.id,
        "revision_id": symbol.id,
        "node_type": "code_symbol",
        "kind": symbol.kind,
        "name": symbol.name,
        "file_path": symbol.file_path,
        "line_start": symbol.line_start,
        "line_end": symbol.line_end,
        "signature": symbol.signature,
        "module": symbol.module,
        "score": round(score, 6),
        "hop": hop,
        "metadata": json_loads(symbol.metadata_json, {}),
    }


def _relation_to_edge(relation: CodeRelation, *, hop: int = 0) -> dict[str, Any]:
    evidence = json_loads(relation.evidence_json, {})
    return {
        "id": relation.logical_id or relation.id,
        "revision_id": relation.id,
        "source": (
            evidence.get("source_evidence_id")
            or relation.source_symbol_id
        ),
        "target": (
            evidence.get("target_evidence_id")
            or relation.target_symbol_id
        ),
        "target_name": relation.target_name,
        "relation_type": relation.relation_type,
        "confidence": relation.confidence,
        "hop": hop,
        "evidence": evidence,
    }


def _lexical_symbol_scores(symbols: list[CodeSymbol], query: str) -> list[tuple[CodeSymbol, float]]:
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    results: list[tuple[CodeSymbol, float]] = []
    unique_query = set(query_tokens)
    for symbol in symbols:
        name_tokens = set(tokenize(symbol.name))
        path_tokens = set(tokenize(symbol.file_path))
        body_tokens = tokenize(
            f"{symbol.signature or ''}\n{symbol.module or ''}\n{symbol.code[:12000]}"
        )
        body_counts = Counter(body_tokens)
        score = 0.0
        normalized_query = query.strip().lower()
        if normalized_query == symbol.name.lower():
            score += 8.0
        elif normalized_query in symbol.name.lower():
            score += 4.0
        score += len(unique_query.intersection(name_tokens)) * 2.5
        score += len(unique_query.intersection(path_tokens)) * 1.2
        score += sum(min(body_counts.get(token, 0), 3) * 0.15 for token in unique_query)
        if score > 0:
            results.append((symbol, score))
    return sorted(results, key=lambda item: item[1], reverse=True)


def search_code_graph(
    db: Session,
    repository_id: str,
    query: str,
    *,
    max_hops: int = 2,
    limit: int = 20,
) -> dict[str, Any]:
    repository = db.get(Repository, repository_id)
    if not repository:
        raise ValueError("Repository not found")
    generation_id = repository.active_graph_generation_id
    if not generation_id:
        return {
            "repository_id": repository_id,
            "query": query,
            "generation_id": None,
            "nodes": [],
            "edges": [],
            "paths": [],
            "stats": {
                "seed_count": 0,
                "expanded_hops": 0,
                "candidate_count": 0,
            },
        }
    query_tokens = sorted(
        {token for token in tokenize(query) if len(token) >= 2},
        key=len,
        reverse=True,
    )[:12]
    candidate_query = select(CodeSymbol).where(
        CodeSymbol.repository_id == repository_id,
        CodeSymbol.generation_id == generation_id,
    )
    if query_tokens:
        token_conditions = []
        for token in query_tokens:
            pattern = f"%{token}%"
            token_conditions.extend([
                CodeSymbol.name.ilike(pattern),
                CodeSymbol.file_path.ilike(pattern),
                CodeSymbol.module.ilike(pattern),
                CodeSymbol.signature.ilike(pattern),
                CodeSymbol.code.ilike(pattern),
            ])
        candidate_query = candidate_query.where(or_(*token_conditions))
    symbols = list(db.scalars(
        candidate_query
        .order_by(CodeSymbol.file_path, CodeSymbol.line_start)
        .limit(5_000)
    ).all())
    ranked = _lexical_symbol_scores(symbols, query)
    seeds = ranked[:max(3, min(limit, 20))]
    if not seeds:
        return {
            "repository_id": repository_id,
            "query": query,
            "generation_id": generation_id,
            "nodes": [],
            "edges": [],
            "paths": [],
            "stats": {
                "seed_count": 0,
                "expanded_hops": 0,
                "candidate_count": len(symbols),
            },
        }

    symbol_map = {symbol.id: symbol for symbol in symbols}
    score_map = {symbol.id: score for symbol, score in seeds}
    hop_map = {symbol.id: 0 for symbol, _ in seeds}
    parent_edge: dict[str, CodeRelation] = {}
    queue = deque(symbol.id for symbol, _ in seeds)
    expanded: set[str] = set()
    edge_map: dict[str, CodeRelation] = {}
    while queue:
        symbol_id = queue.popleft()
        current_hop = hop_map[symbol_id]
        if symbol_id in expanded or current_hop >= max_hops:
            continue
        expanded.add(symbol_id)
        relations = list(db.scalars(select(CodeRelation).where(
            CodeRelation.repository_id == repository_id,
            CodeRelation.generation_id == generation_id,
            or_(
                CodeRelation.source_symbol_id == symbol_id,
                CodeRelation.target_symbol_id == symbol_id,
            ),
        ).limit(1000)).all())
        related_ids = {
            related_id
            for relation in relations
            for related_id in (
                relation.source_symbol_id,
                relation.target_symbol_id,
            )
            if related_id and related_id not in symbol_map
        }
        if related_ids:
            related_symbols = db.scalars(select(CodeSymbol).where(
                CodeSymbol.id.in_(related_ids),
                CodeSymbol.generation_id == generation_id,
            )).all()
            symbol_map.update({
                symbol.id: symbol for symbol in related_symbols
            })
        for relation in relations:
            edge_map[relation.id] = relation
            neighbor_id = (
                relation.target_symbol_id
                if relation.source_symbol_id == symbol_id
                else relation.source_symbol_id
            )
            if not neighbor_id or neighbor_id not in symbol_map:
                continue
            next_hop = current_hop + 1
            relation_weight = {
                "CALLS": 0.85,
                "IMPLEMENTS": 0.9,
                "INHERITS": 0.9,
                "REFERENCES": 0.65,
            }.get(relation.relation_type, 0.5)
            propagated = score_map[symbol_id] * relation_weight / (next_hop + 1)
            if propagated > score_map.get(neighbor_id, 0.0):
                score_map[neighbor_id] = propagated
                hop_map[neighbor_id] = next_hop
                parent_edge[neighbor_id] = relation
                queue.append(neighbor_id)
        if len(score_map) >= max(limit * 8, 100):
            break

    ranked_ids = sorted(
        score_map,
        key=lambda item: (score_map[item], -hop_map[item]),
        reverse=True,
    )[:max(limit * 3, 30)]
    selected_ids = set(ranked_ids)
    nodes = [
        _symbol_to_node(
            symbol_map[symbol_id],
            score=score_map[symbol_id],
            hop=hop_map[symbol_id],
        )
        for symbol_id in ranked_ids
    ]
    edges = [
        _relation_to_edge(
            relation,
            hop=max(
                hop_map.get(relation.source_symbol_id, 0),
                hop_map.get(relation.target_symbol_id or "", 0),
            ),
        )
        for relation in edge_map.values()
        if relation.source_symbol_id in selected_ids
        and (relation.target_symbol_id is None or relation.target_symbol_id in selected_ids)
    ]
    paths: list[dict[str, Any]] = []
    seed_ids = {symbol.id for symbol, _ in seeds}
    for symbol_id in ranked_ids:
        if symbol_id in seed_ids:
            continue
        path_edges: list[dict[str, Any]] = []
        current = symbol_id
        visited: set[str] = set()
        while current not in seed_ids and current not in visited and current in parent_edge:
            visited.add(current)
            edge = parent_edge[current]
            path_edges.append(_relation_to_edge(edge, hop=hop_map.get(current, 0)))
            current = (
                edge.source_symbol_id
                if edge.target_symbol_id == current
                else (edge.target_symbol_id or edge.source_symbol_id)
            )
        if current in seed_ids and path_edges:
            paths.append({
                "from": (
                    symbol_map[current].logical_id
                    or symbol_map[current].id
                ),
                "to": (
                    symbol_map[symbol_id].logical_id
                    or symbol_map[symbol_id].id
                ),
                "edges": list(reversed(path_edges)),
            })
    return {
        "repository_id": repository_id,
        "query": query,
        "generation_id": generation_id,
        "nodes": nodes,
        "edges": edges,
        "paths": paths,
        "stats": {
            "seed_count": len(seeds),
            "expanded_hops": max(hop_map.values(), default=0),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "candidate_count": len(symbols),
        },
    }


def code_graph_snapshot(
    db: Session,
    repository_id: str,
    *,
    query: str | None = None,
    relation_type: str | None = None,
    limit: int = 300,
) -> dict[str, Any]:
    repository = db.get(Repository, repository_id)
    if not repository:
        raise ValueError("Repository not found")
    generation_id = repository.active_graph_generation_id
    if not generation_id:
        return {
            "repository_id": repository_id,
            "generation_id": None,
            "nodes": [],
            "edges": [],
            "stats": {
                "node_count": 0,
                "edge_count": 0,
                "relation_types": {},
                "symbol_kinds": {},
            },
        }
    if query:
        return search_code_graph(
            db,
            repository_id,
            query,
            max_hops=1,
            limit=min(limit, 100),
        )
    relation_query = select(CodeRelation).where(
        CodeRelation.repository_id == repository_id,
        CodeRelation.generation_id == generation_id,
    )
    if relation_type:
        relation_query = relation_query.where(
            CodeRelation.relation_type == relation_type.upper()
        )
    relations = list(db.scalars(relation_query.limit(limit)).all())
    symbol_ids = {
        relation.source_symbol_id for relation in relations
    } | {
        relation.target_symbol_id for relation in relations if relation.target_symbol_id
    }
    symbols = list(db.scalars(select(CodeSymbol).where(
        CodeSymbol.id.in_(symbol_ids)
    )).all()) if symbol_ids else []
    relation_counts = dict(db.execute(
        select(CodeRelation.relation_type, func.count(CodeRelation.id))
        .where(
            CodeRelation.repository_id == repository_id,
            CodeRelation.generation_id == generation_id,
        )
        .group_by(CodeRelation.relation_type)
    ).all())
    symbol_counts = dict(db.execute(
        select(CodeSymbol.kind, func.count(CodeSymbol.id))
        .where(
            CodeSymbol.repository_id == repository_id,
            CodeSymbol.generation_id == generation_id,
        )
        .group_by(CodeSymbol.kind)
    ).all())
    return {
        "repository_id": repository_id,
        "generation_id": generation_id,
        "query": None,
        "nodes": [_symbol_to_node(symbol) for symbol in symbols],
        "edges": [_relation_to_edge(relation) for relation in relations],
        "paths": [],
        "stats": {
            "graph_status": repository.graph_status,
            "symbol_types": symbol_counts,
            "relation_types": relation_counts,
            "returned_nodes": len(symbols),
            "returned_edges": len(relations),
        },
    }
