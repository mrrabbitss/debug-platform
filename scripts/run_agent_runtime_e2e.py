#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SAMPLE_LOG = ROOT / "sample_data" / "collectDebuginfo_demo.zip"
SAMPLE_REPOSITORY = ROOT / "sample_data" / "repository"


class RetryingTemporaryDirectory(tempfile.TemporaryDirectory):
    """Retry transient Windows cleanup failures after SQLite/server shutdown."""

    def cleanup(self) -> None:
        for attempt in range(20):
            try:
                super().cleanup()
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.1)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run(args: list[str], *, env: dict[str, str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=True, timeout=180)


def git(workspace: Path, *args: str) -> None:
    run(["git", "-C", str(workspace), *args], env=os.environ.copy())


def cli(runtime_url: str, env: dict[str, str], *args: str) -> object:
    result = run(
        [sys.executable, "-m", "app.agent_runtime.cli", "--runtime-url", runtime_url, *args],
        env=env,
        cwd=BACKEND,
    )
    return json.loads(result.stdout)


def wait_ready(runtime_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("agent runtime exited during startup")
        try:
            response = httpx.get(runtime_url + "/api/v1/health", timeout=1)
            if response.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError("agent runtime did not become ready")


def main() -> int:
    if not shutil.which("git"):
        print(json.dumps({"status": "SKIP", "reason": "git not found"}))
        return 0
    port = free_port()
    runtime_url = f"http://127.0.0.1:{port}"
    with RetryingTemporaryDirectory(prefix="gwap-agent-e2e-") as tmp:
        base = Path(tmp)
        workspace = base / "workspace 中文 path"
        shutil.copytree(SAMPLE_REPOSITORY, workspace)
        git(workspace, "init", "-q")
        git(workspace, "config", "user.email", "e2e@example.invalid")
        git(workspace, "config", "user.name", "Agent E2E")
        git(workspace, "add", ".")
        git(workspace, "commit", "-qm", "initial")
        frontend_dist = base / "frontend-dist"
        (frontend_dist / "assets").mkdir(parents=True)
        (frontend_dist / "index.html").write_text(
            '<!doctype html><html><body><div id="app">agent-ui-smoke</div></body></html>',
            encoding="utf-8",
        )
        (frontend_dist / "assets" / "smoke.txt").write_text("ok", encoding="utf-8")

        model = base / "models" / "bge-test"
        model.mkdir(parents=True)
        (model / "config.json").write_text('{"architectures":["BertModel"],"hidden_size":8}', encoding="utf-8")
        (model / "modules.json").write_text('[{"type":"sentence_transformers.models.Pooling"}]', encoding="utf-8")
        (model / "model.safetensors").write_bytes(b"not-real-weights")

        env = os.environ.copy()
        env.update({
            "PYTHONPATH": str(BACKEND),
            "APP_ENV": "test",
            "AUTH_MODE": "local",
            "AGENT_MODE": "external",
            "AGENT_RUNTIME_PORT": str(port),
            "SERVE_FRONTEND": "true",
            "FRONTEND_DIST": str(frontend_dist),
            "DATA_ROOT_PATH": str(base / "data"),
            "DATABASE_URL": f"sqlite:///{(base / 'data' / 'e2e.db').as_posix()}",
            "STORAGE_ROOT": str(base / "storage"),
            "MODEL_ROOTS": str(base / "models"),
            "WORKSPACE_ROOTS": str(base),
            "GWAP_RUNTIME_URL": runtime_url,
        })
        (base / "data").mkdir()
        (base / "storage").mkdir()
        stdout = (base / "server.out").open("w", encoding="utf-8")
        stderr = (base / "server.err").open("w", encoding="utf-8")
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=stdout,
            stderr=stderr,
            text=True,
        )
        try:
            wait_ready(runtime_url, process)
            status = cli(runtime_url, env, "status")
            assert status["agent_runtime"]["runtime"]["port"] == port
            case = cli(runtime_url, env, "case-create", "Agent runtime E2E", "--device-type", "AP", "--description", "authentication failure")
            case_id = str(case["id"])
            ingest = cli(runtime_url, env, "ingest", case_id, str(SAMPLE_LOG), "--timeout", "120")
            workspace_result = cli(runtime_url, env, "workspace-attach", case_id, str(workspace), "--timeout", "120")
            evidence = cli(runtime_url, env, "evidence", case_id, "--query", "hostapd authentication failure")
            diagnosis = cli(runtime_url, env, "diagnose", case_id, "--timeout", "120")
            report = cli(runtime_url, env, "report", case_id, "--format", "html")
            models = cli(runtime_url, env, "models", "scan", "--no-llm")
            analysis = diagnosis["analysis"]
            result_json = json.loads(analysis["result_json"])
            assert result_json["analysis_engine"] == "rule+agentic-evidence-external"
            assert analysis["provider"] == "deterministic"
            assert analysis["model"] == "rule+agentic-evidence"
            assert analysis["prompt_version"] == "external-evidence-v1"
            assert httpx.get(runtime_url + "/ui/", timeout=2).status_code == 200
            assert httpx.get(runtime_url + f"/ui/cases/{case_id}", timeout=2).status_code == 200
            assert httpx.get(runtime_url + "/ui/assets/smoke.txt", timeout=2).text == "ok"
            assert evidence["mode"] == "external_agent"
            assert workspace_result["index_result"]["status"] == "COMPLETED"
            assert ingest["parse_result"]["status"] == "COMPLETED"
            assert report["format"] == "html"
            assert models["count"] == 1 and models["models"][0]["task_type"] == "embedding"
            mcp_request = json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}
            }) + "\n"
            mcp = subprocess.run(
                [sys.executable, "-m", "app.agent_runtime.mcp_server"],
                cwd=BACKEND,
                env=env,
                input=mcp_request,
                text=True,
                capture_output=True,
                timeout=10,
                check=True,
            )
            tools = json.loads(mcp.stdout.splitlines()[0])["result"]["tools"]
            assert 8 <= len(tools) <= 12
            summary = {
                "status": "PASS",
                "case_id": case_id,
                "events": len(evidence["log_evidence"]),
                "tool_count": len(tools),
                "analysis_engine": result_json["analysis_engine"],
                "workspace_index": workspace_result["index_result"]["status"],
                "report": report["format"],
                "model_scan": models["models"][0]["task_type"],
                "single_port_web": True,
                "analysis_provider": analysis["provider"],
            }
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
            stdout.close()
            stderr.close()


if __name__ == "__main__":
    raise SystemExit(main())
