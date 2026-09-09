"""New expert/ordinary contribution boundaries and atomic reviewed publication only."""
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api import knowledge, knowledge_contributions, knowledge_curation as curation_api
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, json_loads
from app.knowledge_contribution_models import KnowledgeContribution, KnowledgeContributionRevision
from app.model_access_models import ModelProfileAccess
from app.models import (AuditEvent, Job, KnowledgeChunk, KnowledgeCurationSession, KnowledgeCurationSourceFile,
    KnowledgeDocument, KnowledgeGraphState, KnowledgePublication, ModelProfile, UserAccount)
from app.services import jobs, knowledge_contribution_publication as publication, knowledge_curation as curation
from app.services.knowledge_access import authorize_routing_job, knowledge_kind
from app.services.knowledge_contributions import (update_contribution)
from app.workbench_models import WorkbenchRecord


def who(name="owner"):
    return {"id": name, "role": {"owner": "ENGINEER", "other": "ENGINEER", "expert": "EXPERT",
        "admin": "ADMIN", "viewer": "VIEWER"}[name], "type": "user"}


@pytest.fixture
def scope(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'knowledge-review.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(publication, "SessionLocal", factory)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    with factory() as db:
        for name in ("owner", "other", "expert", "admin", "viewer"):
            db.add(UserAccount(id=name, username=name, display_name=name, role=who(name)["role"]))
        db.add_all([
            KnowledgeDocument(id="wiki", title="Shared Wiki", content="# Existing\nORIGINAL_EVIDENCE",
                source_type="document", metadata_json=json_dumps({"content_kind": "KNOWLEDGE"})),
            KnowledgeDocument(id="skill", title="Required Skill", content="# Method\nORIGINAL_METHOD",
                source_type="analysis_skill", metadata_json=json_dumps({"content_kind": "SKILL"})),
            ModelProfile(id="embed", name="Synthetic hashing", task_type="embedding", provider="hashing", mode="local",
                model_name="hashing", is_active=True, enabled=True, active_embedding_generation_id="old-vector"),
            ModelProfile(id="private-owner", name="Owner private", task_type="chat", provider="openai_compatible", mode="api",
                model_name="synthetic", enabled=True, is_active=False),
            ModelProfile(id="shared-chat", name="Shared", task_type="chat", provider="openai_compatible", mode="api",
                model_name="synthetic", enabled=True, is_active=True),
            KnowledgeGraphState(id="domain", status="READY", active_generation_id="old-graph"),
        ])
        db.flush()
        db.add_all([ModelProfileAccess(profile_id="private-owner", owner_id="owner", visibility="PRIVATE"),
                    ModelProfileAccess(profile_id="shared-chat", owner_id="admin", visibility="SHARED")])
        for key in ("wiki", "skill"):
            db.add(KnowledgeChunk(id="chunk-" + key, document_id=key, document_version=1, chunk_index=0,
                                  heading="Existing", content="ORIGINAL_" + key))
        db.commit()

    def auth(request: Request):
        request.state.principal = who(request.headers.get("X-Test-User", "owner"))

    app = FastAPI(dependencies=[Depends(auth)])
    for router in (knowledge_contributions.router, curation_api.router, knowledge.router):
        app.include_router(router)

    def get_test_db():
        with factory() as db:
            yield db
    app.dependency_overrides[get_db] = get_test_db
    with TestClient(app) as client:
        yield factory, client
    engine.dispose()


def draft(client, *, operation="CREATE", content_kind="KNOWLEDGE", target=None):
    values = {"operation": operation, "content_kind": content_kind, "title": "A proposed document", "content": "# Proposed\nREVIEW_ME"}
    if target:
        values["target_document_id"] = target
    response = client.post("/knowledge-contributions", json=values)
    assert response.status_code == 201, response.text
    return response.json()


def submitted(client, **kwargs):
    row = draft(client, **kwargs)
    response = client.post(f"/knowledge-contributions/{row['id']}/submit", json={"expected_version": row["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def approval(client, row, *, user="expert"):
    return client.post(f"/knowledge-contributions/{row['id']}/review", headers={"X-Test-User": user},
        json={"expected_version": row["version"], "expected_content_hash": row["content_hash"], "action": "APPROVE"})


def run_job(factory, row, *, attempt=1, owner="publisher"):
    with factory() as db:
        job = db.get(Job, row["publication_job_id"])
        job.status, job.lease_owner, job.attempt = "RUNNING", owner, attempt
        inputs, job_id = json_loads(job.input_json, {}), job.id
        db.commit()
    return publication.publication_job(jobs.JobContext(job_id, lease_owner=owner), **inputs)


@pytest.mark.parametrize("actor", ["other", "expert", "admin"])
def test_private_draft_owner_isolation(scope, actor):
    _, client = scope
    row = draft(client)
    headers = {"X-Test-User": actor}
    assert client.get(f"/knowledge-contributions/{row['id']}", headers=headers).status_code == 404
    assert client.patch(f"/knowledge-contributions/{row['id']}", headers=headers,
                        json={"expected_version": row["version"], "content": "Stolen"}).status_code == 404
    assert client.delete(f"/knowledge-contributions/{row['id']}?expected_version=1", headers=headers).status_code == 404
    assert client.get("/knowledge-contributions", headers=headers).json() == []


def test_owner_updates_deletes_without_publishing(scope):
    factory, client = scope
    row = draft(client)
    changed = client.patch(f"/knowledge-contributions/{row['id']}", json={"expected_version": 1, "content": "# Edited\nOnly a draft"})
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert client.patch(f"/knowledge-contributions/{row['id']}", json={"expected_version": 1, "content": "Stale"}).status_code == 409
    assert client.delete(f"/knowledge-contributions/{row['id']}?expected_version=2").status_code == 200
    with factory() as db:
        assert len(list(db.scalars(select(KnowledgeDocument)))) == 2
        assert len(list(db.scalars(select(KnowledgeContributionRevision)))) == 3


def test_skill_filename_does_not_grant_skill_or_active_write(scope):
    factory, client = scope
    response = client.post("/knowledge-contributions/upload", files={"file": ("SKILL.md", b"# Pretend Skill\nSome text", "text/markdown")})
    assert response.status_code == 201, response.text
    assert response.json()["content_kind"] == "KNOWLEDGE"
    assert response.json()["candidate"]["metadata"]["content_kind"] == "KNOWLEDGE"
    assert client.post("/knowledge", json={"title": "Skill", "content": "Bypass", "source_type": "analysis_skill"}).status_code == 403
    assert client.patch("/knowledge/skill", json={"expected_lock_version": 1, "content": "Bypass"}).status_code == 403
    assert client.delete("/knowledge/skill").status_code == 403
    with factory() as db:
        assert db.get(KnowledgeDocument, "skill").content.endswith("ORIGINAL_METHOD")


def test_viewer_cannot_contribute_or_extract(scope):
    _, client = scope
    headers = {"X-Test-User": "viewer"}
    assert client.get("/knowledge-contributions", headers=headers).status_code == 403
    assert client.get("/knowledge-curations", headers=headers).status_code == 403


@pytest.mark.parametrize("actor", ["owner", "other"])
def test_only_managers_review_shared_contributions(scope, actor):
    _, client = scope
    row = submitted(client)
    assert approval(client, row, user=actor).status_code == 403
    assert client.patch(f"/knowledge-contributions/{row['id']}/review-draft", headers={"X-Test-User": actor},
        json={"expected_version": row["version"], "content": "Unauthorized"}).status_code == 403


def test_exact_review_edit_hash_and_idempotent_approval(scope):
    factory, client = scope
    old = submitted(client)
    response = client.patch(f"/knowledge-contributions/{old['id']}/review-draft", headers={"X-Test-User": "expert"},
        json={"expected_version": old["version"], "content": "# Corrected\nEXPERT_REVIEWED", "comment": "Corrected evidence"})
    assert response.status_code == 200, response.text
    edited = response.json()
    assert edited["content_hash"] != old["content_hash"]
    assert "REVIEW_ME" in edited["original"]["content"]
    assert "EXPERT_REVIEWED" in edited["diff"]
    assert approval(client, old).status_code == 409
    approved = approval(client, edited)
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    again = approval(client, edited)
    assert again.status_code == 200 and again.json()["publication_job_id"] == approved.json()["publication_job_id"]
    assert client.patch(f"/knowledge-contributions/{old['id']}/review-draft", headers={"X-Test-User": "expert"},
        json={"expected_version": approved.json()["version"], "content": "After approval"}).status_code == 409
    with factory() as db:
        assert len(list(db.scalars(select(Job)))) == 1
        assert not db.get(KnowledgeDocument, approved.json()["published_document_id"]).active
        assert len(list(db.scalars(select(AuditEvent)))) == 4


@pytest.mark.parametrize("target,kind", [("wiki", "KNOWLEDGE"), ("skill", "SKILL")])
def test_target_controls_kind_and_shared_changes_wait_for_review(scope, target, kind):
    factory, client = scope
    row = submitted(client, operation="UPDATE", content_kind="KNOWLEDGE", target=target)
    assert row["content_kind"] == kind
    with factory() as db:
        assert "ORIGINAL" in db.get(KnowledgeDocument, target).content
    approved = approval(client, row).json()
    result = run_job(factory, approved)
    with factory() as db:
        document = db.get(KnowledgeDocument, target)
        assert document.active and "REVIEW_ME" in document.content
        assert knowledge_kind(document) == kind and document.version == 2
        assert db.get(KnowledgeChunk, "chunk-" + target).content.startswith("ORIGINAL")
        manifest = json_loads(db.get(KnowledgePublication, result["publication_id"]).manifest_json, {})
        assert manifest["approved_hash"] == row["content_hash"]
        assert db.get(ModelProfile, "embed").active_embedding_generation_id == result["embedding_generation_id"]
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == result["graph_generation_id"]


def test_new_wiki_publication_switches_document_and_job_together(scope):
    factory, client = scope
    row = approval(client, submitted(client)).json()
    result = run_job(factory, row)
    with factory() as db:
        assert db.get(Job, row["publication_job_id"]).status == "COMPLETED"
        assert db.get(KnowledgeContribution, row["id"]).status == "PUBLISHED"
        assert db.get(KnowledgeDocument, result["document_id"]).active


def test_reviewed_delete_archives_without_destroying_old_evidence(scope):
    factory, client = scope
    row = approval(client, submitted(client, operation="DELETE", target="wiki")).json()
    with factory() as db:
        assert db.get(KnowledgeDocument, "wiki").active
    result = run_job(factory, row)
    with factory() as db:
        assert not db.get(KnowledgeDocument, "wiki").active
        assert db.get(KnowledgeDocument, "wiki").review_status == "ARCHIVED"
        assert db.get(KnowledgeChunk, "chunk-wiki") is not None
        manifest = json_loads(db.get(KnowledgePublication, result["publication_id"]).manifest_json, {})
        assert "wiki" not in manifest["document_versions"]


def test_graph_failure_retains_old_publication_and_exact_approval(scope, monkeypatch):
    factory, client = scope
    row = approval(client, submitted(client, operation="UPDATE", target="wiki")).json()
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic interrupted graph build")
    original = publication.stage_graph
    monkeypatch.setattr(publication, "stage_graph", fail)
    with pytest.raises(ValueError):
        run_job(factory, row)
    with factory() as db:
        assert db.get(KnowledgeDocument, "wiki").content.endswith("ORIGINAL_EVIDENCE")
        assert db.get(ModelProfile, "embed").active_embedding_generation_id == "old-vector"
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == "old-graph"
        assert db.get(KnowledgeContribution, row["id"]).approved_hash == row["approved_hash"]
    monkeypatch.setattr(publication, "stage_graph", original)
    run_job(factory, row, attempt=2, owner="replacement-worker")
    with factory() as db:
        assert db.get(KnowledgeContribution, row["id"]).status == "PUBLISHED"


def test_expired_worker_cannot_publish_and_same_approval_resumes(scope):
    factory, client = scope
    row = approval(client, submitted(client, operation="UPDATE", target="wiki")).json()
    with factory() as db:
        job = db.get(Job, row["publication_job_id"])
        job.status, job.lease_owner, job.attempt = "RUNNING", "old-worker", 1
        data = json_loads(job.input_json, {})
        db.commit()
    fence = publication.ContributionFence(jobs.JobContext(row["publication_job_id"], lease_owner="old-worker"), **data)
    snapshot = publication.prepare(fence)
    with factory() as db:
        job = db.get(Job, row["publication_job_id"])
        job.lease_owner, job.attempt = "new-worker", 2
        db.commit()
    with pytest.raises(jobs.JobLeaseLostError):
        publication.build(fence, snapshot)
    run_job(factory, row, attempt=2, owner="new-worker")
    with factory() as db:
        assert db.get(KnowledgeContribution, row["id"]).status == "PUBLISHED"


def test_reviewer_demotion_revokes_pending_publication(scope):
    factory, client = scope
    row = approval(client, submitted(client)).json()
    with factory() as db:
        db.get(UserAccount, "expert").role = "ENGINEER"
        db.commit()
    with pytest.raises(ValueError):
        run_job(factory, row)
    with factory() as db:
        assert not db.get(KnowledgeDocument, row["published_document_id"]).active


def test_owned_publication_jobs_read_only_for_engineer(scope):
    factory, client = scope
    row = approval(client, submitted(client)).json()
    with factory() as db:
        assert authorize_routing_job(db, row["publication_job_id"], who())
        with pytest.raises(HTTPException):
            authorize_routing_job(db, row["publication_job_id"], who("other"))
        with pytest.raises(HTTPException):
            authorize_routing_job(db, row["publication_job_id"], who(), method="POST")
        assert authorize_routing_job(db, row["publication_job_id"], who("expert"), method="POST")


CASE_MARKDOWN = "# Case\n\n" + "\n\n".join("## " + heading + "\nObserved [SRC-0001:L1-L2]" for heading in
    ("错误形式", "日志分析", "错误定位", "解决方案", "验证结果", "适用范围与限制", "来源证据"))


def extraction(factory):
    with factory() as db:
        session = KnowledgeCurationSession(id="extraction", created_by="owner", status="REVIEWING",
            draft_version=1, draft_title="A case", draft_markdown=CASE_MARKDOWN, model_profile_id="private-owner",
            model_snapshot_json=json_dumps({"profile_id": "private-owner", "config": {"api_key": "NEVER_RETURN"}}),
            source_manifest_json=json_dumps({"model_egress_consent": False}))
        db.add(session)
        db.flush()
        db.add(KnowledgeCurationSourceFile(id="source", session_id=session.id, source_ref="SRC-0001",
            relative_path="synthetic.log", stored_path="synthetic-unused", sha256="0" * 64, size_bytes=20,
            line_count=2, included=True))
        db.add(Job(id="extraction-job", kind="curate_knowledge_folder", input_json=json_dumps({"session_id": session.id})))
        db.commit()


def test_curation_sessions_sources_and_jobs_are_isolated(scope):
    factory, client = scope
    extraction(factory)
    for actor in ("other", "expert"):
        headers = {"X-Test-User": actor}
        assert client.get("/knowledge-curations", headers=headers).json() == []
        assert client.get("/knowledge-curations/extraction", headers=headers).status_code == 404
        assert client.get("/knowledge-curations/extraction/sources/source/preview", headers=headers).status_code == 404
        with factory() as db, pytest.raises(HTTPException):
            authorize_routing_job(db, "extraction-job", who(actor))
    own = client.get("/knowledge-curations/extraction")
    assert own.status_code == 200 and "NEVER_RETURN" not in own.text


def test_curation_confirmation_creates_only_owned_ordinary_draft(scope):
    factory, client = scope
    extraction(factory)
    result = client.post("/knowledge-curations/extraction/confirm", json={"expected_draft_version": 1})
    assert result.status_code == 200, result.text
    row = result.json()["contribution"]
    assert row["content_kind"] == "KNOWLEDGE" and row["status"] == "DRAFT"
    with factory() as db:
        document = db.get(KnowledgeDocument, result.json()["knowledge_document"]["id"])
        assert not document.active
        assert list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))) == []
    again = client.post("/knowledge-curations/extraction/confirm", json={"expected_draft_version": 1})
    assert again.status_code == 200 and again.json()["contribution"]["id"] == row["id"]
    submitted_row = client.post(f"/knowledge-contributions/{row['id']}/submit", json={"expected_version": row["version"]})
    assert submitted_row.status_code == 200
    reviewed = client.get("/knowledge-curations/extraction", headers={"X-Test-User": "expert"})
    assert reviewed.status_code == 200 and reviewed.json()["model_profile_id"] is None
    assert client.post("/knowledge-curations/extraction/chat", headers={"X-Test-User": "expert"},
        json={"instruction": "Use private model", "expected_draft_version": 1}).status_code == 404


def test_old_curation_consent_off_remains_off_and_private_model_denied(scope, monkeypatch):
    factory, _ = scope
    extraction(factory)
    monkeypatch.setattr(curation, "get_llm_provider", lambda profile: SimpleNamespace(model_name="synthetic"))
    with factory() as db:
        with pytest.raises(curation.CurationError, match="consent"):
            curation.resolve_session_model(db, db.get(KnowledgeCurationSession, "extraction"))
        with pytest.raises(curation.CurationError, match="not found"):
            curation.resolve_curation_model(db, "private-owner", who("other"))


def test_new_curation_defaults_consent_on_without_indexing(scope, monkeypatch):
    factory, client = scope
    async def fake_uploads(*args, **kwargs):
        return []
    monkeypatch.setattr(curation, "get_llm_provider", lambda profile: SimpleNamespace(model_name="synthetic"))
    monkeypatch.setattr(curation_api, "persist_curation_uploads", fake_uploads)
    response = client.post("/knowledge-curations", files={"files": ("case.md", b"synthetic", "text/markdown")},
                           data={"model_profile_id": "private-owner"})
    assert response.status_code == 202, response.text
    assert response.json()["session"]["source_manifest"]["model_egress_consent"] is True
    with factory() as db:
        assert len(list(db.scalars(select(Job)))) == 1
        assert len(list(db.scalars(select(KnowledgeDocument)))) == 2


def test_reviewer_ai_uses_own_model_and_preserves_original_and_dialogue(scope, monkeypatch):
    factory, client = scope
    row = submitted(client)
    from app.services import knowledge_contribution_review as review_service
    async def generate(*args, **kwargs):
        return {"title": "Reviewed", "revised_markdown": "# Revised\nSUPPORTED_CONTENT", "assistant_message": "Corrected."}
    monkeypatch.setattr(review_service, "get_llm_provider", lambda profile: SimpleNamespace(generate_json=generate))
    rejected = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Refine", "model_profile_id": "private-owner"})
    assert rejected.status_code == 404
    refined = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Refine", "model_profile_id": "shared-chat"})
    assert refined.status_code == 200, refined.text
    assert len(refined.json()["messages"]) == 2
    assert "REVIEW_ME" in refined.json()["original"]["content"]
    assert "SUPPORTED_CONTENT" in refined.json()["candidate"]["content"]
    assert approval(client, refined.json()).status_code == 200


def test_ai_result_cannot_overwrite_concurrent_human_revision(scope, monkeypatch):
    factory, client = scope
    row = submitted(client)
    from app.services import knowledge_contribution_review as review_service
    async def generate(*args, **kwargs):
        with factory() as db:
            current = db.get(KnowledgeContribution, row["id"])
            update_contribution(db, current, who("expert"), {"expected_version": current.version,
                "content": "# Concurrent\nHUMAN_EDIT"}, review=True)
            db.commit()
        return {"title": "Model", "revised_markdown": "# Stale\nMODEL_EDIT", "assistant_message": "Stale."}
    monkeypatch.setattr(review_service, "get_llm_provider", lambda profile: SimpleNamespace(generate_json=generate))
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Refine"})
    assert response.status_code == 409
    with factory() as db:
        assert "HUMAN_EDIT" in db.get(KnowledgeContribution, row["id"]).candidate_json


def test_case_conclusion_review_keeps_original_report(scope):
    factory, client = scope
    with factory() as db:
        db.add(WorkbenchRecord(id="library-case", kind="library", owner_id="owner",
            payload_json=json_dumps({"title": "Case conclusion", "content": "Original human conclusion",
                "report_markdown": "# Original report\nORIGINAL_REPORT", "status": "PENDING"})))
        db.commit()
    response = client.post("/knowledge-contributions/from-library/library-case")
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["status"] == "SUBMITTED"
    changed = client.patch(f"/knowledge-contributions/{row['id']}/review-draft", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "content": "# Reviewed conclusion\nSUPPORTED_CONCLUSION"})
    approved = approval(client, changed.json()).json()
    run_job(factory, approved)
    with factory() as db:
        value = json_loads(db.get(WorkbenchRecord, "library-case").payload_json, {})
        assert value["status"] == "CONFIRMED"
        assert "ORIGINAL_REPORT" in value["report_markdown"]
        assert "SUPPORTED_CONCLUSION" in value["reviewed_conclusion"]
