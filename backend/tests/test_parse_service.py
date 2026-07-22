from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_loads
from app.models import Artifact, Case
from app.services import parse_service


class FakeJobContext:
    def __init__(self) -> None:
        self.messages = []

    def update(self, progress: int, message: str) -> None:
        self.messages.append((progress, message))


def test_parse_job_marks_artifact_failed_when_no_readable_text_exists(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    source = tmp_path / "binary.txt"
    source.write_bytes(b"header\x00payload")

    with test_session() as db:
        db.add(Case(id="CASE-test", title="test", description=""))
        db.add(Artifact(
            id="ART-test",
            case_id="CASE-test",
            kind="debug_log",
            original_name="binary.txt",
            stored_path=str(source),
            sha256="a" * 64,
            size_bytes=source.stat().st_size,
            status="UPLOADED",
        ))
        db.commit()

    monkeypatch.setattr(parse_service, "SessionLocal", test_session)
    context = FakeJobContext()

    with pytest.raises(ValueError, match="No readable text log files were parsed"):
        parse_service.parse_artifact_job(context, "CASE-test", "ART-test")

    with test_session() as db:
        artifact = db.get(Artifact, "ART-test")
        case = db.get(Case, "CASE-test")
        assert artifact is not None
        assert case is not None
        assert artifact.status == "PARSE_FAILED"
        assert case.status == "UPLOADED"
        assert "inspect_log_file.bat" in json_loads(artifact.metadata_json)["parse_error"]


def test_parse_job_restores_domain_state_after_unexpected_extract_failure(tmp_path: Path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'extract-failure.db'}")
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    source = tmp_path / "unsupported.bin"
    source.write_bytes(b"\x00\x01\x02\x03")

    with test_session() as db:
        db.add(Case(id="CASE-extract", title="test", description=""))
        db.add(Artifact(
            id="ART-extract",
            case_id="CASE-extract",
            kind="debug_log",
            original_name="unsupported.bin",
            stored_path=str(source),
            sha256="b" * 64,
            size_bytes=source.stat().st_size,
            status="UPLOADED",
        ))
        db.commit()

    monkeypatch.setattr(parse_service, "SessionLocal", test_session)
    with pytest.raises(ValueError, match="Unsupported archive"):
        parse_service.parse_artifact_job(FakeJobContext(), "CASE-extract", "ART-extract")

    with test_session() as db:
        artifact = db.get(Artifact, "ART-extract")
        case = db.get(Case, "CASE-extract")
        assert artifact.status == "PARSE_FAILED"
        assert case.status == "UPLOADED"
        assert "Unsupported archive" in json_loads(artifact.metadata_json)["parse_error"]

    engine.dispose()
