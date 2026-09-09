"""Real-gateway format failures remain bounded and cannot bypass human review."""
import json
from types import SimpleNamespace

import pytest

from app.services import knowledge_contribution_review as review_service
from app.services.knowledge_contributions import update_contribution
from app.knowledge_contribution_models import KnowledgeContribution
from tests.test_knowledge_contributions_iteration import scope as scope, submitted, who


@pytest.mark.parametrize("invalid", [
    {"title": "Missing fields"},
    {"title": "Title", "assistant_message": "Message", "revised_markdown": ["UNTRUSTED_VALUE"]},
])
def test_schema_repair_keeps_original_and_commits_only_valid_candidate(scope, monkeypatch, invalid):
    _, client = scope
    row = submitted(client)
    calls = []

    async def generate(system, prompt, **kwargs):
        value = json.loads(prompt)
        calls.append(value)
        assert value["output_contract"]["properties"]["change_summary"]["type"] == "string"
        assert value["output_contract"]["additionalProperties"] is False
        if len(calls) == 1:
            return invalid
        assert "UNTRUSTED_VALUE" not in prompt
        return {"title": "Reviewed", "assistant_message": "Checked", "revised_markdown": "# Evidence\nREVIEW_ME"}

    monkeypatch.setattr(review_service, "get_llm_provider", lambda p: SimpleNamespace(generate_json=generate))
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Clarify the same evidence"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(calls) == 2
    assert calls[0]["candidate"] == calls[1]["candidate"]
    assert result["version"] == row["version"] + 1
    assert len(result["messages"]) == 2
    assert result["original"] == row["original"]
    assert result["status"] == "SUBMITTED"


def test_repeated_invalid_schema_preserves_candidate_and_history(scope, monkeypatch):
    _, client = scope
    row = submitted(client)
    calls = []

    async def generate(*args, **kwargs):
        calls.append(True)
        return {"title": "Incomplete"}

    monkeypatch.setattr(review_service, "get_llm_provider", lambda p: SimpleNamespace(generate_json=generate))
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Clarify"})
    assert response.status_code == 502
    assert len(calls) == 2
    after = client.get(f"/knowledge-contributions/{row['id']}").json()
    assert all(after[key] == row[key] for key in ("version", "candidate", "original", "messages", "status"))


def test_human_revision_during_schema_retry_is_not_overwritten(scope, monkeypatch):
    factory, client = scope
    row = submitted(client)
    calls = []

    async def generate(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            return {}
        with factory() as db:
            current = db.get(KnowledgeContribution, row["id"])
            update_contribution(db, current, who("expert"), {"expected_version": current.version,
                "content": "# Human\nCURRENT_HUMAN_EDIT"}, review=True)
            db.commit()
        return {"title": "Stale", "assistant_message": "Stale", "revised_markdown": "MODEL_EDIT"}

    monkeypatch.setattr(review_service, "get_llm_provider", lambda p: SimpleNamespace(generate_json=generate))
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Clarify"})
    assert response.status_code == 409
    assert "CURRENT_HUMAN_EDIT" in client.get(f"/knowledge-contributions/{row['id']}").json()["candidate"]["content"]
