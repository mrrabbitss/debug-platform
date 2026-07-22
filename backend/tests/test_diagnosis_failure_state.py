from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import AnalysisRun, Artifact, Case, LogEvent
from app.services import diagnosis


class FakeJobContext:
    def update(self, progress: int, message: str) -> None:
        pass


def test_analysis_failure_does_not_leave_running_records(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'diagnosis.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        db.add(Case(id="CASE-failure", title="failure", description="", status="ANALYZING"))
        db.add(AnalysisRun(id="RUN-failure", case_id="CASE-failure", status="RUNNING"))
        db.add(Artifact(
            id="ART-failure",
            case_id="CASE-failure",
            original_name="system.log",
            stored_path=str(tmp_path / "system.log"),
            sha256="a" * 64,
            size_bytes=7,
        ))
        db.add(LogEvent(
            id="EVT-failure",
            case_id="CASE-failure",
            artifact_id="ART-failure",
            source_file="system.log",
            message="failure",
            raw_text="failure",
        ))
        db.commit()

    monkeypatch.setattr(diagnosis, "SessionLocal", session_factory)
    monkeypatch.setattr(
        diagnosis,
        "_analyze_case_impl",
        lambda ctx, case_id: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    with pytest.raises(RuntimeError, match="model unavailable"):
        diagnosis.analyze_case_job(FakeJobContext(), "CASE-failure")

    with session_factory() as db:
        run = db.get(AnalysisRun, "RUN-failure")
        case = db.get(Case, "CASE-failure")
        assert run.status == "FAILED"
        assert run.error_message == "model unavailable"
        assert run.completed_at is not None
        assert case.status == "PARSED"

    engine.dispose()
