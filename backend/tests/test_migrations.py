from pathlib import Path

from sqlalchemy import create_engine, inspect, select, text

from app.core.db import Base
from app.core.migrations import run_database_migrations
from app.models import Case


def test_migrations_create_fresh_database_and_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "fresh.db"
    database_url = f"sqlite:///{database_path.as_posix()}"

    run_database_migrations(database_url)
    run_database_migrations(database_url)

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())
    assert {"cases", "artifacts", "log_events", "model_profiles", "alembic_version"}.issubset(table_names)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
    analysis_column_info = {item["name"]: item for item in inspect(engine).get_columns("analysis_runs")}
    event_indexes = {item["name"] for item in inspect(engine).get_indexes("log_events")}
    model_indexes = {item["name"] for item in inspect(engine).get_indexes("model_profiles")}
    assert "ix_log_events_case_time" in event_indexes
    assert "uq_model_profiles_active_task" in model_indexes
    assert {"model_profile_id", "model_config_json"}.issubset(analysis_column_info)
    assert analysis_column_info["model"]["type"].length == 512
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
    engine.dispose()

    run_database_migrations(database_url)

    engine = create_engine(database_url)
    with engine.connect() as connection:
        title = connection.scalar(select(Case.title).where(Case.id == "CASE-legacy"))
        version = connection.scalar(text("SELECT version_num FROM alembic_version"))
    assert title == "legacy case"
    assert version == "0003"
    engine.dispose()
