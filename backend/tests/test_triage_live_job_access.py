"""Ordinary users can observe their triage job without exposing other cases."""
import pytest

from app.core.utils import json_dumps
from app.diagnostic_models import LogTriageRun
from app.models import Artifact, CaseMember, Job
from app.services import jobs
from tests.test_workbench_access import access_env as access_env


def seed(env, *, case_id="CASE-owned", missing=False, forged_case=False):
    with env.factory() as db:
        db.add(Artifact(id="ART-triage", case_id=case_id, original_name="synthetic.log",
            stored_path="unused-synthetic", sha256="a" * 64, size_bytes=0))
        db.flush()
        if not missing:
            db.add(LogTriageRun(id="LTRIAGE-live", case_id=case_id, artifact_id="ART-triage"))
        db.add(CaseMember(id="MEM-triage-viewer", case_id="CASE-owned", user_id="USR-viewer", permission="VIEWER"))
        data = {"triage_run_id": "LTRIAGE-live"}
        if forged_case:
            data["case_id"] = "CASE-owned"
        db.add(Job(id="JOB-triage-live", kind="log_triage", status="QUEUED", input_json=json_dumps(data)))
        db.commit()


@pytest.mark.parametrize("actor,method,expected", [
    ("engineer", "GET", 200), ("other", "GET", 200), ("viewer", "GET", 200),
    ("engineer", "POST", 200), ("viewer", "POST", 403),
])
def test_triage_job_preserves_case_member_permissions(access_env, monkeypatch, actor, method, expected):
    seed(access_env)
    monkeypatch.setattr(jobs, "SessionLocal", access_env.factory)
    path = "/api/v1/jobs/JOB-triage-live" + ("/cancel" if method == "POST" else "")
    response = access_env.client.request(method, path, headers=access_env.headers[actor])
    assert response.status_code == expected, response.text


@pytest.mark.parametrize("missing,expected", [(False, 403), (True, 404)])
def test_triage_job_cannot_borrow_another_case_id(access_env, missing, expected):
    seed(access_env, case_id="CASE-other", missing=missing, forged_case=True)
    response = access_env.client.get("/api/v1/jobs/JOB-triage-live", headers=access_env.headers["engineer"])
    assert response.status_code == expected, response.text
