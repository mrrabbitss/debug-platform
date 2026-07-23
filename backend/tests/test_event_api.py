from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.core.db import Base, get_db
from app.core.utils import json_dumps
from app.models import Artifact, Case, LogEvent
from app.services.text_files import open_text_lines


def test_event_pagination_stats_and_artifact_id(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'events.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    extract_root = tmp_path / "extracted"
    extract_root.mkdir()
    log_path = extract_root / "system.log"
    log_path.write_text("".join(f"line {index}\n" for index in range(1, 13)), encoding="utf-8")
    line_index: list[list[int]] = []
    opened = open_text_lines(log_path, index_stride=5, line_index=line_index)
    assert opened is not None
    encoding, lines = opened
    assert len(list(lines)) == 12
    with session_factory() as db:
        db.add(Case(id="CASE-events", title="events", description=""))
        db.add(Artifact(
            id="ART-events",
            case_id="CASE-events",
            original_name="system.log",
            stored_path=str(tmp_path / "system.log"),
            sha256="a" * 64,
            size_bytes=1,
            status="PARSED",
            metadata_json=json_dumps({
                "extract_root": str(extract_root),
                "manifest": [{
                    "path": "system.log",
                    "size": log_path.stat().st_size,
                    "line_count": 12,
                    "line_index": line_index,
                    "encoding": encoding,
                }],
            }),
        ))
        for index in range(12):
            db.add(LogEvent(
                id=f"EVT-{index}",
                case_id="CASE-events",
                artifact_id="ART-events",
                source_file="system.log",
                line_start=index + 1,
                line_end=index + 1,
                level="ERROR" if index < 3 else "NOTICE",
                module="WLAN" if index % 2 == 0 else "WAN",
                event_code="TEST_EVENT",
                message=f"event {index}",
                raw_text=f"event {index}",
            ))
        db.commit()

    def override_db():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        page = client.get("/api/v1/cases/CASE-events/events", params={"limit": 5, "offset": 5})
        assert page.status_code == 200
        assert len(page.json()) == 5
        assert all(item["artifact_id"] == "ART-events" for item in page.json())

        stats = client.get("/api/v1/cases/CASE-events/events/stats", params={"level": "ERROR"})
        assert stats.status_code == 200
        assert stats.json()["total"] == 12
        assert stats.json()["filtered_total"] == 3
        assert stats.json()["module_counts"] == {"WAN": 6, "WLAN": 6}

        content = client.get(
            "/api/v1/artifacts/ART-events/content",
            params={"path": "system.log", "start_line": 10, "line_count": 2},
        )
        assert content.status_code == 200
        assert content.text == "line 10\nline 11"
        assert content.headers["x-start-line"] == "10"
        assert content.headers["x-returned-lines"] == "2"
        assert content.headers["x-total-lines"] == "12"
        assert content.headers["x-has-more"] == "true"

        search = client.get(
            "/api/v1/artifacts/ART-events/search",
            params={"path": "system.log", "query": "line 11"},
        )
        assert search.status_code == 200
        assert search.json()["matches"] == [{"line_number": 11, "text": "line 11"}]
        assert search.json()["has_more"] is False

    engine.dispose()
