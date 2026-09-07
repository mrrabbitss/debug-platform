from __future__ import annotations

import copy
import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_packaged_retrieval.py"
    spec = importlib.util.spec_from_file_location("packaged_retrieval_verifier_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_publication_obeys_all_review_transitions(verifier):
    class FakeAPI:
        def __init__(self):
            self.requests = []

        def request(self, path, payload):
            self.requests.append((path, payload))
            if path == "/knowledge":
                assert "active" not in payload and "review_status" not in payload
                assert payload["metadata"]["synthetic"] is True
                return {"id": "DOC-test", "review_status": "DRAFT", "active": False, "lock_version": 1}
            if path.endswith("/submit"):
                assert payload["expected_lock_version"] == 1
                return {"review_status": "IN_REVIEW", "active": False, "lock_version": 2}
            assert path.endswith("/approve") and payload["expected_lock_version"] == 2
            return {"review_status": "ACTIVE", "active": True, "lock_version": 3}

    api = FakeAPI()
    assert verifier.publish_fixture(api, verifier.FIXTURES[0]) == "DOC-test"
    assert [path for path, _payload in api.requests] == [
        "/knowledge", "/knowledge/DOC-test/review/submit", "/knowledge/DOC-test/review/approve",
    ]


def _evaluation(verifier):
    return {
        "id": "ERUN-test", "status": "COMPLETED",
        "config": {
            "memory_writes": False,
            "model_profiles": {
                "embedding": {"id": verifier.EMBEDDING_ID, "provider": "llama_cpp_local",
                              "embedding_generation_id": "EGEN-test"},
                "reranker": {"id": verifier.RERANKER_ID, "provider": "llama_cpp_local"},
            },
        },
        "metrics": {"case_count": 1, "recall_at_k": 1.0, "precision_at_k": 0.5,
                    "mrr": 1.0, "ndcg_at_k": 1.0},
        "results": [{
            "trace": [
                {"stage": "knowledge", "status": "COMPLETED", "candidate_count": 2},
                {"stage": "dense_embedding", "status": "COMPLETED", "candidate_count": 2},
                {"stage": "reranker", "status": "COMPLETED", "candidate_count": 2},
            ],
            "retrieved": [{"evidence_id": "CHK-related", "dense_score": 0.8, "reranker_score": 0.99}],
        }],
    }


def test_evaluation_summary_is_content_safe_and_keeps_quality_metrics(verifier):
    run = _evaluation(verifier)
    run["results"][0]["query"] = "should-not-be-copied-into-summary"
    result = verifier.validate_evaluation(run, ["CHK-related"], "EGEN-test")
    assert result["related_first"] is True
    assert result["metrics"]["ndcg_at_k"] == 1.0
    assert "should-not-be-copied" not in json.dumps(result)


@pytest.mark.parametrize("failure", ["disabled", "fallback", "one_candidate", "wrong_top", "no_score", "old_index"])
def test_evaluation_rejects_false_positive_success(verifier, failure):
    run = copy.deepcopy(_evaluation(verifier))
    if failure == "disabled":
        run["config"]["model_profiles"]["reranker"]["provider"] = "disabled"
    elif failure == "fallback":
        run["results"][0]["trace"][1]["status"] = "FAILED"
    elif failure == "one_candidate":
        run["results"][0]["trace"][2]["candidate_count"] = 1
    elif failure == "wrong_top":
        run["results"][0]["retrieved"][0]["evidence_id"] = "CHK-unrelated"
    elif failure == "no_score":
        run["results"][0]["retrieved"][0]["reranker_score"] = None
    else:
        run["config"]["model_profiles"]["embedding"]["embedding_generation_id"] = "EGEN-old"
    with pytest.raises(verifier.VerificationError):
        verifier.validate_evaluation(run, ["CHK-related"], "EGEN-test")


def test_persisted_index_is_checked_read_only_for_actual_vector_contract(verifier, tmp_path):
    database = tmp_path / "data.db"
    with sqlite3.connect(database) as db:
        db.executescript(
            "CREATE TABLE model_profiles (id TEXT, provider TEXT, active_embedding_generation_id TEXT);"
            "CREATE TABLE knowledge_chunks (id TEXT, document_id TEXT);"
            "CREATE TABLE knowledge_embeddings (chunk_id TEXT, profile_id TEXT, generation_id TEXT, dimension INT, vector_json TEXT);"
        )
        db.execute("INSERT INTO model_profiles VALUES (?, ?, ?)",
                   (verifier.EMBEDDING_ID, "llama_cpp_local", "EGEN-test"))
        for index in range(2):
            db.execute("INSERT INTO knowledge_chunks VALUES (?, ?)", (f"CHK-{index}", f"DOC-{index}"))
            db.execute("INSERT INTO knowledge_embeddings VALUES (?, ?, ?, ?, ?)",
                       (f"CHK-{index}", verifier.EMBEDDING_ID, "EGEN-test", 768,
                        json.dumps([1.0] + [0.0] * 767)))
    before = database.read_bytes()
    result = verifier.read_index_snapshot(database, ["DOC-0", "DOC-1"], "EGEN-test")
    assert result["dimension"] == 768 and result["vector_count"] == 2
    assert result["evidence_by_document"] == {"DOC-0": ["CHK-0"], "DOC-1": ["CHK-1"]}
    assert database.read_bytes() == before
    with sqlite3.connect(database) as db:
        db.execute("UPDATE knowledge_embeddings SET dimension = 384")
    with pytest.raises(verifier.VerificationError, match="768-D"):
        verifier.read_index_snapshot(database, ["DOC-0", "DOC-1"], "EGEN-test")


@pytest.mark.parametrize("extra_task, origin", [(None, "http://127.0.0.1:12345"), ("chat", "http://127.0.0.1:12345"), (None, "https://external.invalid")])
def test_audit_requires_local_real_retrieval_and_no_chat(verifier, tmp_path, extra_task, origin):
    database = tmp_path / "audit.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE audit_events (action TEXT, outcome TEXT, resource_id TEXT, details_json TEXT)")
        for task, profile_id in (("embedding", verifier.EMBEDDING_ID), ("reranker", verifier.RERANKER_ID)):
            db.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?)", (
                "model.egress", "SUCCESS", profile_id, json.dumps({
                    "task_type": task, "provider": "llama_cpp_local", "destination_origin": origin,
                    "content_recorded": False,
                }),
            ))
        if extra_task:
            db.execute("INSERT INTO audit_events VALUES (?, ?, ?, ?)", (
                "model.egress", "SUCCESS", "MODEL-chat", json.dumps({"task_type": extra_task}),
            ))
    if extra_task or "external" in origin:
        with pytest.raises(verifier.VerificationError):
            verifier.read_model_audit(database)
    else:
        result = verifier.read_model_audit(database)
        assert result["backend_chat_calls"] == 0
        assert result["successful_calls"] == {"embedding": 1, "reranker": 1, "chat": 0}


def test_isolated_child_environment_does_not_inherit_credentials_or_change_parent(verifier, monkeypatch):
    monkeypatch.setenv("API_KEY", "parent-only-api-key")
    monkeypatch.setenv("AUTH_MODE", "rbac")
    monkeypatch.setenv("LLM_API_KEY", "parent-only-model-key")
    monkeypatch.setenv("BUNDLED_GGUF_API_KEY", "stale-sidecar-key")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:8080")
    before = dict(os.environ)
    child = verifier.isolated_environment()
    assert child["API_KEY"] == child["LLM_API_KEY"] == ""
    assert child["AUTH_MODE"] == "local" and child["LLM_PROVIDER"] == "mock"
    assert "BUNDLED_GGUF_API_KEY" not in child
    assert child["HTTPS_PROXY"] == before["HTTPS_PROXY"]
    assert dict(os.environ) == before
