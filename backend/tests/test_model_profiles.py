from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import KnowledgeCategory, KnowledgeDocument, KnowledgeEmbedding, ModelProfile
from app.services import retrieval_models, secrets
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
