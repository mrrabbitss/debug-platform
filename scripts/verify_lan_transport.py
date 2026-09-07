"""Real loopback HTTPS + RBAC + MCP smoke; no company data or generative model calls."""
from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def concurrent_clients(client, administrator):
    """Ten independent principals perform simultaneous SQLite writes and reads over TLS."""
    tokens = []
    for index in range(10):
        response = client.post('/api/v1/system/users', headers=administrator, json={
            'username': f'concurrent-{index}', 'display_name': f'Synthetic client {index}',
            'role': 'ENGINEER', 'issue_token': True, 'token_expires_days': 1})
        require(response.status_code == 200, 'concurrent_user_creation_failed')
        tokens.append(response.json()['raw_token'])
    barrier = Barrier(10)

    def run(token):
        headers = {'X-API-Key': token, 'Authorization': f'Bearer {token}'}
        barrier.wait(timeout=30)
        elapsed = []
        started = time.monotonic()
        created = client.post('/api/v1/cases', headers=headers, json={
            'title': 'Synthetic concurrent pilot case', 'device_type': 'AP'})
        require(created.status_code == 200, f'concurrent_case_write_failed_http_{created.status_code}')
        elapsed.append(time.monotonic() - started)
        for _ in range(3):
            started = time.monotonic()
            response = client.get(f'/api/v1/cases/{created.json()["id"]}', headers=headers)
            require(response.status_code == 200, 'concurrent_case_read_failed')
            elapsed.append(time.monotonic() - started)
        return elapsed

    with ThreadPoolExecutor(max_workers=10) as pool:
        samples = sorted(value for group in pool.map(run, tokens) for value in group)
    return {'clients': 10, 'requests': len(samples), 'failures': 0,
            'p95_seconds': round(samples[int(len(samples) * 0.95) - 1], 3),
            'scope': 'simultaneous_authenticated_case_writes_and_reads_not_ten_model_jobs'}


def verify(package: Path, output: Path) -> dict:
    package = package.resolve()
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    backend_port, https_port = free_port(), free_port()
    require(backend_port != https_port, "port_allocation_collision")
    origin = f"https://127.0.0.1:{https_port}"
    data_root = output / "server data 中文"
    python = package / "runtime/python/python.exe"
    admin_script = package / "server_admin.py"
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("MCP_", "BUNDLED_", "LLM_", "DATABASE_", "AUTH_", "SERVER_", "MODEL_"))}
    environment.update(LLM_PROVIDER="mock", EMBEDDING_PROVIDER="hashing", RERANKER_PROVIDER="disabled", PYTHONUTF8="1")
    processes = []
    result = {"passed": False, "real_https": True, "generative_model_calls": 0,
              "windows_services_installed": False, "certificate_store_changed": False, "gguf_tested": False}
    with (output / "processes.log").open("wb") as logs:
        def admin(*arguments):
            completed = subprocess.run([str(python), "-B", "-s", str(admin_script), *arguments],
                                       env=environment, cwd=package, stdout=logs, stderr=subprocess.STDOUT,
                                       creationflags=flags, timeout=120)
            require(completed.returncode == 0, "offline_administration_failed")

        try:
            admin("configure", "--public-url", origin, "--data-root", str(data_root), "--backend-port", str(backend_port))
            config_path = data_root / "config/server.json"
            # Testing never opens a LAN listener or installs a certificate into Windows.
            config = json.loads(config_path.read_text())
            config["gateway_bind"] = "127.0.0.1"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            gateway_path = data_root / "config/caddy.json"
            gateway = json.loads(gateway_path.read_text())
            gateway["apps"]["http"]["servers"]["platform"]["listen"] = [f"127.0.0.1:{https_port}"]
            gateway_path.write_text(json.dumps(gateway), encoding="utf-8")
            admin("initialize-admin", "--config", str(config_path))
            bootstrap = (data_root / "config/bootstrap-token.txt").read_text().strip()
            for command in (
                [str(python), "-B", "-s", str(package / "portable_launcher.py"), "--server-config", str(config_path), "--no-local-retrieval"],
                [str(package / "server-runtime/caddy.exe"), "run", "--config", str(gateway_path)],
            ):
                processes.append(subprocess.Popen(command, env=environment, cwd=package, stdin=subprocess.DEVNULL,
                                                  stdout=logs, stderr=subprocess.STDOUT, creationflags=flags))
            root_cert = data_root / "gateway/pki/authorities/local/root.crt"
            deadline = time.monotonic() + 120
            while not root_cert.is_file() and time.monotonic() < deadline:
                require(all(process.poll() is None for process in processes), "service_exited_before_tls_ready")
                time.sleep(0.2)
            require(root_cert.is_file(), "private_ca_not_created")
            tls = ssl.create_default_context(cafile=str(root_cert))
            with httpx.Client(base_url=origin, verify=tls, trust_env=False, timeout=30, follow_redirects=False) as client:
                while time.monotonic() < deadline:
                    require(all(process.poll() is None for process in processes), "service_exited_before_backend_ready")
                    try:
                        health = client.get("/api/v1/health/ready")
                        if health.status_code == 200 and health.json().get("ready"):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.3)
                else:
                    raise RuntimeError("https_readiness_timeout")
                require(client.get("/cases/lan-smoke-route").status_code == 200, "vue_route_failed")
                require(client.get("/api/v1/system/client-info").status_code == 401, "anonymous_access_not_rejected")
                administrator = {"X-API-Key": bootstrap}
                response = client.post("/api/v1/system/users", headers=administrator,
                                       json={"username": "lan-engineer", "display_name": "LAN smoke engineer", "role": "ENGINEER",
                                             "issue_token": True, "token_expires_days": 1})
                require(response.status_code == 200, "engineer_creation_failed")
                user = response.json()
                raw_token = user["raw_token"]
                headers = {"X-API-Key": raw_token, "Authorization": f"Bearer {raw_token}"}
                info = client.get("/api/v1/system/client-info", headers=headers)
                require(info.status_code == 200 and info.json()["server_id"] == config["server_id"], "engineer_discovery_failed")
                require(client.get("/api/v1/system/status", headers=headers).status_code == 403, "engineer_admin_boundary_failed")
                created = client.post("/api/v1/cases", headers=headers,
                                      json={"title": "Synthetic LAN AP frequent offline", "device_type": "AP", "description": "Isolated transport regression"})
                require(created.status_code == 200, "engineer_case_creation_failed")
                case_id = created.json()["id"]
                for device in ("GW", "AP"):
                    source = ROOT / f"sample_data/demo_ap_frequent_offline/{device}_collectDebuginfo_demo.txt"
                    response = client.post(f"/api/v1/cases/{case_id}/artifacts", headers=headers,
                                           files={"file": (source.name, source.read_bytes(), "text/plain")}, data={"kind": "debug_log"})
                    require(response.status_code == 200, "https_multipart_upload_failed")
                rpc_headers = {**headers, "Accept": "application/json, text/event-stream"}
                init = client.post("/mcp", headers=rpc_headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "lan-smoke", "version": "1.0"}}})
                require(init.status_code == 200, "https_mcp_initialize_failed")
                session = init.headers.get("mcp-session-id")
                require(session, "mcp_session_missing")
                rpc_headers.update({"Mcp-Session-Id": session, "MCP-Protocol-Version": init.json()["result"]["protocolVersion"]})
                initialized = client.post("/mcp", headers=rpc_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
                require(initialized.status_code in {200, 202, 204}, "mcp_notification_failed")
                status = client.post("/mcp", headers=rpc_headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "debug_status", "arguments": {}}})
                require(status.status_code == 200, "mcp_status_failed")
                model = status.json()["result"]["structuredContent"]
                require(model["authenticated_role"] == "ENGINEER", "mcp_engineer_role_failed")
                require(model["server_instance_id"] == info.json()["server_id"], "rest_mcp_identity_mismatch")
                require(model["backend_chat_calls"] == 0 and model["backend_chat_allowed"] is False, "host_model_boundary_failed")
                forged = client.post("/mcp", headers={**rpc_headers, "Origin": "https://untrusted.example.test"},
                                     json={"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
                require(forged.status_code == 403, "origin_rejection_failed")
                result['concurrency'] = concurrent_clients(client, administrator)
                revoked = client.delete(f'/api/v1/system/users/{user["user"]["id"]}/tokens/{user["token"]["id"]}', headers=administrator)
                require(revoked.status_code == 200, "token_revocation_failed")
                require(client.get("/api/v1/system/client-info", headers=headers).status_code == 401, "revoked_rest_token_accepted")
                denied = client.post("/mcp", headers=rpc_headers, json={"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
                require(denied.status_code == 401, "revoked_mcp_session_accepted")
                result.update(passed=True, engineer_rest_mcp=True, additive_client_transport_ready=True,
                              uploaded_devices=["GW", "AP"], vue_route=True, origin_rejected=True, token_revocation=True,
                              server_id=config["server_id"])
        except Exception as error:
            result["error_type"] = type(error).__name__
            result["error"] = str(error) if isinstance(error, RuntimeError) else "Inspect the isolated process log"
            raise
        finally:
            for process in reversed(processes):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            (output / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/lan" / f"transport-{uuid.uuid4().hex[:12]}")
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.package, args.output), indent=2))
    except Exception:
        print(json.dumps({"passed": False, "result": str(args.output / "result.json")}, indent=2))
        raise SystemExit(1) from None
