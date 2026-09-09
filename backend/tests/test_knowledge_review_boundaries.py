"""Changed migration, transaction and validation boundaries for reviewed knowledge."""
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import BACKEND_ROOT
from app.core.utils import json_dumps, json_loads
from app.knowledge_contribution_models import KnowledgeContribution
from app.models import Job, KnowledgeDocument, KnowledgeDraft, UserAccount
from app.schemas import KnowledgeCreate
from app.services import jobs, knowledge_publication
from app.services.knowledge_contributions import review_contribution, update_contribution
from tests.test_knowledge_contributions_iteration import CASE_MARKDOWN, draft, extraction, submitted, who
from tests.test_knowledge_contributions_iteration import scope as scope


@pytest.mark.parametrize("values", [{"title": None}, {"content": None}, {"metadata": None}, {"confidentiality": None}])
def test_invalid_nullable_edits_return_validation_error_without_changes(scope, values):
    _, client = scope
    row = draft(client)
    response = client.patch(f"/knowledge-contributions/{row['id']}", json={"expected_version": row["version"], **values})
    assert response.status_code == 422, response.text
    current = client.get(f"/knowledge-contributions/{row['id']}").json()
    assert current["version"] == row["version"] and current["content_hash"] == row["content_hash"]


def test_concurrent_reviewer_cannot_enqueue_stale_approval(scope):
    factory, client = scope
    row = submitted(client)
    with factory() as first:
        stale = first.get(KnowledgeContribution, row["id"])
        with factory() as second:
            fresh = second.get(KnowledgeContribution, row["id"])
            update_contribution(second, fresh, who("expert"), {"expected_version": fresh.version,
                "content": "# New\nConcurrent correction"}, review=True)
            second.commit()
        with pytest.raises(StaleDataError):
            review_contribution(first, stale, who("expert"), {"expected_version": row["version"],
                "expected_content_hash": row["content_hash"], "action": "APPROVE"})
        first.rollback()
    with factory() as db:
        assert list(db.scalars(select(Job))) == []
        assert len(list(db.scalars(select(KnowledgeDocument)))) == 2


def test_curation_draft_cannot_leak_through_legacy_document_view(scope):
    factory, client = scope
    extraction(factory)
    response = client.post("/knowledge-curations/extraction/confirm", json={"expected_draft_version": 1})
    document_id = response.json()["knowledge_document"]["id"]
    headers = {"X-Test-User": "expert"}
    assert client.get(f"/knowledge/{document_id}", headers=headers).status_code == 404
    assert document_id not in {row["id"] for row in client.get("/knowledge", headers=headers).json()}


def test_invalid_ai_source_citation_preserves_pending_review(scope, monkeypatch):
    factory, client = scope
    extraction(factory)
    row = client.post("/knowledge-curations/extraction/confirm", json={"expected_draft_version": 1}).json()["contribution"]
    row = client.post(f"/knowledge-contributions/{row['id']}/submit", json={"expected_version": row["version"]}).json()
    from app.services import knowledge_contribution_review as review_service, knowledge_curation
    monkeypatch.setattr(knowledge_curation, "_evidence_for_session", lambda session: "[SRC-0001:L1-L2] synthetic evidence")
    async def generate(*args, **kwargs):
        return {"title": "Invalid", "revised_markdown": CASE_MARKDOWN.replace("SRC-0001", "SRC-9999"),
                "assistant_message": "Unsupported citation"}
    monkeypatch.setattr(review_service, "get_llm_provider", lambda profile: SimpleNamespace(generate_json=generate))
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Revise"})
    assert response.status_code == 422, response.text
    current = client.get(f"/knowledge-contributions/{row['id']}").json()
    assert current["content_hash"] == row["content_hash"] and not current["messages"]


def test_reviewer_demotion_during_ai_call_discards_model_edit(scope, monkeypatch):
    factory, client = scope
    row = submitted(client)
    from app.services import knowledge_contribution_review as review_service
    async def generate(*args, **kwargs):
        with factory() as db:
            db.get(UserAccount, "expert").role = "ENGINEER"
            db.commit()
        return {"title": "No longer authorized", "revised_markdown": "# Stale\nDo not persist", "assistant_message": "Changed"}
    monkeypatch.setattr(review_service, "get_llm_provider", lambda profile: SimpleNamespace(generate_json=generate))
    response = client.post(f"/knowledge-contributions/{row['id']}/review-chat", headers={"X-Test-User": "expert"},
        json={"expected_version": row["version"], "instruction": "Revise"})
    assert response.status_code == 403
    assert client.get(f"/knowledge-contributions/{row['id']}").json()["content_hash"] == row["content_hash"]


def legacy_proposal(factory):
    with factory() as db:
        row = KnowledgeDraft(id="legacy-draft", document_id="wiki", base_version=1, owner_key="expert",
            created_by="expert", status="IN_REVIEW", snapshot_json=json_dumps(
                KnowledgeCreate(title="Reviewed old-style draft", content="# Revision\nCORRECTED_LEGACY").model_dump()))
        db.add(row)
        db.commit()


def test_legacy_enqueue_commits_approval_and_job_together(scope, monkeypatch):
    factory, _ = scope
    monkeypatch.setattr(knowledge_publication, "SessionLocal", factory)
    legacy_proposal(factory)
    with factory() as db:
        job = knowledge_publication.enqueue_publication(db, db.get(KnowledgeDocument, "wiki"),
            db.get(KnowledgeDraft, "legacy-draft"), "expert")
        assert job.id
        db.rollback()
    with factory() as db:
        assert not db.get(KnowledgeDraft, "legacy-draft").publication_job_id
        assert list(db.scalars(select(Job))) == []
        job = knowledge_publication.enqueue_publication(db, db.get(KnowledgeDocument, "wiki"),
            db.get(KnowledgeDraft, "legacy-draft"), "expert")
        assert knowledge_publication.enqueue_publication(db, db.get(KnowledgeDocument, "wiki"),
            db.get(KnowledgeDraft, "legacy-draft"), "expert").id == job.id
        job.status, job.lease_owner = "RUNNING", "legacy-worker"
        inputs = json_loads(job.input_json, {})
        db.commit()
    result = knowledge_publication.publication_job(jobs.JobContext(job.id, lease_owner="legacy-worker"),
        **{key: inputs[key] for key in ("document_id", "draft_id", "draft_version", "reviewer")})
    assert result["document_id"] == "wiki"
    with factory() as db:
        assert "CORRECTED_LEGACY" in db.get(KnowledgeDocument, "wiki").content
        assert db.get(Job, job.id).status == "COMPLETED"


def test_0024_migration_preserves_0023_documents_and_is_repeatable(tmp_path):
    url = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0023")
    engine = create_engine(url)
    factory = sessionmaker(engine)
    with factory() as db:
        db.add(KnowledgeDocument(id="retained", title="Preserve", content="# Original\nORIGINAL", active=True,
            review_status="ACTIVE", version=7, metadata_json=json_dumps({"content_kind": "SKILL"})))
        db.commit()
    command.upgrade(config, "0024")
    command.upgrade(config, "0024")
    with factory() as db:
        row = db.get(KnowledgeDocument, "retained")
        assert row.version == 7 and row.content.endswith("ORIGINAL")
        assert db.execute(text("SELECT version_num FROM alembic_version")).scalar_one() == "0024"
    assert {"knowledge_contributions", "knowledge_contribution_revisions"}.issubset(inspect(engine).get_table_names())
    engine.dispose()


def test_legacy_exact_approval_can_resume_failed_index_build(scope, monkeypatch):
    factory, _ = scope
    monkeypatch.setattr(knowledge_publication, "SessionLocal", factory)
    legacy_proposal(factory)
    with factory() as db:
        job = knowledge_publication.enqueue_publication(db, db.get(KnowledgeDocument, "wiki"),
            db.get(KnowledgeDraft, "legacy-draft"), "expert")
        job.status, job.lease_owner, job.attempt = "RUNNING", "old-publisher", 1
        data = json_loads(job.input_json, {})
        db.commit()
    args = {key: data[key] for key in ("document_id", "draft_id", "draft_version", "reviewer")}
    original = knowledge_publication.stage_graph
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic interrupted build")
    monkeypatch.setattr(knowledge_publication, "stage_graph", fail)
    with pytest.raises(RuntimeError):
        knowledge_publication.publication_job(jobs.JobContext(job.id, lease_owner="old-publisher"), **args)
    with factory() as db:
        assert "ORIGINAL" in db.get(KnowledgeDocument, "wiki").content
        assert db.get(KnowledgeDraft, "legacy-draft").status == "FAILED"
        current = db.get(Job, job.id)
        current.lease_owner, current.attempt = "replacement-publisher", 2
        db.commit()
    monkeypatch.setattr(knowledge_publication, "stage_graph", original)
    knowledge_publication.publication_job(jobs.JobContext(job.id, lease_owner="replacement-publisher"), **args)
    with factory() as db:
        assert db.get(Job, job.id).status == "COMPLETED"
        assert db.get(KnowledgeDraft, "legacy-draft").status == "PUBLISHED"


def test_direct_manager_delete_uses_atomic_queue_and_preserves_history(scope):
    factory, client = scope
    from tests.test_knowledge_contributions_iteration import run_job
    response = client.delete("/knowledge/skill", headers={"X-Test-User": "expert"})
    assert response.status_code == 200, response.text
    assert response.json()["publication_pending"] is True
    with factory() as db:
        assert db.get(KnowledgeDocument, "skill").active
    row = response.json()["contribution"]
    run_job(factory, row)
    again = client.delete("/knowledge/skill", headers={"X-Test-User": "expert"})
    assert again.status_code == 200 and again.json()["historical_references_retained"] is True
    with factory() as db:
        assert db.get(KnowledgeDocument, "skill") is not None


def test_failed_filter_tracks_terminal_publication_job(scope):
    factory, client = scope
    from tests.test_knowledge_contributions_iteration import approval
    row = approval(client, submitted(client)).json()
    with factory() as db:
        db.get(Job, row["publication_job_id"]).status = "DEAD_LETTER"
        db.commit()
    results = client.get("/knowledge-contributions?status=FAILED").json()
    assert len(results) == 1 and results[0]["id"] == row["id"] and results[0]["status"] == "FAILED"
