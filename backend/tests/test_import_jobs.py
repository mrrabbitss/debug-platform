from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import (
    Artifact,
    Case,
    KnowledgeChunk,
    KnowledgeDocument,
    Repository,
)
from app.services import import_jobs
from app.services.storage import StorageService


class _Context:
    def __init__(self) -> None:
        self.updates: list[tuple[int, str]] = []

    def update(self, progress: int, message: str) -> None:
        self.updates.append((progress, message))

    def raise_if_cancelled(self) -> None:
        return

    def complete_in_transaction(
        self,
        _db,
        _result,
        message: str = "Completed",
    ) -> None:
        self.updates.append((100, message))


def _session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'imports.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    return engine, factory


def test_repository_archive_is_imported_by_background_handler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _session_factory(tmp_path)
    managed_storage = StorageService(tmp_path / "storage")
    source = managed_storage.artifact_dir("ART-import") / "source.zip"
    with ZipFile(source, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("project/main.py", "print('ready')\n")
        archive.writestr("project/README.md", "# Project\n")

    with factory() as db:
        db.add(Case(id="CASE-import", title="Import", description=""))
        db.add(Artifact(
            id="ART-import",
            case_id="CASE-import",
            kind="source_repository",
            original_name="source.zip",
            stored_path=managed_storage.storage_key(source),
            sha256="a" * 64,
            size_bytes=source.stat().st_size,
            status="UPLOADED",
        ))
        db.flush()
        db.add(Repository(
            id="REPO-import",
            case_id="CASE-import",
            artifact_id="ART-import",
            name="source",
            root_path=managed_storage.storage_key(
                managed_storage.repository_dir("REPO-import")
            ),
            status="IMPORT_QUEUED",
        ))
        db.commit()

    monkeypatch.setattr(import_jobs, "SessionLocal", factory)
    monkeypatch.setattr(import_jobs, "storage", managed_storage)
    result = import_jobs.import_repository_job(_Context(), "REPO-import")

    assert result["files"] == 2
    assert result["import_format"] == "archive"
    with factory() as db:
        repository = db.get(Repository, "REPO-import")
        artifact = db.get(Artifact, "ART-import")
        assert repository is not None
        assert artifact is not None
        assert repository.status == "UPLOADED"
        assert artifact.status == "EXTRACTED"
        root = managed_storage.resolve_path(repository.root_path)
        assert (root / "project" / "main.py").read_text(
            encoding="utf-8"
        ) == "print('ready')\n"
    engine.dispose()


def test_knowledge_document_is_read_and_indexed_by_background_handler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine, factory = _session_factory(tmp_path)
    managed_storage = StorageService(tmp_path / "storage")
    source = managed_storage.artifact_dir("ART-knowledge") / "case.md"
    source.write_text(
        "# 错误形式\n认证失败\n\n# 解决方案\n检查密钥配置",
        encoding="utf-8",
    )

    with factory() as db:
        db.add(Artifact(
            id="ART-knowledge",
            case_id=None,
            kind="knowledge_source",
            original_name="case.md",
            stored_path=managed_storage.storage_key(source),
            sha256="b" * 64,
            size_bytes=source.stat().st_size,
            status="UPLOADED",
        ))
        db.add(KnowledgeDocument(
            id="DOC-knowledge",
            title="case.md",
            source_type="fault_case",
            content="",
            active=False,
            metadata_json=(
                '{"artifact_id":"ART-knowledge","import_status":"QUEUED"}'
            ),
        ))
        db.commit()

    monkeypatch.setattr(import_jobs, "SessionLocal", factory)
    monkeypatch.setattr(import_jobs, "storage", managed_storage)
    result = import_jobs.import_knowledge_job(
        _Context(),
        "DOC-knowledge",
        "ART-knowledge",
    )

    assert result["chunks"] == 2
    with factory() as db:
        document = db.get(KnowledgeDocument, "DOC-knowledge")
        artifact = db.get(Artifact, "ART-knowledge")
        assert document is not None
        assert artifact is not None
        assert document.active is False
        assert document.review_status == "DRAFT"
        assert "认证失败" in document.content
        assert artifact.status == "INDEXED"
        assert db.scalar(select(func.count(KnowledgeChunk.id))) == 2
    engine.dispose()
