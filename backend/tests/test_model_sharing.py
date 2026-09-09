"""New personal/shared Chat contract, using authenticated REST and isolated databases."""
from types import SimpleNamespace

from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api import system, workbench as workbench_api
from app.core import security
from app.core.config import BACKEND_ROOT, Settings
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.utils import json_dumps, json_loads
from app.model_access_models import ModelProfileAccess
from app.models import AgentRun, AnalysisRun, Case, ModelProfile, UserAccount
from app.diagnostic_models import AnalysisRevision
from app.schemas import UserCreate, UserUpdate
from app.services import access_control, llm, model_profiles, secrets, workbench
from app.services.model_access import (
    ModelAccessError, chat_model_snapshot, model_profile_payload, principal_for_model_user,
    require_model_profile, resolve_chat_model_snapshot, resolve_user_chat_profile,
)
from app.workbench_models import WorkbenchRecord


def identity(name):
    role = {"admin": "ADMIN", "expert": "EXPERT", "viewer": "VIEWER"}.get(name, "ENGINEER")
    return {"id": "USR-" + name, "role": role, "type": "user_token"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, app_env="prod", auth_mode="rbac", auth_allow_legacy_admin=False,
        database_url=f"sqlite:///{tmp_path / 'models.db'}", data_root=tmp_path, storage_root=tmp_path / "storage",
        model_secret_key=Fernet.generate_key().decode(), model_endpoint_allowlist="",
        model_allow_private_endpoints=False, api_key=None, llm_provider="mock", llm_base_url="", llm_api_key="")
    for module in (security, access_control, model_profiles, secrets, llm):
        monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr("app.services.storage_capacity.require_storage_capacity", lambda _size=0: None)
    monkeypatch.setattr(llm, "record_model_egress", lambda *args, **kwargs: None)
    secrets._get_fernet.cache_clear()
    engine = create_engine(settings.database_url, connect_args={"check_same_thread": False})
    configure_sqlite_engine(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    Base.metadata.create_all(engine)
    monkeypatch.setattr(workbench, "SessionLocal", factory)
    monkeypatch.setattr(model_profiles, "SessionLocal", factory)
    tokens = {}
    with factory() as db:
        for name in ("admin", "expert", "alice", "bob", "viewer"):
            principal = identity(name)
            user = UserAccount(id=principal["id"], username=name, display_name=name, role=principal["role"])
            db.add(user)
            db.flush()
            _, token = access_control.issue_access_token(db, user)
            tokens[name] = {"X-API-Key": token}
        model_profiles.seed_model_profiles(db)
    app = FastAPI()
    for router in (system.router, workbench_api.router):
        app.include_router(router, prefix="/api/v1", dependencies=[Depends(security.verify_api_key)])

    def isolated_db():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = isolated_db
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, factory=factory, headers=tokens, settings=settings)
    engine.dispose()
    secrets._get_fernet.cache_clear()


def call(env, name, method, path, **kwargs):
    return env.client.request(method, "/api/v1" + path, headers=env.headers[name], **kwargs)


def create(env, name="alice", **changes):
    payload = {"name": "Synthetic " + name, "task_type": "chat", "mode": "api", "provider": "openai_compatible",
               "model_name": "synthetic-chat", "base_url": "http://intranet:8000/v1", "api_key": "synthetic-key-12345678"}
    payload.update(changes)
    response = call(env, name, "POST", "/system/models", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_ownership_defaults_and_never_accepts_forged_owner(env):
    for name, expected in (("alice", "PRIVATE"), ("admin", "SHARED"), ("expert", "SHARED")):
        row = create(env, name)
        assert (row["owner_id"], row["visibility"], row["can_manage"]) == (identity(name)["id"], expected, True)
        assert "api_key" not in row and "api_key_ciphertext" not in row
    assert create(env, "expert", visibility="PRIVATE")["visibility"] == "PRIVATE"
    bad = {"name": "Forged", "task_type": "chat", "mode": "api", "provider": "openai_compatible",
           "model_name": "fake", "base_url": "http://intranet/v1", "owner_id": "USR-bob"}
    assert call(env, "alice", "POST", "/system/models", json=bad).status_code == 422
    bad.pop("owner_id")
    bad["visibility"] = "SHARED"
    assert call(env, "alice", "POST", "/system/models", json=bad).status_code == 403
    assert env.client.get("/api/v1/system/models").status_code == 401


@pytest.mark.parametrize("other", ["bob", "expert", "admin", "viewer"])
def test_private_guessed_ids_are_hidden_in_every_model_route(env, other):
    row = create(env)
    model_id = row["id"]
    for path in ("/system/models", "/system/models?task_type=chat"):
        assert model_id not in {item["id"] for item in call(env, other, "GET", path).json()}
    bootstrap = call(env, other, "GET", "/workbench/bootstrap").json()
    assert model_id not in {item["id"] for item in bootstrap["models"]}
    for method, suffix, body in (("GET", "", None), ("PATCH", "", {"name": "Stolen"}),
        ("PATCH", "", {"proxy_url": "http://proxy:8000"}), ("DELETE", "", None),
        ("POST", "/test", None), ("POST", "/activate", None)):
        response = call(env, other, method, f"/system/models/{model_id}{suffix}", **({"json": body} if body else {}))
        assert response.status_code == 404, response.text
    assert call(env, other, "PUT", "/workbench/preferences", json={"chat_profile_id": model_id}).status_code == 404


def test_private_owner_edit_proxy_use_and_delete_without_resetting_preferences(env, monkeypatch):
    row = create(env)
    model_id = row["id"]
    response = call(env, "alice", "PATCH", f"/system/models/{model_id}",
        json={"base_url": "http://127.0.0.1:9001/v1", "proxy_url": "http://name:pass@proxy:8080"})
    assert response.status_code == 200
    assert response.json()["proxy_url_hint"] == "http://proxy:8080"
    assert "name:pass" not in response.text
    assert call(env, "alice", "PUT", "/workbench/preferences", json={"chat_profile_id": model_id}).status_code == 200
    selected = call(env, "alice", "GET", "/system/model").json()
    assert selected["profile_id"] == model_id and selected["visibility"] == "PRIVATE"
    invoked = []

    async def probe(profile):
        invoked.append(profile.id)
        return {"ok": True}

    monkeypatch.setattr(system, "test_profile_connection", probe)
    assert call(env, "alice", "POST", "/system/model/test").json()["ok"]
    assert invoked == [model_id]
    assert call(env, "alice", "DELETE", f"/system/models/{model_id}").status_code == 200
    with env.factory() as db:
        assert db.get(ModelProfileAccess, model_id) is None
        assert json_loads(db.get(WorkbenchRecord, "pref-USR-alice").payload_json)["chat_profile_id"] == model_id
    assert call(env, "alice", "GET", "/system/model").status_code == 404
    assert call(env, "alice", "POST", "/system/model/test").status_code == 404
    assert call(env, "alice", "GET", "/workbench/bootstrap").json()["model_selection"]["error"]


def test_shared_default_and_personal_priority_cannot_activate_private(env):
    shared = create(env, "expert", config={"headers": {"Authorization": "confidential"}, "max_tokens": 200})
    personal = create(env, "alice")
    assert call(env, "expert", "POST", f"/system/models/{shared['id']}/activate").status_code == 200
    assert call(env, "bob", "GET", "/system/model").json()["profile_id"] == shared["id"]
    public = call(env, "bob", "GET", f"/system/models/{shared['id']}").json()
    assert public["api_key_hint"] is None and public["config"]["headers"] == "[REDACTED]"
    assert public["config"]["max_tokens"] == 200 and not public["can_manage"]
    assert call(env, "alice", "PUT", "/workbench/preferences", json={"chat_profile_id": personal["id"]}).status_code == 200
    assert call(env, "alice", "GET", "/system/model").json()["profile_id"] == personal["id"]
    assert call(env, "alice", "POST", f"/system/models/{shared['id']}/activate").status_code == 403
    assert call(env, "alice", "POST", f"/system/models/{personal['id']}/activate").status_code == 409
    private_admin = create(env, "admin", visibility="PRIVATE")
    assert call(env, "admin", "POST", f"/system/models/{private_admin['id']}/activate").status_code == 409
    assert call(env, "expert", "PATCH", f"/system/models/{shared['id']}", json={"visibility": "PRIVATE"}).status_code == 409
    assert call(env, "alice", "PUT", "/workbench/preferences", json={"chat_profile_id": None}).status_code == 200
    assert call(env, "alice", "GET", "/system/model").json()["profile_id"] == shared["id"]


def test_visibility_retraction_invalidates_preference_and_cannot_be_used_by_admin(env):
    row = create(env, "expert")
    assert call(env, "bob", "PUT", "/workbench/preferences", json={"chat_profile_id": row["id"]}).status_code == 200
    assert call(env, "admin", "PATCH", f"/system/models/{row['id']}", json={"visibility": "PRIVATE"}).status_code == 403
    assert call(env, "admin", "PATCH", f"/system/models/{row['id']}", json={"name": "Reviewed shared"}).status_code == 200
    assert call(env, "expert", "PATCH", f"/system/models/{row['id']}", json={"visibility": "PRIVATE"}).status_code == 200
    assert call(env, "bob", "GET", "/system/model").status_code == 404
    assert call(env, "admin", "GET", f"/system/models/{row['id']}").status_code == 404


@pytest.mark.parametrize("role", ["alice", "expert", "viewer"])
def test_global_retrieval_configuration_admin_only(env, role):
    payload = {"name": "Global", "task_type": "embedding", "mode": "api", "provider": "openai_compatible",
               "model_name": "embed", "base_url": "http://embed/v1"}
    assert call(env, role, "POST", "/system/models", json=payload).status_code == 403
    for method, suffix, body in (("PATCH", "", {"enabled": False}), ("DELETE", "", None),
                               ("POST", "/activate", None), ("POST", "/test", None)):
        assert call(env, role, method, "/system/models/MODEL-embedding-hashing" + suffix,
                    **({"json": body} if body else {})).status_code == 403
    assert call(env, role, "PUT", "/workbench/preferences", json={"chat_profile_id": "MODEL-embedding-hashing"}).status_code == 422


def test_viewer_personal_selection_preserves_legacy_readonly_model_writes(env):
    shared = create(env, "admin")
    assert call(env, "viewer", "PUT", "/workbench/preferences", json={"chat_profile_id": shared["id"]}).status_code == 200
    assert call(env, "viewer", "GET", "/system/model").json()["profile_id"] == shared["id"]
    payload = {"name": "Reader", "task_type": "chat", "mode": "api", "provider": "openai_compatible",
               "model_name": "chat", "base_url": "http://chat/v1"}
    assert call(env, "viewer", "POST", "/system/models", json=payload).status_code == 403


@pytest.mark.parametrize("host", ["gateway", "127.0.0.1:9000", "localhost:8000", "10.0.0.8:80", "[::1]:9090",
                                   "models.example.invalid", "169.254.169.254"])
def test_production_arbitrary_http_hosts_do_not_require_allowlist(env, host):
    row = create(env, base_url=f"http://{host}/v1", proxy_url="http://proxy-user:proxy-pass@127.0.0.1:8080")
    assert row["base_url"] == f"http://{host}/v1"
    with env.factory() as db:
        profile = require_model_profile(db, identity("alice"), row["id"])
        model_profiles.validate_model_endpoint(profile.base_url)
        model_profiles.validate_model_proxy_url("chat", "api", model_profiles.get_profile_proxy_url(profile))


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://model/v1", "http://name:password@model/v1",
    "http://model/v1?key=abc", "http://model/v1#key", "http://model:99999/v1", "http://model:0/v1",
    "http://model:/v1", "http://model\\other/v1", "http://bad host/v1", "http://model/\x00v1", "https://"])
def test_invalid_api_url_syntax_still_rejected(env, url):
    with pytest.raises(ValueError):
        model_profiles.validate_model_endpoint(url)


def test_managed_gguf_identity_validation_not_weakened(env):
    for url in ("http://localhost:8000/v1", "http://10.1.1.1:8000/v1", "http://127.0.0.1/v1"):
        with pytest.raises(ValueError):
            model_profiles.validate_managed_sidecar_endpoint(url)
    model_profiles.validate_managed_sidecar_endpoint("http://127.0.0.1:8000/v1")
    payload = {"name": "Impersonated", "task_type": "embedding", "mode": "api", "provider": "llama_cpp_local",
               "model_name": "fake", "base_url": "http://127.0.0.1:8000/v1"}
    assert call(env, "admin", "POST", "/system/models", json=payload).status_code == 400


def test_real_chat_client_uses_private_local_url_without_host_policy_and_hides_probe_body(env, monkeypatch):
    row = create(env, base_url="http://localhost:8123/v1")
    seen = []

    def transport(request):
        seen.append((str(request.url), request.headers.get("Authorization")))
        return httpx.Response(200, json={"id": "chatcmpl-synthetic", "object": "chat.completion", "created": 1,
            "model": "synthetic-chat", "choices": [{"index": 0, "finish_reason": "stop", "message":
            {"role": "assistant", "content": "MODEL_CONNECTION_OK synthetic-key-12345678"}}]})

    monkeypatch.setattr(llm, "build_chat_http_client", lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(transport)))
    response = call(env, "alice", "POST", f"/system/models/{row['id']}/test")
    assert response.status_code == 200, response.text
    assert response.json()["ok"] and "synthetic-key-12345678" not in response.text
    assert seen == [("http://localhost:8123/v1/chat/completions", "Bearer synthetic-key-12345678")]


def test_probe_error_never_echoes_credentials_or_proxy(env, monkeypatch):
    row = create(env)

    async def failing_probe(profile):
        raise RuntimeError("synthetic-key-12345678 http://name:pass@internal-secret:9000")

    monkeypatch.setattr(system, "test_profile_connection", failing_probe)
    response = call(env, "alice", "POST", f"/system/models/{row['id']}/test")
    assert response.status_code == 502
    assert all(value not in response.text for value in ("synthetic-key", "name:pass", "internal-secret"))


def test_snapshot_uses_personal_selection_detects_key_changes_and_is_content_safe(env):
    row = create(env)
    call(env, "alice", "PUT", "/workbench/preferences", json={"chat_profile_id": row["id"]})
    with env.factory() as db:
        user = identity("alice")
        profile = resolve_user_chat_profile(db, user)
        snap = chat_model_snapshot(db, user, profile)
        assert set(snap) == {"selected_chat_profile_id", "model_actor_id", "model_profile_fingerprint"}
        assert "intranet" not in json_dumps(snap) and "synthetic-key" not in json_dumps(snap)
        case = Case(id="CASE-personal", title="Personal model", owner_id=user["id"], problem_category="network")
        db.add(case)
        db.flush()
        context = workbench.capture_configuration(db, case, principal=user)
        db.commit()
        fixed = workbench.resolve_configuration(db, context)
        assert fixed["selected_chat_profile_id"] == profile.id
        with workbench.use_configuration(fixed):
            assert workbench.selected_profile().id == profile.id
            assert workbench.run_configuration()["model_actor_id"] == user["id"]
        assert workbench.selected_profile() is None
        assert resolve_chat_model_snapshot(db, snap).id == profile.id
        call(env, "alice", "PATCH", f"/system/models/{profile.id}", json={"api_key": "rotated-synthetic-key"})
        with pytest.raises(ModelAccessError, match="credentials changed"):
            resolve_chat_model_snapshot(db, snap)
        with workbench.use_configuration(fixed), pytest.raises(ModelAccessError):
            workbench.selected_profile()


@pytest.mark.parametrize("change", ["disable", "delete", "deactivate_user", "make_private"])
def test_worker_revalidates_account_and_visibility_on_reused_session(env, change):
    row = create(env, "expert")
    with env.factory() as db:
        snap = chat_model_snapshot(db, identity("bob"), require_model_profile(db, identity("bob"), row["id"]))
        assert resolve_chat_model_snapshot(db, snap).id == row["id"]
        with env.factory() as writer:
            if change == "deactivate_user":
                writer.get(UserAccount, "USR-bob").active = False
            elif change == "make_private":
                writer.get(ModelProfileAccess, row["id"]).visibility = "PRIVATE"
            elif change == "delete":
                writer.delete(writer.get(ModelProfile, row["id"]))
            else:
                writer.get(ModelProfile, row["id"]).enabled = False
            writer.commit()
        with pytest.raises(ModelAccessError):
            resolve_chat_model_snapshot(db, snap)


def test_explicit_unknown_personal_selection_never_falls_back_and_roles_refresh(env):
    row = create(env)
    with env.factory() as db:
        with pytest.raises(ModelAccessError):
            resolve_user_chat_profile(db, identity("bob"), row["id"])
        assert model_profile_payload(db, identity("alice"), db.get(ModelProfile, row["id"]))["can_manage"]
        assert principal_for_model_user(db, "USR-expert")["role"] == "EXPERT"
        with pytest.raises(ModelAccessError):
            principal_for_model_user(db, "USR-deleted")
        with pytest.raises(ModelAccessError):
            resolve_chat_model_snapshot(db, {"selected_chat_profile_id": row["id"]})


def test_revision_worker_uses_requesters_snapshot_not_source_authors_private_api(env):
    alice = create(env, "alice")
    bob = create(env, "bob")
    for name, model in (("alice", alice), ("bob", bob)):
        call(env, name, "PUT", "/workbench/preferences", json={"chat_profile_id": model["id"]})
    with env.factory() as db:
        case = Case(id="CASE-revision", title="Shared case", owner_id="USR-alice")
        db.add(case)
        db.flush()
        original = workbench.capture_configuration(db, case, principal=identity("alice"))
        fresh = workbench.capture_configuration(db, case, principal=identity("bob"))
        source = AnalysisRun(id="RUN-original", case_id=case.id, status="COMPLETED", model_config_json=json_dumps(original))
        agent = AgentRun(id="AGENT-bob", case_id=case.id, operation="diagnosis_revision", status="QUEUED",
                         created_by="USR-bob", input_summary_hash="0" * 64, model_config_json=json_dumps(fresh))
        db.add_all([source, agent])
        db.flush()
        revision = AnalysisRevision(id="AREV-bob", case_id=case.id, source_analysis_id=source.id,
                                    agent_run_id=agent.id, instruction="Review evidence", created_by="USR-bob")
        db.add(revision)
        db.commit()

        @workbench.case_model_job
        def prepare(db, case, created_by, source_analysis_id):
            return workbench.selected_profile().id

        assert prepare(db, case, "USR-bob", source.id) == bob["id"]

    @workbench.case_model_job
    def execute(revision_id, agent_run_id, session_factory):
        return workbench.selected_profile().id

    assert execute("AREV-bob", "AGENT-bob", env.factory) == bob["id"]


def test_own_profile_cannot_change_task_or_transfer_ownership(env):
    row = create(env)
    for change in ({"task_type": "embedding"}, {"owner_id": "USR-bob"}, {"mode": None}, {"enabled": None}):
        assert call(env, "alice", "PATCH", f"/system/models/{row['id']}", json=change).status_code == 422
    assert call(env, "alice", "PATCH", f"/system/models/{row['id']}", json={"visibility": "SHARED"}).status_code == 403
    assert call(env, "alice", "PATCH", f"/system/models/{row['id']}", json={"mode": "builtin", "provider": "mock"}).status_code == 422


def test_seed_restart_preserves_private_models_and_preferences(env):
    row = create(env)
    call(env, "alice", "PUT", "/workbench/preferences", json={"chat_profile_id": row["id"]})
    with env.factory() as db:
        model_profiles.seed_model_profiles(db)
    with env.factory() as restarted:
        assert resolve_user_chat_profile(restarted, identity("alice")).id == row["id"]
        assert restarted.get(ModelProfileAccess, row["id"]).visibility == "PRIVATE"
        assert model_profiles.get_active_model_profile("chat", restarted).id != row["id"]


def test_new_case_tasks_follow_personal_settings_even_with_old_case_model_field(env):
    old = create(env, "admin")
    current = create(env, "alice")
    assert call(env, "admin", "POST", f"/system/models/{old['id']}/activate").status_code == 200
    call(env, "alice", "PUT", "/workbench/preferences", json={"chat_profile_id": current["id"]})
    with env.factory() as db:
        case = Case(id="CASE-legacy-choice", title="Old explicit selection", owner_id="USR-alice", chat_profile_id=old["id"])
        db.add(case)
        db.flush()
        captured = workbench.capture_configuration(db, case, principal=identity("alice"))
        assert workbench.resolve_configuration(db, captured)["selected_chat_profile_id"] == current["id"]


def test_activation_refreshes_stale_sharing_state_before_committing_default(env):
    row = create(env, "expert")
    with env.factory() as stale:
        profile = stale.get(ModelProfile, row["id"])
        assert stale.get(ModelProfileAccess, row["id"]).visibility == "SHARED"
        assert call(env, "expert", "PATCH", f"/system/models/{row['id']}", json={"visibility": "PRIVATE"}).status_code == 200
        with pytest.raises(ValueError, match="shared profile"):
            model_profiles.activate_model_profile(stale, profile)
        stale.rollback()
    with env.factory() as db:
        assert db.get(ModelProfile, row["id"]).is_active is False


def test_disabled_shared_test_requires_manager_but_owner_can_probe_private_draft(env, monkeypatch):
    shared = create(env, "expert", enabled=False)
    own = create(env, "alice", enabled=False)

    async def probe(profile):
        return {"ok": True}

    monkeypatch.setattr(system, "test_profile_connection", probe)
    assert call(env, "alice", "POST", f"/system/models/{shared['id']}/test").status_code == 409
    assert call(env, "expert", "POST", f"/system/models/{shared['id']}/test").status_code == 200
    assert call(env, "alice", "POST", f"/system/models/{own['id']}/test").status_code == 200


def test_expert_user_schemas_accept_role_and_invalid_role_is_rejected():
    assert UserCreate(username="expert", display_name="Expert", role="EXPERT").role == "EXPERT"
    assert UserUpdate(role="EXPERT").role == "EXPERT"
    with pytest.raises(ValueError):
        UserUpdate(role="SUPERADMIN")


def test_0023_upgrade_preserves_existing_profiles_preferences_and_private_rows(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "0022")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(ModelProfile.__table__.insert().values(id="MODEL-legacy", name="Existing", task_type="chat",
            mode="api", provider="openai_compatible", model_name="existing", base_url="http://legacy/v1", is_active=True))
        connection.execute(WorkbenchRecord.__table__.insert().values(id="pref-USR-old", kind="preferences",
            owner_id="USR-old", payload_json=json_dumps({"chat_profile_id": "MODEL-legacy"})))
    command.upgrade(config, "0023")
    with engine.connect() as connection:
        assert connection.execute(text("SELECT owner_id, visibility FROM model_profile_access WHERE profile_id='MODEL-legacy'")).one() == (None, "SHARED")
        assert connection.execute(text("SELECT is_active FROM model_profiles WHERE id='MODEL-legacy'")).scalar() == 1
        pref = connection.execute(text("SELECT payload_json FROM workbench_records WHERE id='pref-USR-old'")).scalar()
        assert json_loads(pref)["chat_profile_id"] == "MODEL-legacy"
    command.upgrade(config, "0023")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM model_profile_access")) == 1
    engine.dispose()
