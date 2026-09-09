"""Saving a case conclusion and submitting it for review is one transaction."""
import pytest
from sqlalchemy import select

from app.api import workbench as workbench_api
from app.knowledge_contribution_models import KnowledgeContribution
from app.models import AuditEvent
from app.services import workbench_library
from app.workbench_models import WorkbenchRecord
from tests.test_knowledge_contributions_iteration import scope as scope
from tests.test_knowledge_review_integration import api_client, api_path


@pytest.mark.parametrize("interrupt", [False, True])
def test_library_and_review_queue_commit_together(scope, monkeypatch, interrupt):
    factory, contributions = scope
    original_bridge = workbench_library.conclusion_contribution
    if interrupt:
        def interrupted_bridge(*args):
            original_bridge(*args)
            raise RuntimeError("Synthetic interruption before commit")
        monkeypatch.setattr(workbench_library, "conclusion_contribution", interrupted_bridge)
    with api_client(factory, [workbench_api.router], central=True) as client:
        response = client.post(api_path("/workbench/library"), json={
            "title": "Synthetic resolved case", "content": "# Conclusion\nSynthetic verified evidence.",
            "problem_category": "network"})
    assert response.status_code == (500 if interrupt else 200), response.text
    with factory() as db:
        records = list(db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "library")))
        pending = list(db.scalars(select(KnowledgeContribution)))
        audits = list(db.scalars(select(AuditEvent)))
        if interrupt:
            assert not records and not pending and not audits
            return
        assert len(records) == len(pending) == 1
        row = pending[0]
        assert row.source_library_id == response.json()["id"] == records[0].id
        assert row.id == response.json()["contribution_id"] and row.status == "SUBMITTED"
        assert row.owner_id == "owner" and row.content_kind == "KNOWLEDGE"
    repeated = contributions.post(f"/knowledge-contributions/from-library/{response.json()['id']}")
    assert repeated.status_code == 201 and repeated.json()["id"] == response.json()["contribution_id"]
