"""Only the new offline six-file replacement path; no Chat or remote models."""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select

from app.core.utils import json_dumps, json_loads
from app.models import (Case, Job, KnowledgeChunk, KnowledgeDocument, KnowledgeDraft,
                        KnowledgeEmbedding, KnowledgePublication, ModelProfile)
from app.services import knowledge_reset as reset
from app.workbench_models import WorkbenchRecord
from tests.test_installer_bundled_knowledge import server
from tests.test_bundled_skill_additive import old_corpus

spec = importlib.util.spec_from_file_location("offline_worker", Path(__file__).resolve().parents[2] / "scripts/offline_skill_update.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


@pytest.fixture
def offline(server, monkeypatch):
    monkeypatch.setattr(reset, "KIND", reset.KIND)
    monkeypatch.setattr(reset, "MUTATING_JOBS", reset.MUTATING_JOBS)
    old_corpus(server)
    return server


def run(server, **kwargs):
    return worker.run_update(data_root=server.root, source_zip=server.source,
        archive_root=server.root.parent / "backups", manager_factory=lambda: pytest.fail("No model process expected"), **kwargs)


def test_offline_partial_replacement_keeps_similar_names_drafts_and_indexes(offline):
    with offline.factory() as db:
        db.add_all([
            KnowledgeDocument(id="old-skill", title="hilink-diag/SKILL.md", content="Previous approved root",
                active=True, review_status="ACTIVE", metadata_json=json_dumps({"content_kind": "SKILL", "problem_categories": ["network"]})),
            KnowledgeDocument(id="other-skill", title="other/SKILL.md", content="Other direction", active=True,
                review_status="ACTIVE", confidentiality="INTERNAL",
                metadata_json=json_dumps({"content_kind": "SKILL", "problem_categories": ["network"]})),
            KnowledgeDocument(id="other-category", title="hilink-diag/SKILL.md", content="Connection direction", active=True,
                review_status="ACTIVE", confidentiality="INTERNAL",
                metadata_json=json_dumps({"content_kind": "SKILL", "problem_categories": ["connection"]})),
        ])
        db.flush()
        for key in ("old-skill", "other-skill", "other-category"):
            db.add(KnowledgeChunk(id=key + "-chunk", document_id=key, document_version=1, chunk_index=0, content=key))
        db.commit()
    result = run(offline)
    assert result["status"] == "PUBLISHED" and result["retired_documents"] == 1
    assert Path(result["backup"]).is_file()
    with offline.factory() as db:
        assert db.get(KnowledgeDocument, "old-skill").review_status == "ARCHIVED"
        assert db.get(KnowledgeDocument, "old-skill").content == "Previous approved root"
        assert db.scalar(select(KnowledgePublication).where(KnowledgePublication.document_id == "old-skill"))
        assert db.get(KnowledgeDocument, "other-skill").active
        assert db.get(KnowledgeDocument, "other-category").active
        assert db.get(KnowledgeDraft, "old-draft").status == "DRAFT"
        assert db.get(Case, "case").title == "Retain this synthetic case"
        assert json_loads(db.get(WorkbenchRecord, "template-network").payload_json, {})["document_id"] == "old"
        chunks = set(db.scalars(select(KnowledgeEmbedding.chunk_id).where(
            KnowledgeEmbedding.generation_id == result["embedding_generation_id"])))
        assert {"old-chunk", "other-skill-chunk", "other-category-chunk"} <= chunks
        assert "old-skill-chunk" not in chunks
        assert db.scalar(select(Job.kind)) == worker.KIND
    assert run(offline)["status"] == "UNCHANGED"
    with offline.factory() as db:
        assert len(list(db.scalars(select(Job)))) == 1


def test_failure_retry_preserves_approval_then_updates_old_bundle_template(offline, monkeypatch):
    with offline.factory() as db:
        db.delete(db.get(WorkbenchRecord, "template-network"))
        db.commit()
    first = run(offline)
    with offline.factory() as db:
        root = db.scalar(select(KnowledgeDocument).where(KnowledgeDocument.title == "hilink-diag/SKILL.md"))
        root.content = "Old root changed by administrator"
        old_template = json_loads(db.get(WorkbenchRecord, "template-network").payload_json, {})["document_id"]
        db.commit()
    real_index = reset.index_embeddings
    monkeypatch.setattr(reset, "index_embeddings", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    with pytest.raises(reset.ResetError):
        run(offline)
    with offline.factory() as db:
        assert db.get(ModelProfile, "embedding").active_embedding_generation_id == first["embedding_generation_id"]
        assert db.get(KnowledgeDocument, old_template).active
        failed = db.scalar(select(Job).where(Job.status == "FAILED"))
        job_id = failed.id
        approval = next(json_loads(r.payload_json, {}) for r in db.scalars(select(WorkbenchRecord).where(
            WorkbenchRecord.kind == worker.KIND)) if json_loads(r.payload_json, {}).get("status") != "PUBLISHED")
    monkeypatch.setattr(reset, "index_embeddings", real_index)
    second = run(offline)
    assert second["operation_id"] == approval["operation_id"] and second["retired_documents"] == 6
    with offline.factory() as db:
        assert db.get(Job, job_id).status == "COMPLETED" and db.get(Job, job_id).attempt == 2
        assert not db.get(KnowledgeDocument, old_template).active
        assert json_loads(db.get(WorkbenchRecord, "template-network").payload_json, {})["document_id"] in second["documents"]


def test_external_embedding_requires_explicit_option_before_any_approval(offline):
    with offline.factory() as db:
        profile = db.get(ModelProfile, "embedding")
        profile.mode, profile.provider = "api", "openai_compatible"
        db.commit()
    with pytest.raises(reset.ResetError, match="allow-configured-embedding-api"):
        run(offline)
    with offline.factory() as db:
        assert not list(db.scalars(select(Job)))
        assert db.get(KnowledgeDocument, "old").active
