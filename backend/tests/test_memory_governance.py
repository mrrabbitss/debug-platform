from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import utcnow
from app.models import AnalysisRun, Case, DiagnosisFeedback
from app.schemas import DiagnosisFeedbackCreate
from app.services.memory import extract_memories_from_analysis, search_memories
from app.services.memory_governance import apply_reviewed_resolution, review_memory


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'memory.db').as_posix()}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def candidate(db):
    case = Case(id="CASE-memory-governance", title="AP offline synthetic", device_type="AP", description="")
    run = AnalysisRun(id="RUN-memory-governance", case_id=case.id, status="COMPLETED")
    db.add_all([case, run])
    db.commit()
    items = extract_memories_from_analysis(db, case, run, {
        "summary": "AP offline power reset hypothesis",
        "hypotheses": [{"title": "AP offline power reset", "confidence_score": 0.99, "supporting_evidence": ["EVT-synthetic"]}],
        "recommended_actions": [{"action": "Check AP offline power reset", "reason": "Compare power evidence", "expected_result": "Verify cause"}],
    })
    db.commit()
    memory = next(item for item in items if item.memory_type == "PROCEDURAL")
    return case, run, memory


def test_model_confidence_does_not_publish_or_prove_resolution(db):
    case, _, memory = candidate(db)
    assert memory.outcome == "UNKNOWN"
    assert memory.review_status == "CANDIDATE" and memory.scope == "CASE"
    assert search_memories(db, "AP offline power reset", case_id=case.id)
    assert search_memories(db, "AP offline power reset", case_id="OTHER") == []
    assert search_memories(db, "AP offline power reset", case_id=None) == []
    review_memory(db, memory, action="PUBLISH", expected_version=memory.review_version,
                  reviewer="maintainer", comment="Verified sources and reuse scope")
    db.commit()
    assert search_memories(db, "AP offline power reset", case_id="OTHER")
    assert memory.outcome == "UNKNOWN", "Human reuse approval alone does not imply the fault was fixed"
    memory.expires_at = utcnow() - timedelta(seconds=1)
    db.commit()
    assert search_memories(db, "AP offline power reset", case_id="OTHER") == []


def test_resolution_requires_observation_and_recurrence_quarantines_published_memory(db):
    case, run, memory = candidate(db)
    with pytest.raises(ValidationError):
        DiagnosisFeedbackCreate(analysis_run_id=run.id, verdict="CORRECT", resolution_status="RESOLVED")
    feedback = DiagnosisFeedback(id="FDB-fixed", case_id=case.id, analysis_run_id=run.id, verdict="CORRECT",
                                 status="APPROVED", resolution_status="RESOLVED", resolution_notes="Observed stable for one day",
                                 resolution_observed_at=utcnow() - timedelta(days=1))
    db.add(feedback)
    apply_reviewed_resolution(db, feedback)
    assert memory.outcome == "SUCCESS" and memory.review_status == "CANDIDATE"
    review_memory(db, memory, action="PUBLISH", expected_version=memory.review_version, reviewer="maintainer", comment="Evidence reviewed")
    db.commit()
    recurrence = DiagnosisFeedback(id="FDB-recurred", case_id=case.id, analysis_run_id=run.id, verdict="PARTIAL",
                                   status="APPROVED", resolution_status="RECURRED", resolution_notes="Disconnect recurred after one day",
                                   resolution_observed_at=utcnow())
    db.add(recurrence)
    apply_reviewed_resolution(db, recurrence)
    db.commit()
    assert memory.outcome == "FAILED" and memory.scope == "CASE" and memory.review_status == "CANDIDATE"
    apply_reviewed_resolution(db, feedback)
    assert memory.outcome == "FAILED", "Reviewing older feedback must not override a newer observed recurrence"
    assert search_memories(db, "AP offline power reset", case_id="OTHER") == []


def test_review_is_optimistic_and_archiving_stops_recall(db):
    case, _, memory = candidate(db)
    with pytest.raises(ValueError, match="changed"):
        review_memory(db, memory, action="PUBLISH", expected_version=999, reviewer="maintainer", comment="stale")
    review_memory(db, memory, action="ARCHIVE", expected_version=memory.review_version, reviewer="maintainer", comment="Obsolete procedure")
    db.commit()
    assert all(item.id != memory.id for item, _ in search_memories(db, "AP offline power reset", case_id=case.id))
    with pytest.raises(ValueError, match="Archived"):
        review_memory(db, memory, action="PUBLISH", expected_version=memory.review_version, reviewer="maintainer", comment="invalid")
