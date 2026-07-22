from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api import routes
from app.core.db import Base, configure_sqlite_engine, get_db
from app.models import Artifact, Case, Repository
from app.services.storage import StorageService


def test_case_delete_cascades_database_and_managed_files(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'delete.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    service = StorageService(tmp_path / "storage")
    artifact_dir = service.artifact_dir("ART-delete")
    repository_dir = service.repository_dir("REPO-delete")
    (artifact_dir / "log.txt").write_text("log", encoding="utf-8")
    (repository_dir / "main.c").write_text("int main(void) {}", encoding="utf-8")

    with session_factory() as db:
        db.add(Case(id="CASE-delete", title="delete", description=""))
        db.add(Artifact(
            id="ART-delete",
            case_id="CASE-delete",
            original_name="log.txt",
            stored_path="artifacts/ART-delete/log.txt",
            sha256="a" * 64,
            size_bytes=3,
        ))
        db.flush()
        db.add(Repository(
            id="REPO-delete",
            case_id="CASE-delete",
            artifact_id="ART-delete",
            name="repo",
            root_path="repositories/REPO-delete",
        ))
        db.commit()

    def override_db():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(routes, "storage", service)
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        response = client.delete("/api/v1/cases/CASE-delete")
        assert response.status_code == 200
        assert response.json()["storage_cleanup_errors"] == []

    with session_factory() as db:
        assert db.scalar(select(func.count(Case.id))) == 0
        assert db.scalar(select(func.count(Artifact.id))) == 0
        assert db.scalar(select(func.count(Repository.id))) == 0
    assert not artifact_dir.exists()
    assert not repository_dir.exists()
    engine.dispose()
