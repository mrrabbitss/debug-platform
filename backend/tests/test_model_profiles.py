import json
import ipaddress
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

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
    get_profile_proxy_url,
    model_profile_to_dict,
    seed_model_profiles,
    set_profile_api_key,
    set_profile_proxy_url,
    validate_managed_sidecar_endpoint,
    validate_model_endpoint,
    validate_model_profile,
    validate_model_proxy_url,
)
from app.services.retrieval_models import rerank_documents


def create_test_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'models.db'}",
        poolclass=NullPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def test_local_model_dll_failure_recommends_runtime_isolation() -> None:
    class NativeRuntimeError(Exception):
        winerror = 1114

    error = retrieval_models._local_model_runtime_error(
        "reranker",
        "models/reranker/example",
        NativeRuntimeError("DLL initialization failed"),
    )

    assert "WinError 1114" in str(error)
    assert "separate model service" in str(error)
    assert "do not install it into the platform runtime" in str(error)


def test_local_model_dll_failure_during_inference_uses_same_guidance(
    monkeypatch,
) -> None:
    class BrokenEncoder:
        def encode(self, *_args, **_kwargs):
            error = OSError("DLL initialization routine failed")
            error.winerror = 1114
            raise error

    monkeypatch.setattr(
        retrieval_models,
        "_load_sentence_transformer",
        lambda *_args: BrokenEncoder(),
    )
    profile = ModelProfile(
        id="MODEL-broken-native-runtime",
        name="Broken native runtime",
        task_type="embedding",
        mode="local",
        provider="sentence_transformers",
        model_name="local-model",
    )

    with pytest.raises(retrieval_models.RetrievalModelError, match="separate model service"):
        retrieval_models.embed_texts(profile, ["test"])


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


def test_seeded_profiles_do_not_offer_unbundled_native_models(tmp_path: Path):
    db = create_test_session(tmp_path)
    seed_model_profiles(db)

    native_profiles = list(
        db.scalars(
            select(ModelProfile).where(
                ModelProfile.provider == "sentence_transformers"
            )
        ).all()
    )
    assert native_profiles == []
    assert db.get(ModelProfile, "MODEL-embedding-hashing") is not None
    assert db.get(ModelProfile, "MODEL-reranker-disabled") is not None
    db.close()


def test_portable_policy_deactivates_an_old_in_process_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    db = create_test_session(tmp_path)
    local_profile = ModelProfile(
        id="MODEL-existing-local",
        name="Existing local model",
        task_type="embedding",
        mode="local",
        provider="sentence_transformers",
        model_name="models/embedding/existing",
        is_active=True,
    )
    db.add(local_profile)
    db.commit()
    monkeypatch.setattr(
        model_profiles,
        "get_settings",
        lambda: SimpleNamespace(
            llm_provider="mock",
            llm_api_key="",
            llm_base_url="",
            llm_model="",
            model_disable_in_process_local=True,
        ),
    )

    seed_model_profiles(db)

    assert db.get(ModelProfile, local_profile.id).is_active is False
    assert db.get(ModelProfile, "MODEL-embedding-hashing").is_active is True
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


def test_managed_llama_embedding_applies_instruction_only_to_queries_and_enforces_contract(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {"requests": []}

    class FakeEmbeddings:
        def create(self, **request):
            captured["requests"].append(request)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(index=index, embedding=[3.0, 4.0, 0.0])
                    for index, _text in enumerate(request["input"])
                ],
                usage=SimpleNamespace(prompt_tokens=4, total_tokens=4),
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.embeddings = FakeEmbeddings()

    class FakeHttpClient:
        def close(self):
            captured["http_client_closed"] = True

    def fake_http_client(**kwargs):
        captured["http_client_options"] = kwargs
        return FakeHttpClient()

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "t" * 48)
    monkeypatch.setattr(retrieval_models, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(retrieval_models.httpx, "Client", fake_http_client)
    monkeypatch.setattr(
        retrieval_models,
        "record_model_egress",
        lambda profile, **details: captured.update({"audit": details}),
    )
    profile = ModelProfile(
        id="MODEL-managed-embedding",
        name="Managed BGE GGUF",
        task_type="embedding",
        mode="api",
        provider="llama_cpp_local",
        model_name="bge-base-zh-v1.5",
        base_url="http://127.0.0.1:19001",
        config_json=json.dumps({
            "dimension": 3,
            "normalize": True,
            "query_instruction": "检索：",
        }),
    )

    document_vector = retrieval_models.embed_texts(
        profile,
        ["知识正文"],
        purpose="knowledge_index",
    )[0]
    query_vector = retrieval_models.embed_texts(
        profile,
        ["AP 无法上线"],
        purpose="case_retrieval_query",
    )[0]

    requests = captured["requests"]
    assert requests[0]["input"] == ["知识正文"]
    assert requests[1]["input"] == ["检索：AP 无法上线"]
    assert "dimensions" not in requests[0]
    assert requests[0]["encoding_format"] == "float"
    assert captured["client"]["base_url"] == "http://127.0.0.1:19001/v1"
    assert captured["http_client_options"] == {"trust_env": False}
    assert captured["http_client_closed"] is True
    assert document_vector == pytest.approx([0.6, 0.8, 0.0])
    assert query_vector == pytest.approx([0.6, 0.8, 0.0])
    assert sum(value * value for value in query_vector) == pytest.approx(1.0)


def test_managed_llama_embedding_rejects_dimension_drift(monkeypatch) -> None:
    class FakeEmbeddings:
        def create(self, **_request):
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[1.0, 2.0])],
                usage=None,
            )

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.embeddings = FakeEmbeddings()

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "t" * 48)
    monkeypatch.setattr(retrieval_models, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(retrieval_models, "record_model_egress", lambda *_args, **_kwargs: None)
    profile = ModelProfile(
        id="MODEL-managed-embedding-drift",
        name="Managed BGE GGUF",
        task_type="embedding",
        mode="api",
        provider="llama_cpp_local",
        model_name="bge-base-zh-v1.5",
        base_url="http://127.0.0.1:19002/v1",
        config_json='{"dimension":3,"normalize":true}',
    )

    with pytest.raises(
        retrieval_models.RetrievalModelError,
        match="expected 3, received 2",
    ):
        retrieval_models.embed_texts(profile, ["AP offline"])


def test_external_embedding_keeps_default_environment_proxy_behavior(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeEmbeddings:
        def create(self, **_request):
            return SimpleNamespace(
                data=[SimpleNamespace(index=0, embedding=[1.0, 0.0])],
                usage=None,
            )

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.embeddings = FakeEmbeddings()

    monkeypatch.setattr(retrieval_models, "OpenAI", FakeOpenAI)
    monkeypatch.setattr(
        retrieval_models,
        "get_profile_api_key",
        lambda _profile: "sk-test",
    )
    monkeypatch.setattr(
        retrieval_models,
        "record_model_egress",
        lambda *_args, **_kwargs: None,
    )
    profile = ModelProfile(
        id="MODEL-external-embedding",
        name="External embedding",
        task_type="embedding",
        mode="api",
        provider="openai_compatible",
        model_name="external-embedding",
        base_url="https://example.invalid/v1",
        config_json='{"dimension":2}',
    )

    assert retrieval_models.embed_texts(profile, ["AP offline"]) == [
        [1.0, 0.0]
    ]
    assert "http_client" not in captured


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


def test_chat_proxy_credentials_are_encrypted_and_never_returned(monkeypatch):
    fernet = Fernet(Fernet.generate_key())
    monkeypatch.setattr(secrets, "_get_fernet", lambda: fernet)
    profile = ModelProfile(
        id="MODEL-proxy",
        name="Proxied API model",
        task_type="chat",
        mode="api",
        provider="openai_compatible",
        model_name="glm-5.2",
        base_url="https://model.example.com/v1",
    )

    proxy_url = "http://proxy-user:proxy-password@proxy.example.com:8080"
    set_profile_proxy_url(profile, proxy_url)
    output = model_profile_to_dict(profile)

    assert profile.proxy_url_ciphertext != proxy_url
    assert get_profile_proxy_url(profile) == proxy_url
    assert output["proxy_url_configured"] is True
    assert output["proxy_url_hint"] == "http://proxy.example.com:8080"
    assert output["certificate_revocation_check_skipped"] is True
    assert "proxy_url_ciphertext" not in output
    assert "proxy-user" not in str(output)
    assert "proxy-password" not in str(output)


def test_model_proxy_is_limited_to_api_chat_profiles(monkeypatch):
    monkeypatch.setattr(model_profiles, "get_settings", lambda: _endpoint_settings())
    monkeypatch.setattr(model_profiles, "_resolved_addresses", lambda host, port: set())

    validate_model_proxy_url("chat", "api", "http://proxy.example.com:8080")
    with pytest.raises(ValueError, match="API Chat"):
        validate_model_proxy_url("embedding", "api", "http://proxy.example.com:8080")
    with pytest.raises(ValueError, match="path"):
        validate_model_proxy_url("chat", "api", "http://proxy.example.com:8080/admin")
    with pytest.raises(ValueError, match="http or https"):
        validate_model_proxy_url("chat", "api", "socks5://proxy.example.com:1080")


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
    assert "trust_env" not in captured
    assert captured["audit"]["outcome"] == "SUCCESS"
    assert captured["audit"]["request_items"] == 3
    assert captured["audit"]["request_chars"] == len("authentication failurecolorcheck EAP logs")


def test_managed_llama_reranker_uses_v1_rerank_and_validates_results(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"index": 0, "relevance_score": 0.12},
                    {"index": 1, "score": 0.91},
                ]
            }

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "r" * 48)
    monkeypatch.setattr(retrieval_models.httpx, "post", fake_post)
    monkeypatch.setattr(
        retrieval_models,
        "record_model_egress",
        lambda profile, **details: captured.update({"audit": details}),
    )
    profile = ModelProfile(
        id="MODEL-rerank-managed",
        name="Managed Qwen3 Reranker GGUF",
        task_type="reranker",
        mode="api",
        provider="llama_cpp_local",
        model_name="qwen3-reranker-0.6b",
        base_url="http://127.0.0.1:19003/v1",
        config_json='{"endpoint_path":"/v1/rerank"}',
    )

    ranking = rerank_documents(
        "authentication failure",
        ["color", "check EAP logs"],
        2,
        profile,
    )

    assert ranking == [(1, 0.91), (0, 0.12)]
    assert captured["url"] == "http://127.0.0.1:19003/v1/rerank"
    assert captured["headers"]["Authorization"] == f"Bearer {'r' * 48}"
    assert captured["trust_env"] is False
    assert "instruct" not in captured["json"]
    assert captured["audit"]["outcome"] == "SUCCESS"


def test_managed_llama_reranker_rejects_duplicate_or_out_of_range_indexes(
    monkeypatch,
) -> None:
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"index": 0, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.8},
                ]
            }

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "r" * 48)
    monkeypatch.setattr(
        retrieval_models.httpx,
        "post",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    monkeypatch.setattr(retrieval_models, "record_model_egress", lambda *_args, **_kwargs: None)
    profile = ModelProfile(
        id="MODEL-rerank-managed-invalid",
        name="Managed Qwen3 Reranker GGUF",
        task_type="reranker",
        mode="api",
        provider="llama_cpp_local",
        model_name="qwen3-reranker-0.6b",
        base_url="http://127.0.0.1:19004",
    )

    with pytest.raises(
        retrieval_models.RetrievalModelError,
        match="index or score contract",
    ):
        rerank_documents("failure", ["first", "second"], 2, profile)


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

    assert result["profile_id"] == profile.id
    assert result["vectors"] == 2
    assert result["generation_id"].startswith("EGEN-")
    with session_factory() as db:
        assert db.query(KnowledgeEmbedding).count() == 2
        assert db.get(Job, "JOB-reindex").progress == 100
    engine.dispose()


def test_failed_embedding_rebuild_keeps_previous_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'atomic-reindex.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        profile = ModelProfile(
            id="MODEL-atomic",
            name="Hashing",
            task_type="embedding",
            mode="builtin",
            provider="hashing",
            model_name="hashing-char-384",
            is_active=True,
            config_json='{"batch_size":1}',
        )
        document = KnowledgeDocument(
            id="DOC-atomic",
            title="Atomic vectors",
            content=("A" * 1700) + "\n\n" + ("B" * 1700),
        )
        db.add_all([profile, document])
        db.commit()
        knowledge.index_document(db, document)
        previous_generation = profile.active_embedding_generation_id
        previous_ids = set(db.scalars(select(KnowledgeEmbedding.id)))
        assert previous_generation

        original_embed = retrieval_models.embed_texts
        calls = 0

        def fail_second_batch(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise retrieval_models.RetrievalModelError(
                    "synthetic embedding failure"
                )
            return original_embed(*args, **kwargs)

        monkeypatch.setattr(
            retrieval_models,
            "embed_texts",
            fail_second_batch,
        )
        with pytest.raises(
            retrieval_models.RetrievalModelError,
            match="synthetic embedding failure",
        ):
            retrieval_models.reindex_all_embeddings(db, profile)

        db.refresh(profile)
        assert profile.active_embedding_generation_id == previous_generation
        assert set(db.scalars(select(KnowledgeEmbedding.id))) == previous_ids
    engine.dispose()


def test_cancel_at_embedding_publish_keeps_previous_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'cancel-publish.db'}")
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    Base.metadata.create_all(bind=engine)
    with session_factory() as db:
        profile = ModelProfile(
            id="MODEL-cancel-publish",
            name="Hashing",
            task_type="embedding",
            mode="builtin",
            provider="hashing",
            model_name="hashing-char-384",
            is_active=True,
        )
        document = KnowledgeDocument(
            id="DOC-cancel-publish",
            title="Cancellation",
            content="authentication timeout",
        )
        db.add_all([profile, document])
        db.commit()
        knowledge.index_document(db, document)
        previous_generation = profile.active_embedding_generation_id
        previous_ids = set(db.scalars(select(KnowledgeEmbedding.id)))

    class CancelAtPublish:
        def update(self, _progress: int, _message: str) -> None:
            return

        def complete_in_transaction(self, *_args, **_kwargs) -> None:
            raise jobs.JobCancelledError("cancel at publish")

    monkeypatch.setattr(knowledge, "SessionLocal", session_factory)
    with pytest.raises(jobs.JobCancelledError, match="cancel at publish"):
        knowledge.reindex_knowledge_job(
            CancelAtPublish(),
            "MODEL-cancel-publish",
        )

    with session_factory() as db:
        profile = db.get(ModelProfile, "MODEL-cancel-publish")
        assert profile is not None
        assert profile.active_embedding_generation_id == previous_generation
        assert set(db.scalars(select(KnowledgeEmbedding.id))) == previous_ids
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
    validate_model_endpoint("http://api.example.com/v1")
    with pytest.raises(ValueError, match="credentials"):
        validate_model_endpoint("https://user:password@api.example.com/v1")


def test_private_http_endpoint_uses_private_endpoint_opt_in(monkeypatch):
    monkeypatch.setattr(
        model_profiles,
        "get_settings",
        lambda: _endpoint_settings(allow_private=False),
    )
    with pytest.raises(ValueError, match="Private-network"):
        validate_model_endpoint("http://10.20.30.40:8080/v1")

    monkeypatch.setattr(
        model_profiles,
        "get_settings",
        lambda: _endpoint_settings(allow_private=True),
    )
    validate_model_endpoint("http://10.20.30.40:8080/v1")


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


def test_managed_llama_endpoint_exception_is_literal_loopback_only(monkeypatch):
    monkeypatch.setattr(
        model_profiles,
        "get_settings",
        lambda: _endpoint_settings(app_env="prod"),
    )

    validate_managed_sidecar_endpoint("http://127.0.0.1:19001/v1")
    validate_model_profile(
        "embedding",
        "api",
        "llama_cpp_local",
        "bge-base-zh-v1.5",
        "http://[::1]:19001/v1",
        allow_managed=True,
    )
    with pytest.raises(ValueError, match="literal loopback"):
        validate_model_profile(
            "embedding",
            "api",
            "llama_cpp_local",
            "bge-base-zh-v1.5",
            "http://localhost:19001/v1",
            allow_managed=True,
        )
    with pytest.raises(ValueError, match="literal loopback"):
        validate_model_profile(
            "reranker",
            "api",
            "llama_cpp_local",
            "qwen3-reranker-0.6b",
            "http://10.20.30.40:19002/v1",
            allow_managed=True,
        )
    with pytest.raises(ValueError, match="explicit port"):
        validate_managed_sidecar_endpoint("http://127.0.0.1/v1")


def test_bundled_gguf_profiles_are_seeded_without_persisting_launcher_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "s" * 48)
    monkeypatch.setenv(
        "BUNDLED_GGUF_EMBEDDING_URL",
        "http://127.0.0.1:19101/v1",
    )
    monkeypatch.setenv(
        "BUNDLED_GGUF_RERANKER_URL",
        "http://127.0.0.1:19102",
    )
    db = create_test_session(tmp_path)

    seed_model_profiles(db)

    embedding = db.get(ModelProfile, "MODEL-embedding-bundled-gguf")
    reranker = db.get(ModelProfile, "MODEL-reranker-bundled-gguf")
    assert embedding is not None and embedding.is_active is True
    assert reranker is not None and reranker.is_active is True
    assert embedding.provider == reranker.provider == "llama_cpp_local"
    assert embedding.api_key_ciphertext is None
    assert reranker.api_key_ciphertext is None
    assert model_profile_to_dict(embedding)["api_key_hint"] == "managed by launcher"
    assert json.loads(embedding.config_json)["dimension"] == 768

    monkeypatch.delenv("BUNDLED_GGUF_API_KEY")
    monkeypatch.delenv("BUNDLED_GGUF_EMBEDDING_URL")
    monkeypatch.delenv("BUNDLED_GGUF_RERANKER_URL")
    seed_model_profiles(db)

    assert db.get(ModelProfile, embedding.id).enabled is False
    assert db.get(ModelProfile, reranker.id).enabled is False
    assert get_active_model_profile("embedding", db).provider == "hashing"
    assert get_active_model_profile("reranker", db).provider == "disabled"
    assert json.loads(db.get(ModelProfile, embedding.id).config_json)[
        "launcher_auto_restore_pending"
    ] is True
    assert json.loads(db.get(ModelProfile, reranker.id).config_json)[
        "launcher_auto_restore_pending"
    ] is True

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "s" * 48)
    monkeypatch.setenv(
        "BUNDLED_GGUF_EMBEDDING_URL",
        "http://127.0.0.1:19101/v1",
    )
    monkeypatch.setenv(
        "BUNDLED_GGUF_RERANKER_URL",
        "http://127.0.0.1:19102",
    )
    seed_model_profiles(db)

    assert get_active_model_profile("embedding", db).id == embedding.id
    assert get_active_model_profile("reranker", db).id == reranker.id
    assert "launcher_auto_restore_pending" not in json.loads(
        db.get(ModelProfile, embedding.id).config_json
    )
    assert "launcher_auto_restore_pending" not in json.loads(
        db.get(ModelProfile, reranker.id).config_json
    )
    db.close()


def test_manual_fallback_selection_cancels_bundled_profile_auto_restore(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "s" * 48)
    monkeypatch.setenv(
        "BUNDLED_GGUF_EMBEDDING_URL",
        "http://127.0.0.1:19201/v1",
    )
    monkeypatch.setenv(
        "BUNDLED_GGUF_RERANKER_URL",
        "http://127.0.0.1:19202/v1",
    )
    db = create_test_session(tmp_path)
    seed_model_profiles(db)

    monkeypatch.delenv("BUNDLED_GGUF_API_KEY")
    monkeypatch.delenv("BUNDLED_GGUF_EMBEDDING_URL")
    monkeypatch.delenv("BUNDLED_GGUF_RERANKER_URL")
    seed_model_profiles(db)
    embedding = db.get(ModelProfile, "MODEL-embedding-bundled-gguf")
    reranker = db.get(ModelProfile, "MODEL-reranker-bundled-gguf")
    assert json.loads(embedding.config_json)["launcher_auto_restore_pending"] is True
    assert json.loads(reranker.config_json)["launcher_auto_restore_pending"] is True

    activate_model_profile(db, db.get(ModelProfile, "MODEL-embedding-hashing"))
    activate_model_profile(db, db.get(ModelProfile, "MODEL-reranker-disabled"))
    assert "launcher_auto_restore_pending" not in json.loads(embedding.config_json)
    assert "launcher_auto_restore_pending" not in json.loads(reranker.config_json)

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "s" * 48)
    monkeypatch.setenv(
        "BUNDLED_GGUF_EMBEDDING_URL",
        "http://127.0.0.1:19201/v1",
    )
    monkeypatch.setenv(
        "BUNDLED_GGUF_RERANKER_URL",
        "http://127.0.0.1:19202/v1",
    )
    seed_model_profiles(db)

    assert get_active_model_profile("embedding", db).id == "MODEL-embedding-hashing"
    assert get_active_model_profile("reranker", db).id == "MODEL-reranker-disabled"
    assert embedding.enabled is True and embedding.is_active is False
    assert reranker.enabled is True and reranker.is_active is False
    db.close()


def test_bundled_gguf_first_install_replaces_only_default_fallbacks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("BUNDLED_GGUF_API_KEY", raising=False)
    monkeypatch.delenv("BUNDLED_GGUF_EMBEDDING_URL", raising=False)
    monkeypatch.delenv("BUNDLED_GGUF_RERANKER_URL", raising=False)
    db = create_test_session(tmp_path)
    seed_model_profiles(db)
    assert get_active_model_profile("embedding", db).id == "MODEL-embedding-hashing"
    assert get_active_model_profile("reranker", db).id == "MODEL-reranker-disabled"

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "s" * 48)
    monkeypatch.setenv(
        "BUNDLED_GGUF_EMBEDDING_URL",
        "http://127.0.0.1:19301/v1",
    )
    monkeypatch.setenv(
        "BUNDLED_GGUF_RERANKER_URL",
        "http://127.0.0.1:19302/v1",
    )
    seed_model_profiles(db)

    embedding = db.get(ModelProfile, "MODEL-embedding-bundled-gguf")
    reranker = db.get(ModelProfile, "MODEL-reranker-bundled-gguf")
    assert get_active_model_profile("embedding", db).id == embedding.id
    assert get_active_model_profile("reranker", db).id == reranker.id

    activate_model_profile(db, db.get(ModelProfile, "MODEL-embedding-hashing"))
    activate_model_profile(db, db.get(ModelProfile, "MODEL-reranker-disabled"))
    seed_model_profiles(db)

    assert get_active_model_profile("embedding", db).id == "MODEL-embedding-hashing"
    assert get_active_model_profile("reranker", db).id == "MODEL-reranker-disabled"
    assert db.get(ModelProfile, embedding.id).is_active is False
    assert db.get(ModelProfile, reranker.id).is_active is False
    db.close()


def test_bundled_gguf_seed_preserves_non_default_custom_profiles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("BUNDLED_GGUF_API_KEY", raising=False)
    monkeypatch.delenv("BUNDLED_GGUF_EMBEDDING_URL", raising=False)
    monkeypatch.delenv("BUNDLED_GGUF_RERANKER_URL", raising=False)
    db = create_test_session(tmp_path)
    seed_model_profiles(db)
    custom_embedding = ModelProfile(
        id="MODEL-custom-embedding",
        name="Custom embedding",
        task_type="embedding",
        mode="builtin",
        provider="hashing",
        model_name="custom-hashing",
    )
    custom_reranker = ModelProfile(
        id="MODEL-custom-reranker",
        name="Custom disabled reranker",
        task_type="reranker",
        mode="builtin",
        provider="disabled",
        model_name="custom-disabled",
    )
    db.add_all([custom_embedding, custom_reranker])
    db.commit()
    activate_model_profile(db, custom_embedding)
    activate_model_profile(db, custom_reranker)

    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "s" * 48)
    monkeypatch.setenv(
        "BUNDLED_GGUF_EMBEDDING_URL",
        "http://127.0.0.1:19401/v1",
    )
    monkeypatch.setenv(
        "BUNDLED_GGUF_RERANKER_URL",
        "http://127.0.0.1:19402/v1",
    )
    seed_model_profiles(db)

    assert get_active_model_profile("embedding", db).id == custom_embedding.id
    assert get_active_model_profile("reranker", db).id == custom_reranker.id
    assert db.get(ModelProfile, "MODEL-embedding-bundled-gguf").is_active is False
    assert db.get(ModelProfile, "MODEL-reranker-bundled-gguf").is_active is False
    db.close()


def test_managed_llama_activation_requires_launcher_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("BUNDLED_GGUF_API_KEY", raising=False)
    db = create_test_session(tmp_path)
    profile = ModelProfile(
        id="MODEL-managed-no-token",
        name="Managed BGE",
        task_type="embedding",
        mode="api",
        provider="llama_cpp_local",
        model_name="bge",
        base_url="http://127.0.0.1:19001/v1",
    )
    db.add(profile)
    db.commit()

    with pytest.raises(ValueError, match="BUNDLED_GGUF_API_KEY"):
        activate_model_profile(db, profile)
    db.close()
