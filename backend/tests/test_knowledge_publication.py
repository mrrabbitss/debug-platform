"""Publication swaps preserve both online evidence and old revision references."""
import pytest
from sqlalchemy import select

from app.core.utils import json_loads
from app.models import KnowledgeChunk, KnowledgeDocument, KnowledgeGraphState, KnowledgePublication, ModelProfile
from app.services import knowledge_publication
from app.services.knowledge import index_document
from app.services.knowledge_drafts import draft_for_document, review_draft, save_draft
from app.services.knowledge_governance import create_document_revision
from app.services.knowledge_visibility import current_chunk_clause
from app.services.model_profiles import seed_model_profiles
from tests.test_knowledge_governance_graph_evaluation import _Context, _factory, _admin_app
from fastapi.testclient import TestClient


@pytest.fixture
def publication(tmp_path, monkeypatch):
    engine, factory = _factory(tmp_path, "publication.db")
    monkeypatch.setattr(knowledge_publication, "SessionLocal", factory)
    with factory() as db:
        seed_model_profiles(db)
        document = KnowledgeDocument(id="DOC-publication", title="AP old", source_type="document",
                                     content="# Logs\nOLD_AUTH_TIMEOUT", active=True, review_status="ACTIVE")
        db.add(document)
        db.commit()
        index_document(db, document)
        create_document_revision(db, document, created_by="admin", change_summary="Old release")
        db.commit()
        old_chunks = [row.id for row in db.scalars(select(KnowledgeChunk))]
    yield factory, old_chunks
    engine.dispose()


def proposal(factory):
    with factory() as db:
        document = db.get(KnowledgeDocument, "DOC-publication")
        draft = save_draft(db, document, {"content": "# Logs\nNEW_POWER_FAILURE", "title": "AP new"},
                           expected_lock_version=document.lock_version, expected_draft_version=None, author="engineer")
        review_draft(db, draft, action="SUBMIT", expected_version=draft.version, reviewer="admin", comment="Reviewed")
        db.commit()
        return draft.id, draft.version


def test_draft_edit_keeps_published_content_and_conflicts(publication):
    factory, old_ids = publication
    with TestClient(_admin_app(factory)) as client:
        url = "/api/v1/knowledge/DOC-publication"
        response = client.patch(url, json={"expected_lock_version": 1, "content": "New proposal"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["active"] and data["version"] == 1 and "OLD_AUTH_TIMEOUT" in data["content"]
        assert data["pending_draft"]["snapshot"]["content"] == "New proposal"
        assert client.patch(url, json={"expected_lock_version": 1, "content": "Lost update"}).status_code == 409
        result = client.get(f"{url}/versions/1/chunks/{old_ids[0]}")
        assert result.status_code == 200 and "OLD_AUTH_TIMEOUT" in result.json()["content"]


def test_atomic_publication_retains_old_chunks(publication):
    factory, old_ids = publication
    with factory() as db:
        previous_generation = db.scalar(select(ModelProfile).where(ModelProfile.task_type == "embedding", ModelProfile.is_active.is_(True))).active_embedding_generation_id
    draft_id, version = proposal(factory)
    result = knowledge_publication.publication_job(_Context(), "DOC-publication", draft_id, version, "admin")
    assert result["version"] == 2
    with factory() as db:
        document = db.get(KnowledgeDocument, "DOC-publication")
        assert document.title == "AP new" and document.active
        current = list(db.scalars(select(KnowledgeChunk).join(KnowledgeDocument).where(current_chunk_clause())))
        assert len(current) == 1 and current[0].content == "NEW_POWER_FAILURE"
        assert db.get(KnowledgeChunk, old_ids[0]).content == "OLD_AUTH_TIMEOUT"
        manifest = json_loads(db.get(KnowledgePublication, result["publication_id"]).manifest_json, {})
        assert manifest["chunk_ids"][document.id] == [current[0].id]
        assert manifest["embedding_generation_id"] != previous_generation
        assert db.get(KnowledgeGraphState, "domain").active_generation_id == manifest["graph_generation_id"]
        assert draft_for_document(db, document.id).status == "PUBLISHED"


@pytest.mark.parametrize("failure", ["embedding", "graph", "cancel", "concurrent"])
def test_failed_preparation_never_withdraws_publication(publication, monkeypatch, failure):
    factory, old_ids = publication
    draft_id, version = proposal(factory)
    def fail(*args, **kwargs):
        raise RuntimeError("Synthetic failure")
    context = _Context()
    if failure == "embedding":
        monkeypatch.setattr(knowledge_publication, "index_embeddings", fail)
    elif failure == "graph":
        monkeypatch.setattr(knowledge_publication, "stage_graph", fail)
    elif failure == "cancel":
        context.raise_if_cancelled = fail
    else:
        original = knowledge_publication.stage_graph
        def conflicting(*args, **kwargs):
            result = original(*args, **kwargs)
            with factory() as db:
                document = db.get(KnowledgeDocument, "DOC-publication")
                document.lock_version += 1
                db.commit()
            return result
        monkeypatch.setattr(knowledge_publication, "stage_graph", conflicting)
    with pytest.raises((ValueError, RuntimeError)):
        knowledge_publication.publication_job(context, "DOC-publication", draft_id, version, "admin")
    with factory() as db:
        document = db.get(KnowledgeDocument, "DOC-publication")
        assert document.active and document.version == 1 and document.title == "AP old"
        current = list(db.scalars(select(KnowledgeChunk).join(KnowledgeDocument).where(current_chunk_clause())))
        assert [chunk.id for chunk in current] == old_ids
        assert not db.scalar(select(KnowledgePublication))
        assert draft_for_document(db, document.id).status == "FAILED"
    with TestClient(_admin_app(factory)) as client:
        with factory() as db:
            staged = db.scalar(select(KnowledgeChunk).where(KnowledgeChunk.document_version == 2))
            if staged:
                assert client.get(f"/api/v1/knowledge/DOC-publication/versions/2/chunks/{staged.id}").status_code == 404
