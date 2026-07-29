import shutil
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import (
    Artifact,
    Case,
    CodeRelation,
    CodeSymbol,
    CommitFileChange,
    CommitRecord,
    Repository,
)
from app.services import code_index, commit_graph
from app.services.code_graph import code_graph_snapshot, search_code_graph
from app.services.commit_graph import commit_graph_snapshot, search_commits
from app.services.git_repository import run_git


class _JobContext:
    def __init__(self) -> None:
        self.updates: list[tuple[int, str]] = []

    def update(self, progress: int, message: str) -> None:
        self.updates.append((progress, message))


def _write_repository(root: Path) -> None:
    (root / "auth.py").write_text(
        """class Base:
    pass

def helper():
    return "ok"

class Worker(Base):
    def execute(self):
        return helper()
""",
        encoding="utf-8",
    )
    (root / "Authenticator.java").write_text(
        """interface Authenticator {
}

class WifiAuth implements Authenticator {
    public void authenticate() {
        helper();
    }

    public void helper() {
    }
}
""",
        encoding="utf-8",
    )
    (root / "网络.py").write_text(
        "def network_probe():\n    return True\n",
        encoding="utf-8",
    )


@pytest.mark.skipif(shutil.which("git") is None, reason="Git is not installed")
def test_repository_index_builds_code_and_commit_graphs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository_root = tmp_path / "source"
    repository_root.mkdir()
    run_git(["init"], cwd=repository_root)
    run_git(["config", "user.name", "Graph Test"], cwd=repository_root)
    run_git(["config", "user.email", "graph@example.invalid"], cwd=repository_root)
    _write_repository(repository_root)
    run_git(["add", "."], cwd=repository_root)
    run_git(
        ["commit", "-m", "add authentication worker and network probe"],
        cwd=repository_root,
    )
    with (repository_root / "auth.py").open("a", encoding="utf-8") as handle:
        handle.write("\nAUTH_TIMEOUT_SECONDS = 30\n")
    run_git(["add", "auth.py"], cwd=repository_root)
    run_git(
        ["commit", "-m", "fix authentication timeout regression"],
        cwd=repository_root,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'graphs.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        db.add(Case(id="CASE-graph", title="authentication regression", description=""))
        db.add(Artifact(
            id="ART-graph",
            case_id="CASE-graph",
            kind="source_repository",
            original_name="source.bundle",
            stored_path="ignored.bundle",
            sha256="a" * 64,
            size_bytes=1,
            status="EXTRACTED",
        ))
        db.flush()
        db.add(Repository(
            id="REPO-graph",
            case_id="CASE-graph",
            artifact_id="ART-graph",
            name="source",
            root_path="ignored",
            status="UPLOADED",
        ))
        db.commit()

    monkeypatch.setattr(code_index, "SessionLocal", session_factory)
    monkeypatch.setattr(commit_graph, "SessionLocal", session_factory)
    monkeypatch.setattr(
        code_index.storage,
        "resolve_path",
        lambda value: repository_root,
    )

    context = _JobContext()
    indexed = code_index._index_repository_impl(context, "REPO-graph")
    assert indexed["symbols"] >= 8
    assert indexed["relations"] >= 4
    assert indexed["commit_graph"]["status"] == "INDEXED"
    assert indexed["commit_graph"]["commits"] == 2
    assert context.updates

    with session_factory() as db:
        repository = db.get(Repository, "REPO-graph")
        assert repository is not None
        assert repository.status == "INDEXED"
        assert repository.graph_status == "INDEXED"
        assert repository.commit_graph_status == "INDEXED"

        relation_types = set(db.scalars(
            select(CodeRelation.relation_type).where(
                CodeRelation.repository_id == "REPO-graph"
            )
        ))
        assert {"CALLS", "REFERENCES", "INHERITS", "IMPLEMENTS"}.issubset(
            relation_types
        )

        graph = search_code_graph(
            db,
            "REPO-graph",
            "execute",
            max_hops=2,
            limit=20,
        )
        assert any(node["name"] == "Worker.execute" for node in graph["nodes"])
        assert any(node["name"] == "helper" for node in graph["nodes"])
        assert all(node["node_type"] == "code_symbol" for node in graph["nodes"])
        assert graph["paths"]

        snapshot = code_graph_snapshot(db, "REPO-graph", limit=500)
        assert snapshot["stats"]["relation_types"]["CALLS"] >= 1
        assert snapshot["stats"]["relation_types"]["IMPLEMENTS"] >= 1

        commits = search_commits(
            db,
            "REPO-graph",
            "authentication regression",
            limit=10,
        )
        assert commits
        assert commits[0]["subject"] == "fix authentication timeout regression"
        assert any(
            change.file_path == "网络.py"
            for change in db.scalars(select(CommitFileChange))
        )

        commit_snapshot = commit_graph_snapshot(
            db,
            "REPO-graph",
            query="authentication worker",
            limit=20,
        )
        assert any(node["node_type"] == "commit" for node in commit_snapshot["nodes"])
        assert any(edge["edge_type"] == "CHANGED" for edge in commit_snapshot["edges"])
        assert db.scalar(select(CommitRecord.id).limit(1)) is not None
        first_generation_id = repository.active_graph_generation_id
        first_symbol_ids = set(db.scalars(select(CodeSymbol.id)))
        first_symbol_logical_ids = set(
            db.scalars(select(CodeSymbol.logical_id))
        )
        first_relation_ids = set(db.scalars(select(CodeRelation.id)))
        first_relation_logical_ids = set(
            db.scalars(select(CodeRelation.logical_id))
        )
        first_commit_ids = set(db.scalars(select(CommitRecord.id)))
        first_change_ids = set(db.scalars(select(CommitFileChange.id)))

    reindexed = code_index._index_repository_impl(context, "REPO-graph")
    assert reindexed["symbols"] == indexed["symbols"]
    with session_factory() as db:
        repository = db.get(Repository, "REPO-graph")
        assert repository is not None
        assert repository.active_graph_generation_id != first_generation_id
        second_symbol_ids = set(db.scalars(select(CodeSymbol.id)))
        second_symbol_logical_ids = set(
            db.scalars(select(CodeSymbol.logical_id))
        )
        second_relation_ids = set(db.scalars(select(CodeRelation.id)))
        second_relation_logical_ids = set(
            db.scalars(select(CodeRelation.logical_id))
        )
        assert len(second_symbol_ids) == len(first_symbol_ids)
        assert len(second_relation_ids) == len(first_relation_ids)
        assert second_symbol_ids.isdisjoint(first_symbol_ids)
        assert second_relation_ids.isdisjoint(first_relation_ids)
        assert second_symbol_logical_ids == first_symbol_logical_ids
        assert second_relation_logical_ids == first_relation_logical_ids
        assert set(db.scalars(select(CommitRecord.id))) == first_commit_ids
        assert set(db.scalars(select(CommitFileChange.id))) == first_change_ids

    active_generation = reindexed["graph_generation_id"]
    active_symbol_ids = second_symbol_ids

    def fail_extract(*_args, **_kwargs):
        raise RuntimeError("synthetic graph build failure")

    monkeypatch.setattr(code_index, "extract_symbols", fail_extract)
    with pytest.raises(RuntimeError, match="synthetic graph build failure"):
        code_index.index_repository_job(context, "REPO-graph")
    with session_factory() as db:
        repository = db.get(Repository, "REPO-graph")
        assert repository is not None
        assert repository.active_graph_generation_id == active_generation
        assert repository.graph_status == "INDEXED"
        assert repository.status == "INDEX_FAILED"
        assert set(db.scalars(select(CodeSymbol.id))) == active_symbol_ids
    engine.dispose()
