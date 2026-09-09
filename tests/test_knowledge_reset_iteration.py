"""New reset-only checks. All content/data is synthetic and outside the repository."""
from __future__ import annotations

import os
import json
import importlib.util
import sqlite3
import stat
import subprocess
import sys
import zipfile
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(next(parent for parent in Path(__file__).resolve().parents
    if (parent / "backend/app").is_dir()) / "backend"))
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, json_loads, utcnow
from app.models import (AgentMemory, AnalysisRun, AuditEvent, Case, Job, KnowledgeChunk,
    KnowledgeDocument, KnowledgeDraft, KnowledgeEmbedding, KnowledgeGraphState,
    KnowledgePublication, KnowledgeRevision, ModelProfile, UserAccount)
from app.workbench_models import WorkbenchRecord
from app.api import knowledge_reset as api
from app.services import knowledge_reset as reset, jobs, retrieval_models
from app.services.knowledge_governance import create_document_revision
from app.services.skill_dependencies import bundle_dependencies
from app.services.workbench import template_snapshot

REPO = next(parent for parent in Path(__file__).resolve().parents if (parent / "backend/app").is_dir())


def source_files():
    root = "---\nname: synthetic-network\n---\n# Synthetic root\nUse the current CLI model.\n"
    root += "\n".join(f"[{name}](references/{name})" for name in reset.ROLES if name != "SKILL.md")
    root += "\nBare prose alias: `fault-tree.md`\n"
    result = {"bundle/SKILL.md": root}
    for name in reset.ROLES:
        if name != "SKILL.md":
            result["bundle/references/" + name] = f"# {name}\nSynthetic GW and AP log evidence only.\n[Root](../SKILL.md)\n"
    return result


def write_zip(path, files=None, extras=()):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in (files or source_files()).items():
            archive.writestr(name, content)
        for info, data in extras:
            archive.writestr(info, data)
    return path


@pytest.fixture
def store(tmp_path, monkeypatch):
    root = tmp_path / "data"
    (root / "storage").mkdir(parents=True)
    database = root / "gw_ap_debug.db"
    engine = create_engine("sqlite:///" + database.as_posix(), connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(reset, "SessionLocal", factory)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(reset, "get_settings", lambda: SimpleNamespace(data_root=root,
        auth_mode="rbac", auth_allow_legacy_admin=False))
    monkeypatch.setattr(retrieval_models, "_mirror_vectors_to_qdrant", lambda *a, **kw: None)
    monkeypatch.setattr(api.job_runner, "_schedule", lambda *a, **kw: None)
    source = write_zip(tmp_path / "synthetic.zip")
    monkeypatch.setenv("KNOWLEDGE_RESET_SOURCE_ZIP", str(source))
    monkeypatch.delenv("KNOWLEDGE_RESET_ARCHIVE_ROOT", raising=False)
    with factory() as db:
        db.add_all([UserAccount(id=role, username=role, display_name=role, role=role, active=True)
            for role in ("ADMIN", "EXPERT", "ENGINEER", "VIEWER")])
        db.add(ModelProfile(id="embedding", name="Synthetic hashing", task_type="embedding", mode="local",
            provider="hashing", model_name="synthetic", enabled=True, is_active=True,
            active_embedding_generation_id="old-vector", config_json="{}"))
        db.add(KnowledgeGraphState(id="domain", status="READY", active_generation_id="old-graph"))
        db.add_all([KnowledgeDocument(id="old", title="Old synthetic knowledge", source_type="analysis_method",
            content="# Old\nSynthetic reference must remain immutable.", active=True, review_status="ACTIVE"),
            KnowledgeDocument(id="old-draft", title="Synthetic draft", source_type="document", content="Draft content",
                active=False, review_status="DRAFT", metadata_json=json_dumps({"content_kind": "KNOWLEDGE"}))])
        db.flush()
        db.add(Case(id="case", title="Synthetic case", owner_id="ENGINEER"))
        db.add(KnowledgeChunk(id="old-chunk", document_id="old", document_version=1, chunk_index=0, content="Old evidence"))
        db.add(KnowledgeDraft(id="old-proposal", document_id="old", base_version=1, snapshot_json='{"synthetic":true}',
            status="DRAFT", owner_key="ENGINEER"))
        db.flush()
        db.add(KnowledgeEmbedding(id="old-point", chunk_id="old-chunk", profile_id="embedding", generation_id="old-vector",
            dimension=2, vector_json="[1,0]"))
        db.add(AnalysisRun(id="report", case_id="case", status="COMPLETED", result_json='{"synthetic_report":true}'))
        db.add(AuditEvent(id="old-audit", action="synthetic.original", details_json="{}"))
        db.add(WorkbenchRecord(id="template-network", kind="template_default", payload_json='{"document_id":"old"}'))
        db.add(WorkbenchRecord(id="old-run", kind="run_context", payload_json='{"immutable_reference":"old-chunk"}'))
        db.add(WorkbenchRecord(id="user-pref", kind="preferences", owner_id="ENGINEER", payload_json='{"synthetic":true}'))
        for scope in ("CASE", "GLOBAL"):
            db.add(AgentMemory(id=scope, scope=scope, case_id="case", memory_type="lesson", source_kind="synthetic",
                title="Synthetic memory", content="Synthetic evidence", fingerprint=scope, review_status="ACTIVE"))
        create_document_revision(db, db.get(KnowledgeDocument, "old"), created_by="ADMIN", change_summary="Synthetic baseline")
        db.commit()
    yield SimpleNamespace(factory=factory, engine=engine, root=root, source=source, database=database, temp=tmp_path)
    engine.dispose()


def preview(store, operation_id="reset-one", actor="ADMIN"):
    with store.factory() as db:
        return reset.preview_reset(db, operation_id=operation_id, data_root=store.root, source_zip=store.source, actor=actor)


def confirm(store, value=None, **changes):
    value = value or preview(store)
    args = dict(operation_id=value["operation_id"], data_root=store.root, source_zip=store.source, actor="ADMIN",
        expected_source_sha256=value["source_sha256"], expected_preview_hash=value["preview_hash"],
        confirmed=True, model_egress_approved=False)
    args.update(changes)
    with store.factory() as db:
        row, job = reset.confirm_reset(db, **args)
        return reset.operation_payload(row), job.id


def worker(store, job_id, attempt=1, owner="synthetic-worker"):
    with store.factory() as db:
        job = db.get(Job, job_id)
        job.status, job.attempt, job.lease_owner = "RUNNING", attempt, owner
        job.lease_expires_at = utcnow() + timedelta(minutes=10)
        db.commit()
    return jobs.JobContext(job_id, lease_owner=owner, lease_seconds=600)


def assert_old_active(store):
    with store.factory() as db:
        assert db.get(KnowledgeDocument, "old").active
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == "old-graph"
        assert db.get(ModelProfile, "embedding").active_embedding_generation_id == "old-vector"
        assert db.scalar(select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))) == 1


def test_preview_is_read_only_and_complete(store):
    before = sorted(str(p) for p in store.root.rglob("*"))
    value = preview(store)
    assert sorted(str(p) for p in store.root.rglob("*")) == before
    assert len(value["manifest"]) == 6
    assert value["counts"]["knowledge_and_skills"] == 2
    assert value["approval_type"] == "HUMAN_DIRECT_IMPORT"
    assert value["content_kind"] == "SKILL"
    assert value["target"]["data_root"] == str(store.root)
    assert len([f for f in value["manifest"] if f["adaptation_diff"]]) == 1
    assert value["manifest"][0]["source_sha256"] != value["manifest"][0]["sha256"]
    assert "source" in value["manifest"][0]["adaptation_diff"]
    with store.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
        assert db.scalar(select(func.count()).select_from(WorkbenchRecord).where(WorkbenchRecord.kind == reset.KIND)) == 0
    assert_old_active(store)


def test_confirm_backup_includes_wal_and_atomic_outbox(store):
    value, job_id = confirm(store)
    archived = Path(value["archive_zip"])
    assert archived.read_bytes() == store.source.read_bytes()
    assert archived.parent.is_relative_to(store.root)
    with sqlite3.connect(value["backup"]["path"]) as db:
        assert db.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM audit_events").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM knowledge_documents").fetchone()[0] == 2
    with store.factory() as db:
        assert db.get(Job, job_id).status == "QUEUED"
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 2
        assert value["status"] == "APPROVED"
    assert_old_active(store)


def test_complete_import_preserves_history_and_binds_template(store):
    before = preview(store)
    approved, job_id = confirm(store, before, actor="EXPERT")
    ctx = worker(store, job_id)
    result = reset.reset_job(ctx, "reset-one")
    originals = source_files()
    with store.factory() as db:
        active = list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))))
        assert len(active) == 6
        root = next(d for d in active if d.title.endswith("SKILL.md"))
        dependencies, missing = bundle_dependencies(root, active)
        assert len(dependencies) == 5 and not missing
        for doc in active:
            metadata = json_loads(doc.metadata_json, {})
            path = metadata["source_paths"][0]
            assert metadata["content_kind"] == "SKILL"
            assert metadata["problem_categories"] == ["network"]
            assert metadata["knowledge_role"] == reset.ROLES[path.rsplit("/", 1)[-1]]
            assert metadata["approval_type"] == "HUMAN_DIRECT_IMPORT"
            if path.endswith("SKILL.md"):
                assert doc.content.replace(reset.ADAPTER, "", 1) == originals[path]
            else:
                assert doc.content == originals[path]
            assert db.scalar(select(func.count()).select_from(KnowledgeChunk).where(
                KnowledgeChunk.document_id == doc.id, KnowledgeChunk.document_version == doc.version)) > 0
        selected = template_snapshot(db, "network")
        assert selected["content"] == originals["bundle/references/report-format.md"]
        assert selected["id"] in result["documents"]
        assert db.get(KnowledgeDocument, "old").review_status == "ARCHIVED"
        assert db.get(KnowledgeDocument, "old-draft").review_status == "ARCHIVED"
        assert db.get(KnowledgeDraft, "old-proposal").status == "ARCHIVED"
        old = db.scalar(select(KnowledgeRevision).where(KnowledgeRevision.document_id == "old"))
        assert old.content_hash == reset.digest("# Old\nSynthetic reference must remain immutable.")
        assert db.get(KnowledgeChunk, "old-chunk").content == "Old evidence"
        assert db.get(KnowledgeEmbedding, "old-point").generation_id == "old-vector"
        assert db.get(AgentMemory, "CASE").review_status == "ACTIVE"
        assert db.get(AgentMemory, "GLOBAL").review_status == "ARCHIVED"
        assert db.get(Case, "case").title == "Synthetic case"
        assert db.get(AnalysisRun, "report").result_json == '{"synthetic_report":true}'
        assert db.get(WorkbenchRecord, "old-run").payload_json == '{"immutable_reference":"old-chunk"}'
        assert db.get(WorkbenchRecord, "user-pref").payload_json == '{"synthetic":true}'
        assert db.scalar(select(func.count()).select_from(UserAccount)) == 4
        assert db.scalar(select(func.count()).select_from(ModelProfile)) == 1
        assert db.get(AuditEvent, "old-audit")
        assert db.scalar(select(func.count()).select_from(WorkbenchRecord).where(WorkbenchRecord.kind.like("assistant%"))) == 0
        assert db.get(Job, job_id).status == "COMPLETED"
        assert reset.read_operation(db, "reset-one")[1]["status"] == "PUBLISHED"
        assert db.scalar(select(func.count()).select_from(KnowledgePublication)) == 7
    replay, replay_job = confirm(store, before)
    assert replay["status"] == "PUBLISHED" and replay_job == job_id
    assert replay["backup"] == approved["backup"]


def test_repeat_confirmation_returns_same_job(store):
    value = preview(store)
    first, first_job = confirm(store, value)
    second, second_job = confirm(store, value)
    assert first_job == second_job and first["backup"] == second["backup"]
    with store.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 1
    with pytest.raises(reset.ResetError, match="already bound"):
        confirm(store, value, expected_source_sha256="0" * 64)


@pytest.mark.parametrize("change", ["source", "document", "profile", "template"])
def test_changed_preview_cannot_be_confirmed(store, change):
    value = preview(store)
    if change == "source":
        files = source_files()
        files["bundle/references/report-format.md"] += "\nChanged."
        write_zip(store.source, files)
    else:
        with store.factory() as db:
            if change == "document":
                db.get(KnowledgeDocument, "old").content += "\nChanged."
            elif change == "profile":
                db.get(ModelProfile, "embedding").config_json = '{"batch_size":8}'
            else:
                db.get(WorkbenchRecord, "template-network").payload_json = '{"document_id":"old-draft"}'
            db.commit()
    with pytest.raises(reset.ResetError, match="changed"):
        confirm(store, value)
    assert not (store.root / "knowledge-reset-archives").exists()


def test_backup_failure_leaves_no_approval_or_job(store, monkeypatch):
    def fail(*a, **kw):
        raise reset.ResetError("Synthetic backup failure")
    monkeypatch.setattr(reset, "consistent_backup", fail)
    with pytest.raises(reset.ResetError, match="backup failure"):
        confirm(store)
    with store.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    assert_old_active(store)


@pytest.mark.parametrize("boundary", ["vector", "graph", "commit"])
def test_index_or_commit_failure_preserves_old_corpus(store, monkeypatch, boundary):
    approved, job_id = confirm(store)
    ctx = worker(store, job_id)
    def fail(*a, **kw):
        raise RuntimeError("Synthetic failure at " + boundary)
    if boundary == "vector":
        monkeypatch.setattr(reset, "index_embeddings", fail)
    elif boundary == "graph":
        monkeypatch.setattr(reset, "stage_graph", fail)
    else:
        monkeypatch.setattr(ctx, "complete_in_transaction", fail)
    with pytest.raises(reset.ResetError, match="existing corpus is unchanged"):
        reset.reset_job(ctx, "reset-one")
    assert_old_active(store)
    with store.factory() as db:
        _, value = reset.read_operation(db, "reset-one")
        assert value["status"] == "FAILED"
        assert value["preview_hash"] == approved["preview_hash"]
        assert db.get(KnowledgeGraphState, "domain").building_generation_id is None
        assert db.get(KnowledgeDraft, "old-proposal").status == "DRAFT"
        assert db.get(AgentMemory, "GLOBAL").review_status == "ACTIVE"


def test_interrupted_build_reopens_database_and_resumes_exact_approval(store, monkeypatch):
    approved, job_id = confirm(store)
    ctx = worker(store, job_id)
    original = reset.stage_graph
    def interrupt(*a, **kw):
        raise SystemExit("Synthetic abrupt process boundary")
    monkeypatch.setattr(reset, "stage_graph", interrupt)
    with pytest.raises(SystemExit):
        reset.reset_job(ctx, "reset-one")
    assert_old_active(store)
    with store.factory() as db:
        value = reset.read_operation(db, "reset-one")[1]
        assert value["status"] == "BUILDING"
        old_generation = value["building_generation_id"]
    store.engine.dispose()  # Reopen persistent state, not in-memory test objects.
    monkeypatch.setattr(reset, "stage_graph", original)
    result = reset.reset_job(worker(store, job_id, attempt=2, owner="new-worker"), "reset-one")
    with store.factory() as db:
        value = reset.read_operation(db, "reset-one")[1]
        assert value["status"] == "PUBLISHED"
        assert value["approved_at"] == approved["approved_at"]
        assert value["preview_hash"] == approved["preview_hash"]
        assert db.scalar(select(func.count()).select_from(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))) == 6
        assert db.scalar(select(func.count()).select_from(Job)) == 1
        assert result["graph_generation_id"] != old_generation


def test_stale_worker_cannot_activate_after_lease_takeover(store):
    _, job_id = confirm(store)
    first = reset.ResetFence(worker(store, job_id), "reset-one")
    prepared = reset._prepare(first)
    second = reset.ResetFence(worker(store, job_id, attempt=2), "reset-one")
    reset._prepare(second)
    with pytest.raises(jobs.JobLeaseLostError):
        reset._publish(first, prepared[1], prepared[2], prepared[3], prepared[4], {})
    with store.factory() as db:
        assert db.get(KnowledgeGraphState, "domain").building_generation_id == second.graph_id
    assert_old_active(store)


def test_actor_revocation_prevents_queued_reset(store):
    _, job_id = confirm(store, actor="EXPERT")
    with store.factory() as db:
        db.get(UserAccount, "EXPERT").role = "ENGINEER"
        db.commit()
    with pytest.raises(reset.ResetError):
        reset.reset_job(worker(store, job_id), "reset-one")
    assert_old_active(store)


@pytest.mark.parametrize("tamper", ["archive", "backup", "plan"])
def test_retained_source_backup_or_plan_tamper_blocks_publish(store, tamper):
    value, job_id = confirm(store)
    if tamper == "archive":
        Path(value["archive_zip"]).write_bytes(b"corrupt archive")
    elif tamper == "backup":
        Path(value["backup"]["path"]).write_bytes(b"corrupt backup")
    else:
        with store.factory() as db:
            row, data = reset.read_operation(db, "reset-one")
            data["approved_plan"]["content_kind"] = "KNOWLEDGE"
            row.payload_json = json_dumps(data)
            db.commit()
    with pytest.raises(reset.ResetError):
        reset.reset_job(worker(store, job_id), "reset-one")
    assert_old_active(store)


def test_concurrent_corpus_change_during_build_prevents_switch(store, monkeypatch):
    _, job_id = confirm(store)
    original = reset.stage_graph
    def changed(*a, **kw):
        result = original(*a, **kw)
        with store.factory() as db:
            db.get(KnowledgeDocument, "old").content += "\nConcurrent edit"
            db.commit()
        return result
    monkeypatch.setattr(reset, "stage_graph", changed)
    with pytest.raises(reset.ResetError):
        reset.reset_job(worker(store, job_id), "reset-one")
    assert_old_active(store)


def test_actual_process_exit_before_commit_rolls_back_and_recovers(store):
    approved, job_id = confirm(store)
    worker(store, job_id, owner="crash-worker")
    child = '''
import os, sys
from app.services import knowledge_reset as reset, retrieval_models
from app.services.jobs import JobContext
retrieval_models._mirror_vectors_to_qdrant = lambda *a, **kw: None
ctx = JobContext(sys.argv[1], lease_owner="crash-worker", lease_seconds=600)
ctx.complete_in_transaction = lambda *a, **kw: os._exit(73)
reset.reset_job(ctx, "reset-one")
'''
    environment = {**os.environ, "DATA_ROOT": str(store.root), "STORAGE_ROOT": str(store.root / "storage"),
        "DATABASE_URL": "sqlite:///" + store.database.as_posix(), "AUTH_MODE": "rbac", "APP_ENV": "test",
        "DEBUG_PLATFORM_ENV_FILE": str(store.temp / "no-environment-file"), "QDRANT_URL": "",
        "PYTHONPATH": str(REPO / "backend"), "PYTHONDONTWRITEBYTECODE": "1"}
    process = subprocess.run([sys.executable, "-c", child, job_id], env=environment,
        capture_output=True, timeout=45, cwd=store.temp)
    assert process.returncode == 73, process.stderr.decode(errors="replace")
    assert_old_active(store)
    with store.factory() as db:
        value = reset.read_operation(db, "reset-one")[1]
        assert value["status"] == "BUILDING"
        job = db.get(Job, job_id)
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    api.job_runner._recover_and_schedule()
    with store.factory() as db:
        assert db.get(Job, job_id).status == "QUEUED"
    reset.reset_job(worker(store, job_id, attempt=2, owner="recovery-worker"), "reset-one")
    with store.factory() as db:
        value = reset.read_operation(db, "reset-one")[1]
        assert value["status"] == "PUBLISHED"
        assert value["preview_hash"] == approved["preview_hash"]
        assert db.scalar(select(func.count()).select_from(KnowledgePublication)) == 7


def test_unrelated_document_cannot_hide_from_reset_with_reservation_metadata(store):
    with store.factory() as db:
        db.get(KnowledgeDocument, "old-draft").metadata_json = json_dumps({"reset_reservation": "reset-one"})
        db.commit()
    _, job_id = confirm(store)
    reset.reset_job(worker(store, job_id), "reset-one")
    with store.factory() as db:
        assert db.get(KnowledgeDocument, "old-draft").review_status == "ARCHIVED"


def test_staged_reserved_content_tamper_does_not_get_overwritten(store):
    _, job_id = confirm(store)
    fence = reset.ResetFence(worker(store, job_id), "reset-one")
    _, _, candidates, _, _ = reset._prepare(fence)
    with store.factory() as db:
        db.get(KnowledgeDocument, candidates[0].id).content = "Unexpected separately authored content"
        db.commit()
    with pytest.raises(reset.ResetError):
        reset.reset_job(worker(store, job_id, attempt=2), "reset-one")
    with store.factory() as db:
        assert db.get(KnowledgeDocument, candidates[0].id).content == "Unexpected separately authored content"
    assert_old_active(store)


def test_external_embedding_requires_consent_and_profile_fence(store):
    with store.factory() as db:
        profile = db.get(ModelProfile, "embedding")
        profile.mode, profile.provider, profile.base_url = "api", "openai_compatible", "https://synthetic.invalid/v1"
        db.commit()
    with pytest.raises(reset.ResetError, match="explicit consent"):
        confirm(store)
    assert not (store.root / "knowledge-reset-archives").exists()
    _, job_id = confirm(store, model_egress_approved=True)
    fence = reset.ResetFence(worker(store, job_id), "reset-one")
    reset._prepare(fence)
    with store.factory() as db:
        db.get(ModelProfile, "embedding").base_url = "https://changed.invalid/v1"
        db.commit()
    with pytest.raises(reset.ResetError, match="profile changed"):
        fence.raise_if_cancelled()
    assert_old_active(store)


def test_cancellation_keeps_old_projection(store):
    _, job_id = confirm(store)
    fence = reset.ResetFence(worker(store, job_id), "reset-one")
    reset._prepare(fence)
    with store.factory() as db:
        db.get(Job, job_id).status = "CANCEL_REQUESTED"
        db.commit()
    with pytest.raises(jobs.JobCancelledError):
        fence.raise_if_cancelled()
    reset._failed(fence, jobs.JobCancelledError("Synthetic cancellation"))
    assert_old_active(store)


def test_terminal_interrupted_worker_recovery_releases_only_its_latch(store):
    approved, job_id = confirm(store)
    fence = reset.ResetFence(worker(store, job_id), "reset-one")
    reset._prepare(fence)
    with store.factory() as db:
        assert reset.recover_abandoned_resets(db) == 0
        db.get(Job, job_id).status = "CANCELLED"
        db.get(Job, job_id).lease_owner = None
        db.commit()
    with store.factory() as db:
        assert reset.recover_abandoned_resets(db) == 1
        db.commit()
        value = reset.read_operation(db, "reset-one")[1]
        assert value["preview_hash"] == approved["preview_hash"]
        assert value["status"] == "CANCELLED"
        assert db.get(KnowledgeGraphState, "domain").building_generation_id is None
        assert reset.recover_abandoned_resets(db) == 0
    assert_old_active(store)


@pytest.mark.parametrize("bad_path", ["../case.md", "/outside.md", "C:/db.md", "folder\\bad.md", "folder/../db.md",
    "folder/NUL.md", "folder/trailing. /file.md", "bundle/references/fault-tree.md:stream"])
def test_zip_slip_and_windows_paths_are_rejected_before_writes(store, bad_path):
    write_zip(store.source, extras=[(bad_path, "Never write this")])
    with pytest.raises(reset.ResetError):
        preview(store)
    assert not (store.root / "knowledge-reset-archives").exists()


@pytest.mark.parametrize("kind", ["symlink", "duplicate", "binary", "extra", "missing", "broken_reference"])
def test_bundle_integrity_rejects_partial_or_unsafe_archives(store, kind):
    files, extras = source_files(), []
    if kind == "symlink":
        info = zipfile.ZipInfo("bundle/link.md")
        info.create_system, info.external_attr = 3, (stat.S_IFLNK | 0o777) << 16
        extras.append((info, "../../cases"))
    elif kind == "duplicate":
        extras.append(("BUNDLE/skill.md", "Shadow"))
    elif kind == "binary":
        files["bundle/SKILL.md"] = b"\xff\xfeinvalid"
    elif kind == "extra":
        extras.append(("bundle/run.ps1", "should never execute"))
    elif kind == "missing":
        del files["bundle/references/report-format.md"]
    else:
        files["bundle/SKILL.md"] += "\n[Missing](references/absent.md)"
    write_zip(store.source, files, extras)
    with pytest.raises(reset.ResetError):
        preview(store)


def test_rejects_implicit_root_git_archive_and_unconfirmed_execute(store):
    with store.factory() as db:
        with pytest.raises(reset.ResetError, match="explicit"):
            reset.preview_reset(db, operation_id="one", data_root=".", source_zip=store.source, actor="ADMIN")
        with pytest.raises(reset.ResetError, match="match"):
            reset.preview_reset(db, operation_id="one", data_root=store.temp, source_zip=store.source, actor="ADMIN")
    with pytest.raises(reset.ResetError, match="confirmation"):
        confirm(store, confirmed=False)
    git = store.temp / "repository"
    (git / ".git").mkdir(parents=True)
    with pytest.raises(reset.ResetError, match="outside Git"):
        confirm(store, archive_root=git / "archives")
    assert not (git / "archives").exists()


def test_other_mutation_blocks_reset(store):
    value = preview(store)
    with store.factory() as db:
        db.add(Job(id="other", kind="reindex_knowledge", status="QUEUED"))
        db.commit()
    with pytest.raises(reset.ResetError, match="Another knowledge mutation"):
        confirm(store, value)
    assert_old_active(store)


@pytest.fixture
def client(store):
    app = FastAPI()
    app.include_router(api.router)
    principal = {"id": "ADMIN", "role": "ADMIN"}
    @app.middleware("http")
    async def identity(request, call_next):
        request.state.principal = principal.copy()
        return await call_next(request)
    def database():
        with store.factory() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app) as client:
        client.principal = principal
        yield client


@pytest.mark.parametrize("role", ["ADMIN", "EXPERT", "ENGINEER", "VIEWER"])
def test_api_admin_expert_gate(store, client, role):
    client.principal.update(id=role, role=role)
    result = client.post("/workbench/knowledge-reset/preview", json={"operation_id": "api-one", "data_root": str(store.root)})
    assert result.status_code == (200 if role in {"ADMIN", "EXPERT"} else 403)


def test_api_forbids_body_file_policy_and_confirm_requires_hashes(store, client):
    data = {"operation_id": "api-one", "data_root": str(store.root)}
    assert client.post("/workbench/knowledge-reset/preview", json={**data, "source_zip": "C:/anything.zip"}).status_code == 422
    assert client.post("/workbench/knowledge-reset/preview", json={**data, "archive_root": "C:/anything"}).status_code == 422
    assert client.post("/workbench/knowledge-reset/confirm", json={**data, "confirmed": True}).status_code == 422
    value = client.post("/workbench/knowledge-reset/preview", json=data).json()
    result = client.post("/workbench/knowledge-reset/confirm", json={**data, "confirmed": True,
        "expected_source_sha256": value["source_sha256"], "expected_preview_hash": value["preview_hash"]})
    assert result.status_code == 200, result.text
    assert result.json()["job"]["status"] == "QUEUED"
    assert client.get("/workbench/knowledge-reset/api-one").json()["operation"]["status"] == "APPROVED"
    client.principal.update(id="ENGINEER", role="ENGINEER")
    assert client.get("/workbench/knowledge-reset/api-one").status_code == 403


@pytest.mark.parametrize("confirmation", [1, "true", False, None])
def test_api_requires_literal_boolean_confirmation(store, client, confirmation):
    payload = {"operation_id": "api-confirm", "data_root": str(store.root), "confirmed": confirmation,
        "expected_source_sha256": "a" * 64, "expected_preview_hash": "b" * 64}
    assert client.post("/workbench/knowledge-reset/confirm", json=payload).status_code == 422


def test_cli_preview_confirm_status_uses_only_explicit_existing_data(store):
    script = REPO / "scripts/reset_knowledge.py"
    environment = {**os.environ, "DEBUG_PLATFORM_ENV_FILE": str(store.temp / "absent-env"),
        "AUTH_MODE": "rbac", "APP_ENV": "test", "DEPLOYMENT_MODE": "standalone",
        "PYTHONDONTWRITEBYTECODE": "1", "QDRANT_URL": ""}
    arguments = ["--data-root", str(store.root), "--source-zip", str(store.source),
        "--operation-id", "cli-one", "--actor", "EXPERT"]
    def run(command, more=()):
        result = subprocess.run([sys.executable, str(script), command, *arguments, *more],
            env=environment, capture_output=True, timeout=30, cwd=store.temp)
        assert result.returncode == 0, result.stderr.decode(errors="replace")
        return json.loads(result.stdout)
    value = run("preview")
    assert len(value["manifest"]) == 6
    with store.factory() as db:
        assert db.scalar(select(func.count()).select_from(Job)) == 0
    queued = run("confirm", ["--confirm", "--expected-source-sha256", value["source_sha256"],
        "--expected-preview-hash", value["preview_hash"]])
    assert queued["job_status"] == "QUEUED"
    status = run("status")
    assert status["operation"]["status"] == "APPROVED"
    assert status["operation"]["job_id"] == queued["job_id"]
    assert_old_active(store)


def test_inventory_is_bounded_to_paths_and_counts_without_secret_output(store, monkeypatch):
    script = REPO / "scripts/reset_knowledge.py"
    spec = importlib.util.spec_from_file_location("reset_cli_inventory", script)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    fake_repo = store.temp / "fake-repository"
    fake_repo.mkdir()
    (fake_repo / ".env").write_text("DATA_ROOT=" + str(store.root) +
        "\nLLM_API_KEY=SECRET_NOT_FOR_OUTPUT\nLLM_BASE_URL=https://private.invalid/v1", encoding="utf-8")
    monkeypatch.setattr(cli, "REPO", fake_repo)
    for key in ("GWAP_SERVER_DATA_ROOT", "DATA_ROOT", "DEBUG_PLATFORM_ENV_FILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(store.temp / "fake-local"))
    result = cli.inventory()
    text = json.dumps(result)
    assert "SECRET_NOT_FOR_OUTPUT" not in text and "private.invalid" not in text
    assert "Synthetic case" not in text and "Old evidence" not in text
    data = next(item for item in result if item["data_root"] == str(store.root))
    assert data["databases"][0]["counts"]["cases"] == 1
    assert_old_active(store)
