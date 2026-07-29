from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import AnalysisRun, Case, Report
from app.services import report
from app.services.storage import StorageService


def test_report_versions_are_reserved_uniquely_and_published_atomically(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'reports.db'}")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with factory() as db:
        db.add(Case(
            id="CASE-report",
            title="Report",
            description="",
        ))
        db.add(AnalysisRun(
            id="ANL-report",
            case_id="CASE-report",
            status="COMPLETED",
            result_json='{"summary":"<b>untrusted</b>"}',
            evidence_json="[]",
        ))
        db.commit()

    managed_storage = StorageService(tmp_path / "storage")
    monkeypatch.setattr(report, "SessionLocal", factory)
    monkeypatch.setattr(report, "storage", managed_storage)

    first = report.generate_html_file("CASE-report", "ANL-report")
    second = report.generate_html_file("CASE-report", "ANL-report")

    assert (first.version, second.version) == (1, 2)
    assert first.sha256 and second.sha256
    first_path = managed_storage.resolve_path(first.stored_path)
    second_path = managed_storage.resolve_path(second.stored_path)
    assert first_path.exists()
    assert second_path.exists()
    assert first_path != second_path
    assert "&lt;b&gt;untrusted&lt;/b&gt;" in first_path.read_text(
        encoding="utf-8"
    )
    assert not list(first_path.parent.glob("*.tmp"))
    with factory() as db:
        assert list(db.scalars(
            select(Report.version).order_by(Report.version)
        )) == [1, 2]
    engine.dispose()
