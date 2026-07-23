from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes
from app.core.db import Base, get_db
from app.models import Artifact, Case, Job, KnowledgeDocument
from app.services.health import readiness_report, system_status_report


def _database(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'health.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return engine, session_factory


def test_readiness_checks_database_and_writable_storage(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    with session_factory() as db:
        report = readiness_report(db, storage_root)
        assert report["ready"] is True
        assert report["checks"]["database"]["dialect"] == "sqlite"
        assert report["checks"]["storage"]["free_bytes"] > 0

        not_a_directory = tmp_path / "file"
        not_a_directory.write_text("x", encoding="utf-8")
        failed = readiness_report(db, not_a_directory)
        assert failed["ready"] is False
        assert failed["checks"]["storage"]["error_type"] == "StorageDirectoryMissing"
    engine.dispose()


def test_system_status_reports_operational_counts_without_paths(tmp_path: Path) -> None:
    engine, session_factory = _database(tmp_path)
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    with session_factory() as db:
        db.add(Case(id="CASE-health", title="health", description=""))
        db.add(Artifact(
            id="ART-health",
            case_id="CASE-health",
            original_name="health.log",
            stored_path="artifacts/ART-health/health.log",
            sha256="a" * 64,
            size_bytes=123,
        ))
        db.add(Job(id="JOB-health", kind="parse_artifact", status="QUEUED"))
        db.add(KnowledgeDocument(
            id="KD-health",
            title="health",
            source_type="manual",
            content="content",
        ))
        db.commit()
        report = system_status_report(db, storage_root, job_workers=4)

    assert report["entities"] == {"cases": 1, "artifacts": 1, "knowledge_documents": 1}
    assert report["storage"]["artifact_bytes"] == 123
    assert report["jobs"]["counts"] == {"QUEUED": 1}
    assert str(tmp_path) not in str(report)
    engine.dispose()


def test_health_endpoints_return_liveness_readiness_and_admin_status(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = _database(tmp_path)
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    settings = SimpleNamespace(
        storage_root=storage_root,
        job_workers=2,
        app_name="test-platform",
        app_env="test",
    )
    monkeypatch.setattr(routes, "get_settings", lambda: settings)

    def override_db():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        assert client.get("/api/v1/health/live").json() == {"status": "alive"}
        ready = client.get("/api/v1/health/ready")
        assert ready.status_code == 200
        assert ready.json()["ready"] is True
        assert ready.json()["checks"] == {"database": {"ok": True}, "storage": {"ok": True}}
        assert "free_bytes" not in ready.text
        status = client.get("/api/v1/system/status")
        assert status.status_code == 200
        assert status.json()["database"]["dialect"] == "sqlite"
        assert status.json()["app"] == "test-platform"
    engine.dispose()
