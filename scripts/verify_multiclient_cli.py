"""Two real Codex CLI clients over isolated HTTPS, with durable-run recovery.

Uses synthetic fixtures only. Requires an already authenticated Codex CLI and a
complete server package. Does not change global CLI config or Windows trust.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request

import certifi
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from verify_lan_transport import free_port, require
from verify_packaged_retrieval import read_model_audit, stop_managed_process

ROOT = Path(__file__).resolve().parents[1]
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
METHOD = """# Synthetic AP outage diagnosis

## Scope
Synthetic AP offline, restart and authentication incidents. Knowledge gives
checks, never proves the cause of the current incident.

## Checks
Read current-case logs and establish the order of fault and recovery.
Search `undervoltage`, `power`, `reboot`, `authentication`, `credential`,
`reject`, `uptime`, `link`, and `recovered` as applicable.
Power loss requires current-case power evidence plus a restart sequence.
Authentication failures require explicit rejection evidence. Compare uptime
and link state to avoid mistaking authentication for power failure.
Read the full selected evidence. Use at least two planning rounds: first
establish the failure mechanism, then check alternative causes and recovery.
Report remaining uncertainty and a bounded verification action.
"""
FIXTURES = (
    ("power", "AP offline and restart", [
        "NOTICE 2026-09-07 10:00:00.000[90][SYSTEM]SYNTHETIC-POWER-A uptime=86400 power input=12.0V link up",
        "CRITICAL 2026-09-07 10:00:01.000[90][POWER]SYNTHETIC-POWER-A undervoltage input=8.1V threshold=10.8V power loss",
        "WARN 2026-09-07 10:00:02.000[90][SYSTEM]SYNTHETIC-POWER-A reboot reason=undervoltage uptime=0",
        "NOTICE 2026-09-07 10:00:30.000[90][POWER]SYNTHETIC-POWER-A power recovered input=12.1V",
        "NOTICE 2026-09-07 10:00:31.000[90][AUTH]SYNTHETIC-POWER-A authentication accepted link up",
    ]),
    ("authentication", "AP clients cannot authenticate", [
        "NOTICE 2026-09-07 11:00:00.000[90][SYSTEM]SYNTHETIC-AUTH-B uptime=86400 power input=12.0V link up",
        "ERROR 2026-09-07 11:00:01.000[90][AUTH]SYNTHETIC-AUTH-B authentication rejected reason=invalid credential",
        "ERROR 2026-09-07 11:00:05.000[90][AUTH]SYNTHETIC-AUTH-B authentication rejected reason=invalid credential",
        "NOTICE 2026-09-07 11:00:10.000[90][SYSTEM]SYNTHETIC-AUTH-B uptime=86410 power input=12.0V link up no reboot",
        "NOTICE 2026-09-07 11:00:20.000[90][AUTH]SYNTHETIC-AUTH-B credential corrected authentication accepted service recovered",
    ]),
)


def rows(database, query, parameters=()):
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query, parameters)]


def cli_environment():
    """Honor the machine's existing proxy for CLI HTTPS, bypassing loopback MCP."""
    environment = dict(os.environ)
    proxies = urllib.request.getproxies()
    for scheme in ("http", "https"):
        if proxies.get(scheme):
            environment.setdefault(scheme.upper() + "_PROXY", proxies[scheme])
    environment["NO_PROXY"] = ",".join(filter(None, [environment.get("NO_PROXY", environment.get("no_proxy", "")),
                                                   "127.0.0.1", "localhost", "::1"]))
    return environment


def validate_diagnosis(diagnosis, allowed_ids, current_log_ids, expected_signal):
    """Reject plausible answers whose facts/citations are from another case."""
    facts, hypotheses = diagnosis.get("confirmed_facts", []), diagnosis.get("hypotheses", [])
    require(facts and hypotheses, "empty_diagnosis")
    require(expected_signal in json.dumps(hypotheses[0]).lower(), "wrong_top_hypothesis")
    references = set()
    for fact in facts:
        citations = set(fact.get("evidence_ids", []))
        require(bool(citations & current_log_ids), "fact_lacks_current_case_log")
        references.update(citations)
    for hypothesis in hypotheses:
        citations = set(hypothesis.get("supporting_evidence", []))
        require(bool(citations & current_log_ids), "hypothesis_lacks_current_case_log")
        references.update(citations)
        references.update(hypothesis.get("contradicting_evidence", []))
    require(references <= allowed_ids, "citation_outside_run")
    return {"facts": len(facts), "hypotheses": len(hypotheses), "validated_citations": len(references)}


def verify_stopped_data(output):
    """Reopen persisted data after process shutdown; no running API can mask loss."""
    database = output / "server-data/data/gw_ap_debug.db"
    checks = []
    for session in rows(database, "SELECT * FROM host_agent_sessions"):
        require(session["status"] == "COMPLETED", "stopped_session_incomplete")
        analyses = rows(database, "SELECT * FROM analysis_runs WHERE agent_run_id=?", (session["agent_run_id"],))
        require(len(analyses) == 1 and analyses[0]["provider"] == "host_cli", "stopped_analysis_missing")
        events = rows(database, "SELECT id,raw_text FROM log_events WHERE case_id=?", (session["case_id"],))
        expected = "undervoltage" if any("SYNTHETIC-POWER-A" in event["raw_text"] for event in events) else "credential"
        citations = validate_diagnosis(json.loads(analyses[0]["result_json"]),
            set(json.loads(session["allowed_evidence_ids_json"])), {event["id"] for event in events}, expected)
        reports = rows(database, "SELECT * FROM reports WHERE analysis_run_id=?", (analyses[0]["id"],))
        require(reports, "stopped_report_missing")
        for report in reports:
            path = Path(report["stored_path"])
            if not path.is_absolute():
                path = database.parent / "storage" / path
            require(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == report["sha256"],
                    "stopped_report_hash_mismatch")
        checks.append({"case_id": session["case_id"], "citation_check": citations, "report_hashes_match": True})
    require(len(checks) == 2, "stopped_case_count_mismatch")
    require(rows(database, "SELECT count(*) AS n FROM knowledge_documents WHERE active=1 AND source_type='analysis_skill'")[0]["n"] == 1,
            "published_method_not_retained")
    return {"cases": checks, "published_method_retained": True, "database_reopened_read_only": True}


def api(client, path, token, payload=None, **kwargs):
    response = client.request("POST" if payload is not None or "files" in kwargs else "GET",
                              "/api/v1" + path, headers={"X-API-Key": token}, json=payload, **kwargs)
    require(response.status_code == 200, f"api_{path}_http_{response.status_code}: {response.text[:500]}")
    return response.json()


def wait_job(client, token, job_id):
    deadline = time.monotonic() + 240
    while time.monotonic() < deadline:
        job = api(client, f"/jobs/{job_id}", token)
        if job["status"] == "COMPLETED":
            return job
        require(job["status"] not in {"FAILED", "CANCELLED"}, f"job_failed_{job_id}")
        time.sleep(0.3)
    raise RuntimeError("job_timeout")


def rpc(client, token, name, arguments):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    init = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                   "clientInfo": {"name": "synthetic-recovery-probe", "version": "1"}}})
    require(init.status_code == 200, "probe_mcp_initialize_failed")
    headers.update({"Mcp-Session-Id": init.headers["mcp-session-id"],
                    "MCP-Protocol-Version": init.json()["result"]["protocolVersion"]})
    client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2,
        "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    require(response.status_code == 200, "probe_mcp_call_failed")
    result = response.json()
    client.delete("/mcp", headers=headers)
    return result


def run(package, codex, output, timeout):
    package, codex, output = package.resolve(), codex.resolve(), output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    summary = {"status": "FAIL", "synthetic_only": True, "physical_machines": 1,
               "cli_clients": 2, "windows_trust_changed": False, "global_cli_config_changed": False}
    started = time.monotonic()
    backend_port, tls_port = free_port(), free_port()
    require(backend_port != tls_port, "port_collision")
    origin = f"https://127.0.0.1:{tls_port}"
    data = output / "server-data"
    database = data / "data/gw_ap_debug.db"
    environment = {key: value for key, value in os.environ.items() if not key.startswith(
        ("MCP_", "BUNDLED_", "LLM_", "DATABASE_", "AUTH_", "SERVER_", "MODEL_"))}
    environment.update(LLM_PROVIDER="mock", PYTHONUTF8="1")
    python = package / "runtime/python/python.exe"
    processes, children, handles, clients = [], [], [], []
    with (output / "server.log").open("wb") as log, tempfile.TemporaryDirectory(prefix="gwap-cli-machines-") as temporary:
        try:
            def admin(*arguments):
                result = subprocess.run([str(python), "-B", "-s", str(package / "server_admin.py"), *arguments],
                    cwd=package, env=environment, stdout=log, stderr=log, timeout=120, creationflags=FLAGS)
                require(result.returncode == 0, "server_configuration_failed")

            admin("configure", "--public-url", origin, "--data-root", str(data), "--backend-port", str(backend_port))
            config_path, gateway_path = data / "config/server.json", data / "config/caddy.json"
            config = json.loads(config_path.read_text())
            config["gateway_bind"] = "127.0.0.1"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            gateway = json.loads(gateway_path.read_text())
            gateway["apps"]["http"]["servers"]["platform"]["listen"] = [f"127.0.0.1:{tls_port}"]
            gateway_path.write_text(json.dumps(gateway), encoding="utf-8")
            admin("initialize-admin", "--config", str(config_path))
            administrator = (data / "config/bootstrap-token.txt").read_text().strip()
            for command in (
                [str(python), "-B", "-s", str(package / "portable_launcher.py"), "--server-config", str(config_path),
                 "--managed-stdin", "--no-browser", "--model-start-timeout", "180"],
                [str(package / "server-runtime/caddy.exe"), "run", "--config", str(gateway_path)],
            ):
                processes.append(subprocess.Popen(command, cwd=package, env=environment, stdin=subprocess.PIPE,
                    stdout=log, stderr=log, creationflags=FLAGS))
            cert = data / "gateway/pki/authorities/local/root.crt"
            deadline = time.monotonic() + 420
            while not cert.is_file() and time.monotonic() < deadline:
                require(all(p.poll() is None for p in processes), "server_exited")
                time.sleep(0.2)
            require(cert.is_file(), "tls_ca_timeout")
            with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(cert)), trust_env=False, timeout=90) as client:
                while time.monotonic() < deadline:
                    require(all(p.poll() is None for p in processes), "server_exited")
                    try:
                        ready = client.get("/api/v1/health/ready")
                        if ready.status_code == 200 and ready.json().get("ready"):
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.5)
                else:
                    raise RuntimeError("server_readiness_timeout")
                print("HTTPS server and real local retrieval ready", flush=True)
                document = api(client, "/knowledge", administrator, {"title": "Synthetic AP outage method",
                    "content": METHOD, "source_type": "analysis_skill", "device_type": "AP", "trust_level": "HIGH"})
                published = api(client, f"/knowledge/{document['id']}/adopt-human-verified", administrator,
                    {"human_verified": True, "expected_lock_version": document["lock_version"],
                     "content_sha256": hashlib.sha256(METHOD.encode()).hexdigest()})
                wait_job(client, administrator, published["job"]["id"])
                for label, title, lines in FIXTURES:
                    user = api(client, "/system/users", administrator, {"username": f"cli-{label}",
                        "display_name": f"Synthetic {label} client", "role": "ENGINEER", "issue_token": True,
                        "token_expires_days": 1})
                    token = user["raw_token"]
                    case = api(client, "/cases", token, {"title": title, "device_type": "AP",
                        "description": "Synthetic local multi-client inference test; diagnose from uploaded evidence."})
                    raw = "Start run collect command:WAP:display debuglog info\n" + "\n".join(lines) + "\n"
                    artifact = api(client, f"/cases/{case['id']}/artifacts", token,
                        files={"file": (f"synthetic-{label}.txt", raw.encode(), "text/plain")}, data={"kind": "debug_log"})
                    job = api(client, f"/cases/{case['id']}/artifacts/{artifact['id']}/parse", token, {})
                    wait_job(client, token, job["id"])
                    require(rows(database, "SELECT count(*) AS n FROM log_events WHERE case_id=?", (case["id"],))[0]["n"] >= 5,
                            "fixture_logs_not_parsed")
                    work = Path(temporary) / label
                    work.mkdir()
                    clients.append({"label": label, "token": token, "case_id": case["id"], "work": work, "user": user})
                ca = Path(temporary) / "test-ca.pem"
                certificates = [Path(certifi.where()).read_bytes(), cert.read_bytes()]
                custom_ca = os.environ.get("CODEX_CA_CERTIFICATE") or os.environ.get("SSL_CERT_FILE")
                if custom_ca and Path(custom_ca).is_file():
                    certificates.append(Path(custom_ca).read_bytes())
                ca.write_bytes(b"\n".join(certificates))
                reference = "\n".join((ROOT / ".agents/skills/gw-ap-debug" / name).read_text(encoding="utf-8")
                    for name in ("SKILL.md", "references/workflow.md", "references/schemas.md"))

                def launch(item, resume=None):
                    attempt = "resumed" if resume else "initial"
                    prompt = (f"Perform a real synthetic GW/AP diagnosis ONLY for case {item['case_id']}. "
                        "The user authorizes sending these synthetic fixtures to this authenticated CLI model, all necessary "
                        "MCP diagnosis mutations for this assigned case, and generating its HTML report. "
                        "Use ONLY debugplatform MCP tools; no shell, browsing, other services, or subagents. "
                        "You are the sole reasoner; no backend Chat. Call debug_status first and verify host mode. "
                        "Use executor codex. Read all required methods. Perform real knowledge search (top_k=3), "
                        "log searches and evidence reads. Complete at least two evidence-driven planning rounds "
                        "with exact returned receipt IDs and accepted_arguments; do not invent receipt fields. "
                        "Keep tool calls sequential and use the latest run version. Retrieve bounded data only. "
                        "Recover from validation errors using live schemas/state. Finalize a grounded diagnosis, "
                        "then generate its HTML report. Finish with JSON containing case_id, session_id, analysis_id, "
                        "report_id and cause (power, authentication or insufficient). Do not stop at a plan.\n")
                    if resume:
                        prompt += (f"This client was deliberately interrupted. RESUME durable session {resume}; "
                            "do not begin another run. debug_get_host_run with include_evidence=true and "
                            "include_planning_payloads=true restores persisted receipts, coverage and versions.\n")
                    prompt += "\nAuthoritative repository Skill/reference follows:\n" + reference
                    env = cli_environment()
                    env.update(GWAP_SIM_TOKEN=item["token"], CODEX_CA_CERTIFICATE=str(ca),
                               CODEX_SQLITE_HOME=str(item["work"] / "sqlite"))
                    arguments = [str(codex), "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
                        "-C", str(item["work"]), "-s", "read-only", "--json",
                        "-c", 'approval_policy="never"', "-c", f'mcp_servers.debugplatform.url="{origin}/mcp"',
                        "-c", 'mcp_servers.debugplatform.bearer_token_env_var="GWAP_SIM_TOKEN"',
                        "-c", "mcp_servers.debugplatform.required=true", "-c", "mcp_servers.debugplatform.tool_timeout_sec=180",
                        "-c", 'mcp_servers.debugplatform.default_tools_approval_mode="auto"',
                        "-o", str(output / f"{item['label']}-{attempt}-answer.txt"), "-"]
                    stream = (output / f"{item['label']}-{attempt}.jsonl").open("wb")
                    handles.append(stream)
                    process = subprocess.Popen(arguments, env=env, cwd=item["work"], stdin=subprocess.PIPE,
                        stdout=stream, stderr=stream, creationflags=FLAGS)
                    process.stdin.write(prompt.encode())
                    process.stdin.close()
                    children.append(process)
                    item.update(process=process, attempt=attempt)
                    print(f"CLI {item['label']} {attempt} started", flush=True)

                for item in clients:
                    launch(item)
                interrupted = None
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    first = clients[0]
                    sessions = rows(database, "SELECT * FROM host_agent_sessions WHERE case_id=?", (first["case_id"],))
                    if not interrupted and sessions and "read_diagnostic_documents" in sessions[0]["tool_receipts_json"]:
                        require(clients[1]["process"].poll() is None, "second_client_finished_before_fault_injection")
                        interrupted = sessions[0]["id"]
                        first["process"].terminate()
                        first["process"].wait(timeout=15)
                        summary["interrupted_session_id"] = interrupted
                        summary["second_client_alive_during_interruption"] = True
                        launch(first, interrupted)
                    if all(item["process"].poll() is not None for item in clients):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("cli_simulation_timeout")
                summary["cli_exit_codes"] = [item["process"].returncode for item in clients]
                require(all(code == 0 for code in summary["cli_exit_codes"]), "cli_process_failed_see_jsonl")
                require(interrupted, "restart_not_exercised")
                checks = []
                for item in clients:
                    sessions = rows(database, "SELECT * FROM host_agent_sessions WHERE case_id=?", (item["case_id"],))
                    require(len(sessions) == 1 and sessions[0]["status"] == "COMPLETED", "durable_run_not_completed_once")
                    session = sessions[0]
                    analyses = api(client, f"/cases/{item['case_id']}/analyses", item["token"])
                    require(len(analyses) == 1 and analyses[0]["status"] == "COMPLETED", "analysis_not_persisted_once")
                    analysis = analyses[0]
                    diagnosis = json.loads(analysis["result_json"])
                    expected = "undervoltage" if item["label"] == "power" else "credential"
                    citation_check = validate_diagnosis(diagnosis,
                        set(json.loads(session["allowed_evidence_ids_json"])),
                        {row["id"] for row in rows(database, "SELECT id FROM log_events WHERE case_id=?", (item["case_id"],))}, expected)
                    other_marker = "SYNTHETIC-AUTH-B" if item["label"] == "power" else "SYNTHETIC-POWER-A"
                    require(other_marker not in json.dumps(diagnosis), "diagnosis_contains_other_case_marker")
                    require(len(json.loads(session["planning_rounds_json"])) >= 2, "missing_planning_rounds")
                    reports = rows(database, "SELECT * FROM reports WHERE analysis_run_id=?", (analysis["id"],))
                    require(reports, "report_not_persisted")
                    response = client.get(f"/api/v1/reports/{reports[0]['id']}/download", headers={"X-API-Key": item["token"]})
                    require(response.status_code == 200 and len(response.content) > 100, "report_download_failed")
                    (output / f"{item['label']}-report.html").write_bytes(response.content)
                    other = clients[1] if item is clients[0] else clients[0]
                    denied = rpc(client, other["token"], "debug_get_host_run", {"session_id": session["id"]})
                    require(denied.get("error") or denied.get("result", {}).get("isError"), "other_principal_read_host_run")
                    checks.append({"client": item["label"], "case_id": item["case_id"], "session_id": session["id"],
                        "analysis_id": analysis["id"], "report_id": reports[0]["id"], "rounds": len(json.loads(session["planning_rounds_json"])),
                        "cause_signal_present": True, "other_principal_run_access_denied": True,
                        "citation_check": citation_check})
                summary.update(checks=checks, model_audit=read_model_audit(database), durable_resume_same_run=True,
                               simultaneous_processes=True, real_https=True, status="PASS")
        except Exception as error:
            summary.update(status="FAIL", error_type=type(error).__name__,
                           error=str(error) if isinstance(error, RuntimeError) else "Inspect isolated logs and traceback")
            raise
        finally:
            for child in children:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=15)
            for handle in handles:
                handle.close()
            if clients:
                # Clean up failed runs too, while their owning credentials still work.
                try:
                    with httpx.Client(base_url=origin, verify=ssl.create_default_context(cafile=str(cert)),
                                      trust_env=False, timeout=30) as cleanup:
                        for item in clients:
                            for session in rows(database, "SELECT id,version,status FROM host_agent_sessions WHERE case_id=?", (item["case_id"],)):
                                if session["status"] not in {"COMPLETED", "CANCELLED", "EXPIRED"}:
                                    cancelled = rpc(cleanup, item["token"], "debug_cancel_host_run", {
                                        "session_id": session["id"], "expected_version": session["version"],
                                        "reason": "Synthetic verifier ended before completion"})
                                    require(not cancelled.get("error") and not cancelled.get("result", {}).get("isError"),
                                            "incomplete_run_cancellation_failed")
                            user = item["user"]
                            revoked = cleanup.delete(f"/api/v1/system/users/{user['user']['id']}/tokens/{user['token']['id']}",
                                                     headers={"X-API-Key": administrator})
                            require(revoked.status_code == 200, "test_token_revocation_failed")
                    summary["credentials_revoked_and_runs_closed"] = True
                except Exception:
                    summary.update(status="FAIL", credentials_revoked_and_runs_closed=False)
            if processes:
                summary["server_graceful_shutdown"] = stop_managed_process(processes[0])
            for process in processes[1:]:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
            summary["duration_seconds"] = round(time.monotonic() - started, 2)
            (output / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    if summary["status"] == "PASS":
        try:
            require(summary.get("server_graceful_shutdown"), "server_shutdown_failed")
            summary["after_shutdown"] = verify_stopped_data(output)
        except Exception:
            summary.update(status="FAIL", after_shutdown_verification_failed=True)
            raise
        finally:
            (output / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts/lan" / f"multi-cli-{uuid.uuid4().hex[:10]}")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args()
    result = run(args.package, args.codex, args.output, args.timeout)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
