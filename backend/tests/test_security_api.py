from pathlib import Path
from types import SimpleNamespace

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import routes
from app.core import security
from app.core.db import Base, configure_sqlite_engine, get_db
from app.core.security import verify_api_key
from app.models import Case, CaseMember, UserAccount
from app.services.access_control import issue_access_token


def test_rbac_dependency_enforces_token_role_and_case_scope(tmp_path: Path, monkeypatch) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'security.db'}",
        connect_args={"check_same_thread": False},
    )
    configure_sqlite_engine(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        admin = UserAccount(
            id="USR-admin",
            username="admin",
            display_name="Administrator",
            role="ADMIN",
        )
        engineer = UserAccount(
            id="USR-engineer",
            username="engineer",
            display_name="Engineer",
            role="ENGINEER",
        )
        viewer = UserAccount(
            id="USR-viewer",
            username="viewer",
            display_name="Viewer",
            role="VIEWER",
        )
        db.add_all([admin, engineer, viewer])
        db.flush()
        private_case = Case(
            id="CASE-private",
            title="Private case",
            description="",
            owner_id=engineer.id,
        )
        other_case = Case(
            id="CASE-other",
            title="Other case",
            description="",
            owner_id=admin.id,
        )
        db.add_all([private_case, other_case])
        db.flush()
        db.add(CaseMember(
            id="MEM-viewer",
            case_id=private_case.id,
            user_id=viewer.id,
            permission="VIEWER",
        ))
        db.commit()
        _, admin_token = issue_access_token(db, admin)
        _, engineer_token = issue_access_token(db, engineer)
        _, viewer_token = issue_access_token(db, viewer)

    settings = SimpleNamespace(
        auth_mode="rbac",
        api_key=None,
        auth_allow_legacy_admin=False,
        app_name="test",
        app_env="test",
    )
    monkeypatch.setattr(security, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "get_settings", lambda: settings)

    def override_db():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(
        routes.router,
        prefix="/api/v1",
        dependencies=[Depends(verify_api_key)],
    )
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}
        assert client.get("/api/v1/system/auth-info").status_code == 200
        assert client.get("/api/v1/cases").status_code == 401

        engineer_headers = {"X-API-Key": engineer_token}
        engineer_cases = client.get("/api/v1/cases", headers=engineer_headers)
        assert engineer_cases.status_code == 200
        assert [item["id"] for item in engineer_cases.json()] == ["CASE-private"]
        assert client.get(
            "/api/v1/cases/CASE-other",
            headers=engineer_headers,
        ).status_code == 403
        access = client.get(
            "/api/v1/cases/CASE-private/access",
            headers=engineer_headers,
        )
        assert access.status_code == 200
        assert access.json()["permission"] == "OWNER"
        directory = client.get("/api/v1/system/user-directory", headers=engineer_headers)
        assert directory.status_code == 200
        assert {item["username"] for item in directory.json()} == {"admin", "engineer", "viewer"}
        assert client.get("/api/v1/system/models", headers=engineer_headers).status_code == 200

        viewer_headers = {"X-API-Key": viewer_token}
        assert client.get(
            "/api/v1/cases/CASE-private",
            headers=viewer_headers,
        ).status_code == 200
        assert client.post(
            "/api/v1/cases",
            headers=viewer_headers,
            json={"title": "denied"},
        ).status_code == 403
        assert client.get(
            "/api/v1/cases/CASE-private/members",
            headers=viewer_headers,
        ).status_code == 403
        assert client.get("/api/v1/system/users", headers=viewer_headers).status_code == 403
        assert client.get("/api/v1/system/models", headers=viewer_headers).status_code == 403
        assert client.get("/api/v1/system/retrieval", headers=viewer_headers).status_code == 403
        assert client.get(
            "/api/v1/system/user-directory",
            headers=viewer_headers,
        ).status_code == 403

        admin_headers = {"X-API-Key": admin_token}
        assert client.get("/api/v1/system/users", headers=admin_headers).status_code == 200
        assert client.get(
            "/api/v1/cases/CASE-missing/access",
            headers=admin_headers,
        ).status_code == 404
        assert client.put(
            "/api/v1/cases/CASE-private/members/USR-engineer",
            headers=admin_headers,
            json={"permission": "EDITOR"},
        ).status_code == 409
        identity = client.get("/api/v1/system/me", headers=admin_headers)
        assert identity.status_code == 200
        assert identity.json()["username"] == "admin"

    engine.dispose()
