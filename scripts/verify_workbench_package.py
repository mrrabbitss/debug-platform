"""Verify the 0.4 workbench from a built package in a new synthetic data directory.

Uses only the package interpreter and loopback HTTP. No external model, TLS setup,
installed-server data, certificate stores or historical regression suites are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def compare_sources(package):
    count = 0
    for source, target in ((ROOT / "backend/app", package / "app/backend/app"),
                           (ROOT / "frontend/dist", package / "web")):
        expected = {file.relative_to(source).as_posix(): digest(file) for file in source.rglob("*")
                    if file.is_file() and "__pycache__" not in file.parts and "data" not in file.relative_to(source).parts
                    and file.suffix != ".pyc"}
        actual = {file.relative_to(target).as_posix(): digest(file) for file in target.rglob("*")
                  if file.is_file() and "__pycache__" not in file.parts and file.suffix != ".pyc"}
        if expected != actual:
            raise ValueError("Packaged application/frontend differs from current source")
        count += len(expected)
    return count


class Server:
    def __init__(self, package, output):
        self.package, self.output = package, output
        self.token = secrets.token_urlsafe(32)
        self.process = None
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, path, value=None, *, data=None, content_type=None, status=200):
        headers = {"X-API-Key": self.token}
        if value is not None:
            data, content_type = json.dumps(value).encode(), "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            response = self.http.open(req, timeout=20)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            body = response.read()
            if response.status != status:
                raise ValueError(f"{method} {path}: expected {status}, got {response.status}")
            return json.loads(body) if "application/json" in response.headers.get("Content-Type", "") else body

    def start(self, phase):
        data = self.output / "data"
        inherited = {key: value for key, value in os.environ.items() if not key.startswith("BUNDLED_GGUF_")}
        env = {**inherited, "APP_ENV": "test", "DEPLOYMENT_MODE": "standalone", "AUTH_MODE": "local",
               "DEBUG_PLATFORM_ENV_FILE": str(data / "isolated-no-secrets.env"),
               "API_KEY": self.token, "SIMPLE_ENGINEER_LOGIN": "false", "MCP_BEARER_TOKEN": self.token,
               "MCP_PUBLIC_BASE_URL": self.base, "DATA_ROOT": str(data), "STORAGE_ROOT": str(data / "storage"),
               "DATABASE_URL": "sqlite:///" + (data / "workbench.db").as_posix(),
               "STATIC_FRONTEND_ROOT": str(self.package / "web"), "LLM_PROVIDER": "mock",
               "LLM_API_KEY": "", "LLM_BASE_URL": "", "LLM_MODEL": "",
               "MODEL_ENDPOINT_ALLOWLIST": "", "MODEL_ALLOW_PRIVATE_ENDPOINTS": "false",
               "QDRANT_URL": "", "QDRANT_API_KEY": "", "MODEL_DOWNLOAD_ROOT": str(data / "models"),
               "TRUSTED_HOSTS": "127.0.0.1,localhost", "MODEL_SECRET_KEY": "",
               "MINIMUM_FREE_STORAGE_BYTES": "0", "JOB_WORKERS": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        data.mkdir(exist_ok=True)
        self.log = (self.output / f"runtime-{phase}.log").open("wb")
        code = (f"import sys;sys.path.insert(0,{str(self.package / 'app/backend')!r});"
                f"import uvicorn;uvicorn.run('app.main:app',host='127.0.0.1',port={self.port},log_level='warning')")
        self.process = subprocess.Popen([str(self.package / "runtime/python/python.exe"), "-B", "-s", "-c", code],
            cwd=self.package, env=env, stdin=subprocess.DEVNULL, stdout=self.log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        deadline = time.monotonic() + 55
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise ValueError("Packaged server exited during startup; inspect isolated runtime log")
            try:
                self.request("GET", "/api/v1/health/ready")
                return
            except (urllib.error.URLError, TimeoutError, ValueError):
                time.sleep(0.25)
        raise TimeoutError("Packaged workbench startup exceeded 55 seconds")

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if getattr(self, "log", None):
            self.log.close()


def verify(package, output):
    if output.exists():
        raise ValueError("Choose a new validation output directory")
    output.mkdir(parents=True)
    server = Server(package, output)
    evidence = {"source_files_compared": compare_sources(package), "external_model_calls": 0}
    api = "/api/v1"
    try:
        server.start("first")
        for route in ("/cases", "/knowledge", "/settings"):
            assert b'<div id="app"' in server.request("GET", route)
        config = server.request("GET", api + "/workbench/bootstrap")
        assert config["principal"]["role"] == "ADMIN"
        assert {"network", "connection", "unknown"} <= {item["id"] for item in config["categories"]}
        case = server.request("POST", api + "/cases", {"title": "Synthetic Workbench Package", "device_type": "AP"})
        assert case["problem_category"] == "unknown" and case["model_egress_approved"] is True
        case_id = case["id"]
        server.request("PATCH", api + f"/cases/{case_id}", {"problem_category": "network", "model_egress_approved": False})
        job = server.request("POST", api + f"/cases/{case_id}/analyses")
        deadline = time.monotonic() + 55
        while job["status"] in {"QUEUED", "RUNNING"} and time.monotonic() < deadline:
            time.sleep(0.25)
            job = server.request("GET", api + "/jobs/" + job["id"])
        assert job["status"] == "COMPLETED", "Synthetic diagnosis did not complete"
        analysis = server.request("GET", api + f"/cases/{case_id}/analyses")[0]
        assert analysis["status"] == "COMPLETED"
        evidence["reports"] = {}
        for fmt in ("html", "docx", "pdf"):
            report = server.request("POST", api + f"/cases/{case_id}/analyses/{analysis['id']}/reports/{fmt}")
            body = server.request("GET", api + "/reports/" + report["report_id"] + "/download")
            assert hashlib.sha256(body).hexdigest() == report["sha256"]
            (output / f"synthetic-report.{fmt}").write_bytes(body)
            evidence["reports"][fmt] = {"bytes": len(body), "sha256": report["sha256"]}
            if fmt == "html":
                assert all(text in body.decode("utf-8") for text in ("组网总览", "异常设备时间轨迹", "异常AP分析推理", "结论与后续方向"))
        record = server.request("POST", api + "/workbench/library", {"title": "Synthetic reviewed case",
            "content": "Synthetic observations reviewed by a human", "problem_category": "network",
            "case_id": case_id, "analysis_id": analysis["id"]})
        assert record["status"] == "PENDING" and record["report_markdown"]
        record = server.request("POST", api + f"/workbench/library/{record['id']}/review", {"version": record["version"], "approve": True})
        assert record["status"] == "CONFIRMED"
        fields = urllib.parse.urlencode({"message": "Synthetic folder planning", "paths": "[]", "model_egress_approved": "false"}).encode()
        session = server.request("POST", api + "/workbench/assistant", data=fields, content_type="application/x-www-form-urlencoded")
        assert session["status"] == "PAUSED" and not session.get("job_id")
        server.request("POST", api + f"/workbench/assistant/{session['id']}/retry", {"version": session["version"]}, status=409)
        server.stop()
        server.start("restart")
        saved = server.request("GET", api + f"/cases/{case_id}")
        assert saved["model_egress_approved"] is False and saved["problem_category"] == "network"
        assert server.request("GET", api + f"/workbench/assistant/{session['id']}")["status"] == "PAUSED"
        assert any(item["id"] == record["id"] and item["status"] == "CONFIRMED"
                   for item in server.request("GET", api + "/workbench/library"))
        evidence.update(status="PASS", restart_persistence=True, new_default_consent=True,
                        fixed_report_and_library=True, paused_assistant_without_egress=True)
    finally:
        server.stop()
    (output / "result.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    print(json.dumps(verify(options.package.resolve(), options.output.resolve()), indent=2))
