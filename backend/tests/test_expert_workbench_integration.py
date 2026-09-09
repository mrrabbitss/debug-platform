"""Real assembled REST routes plus central RBAC, using an isolated database."""
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.core.db import Base, get_db
from app.models import UserAccount
from app.services.access_control import authorize_request


def test_expert_category_reaches_engineer_case_and_management_stays_protected(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'rest-integration.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    roles = {"engineer": "ENGINEER", "expert": "EXPERT", "admin": "ADMIN"}
    with factory() as db:
        for identity, role in roles.items():
            db.add(UserAccount(id=identity, username=identity, display_name=identity, role=role, active=True))
        db.commit()
    def database():
        with factory() as db:
            yield db
    def access(request: Request, db=Depends(get_db)):
        key = request.headers.get("X-Test-Actor", "engineer")
        principal = {"id": key, "role": roles[key], "type": "user_token"}
        request.state.principal = principal
        authorize_request(db, request, principal)
    app = FastAPI()
    app.dependency_overrides[get_db] = database
    app.include_router(router, prefix="/api/v1", dependencies=[Depends(access)])
    with TestClient(app) as client:
        expert = {"X-Test-Actor": "expert"}
        created = client.post("/api/v1/workbench/categories", headers=expert, json={"name": "Synthetic new fault"})
        assert created.status_code == 200, created.text
        category = created.json()["id"]
        bootstrap = client.get("/api/v1/workbench/bootstrap").json()
        assert next(item for item in bootstrap["categories"] if item["id"] == category)["skill_status"]["mode"] == "EVIDENCE_ONLY"
        case = client.post("/api/v1/cases", json={"title": "Synthetic case", "description": "No diagnostic model call", "device_type": "GW", "problem_category": category})
        assert case.status_code == 200, case.text
        assert case.json()["problem_category"] == category and case.json()["model_egress_approved"] is True
        assert client.post("/api/v1/workbench/categories", json={"name": "Forbidden"}).status_code == 403
        assert client.get("/api/v1/system/users", headers=expert).status_code == 403
        assert client.get("/api/v1/system/audit", headers=expert).status_code == 200
        assert client.get("/api/v1/system/users", headers={"X-Test-Actor": "admin"}).status_code == 200
        rename = client.patch(f"/api/v1/workbench/categories/{category}", headers=expert,
            json={"version": created.json()["version"], "name": "Synthetic revised category"})
        assert rename.status_code == 200
        removed = client.request("DELETE", f"/api/v1/workbench/categories/{category}", headers=expert,
            json={"version": rename.json()["version"]})
        assert removed.status_code == 200
        assert client.get("/api/v1/cases/" + case.json()["id"]).json()["problem_category"] == category
    engine.dispose()
