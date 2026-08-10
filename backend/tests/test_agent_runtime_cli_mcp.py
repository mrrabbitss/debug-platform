from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from app.agent_runtime import cli, client, mcp_server
from app.agent_runtime.client import RuntimeClient, RuntimeClientError
from app.agent_runtime.contracts import ToolResult


class FakeCliClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def status(self) -> dict[str, Any]:
        self.calls.append(("status", None))
        return {"health": {"status": "ok"}}

    def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get", (path, kwargs)))
        return {"path": path}

    def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("post", (path, kwargs)))
        return {"path": path}

    def open_ui(self, case_id: str | None) -> dict[str, Any]:
        self.calls.append(("open_ui", case_id))
        return {"url": "http://runtime/ui/", "opened": True}

    def create_case(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("create_case", payload))
        return {"id": "CASE-1"}

    def ingest(self, case_id: str, file_path: str) -> dict[str, Any]:
        self.calls.append(("ingest", (case_id, file_path)))
        return {"job": {"id": "JOB-PARSE"}}

    def wait_job(self, job_id: str, timeout: int) -> dict[str, Any]:
        self.calls.append(("wait_job", (job_id, timeout)))
        return {"id": job_id, "status": "COMPLETED"}

    def evidence_bundle(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("evidence_bundle", (case_id, payload)))
        return {"case_id": case_id}

    def search(self, case_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(("search", (case_id, payload)))
        return {"case_id": case_id}

    def attach_workspace(self, case_id: str, path: str, name: str | None) -> dict[str, Any]:
        self.calls.append(("attach_workspace", (case_id, path, name)))
        return {"id": "REPO-1"}

    def index_workspace(self, repository_id: str) -> dict[str, Any]:
        self.calls.append(("index_workspace", repository_id))
        return {"job_id": "JOB-INDEX"}

    def diagnose(self, case_id: str) -> dict[str, Any]:
        self.calls.append(("diagnose", case_id))
        return {"id": "JOB-DIAGNOSE"}

    def latest_analysis(self, case_id: str) -> dict[str, Any]:
        self.calls.append(("latest_analysis", case_id))
        return {"id": "ANALYSIS-1"}

    def generate_report(self, case_id: str, fmt: str) -> dict[str, Any]:
        self.calls.append(("generate_report", (case_id, fmt)))
        return {"format": fmt}


@pytest.mark.parametrize(
    "arguments,expected_call",
    [
        (["status"], "status"),
        (["doctor"], "status"),
        (["open", "--case-id", "CASE-1"], "open_ui"),
        (["models", "list"], "get"),
        (["models", "scan", "--no-llm"], "post"),
        (["models", "validate", "MODEL-1", "--device", "cuda"], "post"),
        (["models", "activate", "MODEL-1", "--force"], "post"),
        (["case-create", "test", "--device-type", "AP"], "create_case"),
        (["ingest", "CASE-1", "debug.zip"], "ingest"),
        (["job-wait", "JOB-1"], "wait_job"),
        (["evidence", "CASE-1", "--query", "hostapd"], "evidence_bundle"),
        (["search", "CASE-1", "authentication"], "search"),
        (["workspace-attach", "CASE-1", "D:\\src"], "attach_workspace"),
        (["workspace-attach", "CASE-1", "D:\\src", "--no-index"], "attach_workspace"),
        (["diagnose", "CASE-1"], "diagnose"),
        (["diagnose", "CASE-1", "--no-wait"], "diagnose"),
        (["report", "CASE-1", "--format", "pdf"], "generate_report"),
    ],
)
def test_cli_main_dispatches_supported_commands(
    arguments: list[str],
    expected_call: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fake = FakeCliClient()
    monkeypatch.setattr(cli, "RuntimeClient", lambda _url: fake)
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: SimpleNamespace(model_root_paths=[Path("models").resolve()]),
    )
    monkeypatch.setattr(sys, "argv", ["gwap", *arguments])

    cli.main()

    output = capsys.readouterr()
    assert json.loads(output.out)
    assert expected_call in {name for name, _value in fake.calls}


def test_cli_main_dispatches_start_stop_and_reports_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "start_runtime", lambda _args: {"started": True})
    monkeypatch.setattr(cli, "stop_runtime", lambda: {"stopped": True})

    monkeypatch.setattr(sys, "argv", ["gwap", "start", "--port", "8766"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["started"] is True

    monkeypatch.setattr(sys, "argv", ["gwap", "stop"])
    cli.main()
    assert json.loads(capsys.readouterr().out)["stopped"] is True

    class BrokenClient:
        def __init__(self, _url: str) -> None:
            pass

        def status(self) -> dict[str, Any]:
            raise RuntimeClientError("offline")

    monkeypatch.setattr(cli, "RuntimeClient", BrokenClient)
    monkeypatch.setattr(sys, "argv", ["gwap", "status"])
    with pytest.raises(SystemExit, match="2"):
        cli.main()
    assert json.loads(capsys.readouterr().err)["message"] == "offline"


def test_cli_emit_and_runtime_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cli.emit({"status": "ok"}, human=True)
    assert capsys.readouterr().out.strip() == "status: ok"
    cli.emit(["ok"], human=True)
    assert json.loads(capsys.readouterr().out) == ["ok"]

    data_root = tmp_path / "data"
    monkeypatch.setenv("DATA_ROOT_PATH", str(data_root))
    assert cli.runtime_pid_file() == data_root.resolve() / "runtime" / "agent-runtime.pid"
    assert cli.runtime_log_file() == data_root.resolve().parent / "logs" / "agent-runtime.log"

    alternate = tmp_path / "state"
    monkeypatch.setenv("DATA_ROOT_PATH", str(alternate))
    assert cli.runtime_log_file() == alternate.resolve() / "logs" / "agent-runtime.log"

    monkeypatch.delenv("DATA_ROOT_PATH")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(data_root=tmp_path / "fallback"))
    assert cli._runtime_data_root() == tmp_path / "fallback"


def test_cli_runtime_process_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_root = tmp_path / "data"
    monkeypatch.setenv("DATA_ROOT_PATH", str(data_root))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("AGENT_RUNTIME_PORT", raising=False)
    settings = SimpleNamespace(agent_runtime_port=8765, agent_runtime_host="127.0.0.1")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)

    class StartupClient:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url

        def status(self) -> dict[str, Any]:
            if self.base_url == "http://offline":
                raise RuntimeClientError("offline")
            return {"health": {"status": "ok"}}

    class FakeProcess:
        pid = 4321

        @staticmethod
        def poll() -> None:
            return None

    popen_calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_popen(command: list[str], **kwargs: Any) -> FakeProcess:
        popen_calls.append((command, kwargs))
        return FakeProcess()

    monkeypatch.setattr(cli, "RuntimeClient", StartupClient)
    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)
    args = argparse.Namespace(runtime_url="http://offline", port=8766)

    result = cli.start_runtime(args)

    assert result["started"] is True
    assert result["pid"] == 4321
    assert popen_calls[0][0][-1] == "8766"
    assert cli.runtime_pid_file().read_text(encoding="ascii") == "4321"

    monkeypatch.setattr(
        cli,
        "RuntimeClient",
        lambda _url: SimpleNamespace(status=lambda: {"health": {"status": "ok"}}),
    )
    assert cli.start_runtime(args)["reason"] == "already_running"

    stopped_calls: list[Any] = []
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *args, **kwargs: stopped_calls.append((args, kwargs)),
    )
    monkeypatch.setattr(cli.os, "kill", lambda *args: stopped_calls.append(args))
    stopped = cli.stop_runtime()
    assert stopped == {"stopped": True, "pid": 4321}
    assert stopped_calls
    assert cli.stop_runtime()["reason"] == "pid_file_missing"


def test_runtime_client_http_errors_and_wrappers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(method: str, url: str, **kwargs: Any) -> httpx.Response:
        calls.append((method, url, kwargs))
        if url.endswith("/plain"):
            return httpx.Response(200, text="plain", headers={"content-type": "text/plain"})
        return httpx.Response(200, json={"ok": True}, headers={"content-type": "application/json"})

    monkeypatch.setattr(client.httpx, "request", request)
    runtime = RuntimeClient("http://runtime/", "secret")
    assert runtime.headers == {"X-API-Key": "secret"}
    assert runtime.get("/health") == {"ok": True}
    assert runtime.post("/plain", json={}) == "plain"
    assert calls[0][1] == "http://runtime/api/v1/health"
    assert calls[0][2]["trust_env"] is True

    loopback = RuntimeClient("http://127.0.0.1:8766")
    assert loopback.get("/health") == {"ok": True}
    assert calls[-1][2]["trust_env"] is False

    monkeypatch.setattr(
        client.httpx,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(httpx.ConnectError("offline")),
    )
    with pytest.raises(RuntimeClientError, match="request failed"):
        runtime.get("/health")

    monkeypatch.setattr(
        client.httpx,
        "request",
        lambda *_args, **_kwargs: httpx.Response(500, json={"detail": "bad request"}),
    )
    with pytest.raises(RuntimeClientError, match="bad request"):
        runtime.get("/health")

    get_calls: list[Any] = []
    post_calls: list[Any] = []
    monkeypatch.setattr(runtime, "get", lambda path, **kwargs: get_calls.append((path, kwargs)) or [])
    monkeypatch.setattr(runtime, "post", lambda path, **kwargs: post_calls.append((path, kwargs)) or {})
    runtime.status()
    runtime.create_case({"title": "case"})
    runtime.inspect("CASE-1", level="ERROR", search="")
    runtime.search("CASE-1", {"query": "auth"})
    runtime.evidence_bundle("CASE-1", {"query": "auth"})
    runtime.attach_workspace("CASE-1", "D:\\src", "src")
    runtime.index_workspace("REPO-1")
    runtime.code_context("REPO-1", {"query": "auth"})
    runtime.diagnose("CASE-1")
    assert runtime.latest_analysis("CASE-1") is None
    assert get_calls and post_calls

    monkeypatch.setattr(runtime, "latest_analysis", lambda _case_id: None)
    with pytest.raises(RuntimeClientError, match="No completed analysis"):
        runtime.generate_report("CASE-1", "html")
    monkeypatch.setattr(runtime, "latest_analysis", lambda _case_id: {"id": "ANALYSIS-1"})
    runtime.generate_report("CASE-1", "pdf")

    source = tmp_path / "debug.log"
    source.write_text("synthetic", encoding="utf-8")
    monkeypatch.setattr(
        runtime,
        "_request",
        lambda *_args, **_kwargs: httpx.Response(200, json={"id": "ART-1"}),
    )
    monkeypatch.setattr(runtime, "post", lambda *_args, **_kwargs: {"id": "JOB-1"})
    assert runtime.ingest("CASE-1", str(source))["job"]["id"] == "JOB-1"
    with pytest.raises(RuntimeClientError, match="existing file"):
        runtime.ingest("CASE-1", str(tmp_path))

    monkeypatch.setattr(runtime, "get", lambda _path: {"status": "COMPLETED"})
    assert runtime.wait_job("JOB-1", 5)["status"] == "COMPLETED"
    monkeypatch.setattr(
        runtime,
        "get",
        lambda _path: {"status": "FAILED", "error_message": "parse failed"},
    )
    with pytest.raises(RuntimeClientError, match="parse failed"):
        runtime.wait_job("JOB-1", 5)

    monkeypatch.setattr(client.webbrowser, "open", lambda _url: True)
    assert runtime.ui_url("CASE-1").endswith("/ui/cases/CASE-1")
    assert runtime.ui_url().endswith("/ui/")
    assert runtime.open_ui()["opened"] is True


@pytest.mark.parametrize(
    ("runtime_url", "trust_env"),
    [
        ("http://127.0.0.1:8766", False),
        ("http://127.20.30.40:8766", False),
        ("http://localhost:8766", False),
        ("http://runtime.localhost:8766", False),
        ("http://[::1]:8766", False),
        ("https://runtime.example.com", True),
    ],
)
def test_runtime_client_only_trusts_environment_for_non_loopback_urls(
    runtime_url: str,
    trust_env: bool,
) -> None:
    assert RuntimeClient(runtime_url).trust_env is trust_env


def test_mcp_protocol_methods_and_stdio_boundary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    messages: list[dict[str, Any]] = []
    monkeypatch.setattr(mcp_server, "_write", messages.append)

    mcp_server.handle({"id": 1, "method": "initialize", "params": {}})
    assert messages[-1]["result"]["serverInfo"]["name"] == "gw-ap-debug"
    mcp_server.handle({"id": 2, "method": "ping"})
    assert messages[-1]["result"] == {}
    mcp_server.handle({"method": "notifications/initialized"})
    mcp_server.handle({"id": 3, "method": "tools/list"})
    assert len(messages[-1]["result"]["tools"]) >= 8

    mcp_server.handle(
        {"id": 4, "method": "tools/call", "params": {"name": "debug_status", "arguments": "invalid"}}
    )
    assert messages[-1]["error"]["code"] == -32602

    monkeypatch.setattr(
        mcp_server,
        "invoke_agent_tool",
        lambda *_args, **_kwargs: ToolResult(data={"status": "ok"}),
    )
    mcp_server.handle({"id": 5, "method": "tools/call", "params": {"name": "debug_status", "arguments": {}}})
    assert messages[-1]["result"]["isError"] is False

    monkeypatch.setattr(
        mcp_server,
        "invoke_agent_tool",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("tool failed")),
    )
    mcp_server.handle({"id": 6, "method": "tools/call", "params": {"name": "debug_status", "arguments": {}}})
    assert messages[-1]["result"]["isError"] is True
    mcp_server.handle({"id": 7, "method": "unknown"})
    assert messages[-1]["error"]["code"] == -32601

    monkeypatch.undo()
    monkeypatch.setattr(
        mcp_server.sys,
        "stdin",
        io.StringIO('\nnot-json\n[]\n{"jsonrpc":"2.0","id":8,"method":"ping"}\n'),
    )
    mcp_server.main()
    output = capsys.readouterr()
    assert json.loads(output.out)["id"] == 8
    assert "MCP server error" in output.err
