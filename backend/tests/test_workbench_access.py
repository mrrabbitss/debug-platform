"""0.4.0 role boundaries, exercised through real REST authentication and domain gates."""
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.routes import router
from app.core import security
from app.core.config import Settings
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, json_loads
from app.models import (
    AnalysisRun, Case, CaseMember, Job, KnowledgeAccess, KnowledgeChunk,
    KnowledgeDocument, KnowledgeDraft, KnowledgePublication, KnowledgeRevision,
    KnowledgeWorkingRevision, ModelProfile, UserAccount,
)
from app.services import access_control
from app.services.knowledge_access import (
    KNOWLEDGE_MANAGEMENT_JOB_KINDS, authorize_knowledge_request, authorize_routing_job,
    can_publish, can_read_revision, require_knowledge_access, require_knowledge_admin,
    require_publisher,
)
from app.services.knowledge_compiler import digest
from app.services.knowledge_personal import personal_view, working_documents
from app.workbench_models import WorkbenchRecord


def identity(role="ENGINEER", user="engineer"):
    return {"id": "USR-" + user, "role": role, "type": "user_token"}


@pytest.fixture
def access_env(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, app_env="prod", auth_mode="rbac",
        auth_allow_legacy_admin=False, api_key=None, mcp_bearer_token="",
        server_instance_id="GWAP-" + "a" * 32, mcp_public_base_url="https://testserver",
        mcp_allowed_hosts="testserver", mcp_allowed_origins="https://testserver",
        cors_origins="https://testserver", trusted_hosts="testserver",
        deployment_mode="lan_server", data_root=tmp_path, storage_root=tmp_path / "storage",
        database_url=f"sqlite:///{tmp_path / 'workbench-access.db'}",
        llm_provider="mock", llm_api_key="", llm_base_url="", llm_model="")
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(access_control, "get_settings", lambda: settings)
    monkeypatch.setattr("app.services.storage_capacity.require_storage_capacity", lambda _size=0: None)
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    headers = {}
    with factory() as db:
        for name, role in (("engineer", "ENGINEER"), ("other", "ENGINEER"), ("viewer", "VIEWER"), ("admin", "ADMIN")):
            user = UserAccount(id="USR-" + name, username=name, display_name=name, role=role)
            db.add(user)
            db.flush()
            _, token = access_control.issue_access_token(db, user)
            headers[name] = {"X-API-Key": token}
        for name, active, status, confidentiality in (
            ("published", True, "ACTIVE", "INTERNAL"), ("public", True, "ACTIVE", "PUBLIC"),
            ("draft", False, "DRAFT", "INTERNAL"), ("restricted", True, "ACTIVE", "RESTRICTED"),
            ("archived", False, "ARCHIVED", "INTERNAL"), ("health", True, "ACTIVE", "INTERNAL"),
        ):
            document = KnowledgeDocument(id="DOC-" + name if name != "health" else name,
                title="Synthetic " + name, content="# Skill\nSynthetic " + name + " content.",
                source_type="analysis_skill", active=active, review_status=status,
                confidentiality=confidentiality, version=5 if name == "published" else 1)
            db.add(document)
            db.flush()
            db.add(KnowledgeAccess(document_id=document.id, owner_id="USR-engineer", publisher_id="USR-engineer"))
        doc = db.get(KnowledgeDocument, "DOC-published")
        for version, confidential in ((1, "INTERNAL"), (2, "INTERNAL"), (3, "RESTRICTED"), (5, "INTERNAL")):
            content = f"# Version {version}\nSynthetic revision content."
            db.add(KnowledgeRevision(id=f"REV-{version}", document_id=doc.id, version=version,
                content_hash=digest(content), snapshot_json=json_dumps({"content": content, "confidentiality": confidential})))
            db.add(KnowledgeChunk(id=f"CHUNK-{version}", document_id=doc.id, chunk_index=0, document_version=version,
                heading="Synthetic", content=content))
            if version in {1, 3, 5}:
                db.add(KnowledgePublication(id=f"PUB-{version}", document_id=doc.id,
                    document_version=version, manifest_json=json_dumps({"document_id": doc.id,
                        "document_version": version, "document_versions": {doc.id: version},
                        "chunk_ids": {doc.id: [f"CHUNK-{version}"]}})))
        old_snapshot = json_dumps({"title": "Historical personal skill", "content": "# Old\nPINNED_PERSONAL_CONTENT",
            "source_type": "analysis_skill", "confidentiality": "INTERNAL"})
        db.add(KnowledgeDraft(id="DRAFT-old", document_id=doc.id, base_version=1, owner_key="USR-engineer",
            created_by="USR-engineer", snapshot_json=old_snapshot))
        db.flush()
        db.add(KnowledgeWorkingRevision(id="WORKING-old", document_id=doc.id, draft_id="DRAFT-old",
            owner_key="USR-engineer", draft_version=1, base_version=1, snapshot_json=old_snapshot))
        db.add_all([
            Case(id="CASE-owned", title="Synthetic case", description="", owner_id="USR-engineer"),
            Case(id="CASE-other", title="Other case", description="", owner_id="USR-other"),
            Case(id="health", title="Health-named case", description="", owner_id="USR-engineer"),
        ])
        db.flush()
        db.add(CaseMember(id="MEM-editor", case_id="CASE-owned", user_id="USR-other", permission="EDITOR"))
        db.add_all([
            AnalysisRun(id="RUN-done", case_id="CASE-owned", status="COMPLETED"),
            AnalysisRun(id="RUN-running", case_id="CASE-owned", status="RUNNING"),
            AnalysisRun(id="RUN-other", case_id="CASE-other", status="COMPLETED"),
            ModelProfile(id="MODEL-preset", name="Preset", task_type="chat", mode="api",
                provider="openai_compatible", model_name="synthetic-unused", enabled=True, is_active=True),
            ModelProfile(id="MODEL-second", name="Second preset", task_type="chat", mode="api",
                provider="openai_compatible", model_name="synthetic-unused", enabled=True, is_active=False),
            ModelProfile(id="MODEL-disabled", name="Disabled", task_type="chat", mode="api",
                provider="openai_compatible", model_name="synthetic-unused", enabled=False),
        ])
        for kind in KNOWLEDGE_MANAGEMENT_JOB_KINDS:
            db.add(Job(id="JOB-" + kind, kind=kind, status="QUEUED",
                input_json=json_dumps({"case_id": "CASE-owned", "created_by": "USR-engineer"})))
        db.commit()
    app = FastAPI()
    app.include_router(router, prefix=settings.api_prefix, dependencies=[Depends(security.verify_api_key)])

    def isolated_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app) as client:
        yield SimpleNamespace(app=app, client=client, factory=factory, headers=headers, settings=settings)
    engine.dispose()


def _request(method, path):
    return Request({"type": "http", "method": method, "path": path,
        "headers": [], "query_string": b"", "scheme": "http", "server": ("testserver", 80)})


def test_every_registered_knowledge_mutation_rejects_direct_engineer_and_viewer(access_env):
    paths = access_env.app.openapi()["paths"]
    checked = []
    for path, operations in paths.items():
        if not path.startswith(("/api/v1/knowledge", "/api/v1/workbench/assistant")):
            continue
        for method in operations:
            if method not in {"post", "put", "patch", "delete"}:
                continue
            concrete = path.replace("{document_id}", "DOC-published")
            import re
            concrete = re.sub(r"\{[^}]+\}", "synthetic", concrete)
            for user in ("engineer", "viewer"):
                response = access_env.client.request(method, concrete, json={}, headers=access_env.headers[user])
                assert response.status_code == 403, (user, method, path, response.text)
            checked.append((method, path))
    assert len(checked) >= 25
    assert ("post", "/api/v1/knowledge/{document_id}/draft/review") in checked
    assert ("post", "/api/v1/knowledge-routing/import") in checked
    with access_env.factory() as db:
        assert db.get(KnowledgeDocument, "DOC-published").version == 5
        assert db.get(KnowledgeDraft, "DRAFT-old").status == "DRAFT"
        assert len(list(db.scalars(select(Job)))) == len(KNOWLEDGE_MANAGEMENT_JOB_KINDS)


def test_global_configuration_mutations_and_new_admin_paths_are_closed(access_env):
    paths = access_env.app.openapi()["paths"]
    checked = []
    for path, operations in paths.items():
        if not path.startswith("/api/v1/system/"):
            continue
        for method in operations:
            if method not in {"post", "put", "patch", "delete"}:
                continue
            import re
            concrete = re.sub(r"\{[^}]+\}", "synthetic", path)
            response = access_env.client.request(method, concrete, json={}, headers=access_env.headers["engineer"])
            assert response.status_code == 403, (method, path, response.text)
            checked.append(path)
    assert "/api/v1/system/models/{profile_id}/activate" in checked
    for path, method in (("/system/new-setting", "GET"), ("/workbench/new-setting", "PUT"),
                         ("/workbench/categories", "POST"), ("/workbench/templates/network", "PUT"),
                         ("/workbench/library/submission/review", "POST"),
                         ("/agent-runs", "GET"), ("/memory-governance/candidates", "GET")):
        with access_env.factory() as db, pytest.raises(HTTPException) as error:
            access_control.authorize_request(db, _request(method, "/api/v1" + path), identity())
        assert error.value.status_code == 403


@pytest.mark.parametrize("user", ["engineer", "viewer"])
def test_published_read_access_and_unpublished_scope_isolation(access_env, user):
    for path in ("/knowledge", "/workbench/knowledge"):
        response = access_env.client.get("/api/v1" + path, headers=access_env.headers[user])
        assert response.status_code == 200, response.text
        ids = {row["id"] for row in response.json()}
        assert {"DOC-published", "DOC-public"} <= ids
        assert not {"DOC-draft", "DOC-restricted", "DOC-archived"} & ids
    for document_id in ("DOC-published", "DOC-public"):
        response = access_env.client.get("/api/v1/knowledge/" + document_id, headers=access_env.headers[user])
        assert response.status_code == 200, response.text
        assert response.json()["can_publish"] is False
        assert response.json()["review_drafts"] == []
    for document_id in ("DOC-draft", "DOC-restricted", "DOC-archived"):
        for suffix in ("", "/revisions", "/sections?content_sha256=" + "a" * 64):
            response = access_env.client.get("/api/v1/knowledge/" + document_id + suffix, headers=access_env.headers[user])
            assert response.status_code == 403, (document_id, suffix, response.text)
    for path in ("/knowledge-curations", "/workbench/assistant", "/knowledge/DOC-published/quality"):
        assert access_env.client.get("/api/v1" + path, headers=access_env.headers[user]).status_code == 403


def test_domain_permissions_ignore_owner_publisher_and_personal_revision(access_env):
    with access_env.factory() as db:
        doc = db.get(KnowledgeDocument, "DOC-published")
        for role in ("ENGINEER", "VIEWER", "invalid"):
            actor = identity(role)
            assert not can_publish(db, doc, actor)
            for call in (lambda: require_knowledge_admin(actor), lambda: require_publisher(db, doc, actor),
                         lambda: require_knowledge_access(db, doc.id, actor, write=True),
                         lambda: authorize_knowledge_request(db, ["knowledge", doc.id], "PATCH", actor)):
                with pytest.raises(HTTPException) as error:
                    call()
                assert error.value.status_code == 403
        admin = identity("ADMIN", "admin")
        require_publisher(db, doc, admin)
        for document_id in ("DOC-published", "DOC-draft", "DOC-restricted", "DOC-archived"):
            assert require_knowledge_access(db, document_id, admin, write=True).id == document_id


def test_admin_can_edit_and_submit_a_draft_while_publication_stays_readable(access_env):
    response = access_env.client.patch("/api/v1/knowledge/DOC-published",
        json={"content": "# Admin draft\nNew synthetic knowledge", "expected_lock_version": 1},
        headers=access_env.headers["admin"])
    assert response.status_code == 200, response.text
    draft = response.json()["pending_draft"]
    assert "New synthetic knowledge" in draft["snapshot"]["content"]
    response = access_env.client.post("/api/v1/knowledge/DOC-published/draft/review",
        json={"draft_id": draft["id"], "action": "SUBMIT", "expected_version": draft["version"]},
        headers=access_env.headers["admin"])
    assert response.status_code == 200 and response.json()["draft"]["status"] == "IN_REVIEW"
    current = access_env.client.get("/api/v1/knowledge/DOC-published", headers=access_env.headers["engineer"]).json()
    assert "Synthetic published content" in current["content"] and current["version"] == 5
    assert current["review_drafts"] == []


def test_only_publication_revisions_are_readable_and_draft_chunks_cannot_leak(access_env):
    response = access_env.client.get("/api/v1/knowledge/DOC-published/revisions", headers=access_env.headers["engineer"])
    assert response.status_code == 200, response.text
    assert {row["version"] for row in response.json()} == {1, 5}
    for version, expected in ((1, 200), (2, 403), (3, 403), (5, 200)):
        response = access_env.client.get(f"/api/v1/knowledge/DOC-published/versions/{version}/chunks/CHUNK-{version}",
            headers=access_env.headers["engineer"])
        assert response.status_code == expected, response.text
    with access_env.factory() as db:
        doc = db.get(KnowledgeDocument, "DOC-published")
        revision = db.get(KnowledgeRevision, "REV-1")
        revision.snapshot_json = "[]"
        assert not can_read_revision(db, doc, revision, identity())
        revision.document_id = "DOC-public"
        assert not can_read_revision(db, doc, revision, identity("ADMIN", "admin"))


def test_historical_personal_snapshot_is_immutable_without_new_overrides(access_env):
    with access_env.factory() as db:
        for actor in ("USR-engineer", "USR-admin", None):
            assert personal_view(db, actor) == []
        before = db.get(KnowledgeWorkingRevision, "WORKING-old").snapshot_json
        db.get(KnowledgeDraft, "DRAFT-old").snapshot_json = json_dumps({"content": "LATER_DRAFT"})
        db.get(KnowledgeDocument, "DOC-published").content = "LATER_PUBLICATION"
        db.commit()
        pinned = working_documents(db, ["WORKING-old"])
        assert len(pinned) == 1 and "PINNED_PERSONAL_CONTENT" in pinned[0].content
        assert json_loads(pinned[0].metadata_json, {})["publication_status"] == "PERSONAL_UNREVIEWED"
        assert db.get(KnowledgeWorkingRevision, "WORKING-old").snapshot_json == before
        assert personal_view(db, "USR-engineer") == []
        with pytest.raises(ValueError, match="Pinned personal"):
            working_documents(db, ["WORKING-missing"])


def test_knowledge_jobs_reject_owner_case_parameter_bypass(access_env):
    with access_env.factory() as db:
        for kind in KNOWLEDGE_MANAGEMENT_JOB_KINDS:
            job_id = "JOB-" + kind
            with pytest.raises(HTTPException) as error:
                authorize_routing_job(db, job_id, identity())
            assert error.value.status_code == 403
            assert authorize_routing_job(db, job_id, identity("ADMIN", "admin"))
            for suffix, method in (("", "GET"), ("/retry", "POST"), ("/cancel", "POST")):
                response = access_env.client.request(method, "/api/v1/jobs/" + job_id + suffix,
                    headers=access_env.headers["engineer"])
                assert response.status_code == 403, (kind, suffix, response.text)


def test_bootstrap_and_personal_model_preset_never_change_global_profiles(access_env):
    for user, role in (("engineer", "ENGINEER"), ("viewer", "VIEWER"), ("admin", "ADMIN")):
        response = access_env.client.get("/api/v1/workbench/bootstrap", headers=access_env.headers[user])
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["principal"]["role"] == role
        assert {model["id"] for model in value["models"]} == {"MODEL-preset", "MODEL-second"}
        assert all(set(model) == {"id", "name", "active"} for model in value["models"])
    for user, expected in (("engineer", 200), ("viewer", 403), ("admin", 200)):
        response = access_env.client.put("/api/v1/workbench/preferences",
            json={"chat_profile_id": "MODEL-second"}, headers=access_env.headers[user])
        assert response.status_code == expected, response.text
    with access_env.factory() as db:
        assert db.get(ModelProfile, "MODEL-preset").is_active is True
        assert db.get(ModelProfile, "MODEL-second").is_active is False
        assert db.get(WorkbenchRecord, "pref-USR-engineer").owner_id == "USR-engineer"
        assert db.get(WorkbenchRecord, "pref-USR-viewer") is None
    own = access_env.client.get("/api/v1/workbench/bootstrap", headers=access_env.headers["engineer"]).json()
    other = access_env.client.get("/api/v1/workbench/bootstrap", headers=access_env.headers["other"]).json()
    assert own["preferences"] == {"chat_profile_id": "MODEL-second"} and other["preferences"] == {}
    response = access_env.client.put("/api/v1/workbench/preferences",
        json={"chat_profile_id": "MODEL-disabled"}, headers=access_env.headers["engineer"])
    assert response.status_code == 422


def test_local_development_keeps_admin_bootstrap_and_management(access_env):
    access_env.settings.app_env = "dev"
    access_env.settings.deployment_mode = "standalone"
    access_env.settings.auth_mode = "local"
    response = access_env.client.get("/api/v1/workbench/bootstrap")
    assert response.status_code == 200, response.text
    assert response.json()["principal"] == {"id": "local-development", "type": "local", "role": "ADMIN"}
    response = access_env.client.post("/api/v1/workbench/categories", json={"name": "Synthetic category"})
    assert response.status_code == 200, response.text
    response = access_env.client.put("/api/v1/workbench/preferences", json={"chat_profile_id": "MODEL-second"})
    assert response.status_code == 200
    response = access_env.client.get("/api/v1/knowledge/DOC-restricted")
    assert response.status_code == 200


def test_health_named_resources_cannot_bypass_rest_authentication(access_env):
    for method, path, expected in (("GET", "/knowledge/health", 401),
                                  ("GET", "/knowledge/health/revisions", 401),
                                  ("POST", "/knowledge/health/extract-method", 401),
                                  ("GET", "/cases/health", 401),
                                  ("POST", "/cases/health/analyses", 401)):
        response = access_env.client.request(method, "/api/v1" + path, json={})
        assert response.status_code == expected, response.text
    response = access_env.client.post("/api/v1/knowledge/health/extract-method", headers=access_env.headers["engineer"])
    assert response.status_code == 403
    assert access_env.client.get("/api/v1/health/live").status_code == 200
    assert access_env.client.get("/api/v1/system/auth-info").status_code == 200


def test_configured_api_prefix_does_not_disable_domain_rbac(access_env):
    access_env.settings.api_prefix = "/custom-api"
    with access_env.factory() as db:
        for path in ("/knowledge/DOC-published", "/system/models", "/workbench/categories"):
            with pytest.raises(HTTPException) as error:
                access_control.authorize_request(db, _request("POST", "/custom-api" + path), identity())
            assert error.value.status_code == 403
        access_control.authorize_request(db, _request("GET", "/custom-api/workbench/bootstrap"), identity())


def test_every_assistant_read_route_is_admin_only(access_env):
    import re
    paths = [path for path, methods in access_env.app.openapi()["paths"].items()
        if path.startswith("/api/v1/workbench/assistant") and "get" in methods]
    assert "/api/v1/workbench/assistant/{session_id}/source" in paths
    assert "/api/v1/workbench/assistant/{session_id}/readings" in paths
    for path in paths:
        for user in ("engineer", "viewer"):
            response = access_env.client.get(re.sub(r"\{[^}]+\}", "synthetic", path), headers=access_env.headers[user])
            assert response.status_code == 403, (user, path, response.text)


def test_legacy_proposal_and_routing_handlers_cannot_reenable_engineer_writes(access_env):
    from app.api.knowledge_drafts import DraftReview, review_proposal
    from app.api.knowledge_routing import _require_admin

    request = _request("POST", "/api/v1/knowledge/DOC-published/draft/review")
    request.state.principal = identity()
    with access_env.factory() as db:
        for action in ("SUBMIT", "APPROVE", "REJECT", "ARCHIVE"):
            with pytest.raises(HTTPException) as error:
                review_proposal("DOC-published", DraftReview(draft_id="DRAFT-old", action=action,
                    expected_version=1), request, db)
            assert error.value.status_code == 403
        assert db.get(KnowledgeDraft, "DRAFT-old").status == "DRAFT"
    with pytest.raises(HTTPException) as error:
        _require_admin(request)
    assert error.value.status_code == 403
