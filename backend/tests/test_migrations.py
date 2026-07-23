from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text

from app.core.db import Base
from app.core.migrations import run_database_migrations
from app.models import Artifact, Case, LogEvent


def test_migrations_create_fresh_database_and_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    run_database_migrations(database_url)
    run_database_migrations(database_url)

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())
    assert {
        "cases", "artifacts", "log_events", "model_profiles", "audit_events",
        "user_accounts", "access_tokens", "case_members", "alembic_version",
    }.issubset(table_names)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0006"
    analysis_column_info = {item["name"]: item for item in inspect(engine).get_columns("analysis_runs")}
    event_indexes = {item["name"] for item in inspect(engine).get_indexes("log_events")}
    model_indexes = {item["name"] for item in inspect(engine).get_indexes("model_profiles")}
    assert "ix_log_events_case_time" in event_indexes
    assert "uq_model_profiles_active_task" in model_indexes
    assert {"model_profile_id", "model_config_json"}.issubset(analysis_column_info)
    assert analysis_column_info["model"]["type"].length == 512
    artifact_columns = {item["name"] for item in inspect(engine).get_columns("artifacts")}
    case_columns = {item["name"] for item in inspect(engine).get_columns("cases")}
    event_columns = {item["name"] for item in inspect(engine).get_columns("log_events")}
    assert "active_parse_run_id" in artifact_columns
    assert "owner_id" in case_columns
    assert "parse_run_id" in event_columns
    engine.dispose()


def test_migrations_adopt_legacy_create_all_database_without_data_loss(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(database_url)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(Case.__table__.insert().values(
            id="CASE-legacy",
            title="legacy case",
            device_type="GW",
            description="",
            status="DRAFT",
            severity="UNKNOWN",
        ))
        connection.execute(Artifact.__table__.insert().values(
            id="ART-legacy",
            case_id="CASE-legacy",
            kind="debug_log",
            original_name="legacy.log",
            stored_path="artifacts/ART-legacy/legacy.log",
            sha256="a" * 64,
            size_bytes=10,
            status="PARSED",
            metadata_json="{}",
        ))
        connection.execute(LogEvent.__table__.insert().values(
            id="EVT-legacy",
            case_id="CASE-legacy",
            artifact_id="ART-legacy",
            source_file="legacy.log",
            line_start=1,
            line_end=1,
            level="ERROR",
            module="SYSTEM",
            component="legacy",
            event_code="LEGACY",
            message="legacy event",
            raw_text="legacy event",
            entities_json="{}",
            parser_id="legacy",
            parser_version="1",
            confidence=1.0,
        ))
    engine.dispose()

    run_database_migrations(database_url)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        title = connection.scalar(select(Case.title).where(Case.id == "CASE-legacy"))
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
        active_run_id = connection.scalar(text(
            "SELECT active_parse_run_id FROM artifacts WHERE id = 'ART-legacy'"
        ))
        event_run_id = connection.scalar(text(
            "SELECT parse_run_id FROM log_events WHERE id = 'EVT-legacy'"
        ))
    assert title == "legacy case"
    assert version == "0006"
    assert active_run_id == "ART-legacy"
    assert event_run_id == "ART-legacy"
    engine.dispose()
