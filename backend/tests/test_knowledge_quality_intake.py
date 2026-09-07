"""Human-curated imports become searchable only after a successful publication."""
import hashlib
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.knowledge_intake import HumanVerifiedApproval, adopt_verified
from app.core.utils import json_dumps
from app.models import Job, KnowledgeDocument
from app.services import knowledge_publication
from app.services.knowledge_access import authorize_routing_job
from app.services.knowledge_compiler import compile_markdown, read_sections
from app.services.knowledge_quality import quality_report
from app.services.model_profiles import seed_model_profiles
from app.services.rag import retriever
from tests.test_knowledge_governance_graph_evaluation import _factory, _Context


def test_long_markdown_middle_and_end_are_addressable():
    content = '# Intro\n' + 'ordinary text\n' * 3500 + '\n## Logs\n`MIDDLE_POWER_FAULT`\n' + 'more text\n' * 3000 + '\n## Verification\nEND_CHECK\n'
    compiled = compile_markdown(content)
    assert compiled['covered_characters'] == len(content)
    pages = []
    offset = 0
    while True:
        page = read_sections(content, content_sha256=compiled['content_sha256'], offset=offset)
        pages.extend(row['content'] for row in page['sections'])
        offset = page['next_offset']
        if offset is None:
            break
    assert ''.join(pages) == content
    with pytest.raises(ValueError, match='changed'):
        read_sections(content + 'changed', content_sha256=compiled['content_sha256'])


def test_human_attestation_publishes_with_quality_and_hash_guards(tmp_path, monkeypatch):
    engine, factory = _factory(tmp_path, 'intake.db')
    monkeypatch.setattr(knowledge_publication, 'SessionLocal', factory)
    captured = {}

    def submit(db, kind, handler, *args, input_data):
        captured.update(input_data)
        job = Job(id='JOB-history', kind=kind, status='QUEUED', input_json=json_dumps(input_data))
        db.add(job)
        db.commit()
        return job

    monkeypatch.setattr('app.api.knowledge_intake.job_runner.submit', submit)
    try:
        with factory() as db:
            seed_model_profiles(db)
            content = '# Logs\n`CONFIRMED_POWER_FAULT` means the power supply failed.\n## Verification\nReplace supply and verify uptime.'
            doc = KnowledgeDocument(id='DOC-history', title='Verified AP power failure', source_type='analysis_skill', content=content, review_status='DRAFT', active=False)
            hidden = KnowledgeDocument(id='DOC-hidden', title=doc.title, source_type='analysis_skill', content=content, confidentiality='RESTRICTED', active=True, review_status='ACTIVE')
            db.add_all([doc, hidden])
            db.commit()
            report = quality_report(db, doc, {'id': 'engineer', 'role': 'ENGINEER'})
            assert report['patterns'] and report['patterns'][0]['line_start'] == 2
            assert not report['findings']
            payload = HumanVerifiedApproval(human_verified=True, expected_lock_version=doc.lock_version, content_sha256=hashlib.sha256(content.encode()).hexdigest())
            request = SimpleNamespace(state=SimpleNamespace(principal={'id': 'admin', 'role': 'ADMIN'}))
            with pytest.raises(HTTPException) as exc:
                adopt_verified(doc.id, payload.model_copy(update={'content_sha256': '0' * 64}), request, db)
            assert exc.value.status_code == 409 and doc.review_status == 'DRAFT'
            result = adopt_verified(doc.id, payload, request, db)
            assert result['publication_pending'] and doc.trust_level == 'HIGH' and not doc.active
            assert not retriever.search('CONFIRMED_POWER_FAULT', db=db, apply_models=False)
            with pytest.raises(HTTPException):
                authorize_routing_job(db, 'JOB-history', {'id': 'other', 'role': 'ENGINEER'})
        knowledge_publication.publication_job(_Context(), **captured)
        with factory() as db:
            doc = db.get(KnowledgeDocument, 'DOC-history')
            assert doc.active and doc.trust_level == 'HIGH'
            assert retriever.search('CONFIRMED_POWER_FAULT', db=db, apply_models=False)
    finally:
        engine.dispose()
