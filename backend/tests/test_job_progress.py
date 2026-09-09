"""New durable progress behavior only; no network or live model requests."""
import asyncio
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.core.config import BACKEND_ROOT
from app.core.db import Base, configure_sqlite_engine
from app.core.utils import json_loads, utcnow
from app.models import Job
from app.schemas import JobOut
from app.services import jobs
from app.services.job_progress import active_job_context, report_progress, track_model_request


@pytest.fixture
def progress_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{(tmp_path / 'progress.db').as_posix()}",
                          connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(jobs, "SessionLocal", factory)
    with factory() as db:
        db.add(Job(id="progress-job", kind="synthetic_progress", status="RUNNING", attempt=1,
                   lease_owner="owner", deadline_at=utcnow() + timedelta(hours=1)))
        db.commit()
    yield factory
    engine.dispose()


def snapshot(factory):
    with factory() as db:
        return JobOut.model_validate(db.get(Job, "progress-job"))


def mark(ctx, percent=30, **extra):
    report_progress(ctx, percent, "Synthetic stage detail", stage="完整阅读 Skill",
        stage_index=3, stage_count=6, completed_units=2, total_units=8, unit="段", **extra)


def test_progress_survives_new_session_and_never_regresses_or_completes_early(progress_db):
    ctx = jobs.JobContext("progress-job", lease_owner="owner")
    mark(ctx)
    first = snapshot(progress_db)
    mark(ctx, 12)
    current = snapshot(progress_db)
    assert current.progress == current.progress_detail["completed_percent"] == 30
    assert current.progress_detail["stage_started_at"] == first.progress_detail["stage_started_at"]
    assert (current.progress_detail["completed_units"], current.progress_detail["total_units"]) == (2, 8)
    ctx.update(100, "Results are not committed yet")
    assert snapshot(progress_db).progress == 99
    with progress_db() as db:
        ctx.complete_in_transaction(db, {"saved": True})
        db.commit()
    assert snapshot(progress_db).progress == 100
    assert snapshot(progress_db).status == "COMPLETED"


def test_wait_is_observed_not_advanced_by_heartbeats_and_paused_work_keeps_milestone(progress_db):
    ctx = jobs.JobContext("progress-job", lease_owner="owner")
    mark(ctx)
    ctx.model_wait(True)
    waiting = snapshot(progress_db)
    assert waiting.progress_detail["waiting_for_model"] is True
    assert waiting.progress_detail["model_started_at"]
    ctx.heartbeat()
    assert snapshot(progress_db).progress_detail == waiting.progress_detail
    ctx.model_wait(True)
    assert snapshot(progress_db).progress_detail["model_started_at"] == waiting.progress_detail["model_started_at"]
    ctx.model_wait(False)
    assert snapshot(progress_db).progress_detail["waiting_for_model"] is False
    with progress_db() as db:
        ctx.complete_in_transaction(db, {"paused": True}, "阅读进度已保存")
        db.commit()
    paused = snapshot(progress_db)
    assert json_loads(paused.result_json, {})["paused"] is True
    assert paused.progress_detail["completed_percent"] == 30  # UI must use this for PAUSED.


@pytest.mark.parametrize("state", ["CANCEL_REQUESTED", "RUNNING"])
def test_cancelled_or_replaced_worker_cannot_overwrite_progress(progress_db, state):
    ctx = jobs.JobContext("progress-job", lease_owner="owner")
    mark(ctx)
    with progress_db() as db:
        job = db.get(Job, "progress-job")
        job.status = state
        if state == "RUNNING":
            job.lease_owner = "replacement"
        db.commit()
    expected = jobs.JobCancelledError if state == "CANCEL_REQUESTED" else jobs.JobLeaseLostError
    with pytest.raises(expected):
        ctx.model_wait(True)
    with pytest.raises(expected):
        mark(ctx, 80)
    assert snapshot(progress_db).progress == 30


def test_runner_context_tracks_model_wait_and_is_cleared_after_failure_and_retry(progress_db):
    observed = []

    @track_model_request
    async def local_response():
        observed.append(snapshot(progress_db).progress_detail)
        return {"synthetic": True}

    def handler(ctx):
        mark(ctx, 20)
        asyncio.run(local_response())
        assert snapshot(progress_db).progress_detail["waiting_for_model"] is False
        raise ValueError("Synthetic failure after response")

    runner = jobs.JobRunner(max_workers=1)
    runner.register("synthetic_progress", handler, (), max_attempts=1)
    with progress_db() as db:
        job = db.get(Job, "progress-job")
        job.status, job.attempt, job.max_attempts = "QUEUED", 0, 1
        job.progress_json = '{"stage":"old attempt","completed_percent":95}'
        db.commit()
    try:
        runner._run("progress-job")
        assert observed[0]["waiting_for_model"] is True
        assert observed[0]["completed_percent"] == 20
        assert active_job_context.get() is None
        failed = snapshot(progress_db)
        assert failed.status == "DEAD_LETTER" and failed.progress == 20
        assert failed.progress_detail["stage"] == "完整阅读 Skill"
    finally:
        if runner.executor:
            runner.executor.shutdown(wait=True)


def test_0025_additive_upgrade_preserves_existing_job_results(tmp_path):
    url = f"sqlite:///{(tmp_path / 'upgrade.db').as_posix()}"
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "0024")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO jobs (id,kind,status,progress,message,input_json,result_json,created_at) "
            "VALUES ('existing','analysis','COMPLETED',100,'saved','{}',:result,CURRENT_TIMESTAMP)"),
            {"result": '{"kept":true}'})
    command.upgrade(config, "0025")
    with engine.connect() as connection:
        row = connection.execute(text("SELECT progress,result_json,progress_json FROM jobs WHERE id='existing'")).one()
        assert tuple(row) == (100, '{"kept":true}', '{}')
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0025"
    assert "progress_json" in {column["name"] for column in inspect(engine).get_columns("jobs")}
    engine.dispose()
