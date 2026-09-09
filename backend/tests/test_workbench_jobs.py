"""Transactional assistant completion must not masquerade as a lost lease."""
import pytest

from app.core.utils import json_loads
from app.models import Job
from app.services import jobs
from tests.test_workbench_snapshot import store


def test_runner_accepts_transactional_publication_but_keeps_worker_fencing(store, monkeypatch):
    monkeypatch.setattr(jobs, "SessionLocal", store)
    runner = jobs.JobRunner(max_workers=1)
    failures = []
    monkeypatch.setattr(runner, "_fail_or_retry", lambda *args: failures.append(args))

    def publish(context):
        with store() as db:
            context.complete_in_transaction(db, {"published": True}, "Human-approved publication")
            db.commit()
        return {"must_not_replace_transaction_result": True}

    runner.register("synthetic_publication", publish, ())
    with store() as db:
        db.add(Job(id="JOB-transactional", kind="synthetic_publication", status="QUEUED", input_json="{}"))
        db.commit()
    try:
        runner._run("JOB-transactional")
        with store() as db:
            job = db.get(Job, "JOB-transactional")
            assert job.status == "COMPLETED" and job.lease_owner is None
            assert json_loads(job.result_json, {}) == {"published": True}
            assert not failures
        context = jobs.JobContext("JOB-transactional", lease_owner=runner.worker_id)
        with pytest.raises(jobs.JobLeaseLostError):
            context.raise_if_cancelled()
        context.raise_if_cancelled(allow_completed=True)
        with store() as db:
            job = db.get(Job, "JOB-transactional")
            job.status, job.lease_owner = "RUNNING", "another-worker"
            db.commit()
        with pytest.raises(jobs.JobLeaseLostError):
            context.raise_if_cancelled(allow_completed=True)
        with store() as db:
            db.get(Job, "JOB-transactional").status = "CANCEL_REQUESTED"
            db.commit()
        with pytest.raises(jobs.JobCancelledError):
            context.raise_if_cancelled(allow_completed=True)
    finally:
        runner.shutdown()


def test_missing_context_finishes_only_requested_analysis_and_trace(store, monkeypatch):
    from app.core.utils import json_dumps
    from app.models import AgentRun, AnalysisRun
    from app.services import workbench
    monkeypatch.setattr(workbench, "SessionLocal", store)
    monkeypatch.setattr(jobs, "SessionLocal", store)
    with store() as db:
        db.add(AgentRun(id="AG-bad-context", case_id="CASE-test", operation="diagnosis", input_summary_hash="synthetic"))
        db.flush()
        for key, state in (("RUN-bad-context", "QUEUED"), ("RUN-history", "COMPLETED")):
            db.add(AnalysisRun(id=key, case_id="CASE-test", status=state, provider="mock", model="synthetic",
                agent_run_id="AG-bad-context", model_config_json=json_dumps({"workbench_snapshot_id": "RC-missing"})))
        db.add(Job(id="JOB-bad-context", kind="synthetic", status="RUNNING", lease_owner="worker"))
        db.commit()

    @workbench.case_model_job
    def worker(ctx, case_id, analysis_run_id, agent_run_id):
        raise AssertionError("The model must not be called with a missing fixed context")

    with pytest.raises(workbench.WorkbenchConfigurationError):
        worker(jobs.JobContext("JOB-bad-context", lease_owner="worker"), "CASE-test", "RUN-bad-context", "AG-bad-context")
    with store() as db:
        assert db.get(AnalysisRun, "RUN-bad-context").status == "FAILED"
        assert db.get(AgentRun, "AG-bad-context").stop_reason == "CONFIGURATION_UNAVAILABLE"
        assert db.get(AnalysisRun, "RUN-history").status == "COMPLETED"


@pytest.mark.parametrize("error", [jobs.JobLeaseLostError, jobs.JobCancelledError, RuntimeError])
def test_stale_worker_cannot_fail_or_cancel_replacement_attempt(store, monkeypatch, error):
    monkeypatch.setattr(jobs, "SessionLocal", store)
    runner = jobs.JobRunner(max_workers=1)

    def interrupted(context):
        with store() as db:
            job = db.get(Job, context.job_id)
            job.attempt, job.lease_owner = 2, "replacement-worker"
            db.commit()
        raise error("Synthetic interrupted request")

    runner.register("synthetic_takeover", interrupted, ())
    with store() as db:
        db.add(Job(id="JOB-takeover", kind="synthetic_takeover", status="QUEUED", input_json="{}", max_attempts=1))
        db.commit()
    try:
        runner._run("JOB-takeover")
        with store() as db:
            job = db.get(Job, "JOB-takeover")
            assert (job.status, job.attempt, job.lease_owner) == ("RUNNING", 2, "replacement-worker")
            assert not job.error_message and not job.completed_at
    finally:
        runner.shutdown()
