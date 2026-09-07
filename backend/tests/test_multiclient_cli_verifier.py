"""Verifier must reject fabricated or cross-case evidence, without launching CLI."""
import copy
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def verifier():
    path = Path(__file__).resolve().parents[2] / "scripts/verify_multiclient_cli.py"
    spec = importlib.util.spec_from_file_location("multiclient_cli_verifier_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def diagnosis():
    return {"confirmed_facts": [{"statement": "undervoltage recorded", "evidence_ids": ["EVT-current"]}],
            "hypotheses": [{"title": "undervoltage", "supporting_evidence": ["EVT-current", "CHK-method"],
                            "contradicting_evidence": []}]}


def test_accepts_current_case_facts_and_method_guidance(verifier):
    assert verifier.validate_diagnosis(diagnosis(), {"EVT-current", "CHK-method"}, {"EVT-current"},
                                       "undervoltage")["validated_citations"] == 2


@pytest.mark.parametrize("failure", ["other_case", "method_only_fact", "unknown_contradiction", "wrong_cause"])
def test_rejects_false_positive_inference_success(verifier, failure):
    payload = copy.deepcopy(diagnosis())
    if failure == "other_case":
        payload["confirmed_facts"][0]["evidence_ids"] = ["EVT-other"]
    elif failure == "method_only_fact":
        payload["confirmed_facts"][0]["evidence_ids"] = ["CHK-method"]
    elif failure == "unknown_contradiction":
        payload["hypotheses"][0]["contradicting_evidence"] = ["EVT-invented"]
    else:
        payload["hypotheses"][0]["title"] = "unrelated mechanism"
    with pytest.raises(RuntimeError):
        verifier.validate_diagnosis(payload, {"EVT-current", "EVT-other", "CHK-method"},
                                    {"EVT-current"}, "undervoltage")


def test_proxy_changes_are_child_local_and_keep_loopback_direct(verifier, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://existing-proxy.test:8080")
    monkeypatch.setenv("NO_PROXY", "existing.test")
    monkeypatch.setattr(verifier.urllib.request, "getproxies", lambda: {"https": "http://system-proxy.test:8081"})
    child = verifier.cli_environment()
    assert child["HTTPS_PROXY"] == "http://existing-proxy.test:8080"
    assert {"127.0.0.1", "localhost", "::1", "existing.test"} <= set(child["NO_PROXY"].split(","))
    assert verifier.os.environ["NO_PROXY"] == "existing.test"
