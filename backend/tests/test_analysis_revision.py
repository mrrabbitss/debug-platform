import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_dumps, json_loads
from app.diagnostic_models import AnalysisRevision
from app.models import AnalysisRun, Case, ConversationMessage
from app.services import analysis_revision


def _diagnosis(evidence_id: str = "EVT-1") -> dict:
    return {
        "summary": "Current diagnosis",
        "case": {"id": "CASE-revision"},
        "confirmed_facts": [{"statement": "Known fact", "evidence_ids": [evidence_id]}],
        "hypotheses": [{
            "rank": 1, "title": "Current root cause", "description": "Current evidence",
            "supporting_evidence": [evidence_id], "contradicting_evidence": [],
            "confidence_score": 0.7, "confidence_level": "MEDIUM",
            "priority": "P1", "needs_human_review": True,
        }],
        "recommended_actions": [{
            "priority": "P1", "action": "Check both devices", "reason": "Joint topology",
            "expected_result": "Cause confirmed or excluded",
        }],
        "missing_information": [], "suspected_modules": ["GW", "AP"],
        "limitations": ["Human review required"],
        "analysis_engine": "test",
    }


class _Provider:
    is_mock = False
    provider_id = "openai_compatible"
    model_name = "glm-5.2"
    last_usage = {"prompt_tokens": 10, "completion_tokens": 10}

    def __init__(self, evidence_id: str = "EVT-1") -> None:
        self.evidence_id = evidence_id

    async def generate_json(self, *args, **kwargs):
        return {
            "change_summary": "Added cross-device verification",
            "assistant_message": "Draft ready for human review",
            "revised_diagnosis": _diagnosis(self.evidence_id),
        }


def test_revision_generation_rejects_unknown_evidence(monkeypatch) -> None:
    monkeypatch.setattr(analysis_revision, "get_llm_provider", lambda: _Provider("FAKE-ID"))
    case = Case(id="CASE-revision", title="Joint case", device_type="AP")
    source = AnalysisRun(
        id="RUN-source", case_id=case.id, status="COMPLETED",
        result_json=json_dumps(_diagnosis()),
    )

    with pytest.raises(ValueError, match="unknown evidence IDs"):
        asyncio.run(analysis_revision._generate_revision(
            case=case, source=source, instruction="Change the report",
            current_message_id="MSG-current",
            evidence=[{"evidence_id": "EVT-1", "content": "known"}],
            conversation_history=[],
        ))


def test_apply_revision_creates_new_analysis_and_rejects_stale_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'revision.db'}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        case = Case(id="CASE-revision", title="Joint case", device_type="AP")
        source = AnalysisRun(
            id="RUN-source", case_id=case.id, status="COMPLETED",
            provider="openai_compatible", model="glm-5.2",
            result_json=json_dumps(_diagnosis()),
            evidence_json=json_dumps([{"evidence_id": "EVT-1", "content": "known"}]),
        )
        message = ConversationMessage(
            id="MSG-revision", case_id=case.id, role="user",
            content="Change the report", status="COMPLETED",
        )
        revision = AnalysisRevision(
            id="AREV-1", case_id=case.id, source_analysis_id=source.id,
            source_message_id=message.id, status="DRAFT", instruction=message.content,
            proposed_result_json=json_dumps(_diagnosis()),
            proposed_evidence_json=source.evidence_json,
            change_summary="Joint scope",
        )
        db.add(case)
        db.flush()
        db.add_all([source, message])
        db.flush()
        db.add(revision)
        db.commit()

        applied = analysis_revision.apply_analysis_revision(
            db, revision, reviewed_by="USER-reviewer",
        )
        db.commit()
        assert applied.id != source.id
        assert revision.status == "APPLIED"
        assert revision.applied_analysis_id == applied.id
        assert json_loads(applied.result_json, {})["revision_provenance"]["status"] == "HUMAN_APPROVED"

        stale = AnalysisRevision(
            id="AREV-stale", case_id=case.id, source_analysis_id=source.id,
            status="DRAFT", instruction="stale", proposed_result_json=json_dumps(_diagnosis()),
            proposed_evidence_json=source.evidence_json,
        )
        db.add(stale)
        db.commit()
        with pytest.raises(ValueError, match="newer diagnosis"):
            analysis_revision.apply_analysis_revision(
                db, stale, reviewed_by="USER-reviewer",
            )
        assert db.scalar(select(AnalysisRun.id).where(AnalysisRun.id == source.id)) == source.id
    engine.dispose()
