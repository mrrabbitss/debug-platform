"""Focused contracts for durable interactive Chat jobs; no network model calls."""
import asyncio

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.api import system as system_api
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads
from app.model_access_models import ModelProfileAccess
from app.knowledge_contribution_models import KnowledgeContribution
from app.models import Job, KnowledgeCurationSession, ModelProfile
from app.services import interactive_model_jobs, jobs, knowledge_contribution_review, knowledge_curation
from app.services.jobs import JobCancelledError, JobContext, JobLeaseLostError
from app.services.knowledge_access import authorize_routing_job
from app.services.model_access import chat_model_snapshot
from app.services.knowledge_curation import CurationError, refine_curation_session
from tests.test_knowledge_contributions_iteration import scope as scope, submitted, who


def _running_job(db, kind: str, data: dict, job_id: str) -> Job:
    row = Job(
        id=job_id,
        kind=kind,
        status="RUNNING",
        lease_owner="interactive-test",
        max_attempts=1,
        timeout_seconds=7200,
        input_json=json_dumps(data),
    )
    db.add(row)
    db.commit()
    return row


def _curation_session(db, session_id: str, *, consent=True):
    profile = db.get(ModelProfile, "private-owner")
    snapshot = chat_model_snapshot(db, who("owner"), profile)
    session = KnowledgeCurationSession(
        id=session_id,
        status="REVIEWING",
        created_by="owner",
        draft_title="Draft",
        draft_markdown="# Existing draft\nEvidence retained",
        draft_version=1,
        model_profile_id=profile.id,
        model_snapshot_json=json_dumps(snapshot),
        source_manifest_json=json_dumps({"model_egress_consent": consent}),
    )
    db.add(session)
    db.flush()
    return session, snapshot


def test_contribution_review_job_persists_progress_and_candidate_atomically(scope, monkeypatch):
    factory, client = scope
    row = submitted(client)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(interactive_model_jobs, "SessionLocal", factory)

    class Provider:
        async def generate_json(self, *args, **kwargs):
            return {
                "assistant_message": "已补充审核结论。",
                "title": "Corrected contribution",
                "revised_markdown": "# Corrected\nEvidence kept",
                "change_summary": "Synthetic review correction",
            }

    monkeypatch.setattr(knowledge_contribution_review, "get_llm_provider", lambda profile: Provider())
    with factory() as db:
        profile = db.get(ModelProfile, "shared-chat")
        snapshot = chat_model_snapshot(db, who("expert"), profile)
        _running_job(db, "refine_knowledge_contribution", {
            "contribution_id": row["id"], "expected_version": row["version"], "instruction": "Correct it",
            "owner_id": "expert", "model_snapshot": snapshot, "consent_model_egress": True,
        }, "JOB-interactive-review")

    result = interactive_model_jobs.contribution_review_job(
        JobContext("JOB-interactive-review", lease_owner="interactive-test"),
        row["id"], row["version"], "Correct it", "expert", snapshot, True,
    )
    assert result == {"contribution_id": row["id"], "version": row["version"] + 1}
    with factory() as db:
        job = db.get(Job, "JOB-interactive-review")
        contribution = db.get(KnowledgeContribution, row["id"])
        assert job.status == "COMPLETED" and job.progress == 100
        assert job.progress_detail["stage"] == "保存待审版本"
        assert job.progress_detail["stage_count"] == 4
        assert json_loads(job.result_json, {}) == result
        assert contribution.version == row["version"] + 1
        assert "Evidence kept" in json_loads(contribution.candidate_json, {})["content"]


def test_new_job_routes_are_owner_scoped_single_attempt_and_secret_free(scope, monkeypatch):
    factory, client = scope
    monkeypatch.setattr(jobs.job_runner, "_schedule", lambda *args, **kwargs: None)
    row = submitted(client)
    response = client.post(
        f"/knowledge-contributions/{row['id']}/review-chat-jobs",
        headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Correct it", "model_profile_id": "shared-chat"},
    )
    assert response.status_code == 200, response.text
    review_job = response.json()
    assert (review_job["kind"], review_job["max_attempts"], review_job["timeout_seconds"]) == (
        "refine_knowledge_contribution", 1, 7200)
    with factory() as db:
        persisted = db.get(Job, review_job["id"])
        data = json_loads(persisted.input_json, {})
        assert data["owner_id"] == "expert"
        assert "api_key" not in persisted.input_json and "candidate" not in persisted.input_json
        assert authorize_routing_job(db, persisted.id, who("expert"))
        try:
            authorize_routing_job(db, persisted.id, who("admin"))
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError("another manager must not read an interactive job")

        owner_profile = db.get(ModelProfile, "private-owner")
        snapshot = chat_model_snapshot(db, who("owner"), owner_profile)
        db.add(KnowledgeCurationSession(
            id="KCUR-interactive", status="REVIEWING", created_by="owner", draft_title="Draft",
            draft_markdown="# Draft\nEvidence", draft_version=1, model_profile_id=owner_profile.id,
            model_snapshot_json=json_dumps(snapshot), source_manifest_json=json_dumps({"model_egress_consent": True}),
        ))
        db.commit()
    curation = client.post("/knowledge-curations/KCUR-interactive/chat-jobs", json={
        "instruction": "Refine it", "expected_draft_version": 1,
    })
    assert curation.status_code == 200, curation.text
    assert (curation.json()["kind"], curation.json()["max_attempts"], curation.json()["timeout_seconds"]) == (
        "refine_knowledge_curation", 1, 7200)


def test_chat_model_connection_test_job_route_is_chat_only_and_durable(scope, monkeypatch):
    factory, _ = scope
    monkeypatch.setattr(jobs.job_runner, "_schedule", lambda *args, **kwargs: None)

    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.principal = who("expert")
        return await call_next(request)

    def test_db():
        with factory() as db:
            yield db

    app.include_router(system_api.router)
    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        response = client.post("/system/models/shared-chat/test-jobs")
        assert response.status_code == 200, response.text
        value = response.json()
        assert (value["kind"], value["max_attempts"], value["timeout_seconds"]) == (
            "test_chat_model_connection", 1, 7200)
        assert client.post("/system/models/embed/test-jobs").status_code == 422
        with factory() as db:
            db.get(ModelProfile, "shared-chat").enabled = False
            db.commit()
        # Match the synchronous endpoint: a manager may probe a disabled Chat
        # draft while ordinary users cannot bypass the disabled state.
        assert client.post("/system/models/shared-chat/test-jobs").status_code == 200
    with factory() as db:
        data = json_loads(db.get(Job, value["id"]).input_json, {})
        assert set(data["model_snapshot"]) == {
            "selected_chat_profile_id", "model_actor_id", "model_profile_fingerprint"}
        assert "api_key" not in json_dumps(data)


def test_connection_test_job_allows_its_viewer_owner_and_reports_negative_probe(scope, monkeypatch):
    factory, _ = scope
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(interactive_model_jobs, "SessionLocal", factory)
    with factory() as db:
        viewer = who("viewer")
        profile = ModelProfile(id="viewer-chat", name="Viewer private", task_type="chat", mode="api",
                               provider="openai_compatible", model_name="viewer", enabled=True)
        db.add(profile)
        db.flush()
        db.add(ModelProfileAccess(profile_id=profile.id, owner_id=viewer["id"], visibility="PRIVATE"))
        db.flush()
        snapshot = chat_model_snapshot(db, viewer, profile)
        job = _running_job(db, "test_chat_model_connection", {
            "profile_id": profile.id, "owner_id": viewer["id"], "model_snapshot": snapshot,
        }, "JOB-viewer-test")
        assert authorize_routing_job(db, job.id, viewer)
        failed = Job(
            id="JOB-viewer-negative",
            kind="test_chat_model_connection",
            status="QUEUED",
            max_attempts=1,
            timeout_seconds=7200,
            input_json=json_dumps({
                "profile_id": profile.id,
                "owner_id": viewer["id"],
                "model_snapshot": snapshot,
            }),
        )
        db.add(failed)
        db.commit()

    async def negative_probe(profile):
        return {"ok": False, "response": "Unexpected test response"}

    monkeypatch.setattr(interactive_model_jobs, "test_profile_connection", negative_probe)
    jobs.job_runner._run("JOB-viewer-negative")
    with factory() as db:
        failed = db.get(Job, "JOB-viewer-negative")
        assert failed.status == "DEAD_LETTER"
        assert "expected response" in (failed.error_message or "")


def test_curation_refinement_rejects_disabled_egress_before_provider_call(scope, monkeypatch):
    factory, _ = scope
    with factory() as db:
        session, _ = _curation_session(db, "KCUR-egress", consent=False)
        db.commit()

        def unexpected_provider(profile):
            raise AssertionError("egress-disabled curation must not construct a provider")

        monkeypatch.setattr(knowledge_curation, "get_llm_provider", unexpected_provider)
        with pytest.raises(CurationError, match="egress consent is disabled"):
            asyncio.run(refine_curation_session(
                db,
                session,
                instruction="Do not call a model",
                expected_draft_version=1,
                actor="owner",
                model_snapshot=json_loads(session.model_snapshot_json, {}),
            ))


def test_connection_test_rejects_profile_change_while_waiting(scope, monkeypatch):
    factory, _ = scope
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(interactive_model_jobs, "SessionLocal", factory)
    with factory() as db:
        profile = db.get(ModelProfile, "shared-chat")
        snapshot = chat_model_snapshot(db, who("expert"), profile)
        _running_job(db, "test_chat_model_connection", {
            "profile_id": profile.id, "owner_id": "expert", "model_snapshot": snapshot,
        }, "JOB-profile-changed")

    async def change_profile(profile):
        with factory() as writer:
            writer.get(ModelProfile, profile.id).model_name = "changed-during-probe"
            writer.commit()
        return {"ok": True, "response": "MODEL_CONNECTION_OK"}

    monkeypatch.setattr(interactive_model_jobs, "test_profile_connection", change_profile)
    with pytest.raises(HTTPException, match="configuration or credentials changed") as raised:
        interactive_model_jobs.chat_model_connection_test_job(
            JobContext("JOB-profile-changed", lease_owner="interactive-test"),
            "shared-chat", "expert", snapshot,
        )
    assert raised.value.status_code == 409
    with factory() as db:
        job = db.get(Job, "JOB-profile-changed")
        assert job.status == "RUNNING" and json_loads(job.result_json, {}) == {}


@pytest.mark.parametrize("stop", [JobCancelledError, JobLeaseLostError])
def test_review_cancellation_or_lease_loss_never_saves_candidate(scope, monkeypatch, stop):
    factory, client = scope
    row = submitted(client)
    monkeypatch.setattr(interactive_model_jobs, "SessionLocal", factory)

    class Provider:
        async def generate_json(self, *args, **kwargs):
            return {
                "assistant_message": "Generated but not saved",
                "title": "Blocked update",
                "revised_markdown": "# Blocked\nMust not persist",
                "change_summary": "must roll back",
            }

    class StopAfterModel:
        def __init__(self):
            self.calls = 0

        def update(self, progress, message):
            pass

        def raise_if_cancelled(self):
            self.calls += 1
            if self.calls >= 3:
                raise stop("stop after model response")

    monkeypatch.setattr(knowledge_contribution_review, "get_llm_provider", lambda profile: Provider())
    with factory() as db:
        profile = db.get(ModelProfile, "shared-chat")
        snapshot = chat_model_snapshot(db, who("expert"), profile)
        before = db.get(KnowledgeContribution, row["id"])
        before_version, before_candidate = before.version, before.candidate_json
    with pytest.raises(stop):
        interactive_model_jobs.contribution_review_job(
            StopAfterModel(), row["id"], row["version"], "Correct it", "expert", snapshot, True,
        )
    with factory() as db:
        after = db.get(KnowledgeContribution, row["id"])
        assert (after.version, after.candidate_json) == (before_version, before_candidate)


def test_curation_revision_and_job_completion_are_atomic(scope, monkeypatch):
    factory, _ = scope
    monkeypatch.setattr(interactive_model_jobs, "SessionLocal", factory)
    with factory() as db:
        session, snapshot = _curation_session(db, "KCUR-atomic")
        db.commit()

    class Provider:
        async def generate_json(self, *args, **kwargs):
            return {
                "assistant_message": "Generated but rolled back",
                "revised_markdown": "# Updated draft\nA sufficiently long synthetic revision.",
                "change_summary": "atomic test",
                "open_questions": [],
                "citations": [],
            }

    class CompletionRejected:
        def update(self, progress, message):
            pass

        def raise_if_cancelled(self):
            return None

        def complete_in_transaction(self, db, result, message):
            raise JobLeaseLostError("completion fence rejected")

    monkeypatch.setattr(knowledge_curation, "get_llm_provider", lambda profile: Provider())
    monkeypatch.setattr(knowledge_curation, "_evidence_for_session", lambda session: "synthetic evidence")
    with pytest.raises(JobLeaseLostError, match="completion fence rejected"):
        interactive_model_jobs.curation_refinement_job(
            CompletionRejected(), "KCUR-atomic", 1, "Refine", "owner", snapshot,
        )
    with factory() as db:
        session = db.get(KnowledgeCurationSession, "KCUR-atomic")
        assert session.draft_version == 1 and session.draft_markdown.startswith("# Existing draft")
        assert list(db.scalars(select(KnowledgeCurationSession).where(
            KnowledgeCurationSession.id == "KCUR-atomic")))
        from app.models import KnowledgeCurationMessage, KnowledgeCurationRevision
        assert not list(db.scalars(select(KnowledgeCurationMessage).where(
            KnowledgeCurationMessage.session_id == session.id)))
        assert not list(db.scalars(select(KnowledgeCurationRevision).where(
            KnowledgeCurationRevision.session_id == session.id)))
