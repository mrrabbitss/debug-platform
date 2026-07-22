import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes
from app.core.db import Base
from app.models import Artifact, Case, CodeSymbol, Repository
from app.schemas import PatchRequest
from app.services import rag


def _case_repository(
    db,
    *,
    case_id: str,
    artifact_id: str,
    repository_id: str,
    symbol_id: str,
    symbol_name: str,
    code: str,
) -> None:
    db.add(Case(id=case_id, title=case_id, description=""))
    db.add(Artifact(
        id=artifact_id,
        case_id=case_id,
        kind="source_repository",
        original_name=f"{case_id}.zip",
        stored_path=f"repositories/{repository_id}/source.zip",
        sha256=artifact_id.lower().ljust(64, "0")[:64],
        size_bytes=1,
    ))
    db.flush()
    db.add(Repository(
        id=repository_id,
        case_id=case_id,
        artifact_id=artifact_id,
        name=case_id,
        root_path=f"repositories/{repository_id}/extracted",
    ))
    db.flush()
    db.add(CodeSymbol(
        id=symbol_id,
        repository_id=repository_id,
        kind="function",
        name=symbol_name,
        file_path=f"src/{symbol_name}.c",
        line_start=1,
        line_end=3,
        code=code,
    ))


def _database(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'case-isolation.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        _case_repository(
            db,
            case_id="CASE-A",
            artifact_id="ART-A",
            repository_id="REPO-A",
            symbol_id="SYM-A",
            symbol_name="normal_handler",
            code="void normal_handler(void) {}",
        )
        _case_repository(
            db,
            case_id="CASE-B",
            artifact_id="ART-B",
            repository_id="REPO-B",
            symbol_id="SYM-B",
            symbol_name="caseonlyneedle",
            code="void caseonlyneedle(void) { /* private to case B */ }",
        )
        db.commit()
    return engine, session_factory


def test_code_retrieval_never_crosses_case_boundary(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _database(tmp_path)
    monkeypatch.setattr(rag, "SessionLocal", session_factory)
    monkeypatch.setattr(rag, "embedding_scores", lambda query, chunk_ids: {})
    monkeypatch.setattr(rag, "rerank_documents", lambda query, documents, top_n: None)

    case_a_hits = rag.LocalHybridRetriever().search("caseonlyneedle", case_id="CASE-A")
    case_b_hits = rag.LocalHybridRetriever().search("caseonlyneedle", case_id="CASE-B")

    assert all(hit.evidence_id != "SYM-B" for hit in case_a_hits)
    assert [hit.evidence_id for hit in case_b_hits] == ["SYM-B"]
    engine.dispose()


def test_patch_suggestion_rejects_symbol_from_another_case(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(routes.patch_suggestion(
                "CASE-A",
                PatchRequest(symbol_id="SYM-B"),
                db,
            ))

    assert exc_info.value.status_code == 404
    engine.dispose()
