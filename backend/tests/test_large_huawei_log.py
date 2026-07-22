import time
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_loads
from app.models import Artifact, Case, LogEvent
from app.services import parse_service
from app.services.text_files import read_text_range


LINE_COUNT = 110_904


class RecordingJobContext:
    def __init__(self) -> None:
        self.updates: list[tuple[int, str]] = []

    def update(self, progress: int, message: str) -> None:
        self.updates.append((progress, message))


def write_large_huawei_log(path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("============================================================\n")
        handle.write("Start run collect command:WAP:get wlan basic laninst 1 wlaninst6\n")
        for index in range(LINE_COUNT - 2):
            level = "ERROR" if index % 20_000 == 0 else "NOTICE"
            handle.write(
                f"{level} 2026-03-02 03:29:17.483[90][DC]DC synthetic runtime line {index}\n"
            )


def test_streams_and_batches_110904_line_huawei_log(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "B89FCC54FBEC_GW_collectDebuginfo_2026_03_02_11_22_490"
    write_large_huawei_log(source)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'large-log.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        db.add(Case(id="CASE-large", title="large log", description=""))
        db.add(Artifact(
            id="ART-large",
            case_id="CASE-large",
            original_name=source.name,
            stored_path=str(source),
            sha256="a" * 64,
            size_bytes=source.stat().st_size,
            status="UPLOADED",
        ))
        db.commit()

    monkeypatch.setattr(parse_service, "SessionLocal", session_factory)
    context = RecordingJobContext()
    started = time.monotonic()
    result = parse_service.parse_artifact_job(context, "CASE-large", "ART-large")
    elapsed = time.monotonic() - started

    assert result["events"] == LINE_COUNT - 1
    assert elapsed < 60
    assert any("1000 events" in message for _, message in context.updates)
    with session_factory() as db:
        artifact = db.get(Artifact, "ART-large")
        event_count = int(db.scalar(
            select(func.count(LogEvent.id)).where(LogEvent.artifact_id == "ART-large")
        ) or 0)
        metadata = json_loads(artifact.metadata_json, {})
        assert artifact.status == "PARSED"
        assert event_count == LINE_COUNT - 1
        assert metadata["manifest"][0]["line_count"] == LINE_COUNT
        assert metadata["manifest"][0]["encoding"] == "utf-8"

    selected = read_text_range(
        Path(metadata["extract_root"]) / metadata["manifest"][0]["path"],
        110_900,
        4,
    )
    assert selected is not None
    assert selected.returned_lines == 4
    assert "synthetic runtime line 110897" in selected.text

    engine.dispose()
