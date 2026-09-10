"""New upgrade-import paths only; local hashing and synthetic old business data."""
from datetime import timedelta

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import bundled_skill as api
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, utcnow
from app.models import (Case, Job, KnowledgeChunk, KnowledgeDocument, KnowledgeDraft,
                        KnowledgeEmbedding, KnowledgePublication, ModelProfile, UserAccount)
from app.services import bundled_knowledge as bundled, jobs, knowledge_reset as reset
from app.workbench_models import WorkbenchRecord
from tests.test_installer_bundled_knowledge import server, initialize


def old_corpus(server):
    with server.factory() as db:
        db.add(KnowledgeDocument(id="old", title="Existing wiki", content="# Retain old evidence", active=True,
                                 review_status="ACTIVE", version=3, confidentiality="INTERNAL"))
        db.flush()
        db.add(KnowledgeChunk(id="old-chunk", document_id="old", document_version=3, chunk_index=0,
                              content="Retain old evidence"))
        db.add(KnowledgeDraft(id="old-draft", document_id="old", base_version=3,
                              snapshot_json=json_dumps({"title": "My unrelated draft", "content": "Keep draft"})))
        db.add(WorkbenchRecord(id="template-network", kind="template_default",
                               payload_json=json_dumps({"document_id": "old", "custom": True})))
        db.commit()
    assert initialize(server)["status"] == "PRESERVED"


def preview(server, operation_id="add-bundle"):
    with server.factory() as db:
        return reset.preview_reset(db, operation_id=operation_id, source_zip=server.source,
            data_root=server.root, actor="admin", preserve_existing=True)


def confirm(server, plan):
    with server.factory() as db:
        return reset.confirm_reset(db, operation_id=plan["operation_id"], source_zip=server.source,
            data_root=server.root, actor="admin", preserve_existing=True, confirmed=True,
            model_egress_approved=False, expected_source_sha256=plan["source_sha256"],
            expected_preview_hash=plan["preview_hash"])


def execute(server, operation_id="add-bundle", attempt=1):
    with server.factory() as db:
        _, value = reset.read_operation(db, operation_id)
        job = db.get(Job, value["job_id"])
        job.status, job.attempt, job.lease_owner = "RUNNING", attempt, "additive-test"
        job.lease_expires_at = utcnow() + timedelta(minutes=5)
        db.commit()
        ctx = jobs.JobContext(job.id, lease_owner=job.lease_owner, lease_seconds=300)
    return reset.reset_job(ctx, operation_id)


def test_old_corpus_import_restart_and_duplicate_confirmation(server):
    old_corpus(server)
    plan = preview(server)
    assert plan["can_confirm"] and plan["counts"]["retired_documents"] == 0
    first, job = confirm(server, plan)
    assert confirm(server, plan)[1].id == job.id
    assert initialize(server)["operation_id"] == "add-bundle"
    result = execute(server)
    assert result["retired_documents"] == 0 and len(result["documents"]) == 6
    assert initialize(server)["status"] == "PUBLISHED"
    with server.factory() as db:
        assert db.get(KnowledgeDocument, "old").version == 3
        assert db.get(KnowledgeDocument, "old").content == "# Retain old evidence"
        assert db.get(KnowledgeDraft, "old-draft").status == "DRAFT"
        assert json_loads(db.get(WorkbenchRecord, "template-network").payload_json, {}) == {"document_id": "old", "custom": True}
        assert db.get(Case, "case").title == "Retain this synthetic case"
        profile = db.get(ModelProfile, "embedding")
        assert db.scalar(select(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id == "old-chunk",
            KnowledgeEmbedding.generation_id == profile.active_embedding_generation_id))
        docs = list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))))
        assert len(docs) == 7
        publication = db.scalar(select(KnowledgePublication))
        assert json_loads(publication.manifest_json, {})["document_versions"]["old"] == 3
    assert not preview(server, "duplicate")["can_confirm"]
    assert {i["disposition"] for i in preview(server, "duplicate")["inspection"]} == {"EXISTS"}


@pytest.mark.parametrize("location", ["active", "deleted", "draft"])
def test_conflicting_or_deleted_files_never_overwritten(server, location):
    old_corpus(server)
    with server.factory() as db:
        if location == "draft":
            db.get(KnowledgeDraft, "old-draft").snapshot_json = json_dumps({"title": "SKILL.md"})
        else:
            db.add(KnowledgeDocument(id="conflict", title="SKILL.md", content="Human changes",
                active=location == "active", review_status="ACTIVE" if location == "active" else "ARCHIVED"))
        db.commit()
    plan = preview(server)
    assert not plan["can_confirm"] and any(i["disposition"] == "CONFLICT" for i in plan["inspection"])
    with pytest.raises(reset.ResetError, match="conflicts"):
        confirm(server, plan)
    with server.factory() as db:
        assert not db.scalar(select(Job))


def test_stale_preview_rejected(server):
    old_corpus(server)
    plan = preview(server)
    with server.factory() as db:
        db.get(KnowledgeDocument, "old").content = "Changed after preview"
        db.commit()
    with pytest.raises(reset.ResetError, match="changed"):
        confirm(server, plan)


def test_failed_index_retry_keeps_old_corpus(server, monkeypatch):
    old_corpus(server)
    confirm(server, preview(server))
    original = reset.index_embeddings
    def fail(*args, **kwargs):
        raise ValueError("Synthetic interrupted index")
    monkeypatch.setattr(reset, "index_embeddings", fail)
    with pytest.raises(reset.ResetError):
        execute(server)
    with server.factory() as db:
        assert db.get(KnowledgeDocument, "old").active
        assert len(list(db.scalars(select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True))))) == 1
    monkeypatch.setattr(reset, "index_embeddings", original)
    execute(server, attempt=2)
    assert initialize(server)["status"] == "PUBLISHED"


def test_api_roles_confirmation_and_server_selected_paths(server, monkeypatch):
    old_corpus(server)
    with server.factory() as db:
        for role in ("EXPERT", "ENGINEER"):
            db.add(UserAccount(id=role, username=role, role=role, display_name=role, active=True))
        db.commit()
    app = FastAPI()
    app.include_router(api.router)
    def sessions():
        with server.factory() as db:
            yield db
    app.dependency_overrides[get_db] = sessions
    @app.middleware("http")
    async def principal(request: Request, call_next):
        role = request.headers.get("x-role", "ADMIN")
        request.state.principal = {"id": "admin" if role == "ADMIN" else role, "role": role}
        return await call_next(request)
    read_package = bundled.read_packaged_bundle
    monkeypatch.setattr(bundled, "read_packaged_bundle", lambda: read_package(server.package))
    monkeypatch.setattr(api.job_runner, "_schedule", lambda *a: None)
    with TestClient(app) as client:
        url = "/workbench/bundled-skill"
        assert client.post(url + "/preview", json={"operation_id": "api"}, headers={"x-role": "ENGINEER"}).status_code == 403
        response = client.post(url + "/preview", json={"operation_id": "api"}, headers={"x-role": "EXPERT"})
        assert response.status_code == 200, response.text
        assert all(i["disposition"] == "ADD" for i in response.json()["manifest"])
        assert client.post(url + "/preview", json={"operation_id": "api", "data_root": "C:/"}).status_code == 422
        plan = client.post(url + "/preview", json={"operation_id": "api"}).json()
        payload = {"operation_id": "api", "expected_source_sha256": plan["source_sha256"],
                   "expected_preview_hash": plan["preview_hash"], "confirmed": True}
        for invalid in (False, 1, "true"):
            assert client.post(url + "/confirm", json={**payload, "confirmed": invalid}).status_code == 422
        response = client.post(url + "/confirm", json=payload)
        assert response.status_code == 200, response.text
        assert client.get(url + "/api").json()["operation"]["approved_plan"]["preserve_existing"] is True
        assert client.get(url + "/api", headers={"x-role": "ENGINEER"}).status_code == 403


def test_cancelled_import_retains_previous_active_vectors(server, monkeypatch):
    old_corpus(server)
    with server.factory() as db:
        profile = db.get(ModelProfile, "embedding")
        reset.index_embeddings(db, profile, [db.get(KnowledgeChunk, "old-chunk")],
                               generation_id="old-generation", activate_if_missing=True)
        db.commit()
    confirm(server, preview(server))
    def cancel(*args, **kwargs):
        raise jobs.JobCancelledError("Synthetic cancel during indexing")
    monkeypatch.setattr(reset, "index_embeddings", cancel)
    with pytest.raises(jobs.JobCancelledError):
        execute(server)
    with server.factory() as db:
        assert db.get(ModelProfile, "embedding").active_embedding_generation_id == "old-generation"
        assert db.scalar(select(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id == "old-chunk",
            KnowledgeEmbedding.generation_id == "old-generation"))
        assert db.get(KnowledgeDocument, "old").active
        assert reset.read_operation(db, "add-bundle")[1]["status"] == "CANCELLED"
