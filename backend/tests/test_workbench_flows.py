"""Focused integration checks for the new workbench boundaries and saved runs."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.db import get_db
from app.core.utils import json_dumps, json_loads
from app.diagnostic_models import AnalysisRevision
from app.models import AnalysisRun, Case, UserAccount
from app.services import workbench
from app.services.workbench_library import prepare_submission, review_submission
from app.services.workbench_snapshot import library_material
from app.services.workbench_retrieval import scope_graph, scope_rows
from tests.test_workbench_snapshot import add_document, store


OWNER = {"id": "engineer-owner", "role": "ENGINEER", "type": "user_token"}
ADMIN = {"id": "admin-test", "role": "ADMIN", "type": "user_token"}


def _own_case(db):
    db.add(UserAccount(id=OWNER["id"], username="owner", display_name="Synthetic Owner", role="ENGINEER"))
    db.flush()
    case = db.get(Case, "CASE-test")
    case.owner_id = OWNER["id"]
    db.flush()
    return case


def _submission(**overrides):
    return {"title": "synthetic resolved case", "content": "Synthetic observations and verified result",
            "problem_category": "network", "case_id": "CASE-test", **overrides}


def test_library_owner_submits_then_admin_confirms_once(store):
    with store() as db:
        _own_case(db)
        value = prepare_submission(db, OWNER, _submission())
        row = workbench.make_record(db, "library", OWNER["id"], value)
        db.commit()
        assert not library_material(db)
        with pytest.raises(PermissionError):
            review_submission(db, OWNER, row.id, row.version, True)
        original_version = row.version
        review_submission(db, ADMIN, row.id, original_version, True)
        db.commit()
        assert len(library_material(db)) == 1
        assert row.version > original_version
        with pytest.raises(ValueError):
            review_submission(db, ADMIN, row.id, original_version, False)
        assert json_loads(row.payload_json, {})["status"] == "CONFIRMED"


def test_library_rejects_foreign_missing_or_unfinished_sources(store):
    with store() as db:
        _own_case(db)
        with pytest.raises(PermissionError):
            prepare_submission(db, {**OWNER, "id": "another-user"}, _submission())
        with pytest.raises(LookupError):
            prepare_submission(db, ADMIN, _submission(case_id="missing"))
        with pytest.raises(ValueError, match="关联"):
            prepare_submission(db, OWNER, _submission(case_id=None, analysis_id="RUN-test"))
        run = AnalysisRun(id="RUN-test", case_id="CASE-test", status="RUNNING", provider="mock", model="synthetic")
        db.add(run)
        db.flush()
        with pytest.raises(ValueError, match="完成"):
            prepare_submission(db, OWNER, _submission(analysis_id=run.id))


def test_run_preparation_persists_resolvable_unsanitized_snapshot(store, monkeypatch):
    from app.services import diagnosis
    monkeypatch.setattr(diagnosis, "get_active_chat_model_info", lambda: {
        "provider": "mock", "profile_id": "environment", "model": "synthetic", "is_mock": True})
    with store() as db:
        add_document(db, "DOC-flow", "SYNTHETIC_METHOD_FULL_CONTENT")
        run, agent = diagnosis.prepare_analysis_run(db, case=db.get(Case, "CASE-test"), created_by="local-development")
        db.commit()
        config = json_loads(run.model_config_json, {})
        assert config["workbench_snapshot_id"] == json_loads(agent.model_config_json, {})["workbench_snapshot_id"]
        assert "content" not in config["report_template"]
        resolved = workbench.resolve_configuration(db, config)
        assert "组网总览" in resolved["report_template"]["content"]
        assert resolved["knowledge_snapshot"]


def test_revision_worker_resolves_original_category_and_dependencies(store):
    @workbench.case_model_job
    def inspect_revision(revision_id, *, db):
        return workbench.run_configuration(), workbench.case_knowledge.get()

    with store() as db:
        add_document(db, "DOC-fixed-flow", "PINNED_METHOD")
        case = db.get(Case, "CASE-test")
        config = workbench.capture_configuration(db, case)
        run = AnalysisRun(id="RUN-flow", case_id=case.id, status="COMPLETED", provider="mock", model="synthetic",
                          model_config_json=json_dumps(config))
        db.add(run)
        db.flush()
        revision = AnalysisRevision(id="AREV-flow", case_id=case.id, source_analysis_id=run.id,
                                    instruction="Synthetic correction", created_by="local-development")
        db.add(revision)
        case.problem_category = "connection"
        db.commit()
        actual, refs = inspect_revision(revision.id, db=db)
        assert actual["workbench_snapshot_id"] == config["workbench_snapshot_id"]
        assert actual["problem_category"] == "network" and refs
    assert workbench.case_context_id.get() is None


def test_live_retrieval_excludes_restricted_or_unpublished_knowledge(store):
    with store() as db:
        hidden = add_document(db, "DOC-secret", "SYNTHETIC_SHARED_TERM")
        hidden.confidentiality = "RESTRICTED"
        draft = add_document(db, "DOC-draft", "SYNTHETIC_SHARED_TERM")
        draft.review_status = "DRAFT"
        db.flush()
        rows = [(SimpleNamespace(id="one", heading="", content=doc.content), doc) for doc in (hidden, draft)]
        assert not scope_rows(db, rows, {}, ["SYNTHETIC"], "CASE-test")[0]
        candidates = [{"metadata": {"document_id": doc.id, "document_version": doc.version}, "paths": []}
                      for doc in (hidden, draft)]
        assert not scope_graph(db, candidates, db.get(Case, "CASE-test"))[0]


def test_deleted_selected_model_does_not_silently_fall_back(store):
    with store() as db:
        case = db.get(Case, "CASE-test")
        case.chat_profile_id = "MODEL-deleted"
        with pytest.raises(ValueError, match="重新选择"):
            workbench.capture_configuration(db, case)


def test_assistant_manifest_dependencies_are_readable_by_diagnosis(store):
    from app.services.assistant_sources import build_manifest
    from app.services.assistant_state import digest
    from app.services.skill_dependencies import bundle_dependencies
    with store() as db:
        contents = {"skill/SKILL.md": "# Root\nRead [details](references/detail.md)",
                    "skill/references/detail.md": "# Detail\nSynthetic pattern"}
        value = {"files": [{"path": path, "content": content, "sha256": digest(content)} for path, content in contents.items()]}
        operations = [{"action": "create", "new_id": "DOC-root", "source_paths": ["skill/SKILL.md"]},
                      {"action": "create", "new_id": "DOC-detail", "source_paths": ["skill/references/detail.md"]}]
        manifest = build_manifest(db, "WB-bundle", value, operations)
        root = add_document(db, "DOC-root", contents["skill/SKILL.md"], bundle_id="WB-bundle",
                            source_paths=["skill/SKILL.md"], bundle_manifest=manifest)
        detail = add_document(db, "DOC-detail", contents["skill/references/detail.md"], bundle_id="WB-bundle",
                              source_paths=["skill/references/detail.md"], bundle_manifest=manifest)
        dependencies, missing = bundle_dependencies(root, [root, detail])
        assert dependencies == [detail.id] and not missing


def test_custom_category_suggestions_use_run_options_without_changing_case(store):
    with store() as db:
        category = workbench.make_record(db, "problem_category", ADMIN["id"], {})
        category.payload_json = json_dumps({"id": category.id, "name": "Synthetic category"})
        db.flush()
        case = db.get(Case, "CASE-test")
        config = workbench.resolve_configuration(db, workbench.capture_configuration(db, case))
        with workbench.use_configuration(config):
            assert any(item["id"] == category.id for item in workbench.category_instructions()["available_problem_categories"])
            workbench.validate_category_suggestion({"suggested_problem_category": category.id})
            with pytest.raises(ValueError):
                workbench.validate_category_suggestion({"suggested_problem_category": "made_up"})
        assert case.problem_category == "network"


def test_filtered_graph_keeps_explainable_path_contract(store):
    with store() as db:
        doc = add_document(db, "DOC-path", "SYNTHETIC_GRAPH")
        path = [{"from": "ENTITY-a", "to": "ENTITY-b", "relation_type": "CAUSES"}]
        candidates = [{"metadata": {"document_id": doc.id, "document_version": doc.version,
                                     "graph_entity_id": "ENTITY-b"}, "paths": path}]
        _, paths = scope_graph(db, candidates, db.get(Case, "CASE-test"))
        assert paths == [{"path_type": "domain_knowledge_graph", "entity_id": "ENTITY-b", "steps": path}]


def test_submitted_report_is_fixed_before_admin_confirmation(store):
    with store() as db:
        case = _own_case(db)
        config = workbench.capture_configuration(db, case)
        analysis = AnalysisRun(id="RUN-library", case_id=case.id, status="COMPLETED", provider="mock", model="synthetic",
            model_config_json=json_dumps(config), result_json=json_dumps({"summary": "证据不足，设备状态待确认",
                "confirmed_facts": [], "hypotheses": [], "recommended_actions": [], "missing_information": ["待确认设备身份"]}))
        db.add(analysis)
        db.flush()
        value = prepare_submission(db, OWNER, _submission(analysis_id=analysis.id))
        assert "report_markdown" in value and "组网总览" in value["report_markdown"]
        row = workbench.make_record(db, "library", OWNER["id"], value)
        db.commit()
        saved_report = json_loads(row.payload_json, {})["report_markdown"]
        case.title, analysis.result_json = "later title", "{}"
        db.commit()
        review_submission(db, ADMIN, row.id, row.version, True)
        db.commit()
        assert json_loads(row.payload_json, {})["report_markdown"] == saved_report


def test_workbench_http_read_preferences_and_review_conflict(store):
    from app.api.workbench import router
    app = FastAPI()
    app.include_router(router)
    identity = dict(OWNER)

    @app.middleware("http")
    async def principal(request: Request, call_next):
        request.state.principal = dict(identity)
        return await call_next(request)

    def database():
        with store() as db:
            yield db

    app.dependency_overrides[get_db] = database
    with store() as db:
        _own_case(db)
        db.commit()
    with TestClient(app) as client:
        config = client.get("/workbench/bootstrap").json()
        assert {item["id"] for item in config["categories"]} >= {"network", "connection", "unknown"}
        assert client.put("/workbench/preferences", json={"chat_profile_id": None}).status_code == 200
        assert client.post("/workbench/categories", json={"name": "new"}).status_code == 403
        record = client.post("/workbench/library", json=_submission()).json()
        assert record["status"] == "PENDING"
        identity.update(ADMIN)
        url = f"/workbench/library/{record['id']}/review"
        review = {"version": record["version"], "approve": True}
        assert client.post(url, json=review).status_code == 200
        assert client.post(url, json=review).status_code == 409
        assert client.post("/workbench/library", json=_submission(case_id="missing")).status_code == 404
