"""New installer-only behavior; synthetic documents, no external model calls."""
import importlib.util
import json
import sys
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base, configure_sqlite_engine
from app.core.utils import json_dumps, json_loads, utcnow
from app.models import (AuditEvent, Case, Job, KnowledgeDocument, KnowledgeGraphState,
                        KnowledgePublication, ModelProfile, UserAccount)
from app.services import bundled_knowledge as bundled, jobs, knowledge_reset as reset, retrieval_models
from app.services.skill_dependencies import bundle_dependencies
from app.workbench_models import WorkbenchRecord

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("installer_bundle_builder", ROOT / "scripts/build_windows_server.py")
builder = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = builder
spec.loader.exec_module(builder)


@pytest.fixture
def server(tmp_path, monkeypatch):
    root = tmp_path / "server-data"
    root.mkdir()
    engine = create_engine("sqlite:///" + (root / "server.db").as_posix(), connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    settings = SimpleNamespace(data_root=root, auth_mode="rbac", auth_allow_legacy_admin=False)
    monkeypatch.setattr(bundled, "get_settings", lambda: settings)
    monkeypatch.setattr(reset, "get_settings", lambda: settings)
    monkeypatch.setattr(reset, "SessionLocal", factory)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(retrieval_models, "_mirror_vectors_to_qdrant", lambda *a, **kw: None)
    monkeypatch.delenv("KNOWLEDGE_RESET_SOURCE_ZIP", raising=False)
    monkeypatch.delenv("KNOWLEDGE_RESET_ARCHIVE_ROOT", raising=False)
    package = tmp_path / "package"
    package.mkdir()
    source = tmp_path / "approved.zip"
    with zipfile.ZipFile(source, "w") as archive:
        archive.writestr("hilink-diag/SKILL.md", "# Synthetic GW AP methodology\n" + "\n".join(
            f"[{name}](references/{name})" for name in reset.ROLES if name != "SKILL.md"))
        for name in reset.ROLES:
            if name != "SKILL.md":
                archive.writestr("hilink-diag/references/" + name, "# Synthetic reference\nGW AP evidence only.\n[Root](../SKILL.md)")
    builder.stage_knowledge_bundle(source, package)
    with factory() as db:
        db.add(UserAccount(id="admin", username="admin", display_name="Synthetic admin", role="ADMIN", active=True))
        db.add(ModelProfile(id="embedding", name="Synthetic local hashing", task_type="embedding", mode="builtin",
                            provider="hashing", model_name="synthetic", enabled=True, is_active=True, config_json="{}"))
        db.add(Case(id="case", title="Retain this synthetic case"))
        db.commit()
    yield SimpleNamespace(root=root, factory=factory, package=package, source=source)
    engine.dispose()


def initialize(server):
    with server.factory() as db:
        bundled.initialize_packaged_knowledge(db, server.package)
        return bundled.packaged_knowledge_status(db, server.package)


def execute(server, attempt=1):
    with server.factory() as db:
        operation = db.get(WorkbenchRecord, reset.operation_key(bundled.OPERATION_ID))
        job = db.get(Job, json_loads(operation.payload_json, {})["job_id"])
        job.status, job.attempt, job.lease_owner = "RUNNING", attempt, "test-worker"
        job.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()
        ctx = jobs.JobContext(job.id, lease_owner="test-worker", lease_seconds=300)
    return reset.reset_job(ctx, bundled.OPERATION_ID)


def test_packaged_empty_server_publishes_all_files_and_preserves_later_edits(server):
    assert initialize(server)["status"] == "APPROVED"
    with server.factory() as db:
        assert db.scalar(select(func.count()).select_from(KnowledgeDocument)) == 0
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "knowledge.reset.approved"))
        assert json_loads(audit.details_json, {})["approval_origin"] == "INSTALLER_DEFAULT"
    result = execute(server)
    assert result["retired_documents"] == 0
    assert initialize(server)["status"] == "PUBLISHED"
    with server.factory() as db:
        docs = list(db.scalars(select(KnowledgeDocument)))
        assert len(docs) == 6 and all(d.active and d.review_status == "ACTIVE" for d in docs)
        assert len(list(db.scalars(select(KnowledgePublication)))) == 6
        assert db.get(Case, "case").title == "Retain this synthetic case"
        report = next(d for d in docs if json_loads(d.metadata_json, {})["knowledge_role"] == "report_template")
        assert json_loads(db.get(WorkbenchRecord, "template-network").payload_json, {})["document_id"] == report.id
        root = next(d for d in docs if d.title.endswith("/SKILL.md"))
        # The persisted full-file dependency manifest resolves every child.
        assert len(json_loads(root.metadata_json, {})["bundle_manifest"]) == 6
        dependencies, missing = bundle_dependencies(root, docs)
        assert missing == [] and len(dependencies) == 5
        root.content = "# Administrator changed this after installation"
        report.active, report.review_status = False, "ARCHIVED"
        db.commit()
        saved = {d.id: (d.content, d.active, d.review_status) for d in docs}
    initialize(server)
    with server.factory() as db:
        assert {d.id: (d.content, d.active, d.review_status) for d in db.scalars(select(KnowledgeDocument))} == saved
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_existing_knowledge_is_not_reset_or_reseeded_after_removal(server):
    with server.factory() as db:
        db.add(KnowledgeDocument(id="custom", title="User wiki", content="Keep user data", active=True))
        db.commit()
    assert initialize(server)["status"] == "PRESERVED"
    with server.factory() as db:
        assert db.get(KnowledgeDocument, "custom").content == "Keep user data"
        db.delete(db.get(KnowledgeDocument, "custom"))
        db.commit()
    assert initialize(server)["status"] == "PRESERVED"
    with server.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_installation_origin_cannot_reset_nonempty_corpus(server):
    with server.factory() as db:
        db.add(KnowledgeDocument(id="custom", title="User wiki", content="Keep user data"))
        db.commit()
        preview = reset.preview_reset(db, operation_id="unsafe", data_root=server.root, source_zip=server.source, actor="admin")
        with pytest.raises(reset.ResetError, match="only initialize an empty"):
            reset.confirm_reset(db, operation_id="unsafe", data_root=server.root, source_zip=server.source, actor="admin",
                expected_source_sha256=preview["source_sha256"], expected_preview_hash=preview["preview_hash"],
                confirmed=True, model_egress_approved=False, approval_origin="INSTALLER_DEFAULT")


def test_failed_initial_index_resumes_same_approval_without_partial_publication(server, monkeypatch):
    initialize(server)
    original = reset.index_embeddings
    monkeypatch.setattr(reset, "index_embeddings", lambda *a, **kw: (_ for _ in ()).throw(ValueError("Synthetic failure")))
    with pytest.raises(reset.ResetError):
        execute(server)
    with server.factory() as db:
        assert not db.scalar(select(KnowledgeDocument.id).where(KnowledgeDocument.active.is_(True)))
        assert db.get(KnowledgeGraphState, "domain").active_generation_id is None
    initialize(server)
    monkeypatch.setattr(reset, "index_embeddings", original)
    execute(server, attempt=2)
    assert initialize(server)["status"] == "PUBLISHED"
    with server.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1


def test_invalid_package_and_external_embedding_do_not_import_or_send_content(server):
    manifest_path = server.package / "bundled-knowledge/manifest.json"
    original = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original)
    manifest["source_sha256"] = "0" * 64
    manifest_path.write_text(json_dumps(manifest), encoding="utf-8")
    assert initialize(server)["status"] == "FAILED"
    manifest_path.write_text(original, encoding="utf-8")
    with server.factory() as db:
        profile = db.get(ModelProfile, "embedding")
        profile.mode, profile.provider = "api", "openai_compatible"
        db.commit()
    assert initialize(server)["status"] == "WAITING_LOCAL_INDEX"
    with server.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
