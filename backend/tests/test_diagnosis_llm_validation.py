import asyncio
from types import SimpleNamespace

import pytest

from app.models import Case
from app.services import diagnosis
from app.services.llm import LLMError, OpenAICompatibleProvider


def _baseline() -> dict:
    return {
        "summary": "deterministic summary",
        "case": {
            "id": "CASE-llm",
            "title": "LLM validation",
            "device_type": "GW",
            "device_model": None,
            "firmware_version": None,
        },
        "confirmed_facts": [{"statement": "known fact", "evidence_ids": ["EVT-1"]}],
        "hypotheses": [{
            "rank": 1,
            "title": "known hypothesis",
            "description": "created by deterministic rules",
            "supporting_evidence": ["EVT-1"],
            "contradicting_evidence": [],
            "confidence_score": 0.7,
            "confidence_level": "MEDIUM",
            "priority": "P1",
            "needs_human_review": True,
            "event_code": "AUTH_FAILED",
        }],
        "recommended_actions": [],
        "missing_information": [],
        "suspected_modules": ["WLAN"],
        "retrieved_knowledge": [{"evidence_id": "DOC-1", "title": "trusted rule"}],
        "related_code": [{"symbol_id": "SYM-1", "file_path": "auth.c"}],
        "limitations": ["human review required"],
        "analysis_engine": "rule+routing+rag",
    }


def _model_result(evidence_id: str = "EVT-1") -> dict:
    return {
        "summary": "validated model summary",
        "confirmed_facts": [{"statement": "authentication failed", "evidence_ids": [evidence_id]}],
        "hypotheses": [{
            "rank": 7,
            "title": "credentials mismatch",
            "description": "the authentication exchange was rejected",
            "supporting_evidence": [evidence_id],
            "contradicting_evidence": [],
            "confidence_score": 0.84,
            "confidence_level": "LOW",
            "priority": "P1",
            "needs_human_review": True,
            "event_code": "AUTH_FAILED",
        }],
        "recommended_actions": [{
            "priority": "P1",
            "action": "verify credentials",
            "reason": "the exchange was rejected",
            "expected_result": "authentication succeeds or a new rejection reason is captured",
        }],
        "missing_information": ["air capture"],
        "suspected_modules": ["WLAN"],
        "limitations": ["configuration was not supplied"],
    }


class _FakeProvider:
    is_mock = False

    def __init__(self, result: dict) -> None:
        self.result = result
        self.system = ""
        self.user = ""

    async def generate_json(self, system: str, user: str, schema_name: str = "diagnosis") -> dict:
        self.system = system
        self.user = user
        return self.result


def test_llm_cannot_cite_fabricated_evidence(monkeypatch) -> None:
    provider = _FakeProvider(_model_result("EVT-FABRICATED"))
    monkeypatch.setattr(diagnosis, "get_llm_provider", lambda: provider)
    baseline = _baseline()

    result = asyncio.run(diagnosis._augment_with_llm(
        Case(id="CASE-llm", title="LLM validation", description=""),
        baseline,
        [{"evidence_id": "EVT-1", "content": "authentication failed"}],
    ))

    assert result["summary"] == "deterministic summary"
    assert result["analysis_engine"] == "rule+routing+rag"
    assert "EVT-FABRICATED" not in str(result["hypotheses"])
    assert "unknown evidence IDs" in result["warnings"][0]


def test_validated_llm_result_preserves_case_scoped_baseline_data(monkeypatch) -> None:
    provider = _FakeProvider(_model_result())
    monkeypatch.setattr(diagnosis, "get_llm_provider", lambda: provider)
    baseline = _baseline()

    result = asyncio.run(diagnosis._augment_with_llm(
        Case(id="CASE-llm", title="LLM validation", description=""),
        baseline,
        [{"evidence_id": "EVT-1", "content": "authentication failed"}],
    ))

    assert result["summary"] == "validated model summary"
    assert result["analysis_engine"] == "rule+rag+llm-validated"
    assert result["hypotheses"][0]["rank"] == 1
    assert result["hypotheses"][0]["confidence_level"] == "HIGH"
    assert result["retrieved_knowledge"] == baseline["retrieved_knowledge"]
    assert result["related_code"] == baseline["related_code"]
    assert result["deterministic_baseline"]["summary"] == "deterministic summary"
    assert "待分析数据" in provider.system


def test_llm_provider_errors_do_not_echo_provider_secrets() -> None:
    class FailingCompletions:
        async def create(self, **kwargs):
            raise RuntimeError("request failed with secret-token-123")

    provider = object.__new__(OpenAICompatibleProvider)
    provider.model_name = "test-model"
    provider.temperature = 0.1
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))

    with pytest.raises(LLMError) as exc_info:
        asyncio.run(provider.generate_text("system", "user"))

    assert "RuntimeError" in str(exc_info.value)
    assert "secret-token-123" not in str(exc_info.value)
