from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_dumps
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
            result_json=json_dumps({
                "summary": "<b>untrusted</b> based on EVT-report and LDE-local",
                "confirmed_facts": [{
                    "statement": "Timeout confirmed by EVT-report",
                    "evidence_ids": ["EVT-report"],
                }, {
                    "statement": "Local identity comparison LDE-local",
                    "evidence_ids": ["LDE-local"],
                }],
                "hypotheses": [{
                    "rank": 1,
                    "title": "Heartbeat loss",
                    "description": "Supported by EVT-report",
                    "confidence_level": "HIGH",
                    "priority": "P0",
                    "supporting_evidence": ["EVT-report"],
                    "contradicting_evidence": [],
                }],
                "recommended_actions": [],
                "missing_information": ["Need peer capture"],
                "limitations": [],
            }),
            evidence_json=json_dumps([{
                "evidence_id": "EVT-report",
                "source_type": "log_event",
                "source_file": "nested/ap.log",
                "line_start": 42,
                "line_end": 42,
                "content": "Heartbeat timeout",
            }, {
                "evidence_id": "LDE-local",
                "source_type": "local_derived_evidence",
                "title": "LDE-local",
                "source_file": "gw.log",
                "line_start": 21,
                "line_end": 21,
                "metadata": {
                    "udn_location": {"source_file": "gw.log", "line": 21},
                    "mac_location": {"source_file": "gw.log", "line": 14},
                },
            }]),
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
    rendered = first_path.read_text(encoding="utf-8")
    assert "&lt;b&gt;untrusted&lt;/b&gt;" in rendered
    assert "nested/ap.log - 第 42 行" in rendered
    assert "gw.log - 第 21 行、gw.log - 第 14 行" in rendered
    assert "EVT-report" not in rendered
    assert "LDE-local" not in rendered
    assert rendered.index("六、缺失信息与限制") < rendered.index("八、已确认事实")
    assert not list(first_path.parent.glob("*.tmp"))
    with factory() as db:
        assert list(db.scalars(
            select(Report.version).order_by(Report.version)
        )) == [1, 2]
    engine.dispose()
