"""Changed iteration behavior: durable exact approval, safe retry and expert gates."""
from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from app.core.utils import json_dumps, utcnow
from app.models import AuditEvent, Job, KnowledgeChunk, KnowledgeDocument, KnowledgeGraphState, KnowledgePublication, UserAccount
from app.services.access_control import authorize_request
from app.services.jobs import JobLeaseLostError
from tests.test_workbench_assistant import (
    store as store, client as client, upload, reviewed, proposal, approve, running, get_value,
    fake_indexes, state, sessions, publication,
)


def interrupt(store, ctx):
    with store() as db:
        job = db.get(Job, ctx.job_id)
        job.status = "QUEUED"
        job.lease_owner = None
        job.lease_expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    with store() as db:
        recovered = state.recover_abandoned_assistant_sessions(db)
        db.commit()
        return recovered


def test_approval_and_outbox_and_audit_rollback_together(store):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic method")], [proposal()])
    with store() as db:
        row, value = state.locked_session(db, key)
        sessions.approve(db, row, value, "local-development")
        db.rollback()
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "assistant_publish")) == 0
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0
    assert get_value(store, key)[0]["status"] == "REVIEW"


def test_replayed_confirmation_returns_same_committed_approval(store, client):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic method")], [proposal()])
    value, version = get_value(store, key)
    payload = {"version": version, "review_digest": value["review_digest"]}
    first = client.post(f"/workbench/assistant/{key}/confirm", json=payload)
    second = client.post(f"/workbench/assistant/{key}/confirm", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    with store() as db:
        assert db.scalar(select(func.count()).select_from(Job).where(Job.kind == "assistant_publish")) == 1
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_expired_worker_resumes_same_approval_without_publishing_abandoned_chunks(store, monkeypatch):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic method")], [proposal()])
    fake_indexes(store, monkeypatch)
    old_ctx, version = approve(store, key)
    old_fence = publication.PublicationFence(old_ctx, key, version, "local-development", "GEN-interrupted")
    snapshot = publication.prepare(old_fence, "VEC-interrupted")
    staged = publication.stage_chunks(old_fence, snapshot)
    abandoned_ids = {chunk.id for chunks in staged.values() for chunk in chunks}
    original = get_value(store, key)[0]
    assert interrupt(store, old_ctx) == 1
    recovered = get_value(store, key)[0]
    assert recovered["status"] == "APPROVED"
    assert recovered["approved_digest"] == original["approved_digest"]
    assert recovered["request_version"] == version
    with store() as db:
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == "KGEN-old"
    new_ctx, new_version = running(store, key)
    result = publication.publication_job(new_ctx, key, new_version, "local-development")
    with store() as db:
        assert db.get(Job, new_ctx.job_id).attempt == 2
        assert db.scalar(select(func.count()).select_from(KnowledgePublication)) == 1
        for chunk_id in abandoned_ids:
            assert db.get(KnowledgeChunk, chunk_id).document_version == 0
        assert all(db.get(KnowledgeDocument, doc_id).active for doc_id in result["documents"])
    assert get_value(store, key)[0]["status"] == "PUBLISHED"
    with pytest.raises(JobLeaseLostError):
        old_fence.raise_if_cancelled()


def test_transient_index_failure_keeps_approval_and_explicit_retry_uses_it(store, monkeypatch):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic method")], [proposal()])
    fake_indexes(store, monkeypatch)
    good_vectors = publication.index_embeddings

    def unavailable(*args, **kwargs):
        raise RuntimeError("SYNTHETIC_PRIVATE_UPSTREAM_BODY")

    monkeypatch.setattr(publication, "index_embeddings", unavailable)
    ctx, version = approve(store, key)
    digest = get_value(store, key)[0]["approved_digest"]
    with pytest.raises(ValueError):
        publication.publication_job(ctx, key, version, "local-development")
    failed = get_value(store, key)[0]
    assert failed["status"] == "PUBLISH_FAILED" and failed["approved_digest"] == digest
    assert "SYNTHETIC_PRIVATE" not in failed["error"]
    with store() as db:
        db.get(Job, ctx.job_id).status = "DEAD_LETTER"
        db.commit()
        row, value = state.locked_session(db, key)
        sessions.retry_reading(db, row, value, "local-development")
        db.commit()
    monkeypatch.setattr(publication, "index_embeddings", good_vectors)
    retried_ctx, retried_version = running(store, key)
    publication.publication_job(retried_ctx, key, retried_version, "local-development")
    assert get_value(store, key)[0]["approved_digest"] == digest
    assert get_value(store, key)[0]["status"] == "PUBLISHED"
    with store() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent)) == 1


def test_tampered_approval_is_not_resumed(store, monkeypatch):
    key = reviewed(store, [upload("folder/SKILL.md", "Synthetic method")], [proposal()])
    fake_indexes(store, monkeypatch)
    ctx, version = approve(store, key)
    fence = publication.PublicationFence(ctx, key, version, "local-development", "GEN-tamper")
    publication.prepare(fence, "VEC-tamper")
    with store() as db:
        row, value = state.locked_session(db, key)
        value["plan"][0]["after"] = "Changed after approval"
        row.payload_json = json_dumps(value)
        db.commit()
    interrupt(store, ctx)
    value, _ = get_value(store, key)
    assert value["status"] == "REVIEW" and value["approved_digest"] is None
    with store() as db:
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == "KGEN-old"


def test_expert_background_identity_must_still_be_active(store):
    with store() as db:
        expert = UserAccount(id="USR-expert", username="expert", display_name="Expert", role="EXPERT", active=True)
        db.add(expert)
        db.commit()
        state.require_admin_actor(db, expert.id)
        expert.active = False
        db.commit()
        with pytest.raises(ValueError):
            state.require_admin_actor(db, expert.id)


@pytest.mark.parametrize("method,path,allowed", [
    ("GET", "/api/v1/system/audit", True),
    ("GET", "/api/v1/system/users", False),
    ("PATCH", "/api/v1/system/users/USR-other", False),
    ("POST", "/api/v1/system/users/USR-other/tokens", False),
    ("POST", "/api/v1/system/model-downloads", False),
    ("POST", "/api/v1/workbench/categories", True),
])
def test_expert_system_privilege_exclusions(store, method, path, allowed):
    request = Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""})
    with store() as db:
        if allowed:
            authorize_request(db, request, {"id": "USR-expert", "role": "EXPERT"})
        else:
            with pytest.raises(HTTPException) as error:
                authorize_request(db, request, {"id": "USR-expert", "role": "EXPERT"})
            assert error.value.status_code == 403
