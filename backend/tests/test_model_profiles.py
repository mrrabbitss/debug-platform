import json
import ipaddress
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import Job, KnowledgeCategory, KnowledgeDocument, KnowledgeEmbedding, ModelProfile
from app.services import jobs, knowledge, model_profiles, retrieval_models, secrets
from app.services.knowledge import index_document
from app.services.knowledge_taxonomy import (
    assign_uncategorized_documents,
    descendant_category_ids,
    seed_knowledge_categories,
    validate_category_parent,
)
from app.services.model_profiles import (
    activate_model_profile,
    get_active_model_profile,
    model_profile_to_dict,
    seed_model_profiles,
    set_profile_api_key,
    validate_model_endpoint,
)
from app.services.retrieval_models import rerank_documents


def create_test_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_seeded_profiles_can_switch_and_hashing_embeddings_are_persisted(tmp_path: Path):
    db = create_test_session(tmp_path)
    seed_knowledge_categories(db)
    seed_model_profiles(db)

    assert get_active_model_profile("chat", db).provider == "mock"
    embedding_profile = get_active_model_profile("embedding", db)
    assert embedding_profile is not None
    assert embedding_profile.provider == "hashing"
    assert get_active_model_profile("reranker", db).provider == "disabled"

    alternate = ModelProfile(
        id="MODEL-test-chat",
        name="Alternate rule engine",
        task_type="chat",
        mode="builtin",
        provider="mock",
        model_name="rule-engine",
    )
    db.add(alternate)
    db.commit()
    activate_model_profile(db, alternate)
    assert get_active_model_profile("chat", db).id == alternate.id

    document = KnowledgeDocument(
        id="DOC-test",
        title="AP authentication troubleshooting",
        source_type="diagnostic_rule",
        content="Check EAP negotiation and the four-way handshake logs.",
    )
    db.add(document)
    db.commit()
    assign_uncategorized_documents(db)
    assert index_document(db, document) == 1
    vector_count = db.scalar(select(func.count(KnowledgeEmbedding.id)))
    assert vector_count == 1
    vector = db.scalar(select(KnowledgeEmbedding).limit(1))
    assert vector is not None
    assert vector.dimension == 384
    db.close()


def test_seeded_project_model_profiles_match_installer_layout(tmp_path: Path):
    db = create_test_session(tmp_path)
    seed_model_profiles(db)

    embedding = db.get(ModelProfile, "MODEL-embedding-local-bge-base-project")
    reranker = db.get(ModelProfile, "MODEL-reranker-local-qwen-project")
    assert embedding is not None
    assert embedding.model_name == "models/embedding/bge-base-zh-v1.5"
    assert json.loads(embedding.config_json)["query_instruction"].startswith("为这个句子")
    assert reranker is not None
    assert reranker.model_name == "models/reranker/Qwen3-Reranker-0.6B"
    assert json.loads(reranker.config_json)["batch_size"] == 4
    db.close()


def test_local_embedding_resolves_project_path_and_prefixes_only_queries(tmp_path: Path, monkeypatch):
    model_dir = tmp_path / "models" / "embedding" / "bge-base-zh-v1.5"
    model_dir.mkdir(parents=True)
    captured: dict[str, object] = {"calls": []}

    class FakeSentenceTransformer:
        def encode(self, texts, **kwargs):
            captured["calls"].append((list(texts), kwargs))
            return [[1.0, 0.0] for _ in texts]

    def fake_loader(model_name, device):
        captured["model_name"] = model_name
        captured["device"] = device
        return FakeSentenceTransformer()

    monkeypatch.setattr(retrieval_models, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(retrieval_models, "_load_sentence_transformer", fake_loader)
    profile = ModelProfile(
        id="MODEL-local-path",
        name="Project local BGE",
        task_type="embedding",
        mode="local",
        provider="sentence_transformers",
        model_name="models/embedding/bge-base-zh-v1.5",
        config_json=json.dumps({
            "device": "cpu",
            "batch_size": 999,
            "normalize": True,
            "query_instruction": "检索：",
        }),
    )

    retrieval_models.embed_texts(profile, ["知识正文"], purpose="knowledge_index")
    retrieval_models.embed_texts(profile, ["AP 无法上线"], purpose="case_retrieval_query")

    calls = captured["calls"]
    assert captured["model_name"] == str(model_dir.resolve())
    assert captured["device"] == "cpu"
    assert calls[0][0] == ["知识正文"]
    assert calls[1][0] == ["检索：AP 无法上线"]
    assert calls[1][1]["batch_size"] == 100


def test_api_keys_are_encrypted_and_never_returned(monkeypatch):
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(secrets, "_get_fernet", lambda: fernet)
    profile = ModelProfile(
        id="MODEL-api",
        name="API model",
        task_type="chat",
        mode="api",
        provider="openai_compatible",
        model_name="qwen-plus",
        base_url="https://example.invalid/v1",
    )
    set_profile_api_key(profile, "sk-secret-1234")

    output = model_profile_to_dict(profile)
    assert profile.api_key_ciphertext != "sk-secret-1234"
    assert output["api_key_configured"] is True
    assert output["api_key_hint"] == "****1234"
    assert "api_key_ciphertext" not in output
    assert "sk-secret-1234" not in str(output)


def test_default_knowledge_taxonomy_has_fault_tree_and_solution_layers(tmp_path: Path):
    db = create_test_session(tmp_path)
    seed_knowledge_categories(db)
    history_ids = descendant_category_ids(db, "KCAT-history")
    assert "KCAT-history-fault-tree" in history_ids
    assert "KCAT-history-solutions" in history_ids
    history = db.get(KnowledgeCategory, "KCAT-history")
    assert history is not None
    with pytest.raises(ValueError, match="cycle"):
        validate_category_parent(db, history, "KCAT-history-fault-tree")
    db.close()


def test_qwen_reranker_api_uses_compatible_reranks_endpoint(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.12},
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(retrieval_models.httpx, "post", fake_post)
    monkeypatch.setattr(retrieval_models, "get_profile_api_key", lambda profile: "sk-test")
    monkeypatch.setattr(
        retrieval_models,
        "record_model_egress",
        lambda profile, **details: captured.update({"audit": details}),
    )
    profile = ModelProfile(
        id="MODEL-rerank-api",
        name="Qwen rerank API",
        task_type="reranker",
        mode="api",
        provider="qwen_rerank_api",
        model_name="qwen3-rerank",
        base_url="https://example.invalid/compatible-api/v1",
        config_json="{}",
    )

    ranking = rerank_documents("authentication failure", ["color", "check EAP logs"], 2, profile)

    assert ranking == [(1, 0.91), (0, 0.12)]
    assert captured["url"] == "https://example.invalid/compatible-api/v1/reranks"
    assert captured["json"]["model"] == "qwen3-rerank"
    assert captured["headers"]["Authorization"] == "Bearer sk-test"
    assert captured["audit"]["outcome"] == "SUCCESS"
    assert captured["audit"]["request_items"] == 3
    assert captured["audit"]["request_chars"] == len("authentication failurecolorcheck EAP logs")


def test_local_qwen_reranker_uses_instruction_and_bounded_batch(monkeypatch):
    captured = {}

    class FakeCrossEncoder:
        def predict(self, pairs, **kwargs):
            captured["pairs"] = pairs
            captured["predict"] = kwargs
            return [0.1, 0.9]

    def fake_loader(model_name, device, instruction):
        captured.update({"model_name": model_name, "device": device, "instruction": instruction})
        return FakeCrossEncoder()

    monkeypatch.setattr(retrieval_models, "_load_cross_encoder", fake_loader)
    profile = ModelProfile(
        id="MODEL-rerank-local",
        name="Local Qwen reranker",
        task_type="reranker",
        mode="local",
        provider="sentence_transformers",
        model_name="Qwen/Qwen3-Reranker-0.6B",
        config_json='{"device":"cpu","batch_size":999,"instruction":"Network diagnosis"}',
    )

    ranking = rerank_documents("failure", ["irrelevant", "check EAP"], 2, profile)

    assert ranking == [(1, 0.9), (0, 0.1)]
    assert captured["model_name"] == "Qwen/Qwen3-Reranker-0.6B"
    assert captured["device"] == "cpu"
    assert captured["instruction"] == "Network diagnosis"
    assert captured["predict"] == {"batch_size": 100, "show_progress_bar": False}


def test_sqlite_reindex_job_updates_progress_without_write_lock(tmp_path: Path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reindex.db'}",
        connect_args={"check_same_thread": False, "timeout": 1},
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        profile = ModelProfile(
            id="MODEL-reindex",
            name="Hashing",
            task_type="embedding",
            mode="builtin",
            provider="hashing",
            model_name="hashing-char-384",
            is_active=True,
            config_json='{"batch_size":1}',
        )
        document = KnowledgeDocument(
            id="DOC-reindex",
            title="Reindex",
            content=("A" * 1900) + "\n\n" + ("B" * 100),
        )
        db.add_all([
            profile,
            document,
            Job(id="JOB-reindex", kind="reindex_knowledge", status="RUNNING"),
        ])
        db.commit()
        knowledge.index_document(db, document)

    monkeypatch.setattr(knowledge, "SessionLocal", session_factory)
    monkeypatch.setattr(jobs, "SessionLocal", session_factory)
    result = knowledge.reindex_knowledge_job(jobs.JobContext("JOB-reindex"), profile.id)

    assert result == {"profile_id": profile.id, "vectors": 2}
    with session_factory() as db:
        assert db.query(KnowledgeEmbedding).count() == 2
        assert db.get(Job, "JOB-reindex").progress == 100
    engine.dispose()


def _endpoint_settings(*, allowlist: str = "", allow_private: bool = False, app_env: str = "dev"):
    return SimpleNamespace(
        app_env=app_env,
        model_endpoint_allowlist_entries=[item.strip() for item in allowlist.split(",") if item.strip()],
        model_allow_private_endpoints=allow_private,
    )


def test_model_endpoint_validation_blocks_unsafe_urls(monkeypatch):
    monkeypatch.setattr(model_profiles, "get_settings", lambda: _endpoint_settings())
    monkeypatch.setattr(
        model_profiles,
        "_resolved_addresses",
        lambda host, port: {ipaddress.ip_address("8.8.8.8")},
    )

    validate_model_endpoint("https://api.example.com/v1")
    with pytest.raises(ValueError, match="http or https"):
        validate_model_endpoint("file:///etc/passwd")
    with pytest.raises(ValueError, match="metadata"):
        validate_model_endpoint("http://metadata.google.internal/latest")
    with pytest.raises(ValueError, match="Loopback"):
        validate_model_endpoint("https://127.0.0.1:8000/v1")
    with pytest.raises(ValueError, match="HTTP model endpoints"):
        validate_model_endpoint("http://api.example.com/v1")
    with pytest.raises(ValueError, match="credentials"):
        validate_model_endpoint("https://user:password@api.example.com/v1")


def test_allowlisted_internal_model_endpoint_is_supported(monkeypatch):
    monkeypatch.setattr(
        model_profiles,
        "get_settings",
        lambda: _endpoint_settings(allowlist="model-gateway.corp.local", app_env="prod"),
    )
    validate_model_endpoint("http://model-gateway.corp.local:8080/v1")


def test_production_model_endpoint_requires_allowlist(monkeypatch):
    monkeypatch.setattr(
        model_profiles,
        "get_settings",
        lambda: _endpoint_settings(app_env="prod"),
    )
    with pytest.raises(ValueError, match="Production"):
        validate_model_endpoint("https://api.example.com/v1")
