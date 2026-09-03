from pathlib import Path

from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.core.db import Base, get_db
from app.services.knowledge_taxonomy import seed_knowledge_categories
from app.services.model_profiles import seed_model_profiles
from app.services import secrets


def test_model_and_layered_knowledge_api_round_trip(tmp_path: Path, monkeypatch):
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(secrets, "_get_fernet", lambda: fernet)
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

        chat_profile = client.post("/api/v1/system/models", json={
            "name": "Proxied GLM",
            "task_type": "chat",
            "mode": "api",
            "provider": "openai_compatible",
            "model_name": "glm-5.2",
            "base_url": "https://model.example.com/v1",
            "api_key": "sk-test-secret",
            "proxy_url": "http://proxy-user:proxy-secret@proxy.example.com:8080",
            "config": {"temperature": 0.1, "timeout_seconds": 30},
            "enabled": True,
        })
        assert chat_profile.status_code == 200, chat_profile.text
        profile_json = chat_profile.json()
        assert profile_json["proxy_url_configured"] is True
        assert profile_json["proxy_url_hint"] == "http://proxy.example.com:8080"
        assert "proxy-secret" not in chat_profile.text

        unmanaged_sidecar = client.post("/api/v1/system/models", json={
            "name": "Impersonated bundled sidecar",
            "task_type": "embedding",
            "mode": "api",
            "provider": "llama_cpp_local",
            "model_name": "bge",
            "base_url": "http://127.0.0.1:19001/v1",
            "enabled": True,
        })
        assert unmanaged_sidecar.status_code == 400
        assert "created and secured by the launcher" in unmanaged_sidecar.text

        cleared_proxy = client.patch(
            f"/api/v1/system/models/{profile_json['id']}",
            json={"clear_proxy_url": True},
        )
        assert cleared_proxy.status_code == 200, cleared_proxy.text
        assert cleared_proxy.json()["proxy_url_configured"] is False

        categories = client.get("/api/v1/knowledge/categories")
        assert categories.status_code == 200
        assert any(item["code"] == "history.fault_trees" for item in categories.json())

        created = client.post("/api/v1/knowledge", json={
            "title": "AP authentication fault tree",
            "source_type": "fault_tree",
            "category_id": "KCAT-history-fault-tree",
            "device_type": "GENERAL",
            "content": "# Symptom\nAuthentication fails.\n\n# Solution\nCheck EAP logs.",
        })
        assert created.status_code == 200, created.text
        document_id = created.json()["id"]
        assert created.json()["category_name"] == "故障树"
        assert created.json()["device_type"] == "GENERAL"
        assert created.json()["chunk_count"] == 2

        updated = client.patch(f"/api/v1/knowledge/{document_id}", json={
            "expected_lock_version": created.json()["lock_version"],
            "title": "Updated AP authentication fault tree",
            "content": "# Symptom\nAuthentication fails.\n\n# Solution\nCheck EAP and handshake logs.",
        })
        assert updated.status_code == 200, updated.text
        assert updated.json()["title"].startswith("Updated")

        retrieval = client.get("/api/v1/system/retrieval")
        assert retrieval.status_code == 200
        assert retrieval.json()["embedding"]["complete"] is True
        assert retrieval.json()["knowledge_graph"]["enabled"] is True
        assert (
            retrieval.json()["knowledge_graph"]["kind"]
            == "derivation_lineage_and_domain_entities"
        )
        assert retrieval.json()["knowledge_graph"]["domain_entity_graph_enabled"] is True
        assert retrieval.json()["knowledge_graph"]["domain_graph"]["status"] == "NOT_BUILT"
        assert "reciprocal_rank_fusion" in retrieval.json()["agentic_search"]["algorithms"]
        assert "graphrag" in retrieval.json()["agentic_search"]["algorithms"]
        assert "domain_graph" in retrieval.json()["agentic_search"]["modules"]
        assert retrieval.json()["agentic_search"]["enabled"] is True

        embedding_test = client.post("/api/v1/system/models/MODEL-embedding-hashing/test")
        assert embedding_test.status_code == 200
        assert embedding_test.json()["dimension"] == 384

    engine.dispose()


def test_managed_llama_profiles_cannot_be_modified_or_deleted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "m" * 48)
    monkeypatch.setenv(
        "BUNDLED_GGUF_EMBEDDING_URL",
        "http://127.0.0.1:19201/v1",
    )
    monkeypatch.setenv(
        "BUNDLED_GGUF_RERANKER_URL",
        "http://127.0.0.1:19202",
    )
    engine = create_engine(
        f"sqlite:///{tmp_path / 'managed-api.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        seed_model_profiles(db)

    def override_db():
        with session_factory() as db:
            yield db

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = override_db

    profile_id = "MODEL-embedding-bundled-gguf"
    with TestClient(app) as client:
        updated = client.patch(
            f"/api/v1/system/models/{profile_id}",
            json={"name": "tampered"},
        )
        assert updated.status_code == 409
        assert "managed by the launcher" in updated.text

        deleted = client.delete(f"/api/v1/system/models/{profile_id}")
        assert deleted.status_code == 409

    engine.dispose()
