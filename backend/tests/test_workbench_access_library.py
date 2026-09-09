"""0.4.0 case/report submission, confirmation and role isolation."""
import pytest
from sqlalchemy.orm.exc import StaleDataError

from app.core.utils import json_dumps, json_loads
from app.services.workbench_library import prepare_submission, review_submission
from app.workbench_models import WorkbenchRecord
from tests.test_workbench_access import access_env, identity  # noqa: F401


def _submission(**changes):
    return {"title": "Synthetic resolved case", "problem_category": "unknown", "content": "Synthetic case details",
        "case_id": "CASE-owned", "analysis_id": None, **changes}


def test_library_rest_owner_submission_and_admin_confirmation(access_env):
    response = access_env.client.post("/api/v1/workbench/library", json=_submission(), headers=access_env.headers["engineer"])
    assert response.status_code == 200, response.text
    record = response.json()
    assert record["status"] == "PENDING" and record["owner_id"] == "USR-engineer"
    for user in ("other", "viewer"):
        rows = access_env.client.get("/api/v1/workbench/library", headers=access_env.headers[user]).json()
        assert record["id"] not in {row["id"] for row in rows}
        response = access_env.client.post("/api/v1/workbench/library", json=_submission(), headers=access_env.headers[user])
        assert response.status_code == 403
    path = f"/api/v1/workbench/library/{record['id']}/review"
    for user in ("engineer", "other", "viewer"):
        response = access_env.client.post(path, json={"version": record["version"], "approve": True}, headers=access_env.headers[user])
        assert response.status_code == 403
    response = access_env.client.post(path, json={"version": record["version"], "approve": True}, headers=access_env.headers["admin"])
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "CONFIRMED" and response.json()["reviewer"] == "USR-admin"
    assert response.json()["version"] > record["version"]
    for user in ("other", "viewer"):
        rows = access_env.client.get("/api/v1/workbench/library", headers=access_env.headers[user]).json()
        assert record["id"] in {row["id"] for row in rows}
    response = access_env.client.post(path, json={"version": record["version"], "approve": False}, headers=access_env.headers["admin"])
    assert response.status_code == 409


def test_library_rest_rejects_missing_case_and_incomplete_or_foreign_analysis(access_env):
    for user in ("engineer", "admin"):
        for changes, expected in (({"case_id": "CASE-missing"}, 404),
                                  ({"analysis_id": "RUN-running"}, 422),
                                  ({"analysis_id": "RUN-other"}, 422),
                                  ({"analysis_id": "RUN-done", "case_id": None}, 422)):
            response = access_env.client.post("/api/v1/workbench/library", json=_submission(**changes), headers=access_env.headers[user])
            assert response.status_code == expected, (user, changes, response.text)


def test_library_domain_enforces_roles_without_rest(access_env):
    with access_env.factory() as db:
        for actor in (identity("VIEWER", "viewer"), identity("ENGINEER", "other")):
            with pytest.raises(PermissionError):
                prepare_submission(db, actor, _submission())
        for actor in (identity(), identity("ADMIN", "admin")):
            with pytest.raises(LookupError):
                prepare_submission(db, actor, _submission(case_id="CASE-missing"))
            with pytest.raises(ValueError):
                prepare_submission(db, actor, _submission(analysis_id="RUN-running"))
        db.add(WorkbenchRecord(id="LIB-domain", kind="library", owner_id="USR-engineer",
            payload_json=json_dumps(prepare_submission(db, identity(), _submission()))))
        db.commit()
        for actor in (identity(), identity("VIEWER", "viewer")):
            with pytest.raises(PermissionError):
                review_submission(db, actor, "LIB-domain", 1, True)
        assert json_loads(db.get(WorkbenchRecord, "LIB-domain").payload_json, {})["status"] == "PENDING"
        row = review_submission(db, identity("ADMIN", "admin"), "LIB-domain", 1, True)
        assert json_loads(row.payload_json, {})["status"] == "CONFIRMED"


def test_completed_report_content_is_generated_on_server(access_env, monkeypatch):
    calls = []

    def report_context(case_id, analysis_id, *, db):
        calls.append((case_id, analysis_id, db is not None))
        return {"case": case_id, "analysis": analysis_id}

    monkeypatch.setattr("app.services.report.get_report_context", report_context)
    monkeypatch.setattr("app.services.category_report.report_markdown", lambda context: "SERVER_REPORT " + context["analysis"])
    response = access_env.client.post("/api/v1/workbench/library",
        json=_submission(analysis_id="RUN-done", report_markdown="FORGED_REPORT", status="CONFIRMED", reviewer="USR-admin"),
        headers=access_env.headers["engineer"])
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["report_markdown"] == "SERVER_REPORT RUN-done"
    assert result["status"] == "PENDING" and result["reviewer"] is None
    assert calls == [("CASE-owned", "RUN-done", True)]


def test_library_concurrent_admin_review_cannot_overwrite_first_decision(access_env):
    with access_env.factory() as db:
        db.add(WorkbenchRecord(id="LIB-race", kind="library", owner_id="USR-engineer",
            payload_json=json_dumps({"status": "PENDING"})))
        db.commit()
    with access_env.factory() as first, access_env.factory() as second:
        first_row = first.get(WorkbenchRecord, "LIB-race")
        stale_row = second.get(WorkbenchRecord, "LIB-race")
        review_submission(first, identity("ADMIN", "admin"), first_row.id, first_row.version, True)
        first.commit()
        with pytest.raises(StaleDataError):
            review_submission(second, identity("ADMIN", "admin"), stale_row.id, stale_row.version, False)
        second.rollback()
    with access_env.factory() as db:
        value = json_loads(db.get(WorkbenchRecord, "LIB-race").payload_json, {})
        assert value["status"] == "CONFIRMED"
