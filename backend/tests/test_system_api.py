from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.core.db import Base, get_db
from app.services.knowledge_taxonomy import seed_knowledge_categories
from app.services.model_profiles import seed_model_profiles


def test_model_and_layered_knowledge_api_round_trip(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'api.db'}", connect_args={"check_same_thread": False})
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        seed_knowledge_categories(db)
        seed_model_profiles(db)

    def override_db():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db

    with TestClient(app) as client:
        models = client.get("/api/v1/system/models")
        assert models.status_code == 200
        assert {item["task_type"] for item in models.json()} == {"chat", "embedding", "reranker"}

        categories = client.get("/api/v1/knowledge/categories")
        assert categories.status_code == 200
        assert any(item["code"] == "history.fault_trees" for item in categories.json())

        created = client.post("/api/v1/knowledge", json={
            "title": "AP authentication fault tree",
            "source_type": "fault_tree",
            "category_id": "KCAT-history-fault-tree",
            "content": "# Symptom\nAuthentication fails.\n\n# Solution\nCheck EAP logs.",
        })
        assert created.status_code == 200, created.text
        document_id = created.json()["id"]
        assert created.json()["category_name"] == "故障树"
        assert created.json()["chunk_count"] == 2

        updated = client.patch(f"/api/v1/knowledge/{document_id}", json={
            "title": "Updated AP authentication fault tree",
            "content": "# Symptom\nAuthentication fails.\n\n# Solution\nCheck EAP and handshake logs.",
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["title"].startswith("Updated")

        retrieval = client.get("/api/v1/system/retrieval")
        assert retrieval.status_code == 200
        assert retrieval.json()["embedding"]["complete"] is True
        assert retrieval.json()["knowledge_graph"] is False

        embedding_test = client.post("/api/v1/system/models/MODEL-embedding-hashing/test")
        assert embedding_test.status_code == 200
        assert embedding_test.json()["dimension"] == 384

    engine.dispose()
