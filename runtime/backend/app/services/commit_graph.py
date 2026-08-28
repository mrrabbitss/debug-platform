import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, stable_id
from app.models import CodeSymbol, CommitFileChange, CommitRecord, Repository
from app.services.git_repository import (
    GitRepositoryError,
    find_git_worktree_root,
    repository_head,
    run_git,
    validate_git_worktree,
)
from app.services.rag import tokenize


MAX_INDEXED_COMMITS = 2000


def _parse_datetime(value: str) -> datetime | None:
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_git_path(value: str) -> str | None:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or path.is_absolute()
        or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        return None
    return path.as_posix()


def _read_commits(root: Any) -> list[dict[str, Any]]:
    result = run_git(
        [
            "-c",
            "core.quotepath=false",
            "log",
            "--all",
            f"--max-count={MAX_INDEXED_COMMITS}",
            "--date=iso-strict",
            "--format=%x1e%H%x1f%P%x1f%aI%x1f%an%x1f%s%x1f%b",
        ],
        cwd=root,
        timeout=300,
    )
    commits: list[dict[str, Any]] = []
    for record in result.stdout.split("\x1e"):
        value = record.strip("\r\n")
        if not value:
            continue
        fields = value.split("\x1f", 5)
        if len(fields) != 6:
            continue
        commit_hash, parents, authored_at, author, subject, body = fields
        if len(commit_hash) < 7:
            continue
        commits.append({
            "commit_hash": commit_hash.strip(),
            "parents": [item for item in parents.strip().split() if item],
            "authored_at": _parse_datetime(authored_at),
            "author_name": author.strip()[:255],
            "subject": subject.strip(),
            "body": body.strip(),
        })
    return commits


def _read_file_changes(root: Any) -> dict[str, list[dict[str, str | None]]]:
    result = run_git(
        [
            "-c",
            "core.quotepath=false",
            "log",
            "--all",
            f"--max-count={MAX_INDEXED_COMMITS}",
            "--format=%x1e%H",
            "--name-status",
            "--no-ext-diff",
            "--find-renames",
        ],
        cwd=root,
        timeout=300,
    )
    changes: dict[str, list[dict[str, str | None]]] = defaultdict(list)
    for record in result.stdout.split("\x1e"):
        lines = [line for line in record.splitlines() if line.strip()]
        if not lines:
            continue
        commit_hash = lines[0].strip()
        if len(commit_hash) < 7:
            continue
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            status = parts[0].strip().upper()
            change_type = status[:1] if status else "M"
            old_path: str | None = None
            if change_type in {"R", "C"} and len(parts) >= 3:
                old_path = _safe_git_path(parts[1])
                file_path = _safe_git_path(parts[2])
            else:
                file_path = _safe_git_path(parts[1])
            if not file_path:
                continue
            changes[commit_hash].append({
                "change_type": change_type,
                "file_path": file_path,
                "old_path": old_path,
                "raw_status": status,
            })
    return changes


def index_commit_graph(repository_id: str, repository_root: Any) -> dict[str, Any]:
    worktree = find_git_worktree_root(repository_root)
    if not worktree:
        with SessionLocal() as db:
            repository = db.get(Repository, repository_id)
            if repository:
                repository.commit_graph_status = "UNAVAILABLE"
                metadata = json_loads(repository.index_metadata_json, {})
                metadata["commit_graph"] = {
                    "status": "UNAVAILABLE",
                    "reason": "No in-tree .git directory. Upload a Git Bundle to preserve history.",
                }
                repository.index_metadata_json = json_dumps(metadata)
                db.commit()
        return {
            "status": "UNAVAILABLE",
            "commits": 0,
            "file_changes": 0,
            "reason": "No in-tree .git directory",
        }

    try:
        validate_git_worktree(worktree)
        commits = _read_commits(worktree)
        changes_by_hash = _read_file_changes(worktree)
        branch, head_hash = repository_head(worktree)
        with SessionLocal() as db:
            repository = db.get(Repository, repository_id)
            if not repository:
                raise ValueError("Repository not found")
            db.execute(delete(CommitFileChange).where(
                CommitFileChange.repository_id == repository_id
            ))
            db.execute(delete(CommitRecord).where(
                CommitRecord.repository_id == repository_id
            ))
            db.flush()
            commit_ids: dict[str, str] = {}
            for item in commits:
                commit = CommitRecord(
                    id=stable_id("CMT", repository_id, item["commit_hash"]),
                    repository_id=repository_id,
                    commit_hash=item["commit_hash"],
                    parent_hashes_json=json_dumps(item["parents"]),
                    author_name=item["author_name"],
                    authored_at=item["authored_at"],
                    subject=item["subject"],
                    body=item["body"],
                    metadata_json=json_dumps({
                        "intent_tokens": tokenize(
                            f"{item['subject']} {item['body']}"
                        )[:200],
                    }),
                )
                db.add(commit)
                commit_ids[item["commit_hash"]] = commit.id
            db.flush()
            file_change_count = 0
            for commit_hash, changes in changes_by_hash.items():
                commit_id = commit_ids.get(commit_hash)
                if not commit_id:
                    continue
                for item in changes:
                    db.add(CommitFileChange(
                        id=stable_id(
                            "CHG",
                            repository_id,
                            commit_hash,
                            item["raw_status"],
                            item["old_path"] or "",
                            item["file_path"],
                        ),
                        repository_id=repository_id,
                        commit_id=commit_id,
                        change_type=str(item["change_type"]),
                        file_path=str(item["file_path"]),
                        old_path=(
                            str(item["old_path"]) if item["old_path"] is not None else None
                        ),
                        metadata_json=json_dumps({"raw_status": item["raw_status"]}),
                    ))
                    file_change_count += 1
            repository.branch = branch
            repository.commit_hash = head_hash
            repository.commit_graph_status = "INDEXED"
            metadata = json_loads(repository.index_metadata_json, {})
            metadata["commit_graph"] = {
                "status": "INDEXED",
                "commits": len(commits),
                "file_changes": file_change_count,
                "limit": MAX_INDEXED_COMMITS,
                "worktree_relative": str(worktree.relative_to(repository_root.resolve()))
                if worktree != repository_root.resolve()
                else ".",
            }
            repository.index_metadata_json = json_dumps(metadata)
            db.commit()
        return {
            "status": "INDEXED",
            "commits": len(commits),
            "file_changes": file_change_count,
            "branch": branch,
            "head": head_hash,
        }
    except (GitRepositoryError, OSError, ValueError) as exc:
        with SessionLocal() as db:
            repository = db.get(Repository, repository_id)
            if repository:
                repository.commit_graph_status = "INDEX_FAILED"
                metadata = json_loads(repository.index_metadata_json, {})
                metadata["commit_graph"] = {
                    "status": "INDEX_FAILED",
                    "error": str(exc)[:2000],
                }
                repository.index_metadata_json = json_dumps(metadata)
                db.commit()
        return {
            "status": "INDEX_FAILED",
            "commits": 0,
            "file_changes": 0,
            "reason": str(exc),
        }


def _commit_change_map(
    db: Session,
    repository_id: str,
    commit_ids: set[str],
) -> dict[str, list[CommitFileChange]]:
    result: dict[str, list[CommitFileChange]] = defaultdict(list)
    if not commit_ids:
        return result
    for change in db.scalars(select(CommitFileChange).where(
        CommitFileChange.repository_id == repository_id,
        CommitFileChange.commit_id.in_(commit_ids),
    )):
        result[change.commit_id].append(change)
    return result


def search_commits(
    db: Session,
    repository_id: str,
    query: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    commits = list(db.scalars(
        select(CommitRecord)
        .where(CommitRecord.repository_id == repository_id)
        .order_by(CommitRecord.authored_at.desc())
        .limit(MAX_INDEXED_COMMITS)
    ).all())
    changes = _commit_change_map(db, repository_id, {item.id for item in commits})
    query_tokens = tokenize(query)
    if not query_tokens:
        return []
    documents: list[list[str]] = []
    for commit in commits:
        paths = " ".join(change.file_path for change in changes.get(commit.id, []))
        documents.append(tokenize(f"{commit.subject}\n{commit.body}\n{paths}"))
    document_frequency = Counter()
    for tokens in documents:
        document_frequency.update(set(tokens))
    average_length = sum(map(len, documents)) / max(len(documents), 1)
    results: list[dict[str, Any]] = []
    for commit, tokens in zip(commits, documents, strict=True):
        frequencies = Counter(tokens)
        score = 0.0
        matched_terms: list[str] = []
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            matched_terms.append(token)
            inverse_frequency = math.log(
                1
                + (len(commits) - document_frequency[token] + 0.5)
                / (document_frequency[token] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(tokens) / max(average_length, 1)
            )
            score += inverse_frequency * frequency * 2.5 / denominator
        if not matched_terms:
            continue
        file_changes = changes.get(commit.id, [])
        results.append({
            "evidence_id": commit.id,
            "source_type": "commit",
            "score": round(score, 6),
            "commit_hash": commit.commit_hash,
            "parents": json_loads(commit.parent_hashes_json, []),
            "author_name": commit.author_name,
            "authored_at": commit.authored_at,
            "subject": commit.subject,
            "body": commit.body,
            "matched_terms": sorted(set(matched_terms)),
            "files": [
                {
                    "change_id": change.id,
                    "change_type": change.change_type,
                    "file_path": change.file_path,
                    "old_path": change.old_path,
                }
                for change in file_changes[:100]
            ],
        })
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def symbols_for_commit_paths(
    db: Session,
    repository_id: str,
    file_paths: set[str],
    *,
    limit: int = 100,
) -> list[CodeSymbol]:
    if not file_paths:
        return []
    repository = db.get(Repository, repository_id)
    if not repository or not repository.active_graph_generation_id:
        return []
    return list(db.scalars(
        select(CodeSymbol)
        .where(
            CodeSymbol.repository_id == repository_id,
            CodeSymbol.generation_id
            == repository.active_graph_generation_id,
            CodeSymbol.file_path.in_(file_paths),
        )
        .order_by(CodeSymbol.file_path, CodeSymbol.line_start)
        .limit(limit)
    ).all())


def commit_graph_snapshot(
    db: Session,
    repository_id: str,
    *,
    query: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    repository = db.get(Repository, repository_id)
    if not repository:
        raise ValueError("Repository not found")
    if query:
        matched = search_commits(
            db,
            repository_id,
            query,
            limit=min(limit, 100),
        )
        commit_ids = {str(item["evidence_id"]) for item in matched}
        commits = list(db.scalars(select(CommitRecord).where(
            CommitRecord.id.in_(commit_ids)
        )).all()) if commit_ids else []
        score_map = {
            str(item["evidence_id"]): float(item["score"]) for item in matched
        }
    else:
        commits = list(db.scalars(
            select(CommitRecord)
            .where(CommitRecord.repository_id == repository_id)
            .order_by(CommitRecord.authored_at.desc())
            .limit(limit)
        ).all())
        score_map = {}
    changes = _commit_change_map(db, repository_id, {item.id for item in commits})
    all_commits = list(db.scalars(select(CommitRecord).where(
        CommitRecord.repository_id == repository_id
    )).all())
    hash_to_id = {item.commit_hash: item.id for item in all_commits}
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    file_paths: set[str] = set()
    for commit in commits:
        nodes.append({
            "id": commit.id,
            "node_type": "commit",
            "commit_hash": commit.commit_hash,
            "subject": commit.subject,
            "author_name": commit.author_name,
            "authored_at": commit.authored_at,
            "score": score_map.get(commit.id),
        })
        for parent_hash in json_loads(commit.parent_hashes_json, []):
            edges.append({
                "edge_type": "PARENT",
                "source": commit.id,
                "target": hash_to_id.get(parent_hash),
                "target_hash": parent_hash,
            })
        for change in changes.get(commit.id, []):
            file_node_id = f"FILE:{repository_id}:{change.file_path}"
            file_paths.add(change.file_path)
            edges.append({
                "edge_type": "CHANGED",
                "source": commit.id,
                "target": file_node_id,
                "change_type": change.change_type,
            })
    for file_path in sorted(file_paths):
        nodes.append({
            "id": f"FILE:{repository_id}:{file_path}",
            "node_type": "file",
            "file_path": file_path,
        })
    symbols = symbols_for_commit_paths(
        db,
        repository_id,
        file_paths,
        limit=max(limit * 5, 100),
    )
    for symbol in symbols:
        symbol_evidence_id = symbol.logical_id or symbol.id
        nodes.append({
            "id": symbol_evidence_id,
            "revision_id": symbol.id,
            "node_type": "code_symbol",
            "name": symbol.name,
            "kind": symbol.kind,
            "file_path": symbol.file_path,
            "line_start": symbol.line_start,
        })
        edges.append({
            "edge_type": "CONTAINS_SYMBOL",
            "source": f"FILE:{repository_id}:{symbol.file_path}",
            "target": symbol_evidence_id,
        })
    return {
        "repository_id": repository_id,
        "query": query,
        "status": repository.commit_graph_status,
        "nodes": nodes[:5000],
        "edges": edges[:10000],
        "stats": {
            "commits": len(commits),
            "files": len(file_paths),
            "symbols": len(symbols),
            "returned_nodes": min(len(nodes), 5000),
            "returned_edges": min(len(edges), 10000),
        },
    }
