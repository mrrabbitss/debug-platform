"""Personal Chat selection and immutable model binding in Markdown import consumers."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api import knowledge_routing as routing_api
from app.core.utils import json_dumps, json_loads
from app.model_access_models import ModelProfileAccess
from app.models import Artifact, Job, KnowledgeDocument, ModelProfile
from app.services import jobs, knowledge_routing as routing
from app.services.knowledge_taxonomy import seed_knowledge_categories
from app.services.model_access import ModelAccessError
from app.services.storage import StorageService
from app.workbench_models import WorkbenchRecord
from tests.test_knowledge_contributions_iteration import scope as scope
from tests.test_knowledge_review_integration import api_client, api_path


@pytest.fixture
def routing_scope(scope, tmp_path, monkeypatch):
    factory, _ = scope
    isolated = StorageService(tmp_path / "routing-files")
    monkeypatch.setattr(routing, "SessionLocal", factory)
    monkeypatch.setattr(routing, "storage", isolated)
    monkeypatch.setattr(routing_api, "storage", isolated)
    monkeypatch.setattr(jobs.job_runner, "_schedule", lambda *args: None)
    with factory() as db:
        seed_knowledge_categories(db)
        for actor in ("expert", "admin"):
            profile_id = "private-" + actor
            db.add(ModelProfile(id=profile_id, name=profile_id, task_type="chat", provider="openai_compatible",
                mode="api", model_name=profile_id, enabled=True, is_active=False))
            db.flush()
            db.add(ModelProfileAccess(profile_id=profile_id, owner_id=actor, visibility="PRIVATE"))
            db.add(WorkbenchRecord(id="pref-" + actor, kind="preference", owner_id=actor,
                payload_json=json_dumps({"chat_profile_id": profile_id})))
        db.commit()
    with api_client(factory, [routing_api.router], raise_server_exceptions=True) as client:
        yield factory, client


def provider_factory(monkeypatch, *, on_generate=None):
    called = []

    def provider(profile):
        profile_id = profile.id

        async def generate(system, user, **kwargs):
            called.append(profile_id)
            if on_generate:
                on_generate()
            context = json_loads(user, {})
            return {"decisions": [{"document_key": context["documents"][0]["document_key"],
                "category_id": context["categories"][0]["id"], "confidence": 0.9,
                "rationale": "Synthetic complete Markdown classification", "device_type": "GENERAL"}]}

        return SimpleNamespace(model_name=profile_id, generate_json=generate)

    monkeypatch.setattr(routing, "get_llm_provider", provider)
    return called


def import_source(client, actor="expert", **values):
    return client.post(api_path("/knowledge-routing/import"), headers={"X-Test-User": actor}, data=values,
        files={"files": ("method.md", b"# Synthetic method\nRead complete synthetic evidence.", "text/markdown")})


def run_routing(factory, item):
    with factory() as db:
        job = db.get(Job, item["job"]["id"])
        job.status, job.lease_owner = "RUNNING", "routing-test"
        db.commit()
    return routing.route_markdown_knowledge_job(jobs.JobContext(job.id, lease_owner="routing-test"),
        item["artifact_id"], item["document_id"])


@pytest.mark.parametrize("actor", ["expert", "admin"])
def test_markdown_import_uses_personal_selection_and_keeps_it_after_preference_change(routing_scope, monkeypatch, actor):
    factory, client = routing_scope
    called = provider_factory(monkeypatch)
    response = import_source(client, actor)
    assert response.status_code == 202, response.text
    item = response.json()["items"][0]
    with factory() as db:
        metadata = json_loads(db.get(Artifact, item["artifact_id"]).metadata_json, {})
        assert metadata["model_profile_id"] == "private-" + actor
        assert metadata["model_egress_consent"] is True
        assert metadata["model_snapshot"]["model_actor_id"] == actor
        assert metadata["model_snapshot"]["model_profile_fingerprint"]
        db.get(WorkbenchRecord, "pref-" + actor).payload_json = json_dumps({"chat_profile_id": "shared-chat"})
        db.commit()
    result = run_routing(factory, item)
    assert result["review_status"] == "DRAFT" and result["active"] is False
    assert called == ["private-" + actor]
    with factory() as db:
        document = db.get(KnowledgeDocument, item["document_id"])
        assert json_loads(document.metadata_json, {})["knowledge_routing"]["model"] == metadata["model_snapshot"]


@pytest.mark.parametrize("actor", ["expert", "admin"])
def test_markdown_import_cannot_select_another_users_private_chat(routing_scope, monkeypatch, actor):
    factory, client = routing_scope
    called = provider_factory(monkeypatch)
    response = import_source(client, actor, model_profile_id="private-owner")
    assert response.status_code == 404, response.text
    assert not called
    with factory() as db:
        assert list(db.scalars(select(Artifact))) == []


@pytest.mark.parametrize("when", ["before", "during"])
@pytest.mark.parametrize("change", ["configuration", "disabled"])
def test_markdown_model_changes_stop_call_or_discard_result(routing_scope, monkeypatch, when, change):
    factory, client = routing_scope

    def mutate_model():
        with factory() as db:
            profile = db.get(ModelProfile, "private-expert")
            if change == "configuration":
                profile.config_json = json_dumps({"temperature": 0.8})
            else:
                profile.enabled = False
            db.commit()

    called = provider_factory(monkeypatch, on_generate=mutate_model if when == "during" else None)
    response = import_source(client)
    assert response.status_code == 202, response.text
    item = response.json()["items"][0]
    if when == "before":
        mutate_model()
    with pytest.raises(ModelAccessError):
        run_routing(factory, item)
    assert len(called) == (1 if when == "during" else 0)
    with factory() as db:
        assert db.get(KnowledgeDocument, item["document_id"]) is None
        assert db.get(Artifact, item["artifact_id"]).status == "ROUTING_FAILED"


def test_explicit_and_historical_routing_consent_off_remains_off(routing_scope, monkeypatch):
    factory, client = routing_scope
    called = provider_factory(monkeypatch)
    assert import_source(client, consent_model_egress="false").status_code == 409
    item = import_source(client).json()["items"][0]
    with factory() as db:
        artifact = db.get(Artifact, item["artifact_id"])
        metadata = json_loads(artifact.metadata_json, {})
        metadata["model_egress_consent"] = False
        artifact.metadata_json = json_dumps(metadata)
        db.commit()
    with pytest.raises(ValueError, match="consent"):
        run_routing(factory, item)
    assert not called
    with factory() as db:
        assert json_loads(db.get(Artifact, item["artifact_id"]).metadata_json, {})["model_egress_consent"] is False


def test_host_cli_routing_import_never_uses_server_chat(routing_scope, monkeypatch):
    factory, client = routing_scope

    def unexpected(profile):
        pytest.fail("host_cli Markdown routing must not resolve a server Chat provider")

    monkeypatch.setattr(routing, "get_llm_provider", unexpected)
    response = import_source(client, reasoning_owner="host_cli", consent_model_egress="false")
    assert response.status_code == 202, response.text
    result = run_routing(factory, response.json()["items"][0])
    assert result["routing_status"] == "PENDING_HOST_CLASSIFICATION" and result["active"] is False
