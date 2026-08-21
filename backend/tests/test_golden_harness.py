import asyncio
from pathlib import Path

import httpx

from app.harness.golden import run_golden_suite
from tests.fake_openai_server import create_app


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_golden_dataset_quality_gates_pass() -> None:
    report = run_golden_suite(
        REPO_ROOT / "sample_data" / "golden_incident",
        enforce_duration_budgets=False,
    )
    assert report["status"] == "PASS", report["failures"]
    assert report["duration_budgets_enforced"] is False
    assert all(check["budget_ms"] is not None for check in report["checks"])
    assert all(
        check["duration_budget_enforced"] is False for check in report["checks"]
    )
    assert {item["name"] for item in report["checks"]} == {
        "fixture_integrity",
        "log_parser",
        "knowledge_curation",
        "scenario_matrix",
        "code_graph",
        "commit_graph",
        "memory",
        "rag",
        "agentic_search",
        "bounded_agent_executor",
    }


async def _fake_request(path: str, payload: dict, mode: str | None = None):
    transport = httpx.ASGITransport(app=create_app(timeout_seconds=0.2))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        headers = {"X-Fake-Mode": mode} if mode else {}
        return await client.post(path, json=payload, headers=headers)


def test_fake_openai_service_success_and_fault_modes() -> None:
    request = {
        "model": "golden-curation",
        "messages": [{"role": "user", "content": "generate golden draft"}],
    }
    success = asyncio.run(_fake_request("/v1/chat/completions", request))
    assert success.status_code == 200
    assert success.json()["usage"]["total_tokens"] > 0

    rate_limited = asyncio.run(
        _fake_request("/v1/chat/completions", request, "rate-limit")
    )
    assert rate_limited.status_code == 429

    interrupted = asyncio.run(
        _fake_request("/v1/chat/completions", request, "interrupted")
    )
    assert interrupted.status_code == 503
    assert interrupted.json()["error"]["type"] == "synthetic_interruption"

    bad_json = asyncio.run(_fake_request("/v1/chat/completions", request, "bad-json"))
    assert bad_json.status_code == 200
    assert bad_json.json()["choices"][0]["message"]["content"].startswith("{this")

    async def timeout_probe() -> None:
        await asyncio.wait_for(
            _fake_request("/v1/chat/completions", request, "timeout"),
            timeout=0.05,
        )

    try:
        asyncio.run(timeout_probe())
    except TimeoutError:
        pass
    else:
        raise AssertionError("Synthetic timeout mode did not exceed the client deadline")
