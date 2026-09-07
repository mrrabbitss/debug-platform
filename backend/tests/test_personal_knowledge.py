"""Personal corrections improve retrieval without replacing other users' evidence."""
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.utils import json_dumps
from app.models import Case, Job, KnowledgeAccess, KnowledgeDocument, KnowledgeDraft, KnowledgeGraphState
from app.services import knowledge_publication
from app.services.knowledge import index_document
from app.services.knowledge_access import require_knowledge_access, require_publisher
from app.services.knowledge_drafts import save_draft
from app.services.knowledge_personal import personal_view
from app.services.diagnostic_methods import load_applicable_diagnostic_methods
from app.services.rag import retriever
from app.services.model_profiles import seed_model_profiles
from tests.test_knowledge_governance_graph_evaluation import _factory, _Context


@pytest.fixture
def knowledge(tmp_path):
    engine, factory = _factory(tmp_path, "personal.db")
    with factory() as db:
        seed_model_profiles(db)
        db.add(Case(id="CASE-team", title="AP offline", description="", device_type="AP"))
        doc = KnowledgeDocument(id="DOC-team", title="AP", source_type="analysis_skill",
            content="# Logs\n`OLD_AUTH_TIMEOUT`", active=True, review_status="ACTIVE")
        db.add(doc)
        db.flush()
        db.add(KnowledgeAccess(document_id=doc.id, owner_id="publisher", publisher_id="publisher"))
        index_document(db, doc)
        db.commit()
    yield factory
    engine.dispose()


def test_two_engineers_have_independent_immutable_views(knowledge):
    with knowledge() as db:
        doc = db.get(KnowledgeDocument, "DOC-team")
        for author, text in (("alice", "POWER_FAILURE"), ("bob", "DHCP_RENEW_FAILED")):
            require_knowledge_access(db, doc.id, {"id": author, "role": "ENGINEER"}, write=True)
            save_draft(db, doc, {"content": f"# Logs\n`{text}`\nCheck the power supply or lease."},
                expected_lock_version=doc.lock_version, expected_draft_version=None, author=author)
        alice = personal_view(db, "alice")
        bob = personal_view(db, "bob")
        assert alice and bob and alice != bob
        assert not personal_view(db, "charlie")
        db.commit()
        for actor_view, term in ((alice, "POWER_FAILURE"), (bob, "DHCP_RENEW_FAILED")):
            hits = retriever.search(term, db=db, apply_models=False, knowledge_view=actor_view)
            assert hits and term in hits[0].content
            assert hits[0].metadata["publication_status"] == "PERSONAL_UNREVIEWED"
            assert not retriever.search(term, db=db, apply_models=False)
        draft = db.scalar(select(KnowledgeDraft).where(KnowledgeDraft.owner_key == "alice"))
        save_draft(db, doc, {"content": "# Logs\n`NEWER_POWER_FAULT`"}, expected_lock_version=doc.lock_version,
            expected_draft_version=draft.version, author="alice")
        db.commit()
        # An already created run retains the earlier content, not the latest draft.
        hits = retriever.search("POWER_FAILURE", db=db, apply_models=False, knowledge_view=alice)
        assert "POWER_FAILURE" in hits[0].content and "NEWER" not in hits[0].content
        methods = load_applicable_diagnostic_methods(db, db.get(Case, "CASE-team"), knowledge_view=alice)
        method = next(m for m in methods if m.id == "DOC-team")
        assert method.personal_revision_id == alice[0] and "POWER_FAILURE" in method.content
        assert db.get(KnowledgeDocument, doc.id).content == "# Logs\n`OLD_AUTH_TIMEOUT`"


def test_only_admin_or_original_publisher_may_publish(knowledge):
    with knowledge() as db:
        doc = db.get(KnowledgeDocument, "DOC-team")
        require_publisher(db, doc, {"id": "publisher", "role": "ENGINEER"})
        require_publisher(db, doc, {"id": "other-admin", "role": "ADMIN"})
        with pytest.raises(HTTPException) as exc:
            require_publisher(db, doc, {"id": "alice", "role": "ENGINEER"})
        assert exc.value.status_code == 403


def test_initial_publication_builds_complete_generation(knowledge, monkeypatch):
    monkeypatch.setattr(knowledge_publication, "SessionLocal", knowledge)
    with knowledge() as db:
        doc = db.get(KnowledgeDocument, "DOC-team")
        doc.active = False
        doc.review_status = "IN_REVIEW"
        draft = KnowledgeDraft(id="DRAFT-first", document_id=doc.id, base_version=doc.version,
            owner_key="publisher", created_by="publisher", status="IN_REVIEW",
            snapshot_json=json_dumps({"title": "Confirmed AP power issue", "content": "# Logs\nPOWER_FAILURE",
                "source_type": "analysis_skill", "trust_level": "HIGH", "confidentiality": "INTERNAL"}))
        db.add(draft)
        db.commit()
        version = draft.version
    result = knowledge_publication.publication_job(_Context(), "DOC-team", "DRAFT-first", version, "admin")
    assert result["graph_generation_id"]
    with knowledge() as db:
        doc = db.get(KnowledgeDocument, "DOC-team")
        assert doc.active and doc.review_status == "ACTIVE"
        assert retriever.search("POWER_FAILURE", db=db, apply_models=False)
        assert db.get(KnowledgeAccess, doc.id).publisher_id == "publisher"


def test_crashed_publication_releases_only_its_own_build(knowledge):
    with knowledge() as db:
        db.add(Job(id="JOB-crashed", kind="publish_knowledge_revision", status="DEAD_LETTER"))
        db.add(KnowledgeDraft(id="DRAFT-crashed", document_id="DOC-team", base_version=1,
            owner_key="alice", status="BUILDING", snapshot_json="{}", publication_job_id="JOB-crashed",
            building_generation_id="KGEN-abandoned"))
        db.add(KnowledgeGraphState(id="domain", active_generation_id="KGEN-good",
            building_generation_id="KGEN-abandoned", status="BUILDING"))
        db.commit()
        assert knowledge_publication.recover_abandoned_publications(db) == 1
        db.commit()
        assert db.get(KnowledgeDraft, "DRAFT-crashed").status == "FAILED"
        graph = db.get(KnowledgeGraphState, "domain")
        assert graph.active_generation_id == "KGEN-good" and graph.building_generation_id is None
        assert db.get(KnowledgeDocument, "DOC-team").active


def test_web_and_host_entrypoints_pin_the_authors_revision(knowledge, monkeypatch):
    from app.core.utils import json_loads
    from app.models import ModelProfile
    from app.services import diagnosis, host_diagnosis
    from app.services.host_agent_sessions import require_host_agent_session
    from tests.test_host_diagnosis import _seed_case

    _seed_case(knowledge)
    with knowledge() as db:
        doc = db.get(KnowledgeDocument, 'DOC-team')
        save_draft(db, doc, {'content': '# Logs\n`PERSONAL_POWER_FAULT`'},
            expected_lock_version=doc.lock_version, expected_draft_version=None, author='alice')
        profile = db.scalar(select(ModelProfile).where(ModelProfile.task_type == 'chat'))
        monkeypatch.setattr(diagnosis, 'get_active_chat_model_info', lambda: {
            'provider': 'mock', 'model': 'synthetic', 'profile_id': profile.id})
        run, _ = diagnosis.prepare_analysis_run(db, case=db.get(Case, 'CASE-team'), created_by='alice')
        pinned = json_loads(run.model_config_json, {})['personal_knowledge_revisions']
        assert pinned == personal_view(db, 'alice')
        db.commit()
    host = host_diagnosis.begin_host_diagnosis('CASE-host-finalize', executor='codex_cli',
        client_model_claim='synthetic-no-model-call', skill_version='test', created_by='alice', session_factory=knowledge)
    with knowledge() as db:
        view = require_host_agent_session(db, host['run']['id'])
        assert view.snapshot['personal_knowledge_revisions'] == pinned
        draft = db.scalar(select(KnowledgeDraft).where(KnowledgeDraft.owner_key == 'alice'))
        doc = db.get(KnowledgeDocument, 'DOC-team')
        save_draft(db, doc, {'content': '# Logs\n`LATER_CORRECTION`'}, expected_lock_version=doc.lock_version,
            expected_draft_version=draft.version, author='alice')
        db.commit()
    snapshot = host_diagnosis.require_unchanged_host_snapshot(view, session_factory=knowledge)
    assert 'PERSONAL_POWER_FAULT' in next(m.content for m in snapshot.methods if m.id == 'DOC-team')
