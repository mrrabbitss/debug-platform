import threading
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.core.utils import json_dumps
from app.models import Job
from app.services import jobs


def wait_for_terminal(session_factory, job_id: str, timeout: float = 5.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_factory() as db:
            job = db.get(Job, job_id)
            if job and job.status in jobs.TERMINAL_JOB_STATUSES:
                return job
        time.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not finish within {timeout} seconds")


def make_session_factory(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'jobs.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_job_runner_recovers_interrupted_job(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)
    runner = jobs.JobRunner(max_workers=1)
    runner.register("recoverable", lambda ctx, value: {"value": value}, ("value",))

    with session_factory() as db:
        db.add(Job(
            id="JOB-recover",
            kind="recoverable",
            status="RUNNING",
            progress=50,
            input_json=json_dumps({"value": 42}),
        ))
        db.commit()

    assert runner.resume_incomplete() == 1
    completed = wait_for_terminal(session_factory, "JOB-recover")
    assert completed.status == "COMPLETED"
    assert completed.progress == 100
    assert completed.result_json == '{"value": 42}'

    runner.shutdown()
    engine.dispose()


def test_job_runner_deduplicates_active_inputs(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)
    runner = jobs.JobRunner(max_workers=1)
    release = threading.Event()

    def blocking_job(ctx, value):
        release.wait(timeout=5)
        return {"value": value}

    runner.register("blocking", blocking_job, ("value",))
    with session_factory() as db:
        first = runner.submit(
            db,
            "blocking",
            blocking_job,
            7,
            input_data={"value": 7},
        )
    with session_factory() as db:
        second = runner.submit(
            db,
            "blocking",
            blocking_job,
            7,
            input_data={"value": 7},
        )

    assert second.id == first.id
    release.set()
    assert wait_for_terminal(session_factory, first.id).status == "COMPLETED"

    runner.shutdown()
    engine.dispose()


def test_job_error_is_sanitized_for_api(tmp_path: Path, monkeypatch) -> None:
    engine, session_factory = make_session_factory(tmp_path)
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)
    runner = jobs.JobRunner(max_workers=1)

    def failing_job(ctx):
        raise RuntimeError("safe failure message")

    runner.register("failure", failing_job, ())
    with session_factory() as db:
        job = runner.submit(db, "failure", failing_job, input_data={})

    failed = wait_for_terminal(session_factory, job.id)
    assert failed.status == "FAILED"
    assert failed.error_message == "safe failure message"
    assert "Traceback" not in failed.error_message

    runner.shutdown()
    engine.dispose()
