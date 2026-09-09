"""HTTP boundaries for reviewed-job controls, approval outbox and AI model changes."""
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import jobs as jobs_api, knowledge_drafts as drafts_api, knowledge_intake as intake_api
from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads
from app.knowledge_acl_models import KnowledgeAccess
from app.knowledge_contribution_models import KnowledgeContribution
from app.models import AuditEvent, Job, KnowledgeDocument, KnowledgeDraft, ModelProfile
from app.services import knowledge_contribution_review as review_service, knowledge_publication
from app.services.access_control import authorize_request
from app.services.jobs import job_runner
from tests.test_knowledge_contributions_iteration import approval, submitted, who
from tests.test_knowledge_contributions_iteration import scope as scope
from tests.test_knowledge_review_boundaries import legacy_proposal


def api_client(factory, routers, *, central=False, raise_server_exceptions=False):
    def authenticate(request: Request):
        request.state.principal = who(request.headers.get("X-Test-User", "owner"))
        if central:
            with factory() as db:
                authorize_request(db, request, request.state.principal)

    def test_db():
        with factory() as db:
            yield db

    app = FastAPI(dependencies=[Depends(authenticate)])
    for router in routers:
        app.include_router(router, prefix=get_settings().api_prefix)
    app.dependency_overrides[get_db] = test_db
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def api_path(path):
    return get_settings().api_prefix.rstrip("/") + path


@pytest.mark.parametrize("layer", ["central", "api"])
@pytest.mark.parametrize("action", ["cancel", "retry"])
def test_owner_cannot_post_approved_job_controls_at_each_http_boundary(scope, monkeypatch, layer, action):
    factory, contributions = scope
    row = approval(contributions, submitted(contributions)).json()
    job_id = row["publication_job_id"]
    original_status = "QUEUED" if action == "cancel" else "FAILED"
    with factory() as db:
        job = db.get(Job, job_id)
        job.status = original_status
        assert (job.max_attempts, job.timeout_seconds) == (3, 1800)
        db.commit()
    monkeypatch.setattr(job_runner, "_schedule", lambda job_id: None)
    if layer == "central":
        # Exercise the real HTTP route with only the central guard protecting it.
        # Leaving both enabled would hide a missing method at either boundary.
        monkeypatch.setattr(jobs_api, "_protect_system_job", lambda *args: None)
    with api_client(factory, [jobs_api.router], central=layer == "central") as client:
        path = api_path(f"/jobs/{job_id}")
        assert client.get(path).status_code == 200
        assert client.get(path, headers={"X-Test-User": "other"}).status_code == 404
        response = client.post(f"{path}/{action}")
        assert response.status_code == 403, response.text
        with factory() as db:
            assert db.get(Job, job_id).status == original_status
            assert len(list(db.scalars(select(Job)))) == 1
            pending = db.get(KnowledgeContribution, row["id"])
            assert pending.approved_hash == row["approved_hash"] and pending.status == "APPROVED"
        allowed = client.post(f"{path}/{action}", headers={"X-Test-User": "expert"})
        assert allowed.status_code == 200, allowed.text
        if action == "cancel":
            assert allowed.json()["id"] == job_id and allowed.json()["status"] == "CANCELLED"
        else:
            assert allowed.json()["id"] != job_id and allowed.json()["status"] == "QUEUED"
            assert allowed.json()["max_attempts"] == 3


@pytest.mark.parametrize("change", ["configuration", "disabled"])
def test_ai_review_rejects_model_changes_during_request(scope, monkeypatch, change):
    factory, client = scope
    row = submitted(client)
    before = client.get(f"/knowledge-contributions/{row['id']}").json()

    async def generate(*args, **kwargs):
        with factory() as db:
            profile = db.get(ModelProfile, "shared-chat")
            if change == "configuration":
                profile.config_json = json_dumps({"temperature": 0.7})
            else:
                profile.enabled = False
            db.commit()
        return {"title": "Changed model", "revised_markdown": "# Stale response\nDo not persist",
                "assistant_message": "Generated while the model changed"}

    monkeypatch.setattr(review_service, "get_llm_provider", lambda profile: SimpleNamespace(generate_json=generate))
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Revise", "model_profile_id": "shared-chat"})
    assert response.status_code == 409, response.text
    current = client.get(f"/knowledge-contributions/{row['id']}").json()
    for field in ("version", "content_hash", "candidate", "original", "messages", "revisions"):
        assert current[field] == before[field]


def test_ai_review_disabled_model_is_rejected_before_provider_call(scope, monkeypatch):
    factory, client = scope
    row = submitted(client)
    with factory() as db:
        db.get(ModelProfile, "shared-chat").enabled = False
        db.commit()

    def unexpected_provider(profile):
        pytest.fail("A disabled model must not receive pending review content")

    monkeypatch.setattr(review_service, "get_llm_provider", unexpected_provider)
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Revise", "model_profile_id": "shared-chat"})
    assert response.status_code == 409, response.text


def approval_request(factory, kind):
    if kind == "draft":
        legacy_proposal(factory)
        with factory() as db:
            version = db.get(KnowledgeDraft, "legacy-draft").version
        return drafts_api, "/knowledge/wiki/draft/review", {
            "draft_id": "legacy-draft", "action": "APPROVE", "expected_version": version, "comment": "Exact review"}
    content = "# Historical source\nHuman-verified synthetic content"
    with factory() as db:
        db.add(KnowledgeDocument(id="intake", title="Historical", content=content, active=False,
            review_status="DRAFT", metadata_json=json_dumps({"content_kind": "SKILL"})))
        db.commit()
    return intake_api, "/knowledge/intake/adopt-human-verified", {
        "human_verified": True, "expected_lock_version": 1, "content_sha256": hashlib.sha256(content.encode()).hexdigest()}


@pytest.mark.parametrize("kind", ["draft", "intake"])
def test_approval_api_commits_exact_outbox_before_dispatch_interruption(scope, monkeypatch, kind):
    factory, _ = scope
    module, path, payload = approval_request(factory, kind)
    scheduled = []

    def interrupted_dispatch(job_id):
        # The dispatcher has a different connection: nothing it sees may depend
        # on uncommitted request state or an after-approval JobRunner.submit.
        with factory() as db:
            job = db.get(Job, job_id)
            assert job is not None and job.status == "QUEUED"
            assert (job.max_attempts, job.timeout_seconds) == (3, 1800)
            data = json_loads(job.input_json, {})
            proposal = db.get(KnowledgeDraft, data["draft_id"])
            assert proposal.publication_job_id == job_id and proposal.reviewed_by == "expert"
            assert data["_approval_draft_version"] == proposal.version
            assert data["_approval_snapshot_sha256"] == hashlib.sha256(proposal.snapshot_json.encode()).hexdigest()
            audit = db.scalar(select(AuditEvent).where(AuditEvent.action == "knowledge.publication.approve"))
            assert audit is not None and json_loads(audit.details_json, {})["job_id"] == job_id
            document = db.get(KnowledgeDocument, data["document_id"])
            if kind == "draft":
                assert document.active and document.content.endswith("ORIGINAL_EVIDENCE")
                assert proposal.review_comment == "Exact review"
            else:
                assert not document.active and document.review_status == "IN_REVIEW"
                assert document.trust_level == "HIGH" and document.lock_version == 2
                attestation = json_loads(document.metadata_json, {})["human_verified_source"]
                assert attestation["confirmed_by"] == "expert" and attestation["content_sha256"] == payload["content_sha256"]
        scheduled.append(job_id)
        raise RuntimeError("Synthetic interruption after commit, before thread dispatch")

    monkeypatch.setattr(job_runner, "_schedule", interrupted_dispatch)
    with api_client(factory, [module.router]) as client:
        response = client.post(api_path(path), headers={"X-Test-User": "expert"}, json=payload)
    assert response.status_code == 200, response.text
    assert scheduled == [response.json()["job"]["id"]]
    with factory() as db:
        assert db.get(Job, scheduled[0]).status == "QUEUED"


@pytest.mark.parametrize("kind", ["draft", "intake"])
def test_approval_api_rolls_back_review_and_outbox_on_transaction_failure(scope, monkeypatch, kind):
    factory, _ = scope
    module, path, payload = approval_request(factory, kind)

    def interrupted_enqueue(*args):
        job = knowledge_publication.enqueue_publication(*args)
        assert job.id  # Real helper has flushed both approval and job before failure.
        raise ValueError("Synthetic transaction failure before commit")

    monkeypatch.setattr(module, "enqueue_publication", interrupted_enqueue)
    with api_client(factory, [module.router]) as client:
        response = client.post(api_path(path), headers={"X-Test-User": "expert"}, json=payload)
    assert response.status_code == (409 if kind == "draft" else 500), response.text
    with factory() as db:
        assert list(db.scalars(select(Job))) == []
        assert list(db.scalars(select(AuditEvent))) == []
        if kind == "draft":
            proposal = db.get(KnowledgeDraft, "legacy-draft")
            assert not proposal.publication_job_id and not proposal.reviewed_by and not proposal.review_comment
            assert proposal.version == payload["expected_version"] and proposal.status == "IN_REVIEW"
        else:
            document = db.get(KnowledgeDocument, "intake")
            assert document.review_status == "DRAFT" and document.trust_level == "MEDIUM"
            assert document.lock_version == 1 and "human_verified_source" not in json_loads(document.metadata_json, {})
            assert db.get(KnowledgeAccess, "intake") is None
            assert list(db.scalars(select(KnowledgeDraft))) == []
