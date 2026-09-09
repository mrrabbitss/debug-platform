"""Focused synthetic contracts for durable, review-only patch suggestions."""

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import repositories
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, json_loads
from app.model_access_models import ModelProfileAccess
from app.models import Artifact, Case, CodeSymbol, Job, ModelProfile, Repository, UserAccount
from app.services import jobs, patch_suggestions
from app.services.jobs import JobContext
from app.services.model_access import chat_model_snapshot
from app.workbench_models import WorkbenchRecord


ACTOR = {"id": "USR-patch", "role": "ENGINEER", "type": "user_token"}


class FakeProvider:
    is_mock = False

    def __init__(self, after_generate=None):
        self.prompt = ""
        self.after_generate = after_generate

    async def generate_text(self, _system, prompt, *, purpose):
        assert purpose == "patch_suggestion"
        self.prompt = prompt
        if self.after_generate:
            self.after_generate()
        return "--- a/src/handler.c\n+++ b/src/handler.c\n@@\n-return 0;\n+return 1;"


@pytest.fixture
def patch_scope(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'patch.db').as_posix()}",
                           connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with factory() as db:
        db.add(UserAccount(id=ACTOR["id"], username="patch", display_name="Patch", role="ENGINEER", active=True))
        db.flush()
        db.add(Case(id="CASE-patch", owner_id=ACTOR["id"], title="password=case-secret",
                    description="api_key=case-secret", model_egress_approved=True))
        db.flush()
        db.add(Artifact(id="ART-patch", case_id="CASE-patch", kind="source_repository",
                        original_name="source.zip", stored_path="ignored", sha256="a" * 64, size_bytes=1))
        db.flush()
        db.add(Repository(id="REPO-patch", case_id="CASE-patch", artifact_id="ART-patch", name="source",
                          root_path="ignored", active_graph_generation_id="GEN-patch"))
        db.flush()
        db.add(CodeSymbol(id="SYM-patch", logical_id="SYM-logical", repository_id="REPO-patch",
                          generation_id="GEN-patch", kind="function", name="handler", file_path="src/handler.c",
                          line_start=1, line_end=3, code="const char *token=source-secret; return 0;"))
        profile = ModelProfile(id="CHAT-patch", name="Private", task_type="chat", mode="api",
                               provider="openai_compatible", model_name="synthetic",
                               base_url="http://model.invalid/v1", api_key_ciphertext="stored-secret",
                               enabled=True, is_active=False)
        db.add(profile)
        db.flush()
        db.add(ModelProfileAccess(profile_id=profile.id, owner_id=ACTOR["id"], visibility="PRIVATE"))
        db.add(WorkbenchRecord(id="pref-" + ACTOR["id"], kind="preferences", owner_id=ACTOR["id"],
                               payload_json=json_dumps({"chat_profile_id": profile.id})))
        db.commit()
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    monkeypatch.setattr(patch_suggestions, "SessionLocal", factory)
    yield factory
    engine.dispose()


def test_job_route_uses_personal_snapshot_and_secret_free_durable_input(patch_scope, monkeypatch):
    monkeypatch.setattr(jobs.job_runner, "_schedule", lambda *args, **kwargs: None)
    provider = FakeProvider()
    monkeypatch.setattr(patch_suggestions, "get_llm_provider", lambda _profile: provider)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request: Request, call_next):
        request.state.principal = ACTOR
        return await call_next(request)

    def test_db():
        with patch_scope() as db:
            yield db

    app.include_router(repositories.router)
    app.dependency_overrides[get_db] = test_db
    with TestClient(app) as client:
        response = client.post("/cases/CASE-patch/patch-suggestion-jobs", json={
            "symbol_id": "SYM-logical", "instruction": "Fix api_key=request-secret",
        })
    assert response.status_code == 200, response.text
    assert (response.json()["kind"], response.json()["max_attempts"], response.json()["timeout_seconds"]) == (
        "patch_suggestion", 1, 7200)
    with patch_scope() as db:
        data = json_loads(db.get(Job, response.json()["id"]).input_json, {})
    assert set(data) == {"case_id", "actor", "snapshot", "symbol_id", "generation_id", "instruction"}
    assert data["actor"] == ACTOR["id"] and data["generation_id"] == "GEN-patch"
    assert data["snapshot"]["selected_chat_profile_id"] == "CHAT-patch"
    assert "stored-secret" not in json_dumps(data) and "request-secret" not in json_dumps(data)


def test_worker_reports_chinese_stages_masks_prompt_and_completes_atomically(patch_scope, monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(patch_suggestions, "get_llm_provider", lambda _profile: provider)
    with patch_scope() as db:
        snapshot = chat_model_snapshot(db, ACTOR, db.get(ModelProfile, "CHAT-patch"))
        db.add(Job(id="JOB-patch", kind="patch_suggestion", status="RUNNING", lease_owner="patch-test",
                   max_attempts=1, timeout_seconds=7200, input_json=json_dumps({"case_id": "CASE-patch"})))
        db.commit()
    result = patch_suggestions.patch_suggestion_job(
        JobContext("JOB-patch", lease_owner="patch-test"), "CASE-patch", ACTOR["id"], snapshot,
        "SYM-logical", "GEN-patch", "Fix password=request-secret",
    )
    assert result["status"] == "SUGGESTED" and result["auto_applied"] is False
    assert "case-secret" not in provider.prompt
    assert "source-secret" not in provider.prompt
    assert "request-secret" not in provider.prompt
    with patch_scope() as db:
        job = db.get(Job, "JOB-patch")
        assert (job.status, job.progress, job.progress_detail["stage"], job.progress_detail["stage_count"]) == (
            "COMPLETED", 100, "保存", 3)
        assert json_loads(job.result_json, {}) == result


def test_worker_does_not_publish_after_case_egress_is_revoked(patch_scope, monkeypatch):
    def revoke_egress():
        with patch_scope() as db:
            db.get(Case, "CASE-patch").model_egress_approved = False
            db.commit()

    monkeypatch.setattr(patch_suggestions, "get_llm_provider", lambda _profile: FakeProvider(revoke_egress))
    with patch_scope() as db:
        snapshot = chat_model_snapshot(db, ACTOR, db.get(ModelProfile, "CHAT-patch"))
        db.add(Job(id="JOB-revoked", kind="patch_suggestion", status="RUNNING", lease_owner="patch-test",
                   max_attempts=1, timeout_seconds=7200))
        db.commit()
    with pytest.raises(Exception) as error:
        patch_suggestions.patch_suggestion_job(
            JobContext("JOB-revoked", lease_owner="patch-test"), "CASE-patch", ACTOR["id"], snapshot,
            "SYM-logical", "GEN-patch", "Synthetic request",
        )
    assert getattr(error.value, "status_code", None) == 409
    with patch_scope() as db:
        assert db.get(Job, "JOB-revoked").status == "RUNNING"


def test_legacy_patch_response_observes_egress_revocation_during_wait(patch_scope, monkeypatch):
    def revoke():
        with patch_scope() as db:
            db.get(Case, "CASE-patch").model_egress_approved = False
            db.commit()

    monkeypatch.setattr(patch_suggestions, "get_llm_provider", lambda _profile: FakeProvider(revoke))
    with patch_scope() as db:
        values = patch_suggestions.create_patch_suggestion_input(db, case_id="CASE-patch",
            symbol_id="SYM-logical", instruction="Synthetic", principal=ACTOR)
        context = patch_suggestions.resolve_patch_suggestion_context(db, case_id="CASE-patch", actor_id=ACTOR["id"],
            snapshot=values["snapshot"], symbol_id=values["symbol_id"], generation_id=values["generation_id"])
        with pytest.raises(Exception) as error:
            asyncio.run(patch_suggestions.generate_patch_suggestion(db, context, "Synthetic"))
        assert getattr(error.value, "status_code", None) == 409
