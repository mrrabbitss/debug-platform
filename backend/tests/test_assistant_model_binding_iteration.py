"""New assistant requests obey the initiating user's unified model choice."""
import json

import pytest
from app.core.utils import json_dumps, json_loads
from app.model_access_models import ModelProfileAccess
from app.models import ModelProfile, UserAccount
from app.workbench_models import WorkbenchRecord
from app.services import assistant_runtime as runtime, knowledge_assistant as assistant
from tests.test_workbench_assistant import (
    store as store, client as client, running, get_value, FakeChat,
    upload, reviewed, proposal, approve, fake_indexes, publication,
)


def personal_model(store, actor="EXPERT-one"):
    with store() as db:
        db.add(UserAccount(id=actor, username=actor, display_name=actor, role="EXPERT", active=True))
        profile = ModelProfile(id="CHAT-" + actor, name="Private", task_type="chat", mode="api",
            provider="openai_compatible", model_name="synthetic", base_url="http://model.example.test/v1",
            is_active=False, enabled=True)
        db.add(profile)
        db.flush()
        db.add(ModelProfileAccess(profile_id=profile.id, visibility="PRIVATE", owner_id=actor))
        db.add(WorkbenchRecord(id="pref-" + actor, kind="preferences", owner_id=actor,
            payload_json=json_dumps({"chat_profile_id": profile.id})))
        db.commit()
    return profile.id


def create(client):
    response = client.post("/workbench/assistant", data={"paths": json.dumps(["bundle/SKILL.md"]), "mode": "answer"},
                           files=[("files", ("SKILL.md", b"Synthetic published process", "text/markdown"))])
    assert response.status_code == 200, response.text
    return response.json()


def test_personal_choice_is_pinned_and_hidden_from_session_payload(store, client, monkeypatch):
    profile_id = personal_model(store)
    client.principal.update(id="EXPERT-one", role="EXPERT")
    created = create(client)
    assert "model_snapshot" not in created
    stored, _ = get_value(store, created["id"])
    assert stored["model_snapshot"]["selected_chat_profile_id"] == profile_id
    captured = []
    model = FakeChat([{"action": "finish", "answer": "Synthetic answer", "evidence": [{"path": "bundle/SKILL.md"}]}])
    def provider(profile):
        captured.append(profile.id)
        return model
    monkeypatch.setattr(runtime, "get_llm_provider", provider)
    ctx, version = running(store, created["id"])
    assistant.plan_job(ctx, created["id"], version)
    assert captured == [profile_id]
    client.principal.update(id="local-development", role="ADMIN")
    detail = client.get("/workbench/assistant/" + created["id"])
    assert detail.status_code == 200 and profile_id not in detail.text


@pytest.mark.parametrize("mutation", ["disabled", "credentials", "owner_disabled"])
def test_model_change_blocks_queued_reading_without_fallback(store, client, monkeypatch, mutation):
    profile_id = personal_model(store)
    client.principal.update(id="EXPERT-one", role="EXPERT")
    created = create(client)
    with store() as db:
        if mutation == "disabled":
            db.get(ModelProfile, profile_id).enabled = False
        elif mutation == "credentials":
            db.get(ModelProfile, profile_id).api_key_ciphertext = "changed-synthetic"
        else:
            db.get(UserAccount, "EXPERT-one").active = False
        db.commit()
    called = []
    monkeypatch.setattr(runtime, "get_llm_provider", lambda profile=None: called.append(profile))
    ctx, version = running(store, created["id"])
    with pytest.raises(ValueError):
        assistant.plan_job(ctx, created["id"], version)
    assert called == []
    with store() as db:
        value = json_loads(db.get(WorkbenchRecord, created["id"]).payload_json, {})
        assert value["status"] == "FAILED"
        assert not value["coverage"]


def test_assistant_ordinary_knowledge_is_not_converted_into_mandatory_skill(store, monkeypatch):
    from app.models import KnowledgeDocument
    from sqlalchemy import select
    from app.services.knowledge_access import knowledge_kind
    key = reviewed(store, [upload("bundle/wiki.md", "Synthetic Wiki reference")],
        [proposal(path="bundle/wiki.md", content_kind="KNOWLEDGE")])
    assert get_value(store, key)[0]["plan"][0]["content_kind"] == "KNOWLEDGE"
    fake_indexes(store, monkeypatch)
    ctx, version = approve(store, key)
    publication.publication_job(ctx, key, version, "local-development")
    with store() as db:
        doc = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.active.is_(True)))
        assert knowledge_kind(doc) == "KNOWLEDGE" and doc.source_type == "document"
