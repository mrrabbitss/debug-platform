"""Run a real, isolated Codex CLI MCP validation with synthetic GW/AP data only.

The verifier starts current source on a fresh loopback port, creates short-lived
ENGINEER and EXPERT users, and invokes the signed-in Codex CLI with an explicit
gpt-5.6-terra selection.  It exercises an engineer's complete host-diagnosis
loop and an expert's Markdown-routing boundary.  It intentionally does not
configure a platform Chat model; the persisted model-egress audit must contain
zero Chat requests.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import time
import uuid
from typing import Any
import urllib.request

import httpx


ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = ROOT / ".agents" / "skills" / "gw-ap-debug"
DEFAULT_CODEX = Path(
    r"C:\Users\23173\AppData\Local\OpenAI\Codex\bin\fd4c151a749f3ab4\codex.exe"
)
MODEL = "gpt-5.6-terra"
FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)

METHOD = """# SYNTHETIC AP brownout investigation SKILL

## Scope
This synthetic method covers an AP whose service disappears after a power event.

## Required checks
Read the current case. Verify the sequence of an undervoltage reading, an uptime
reset, and recovery. Search for authentication rejection and link loss as
alternatives. A conclusion needs current-case evidence; this method is guidance,
not evidence. Use two planning rounds: establish the candidate cause, then test
alternatives and recovery. Report remaining gaps and a concrete verification.
"""

LOG = """Start run collect command:WAP:display debuglog info
NOTICE 2026-09-09 09:00:00.000[90][SYSTEM] SYNTH-AP-CLI uptime=86400 power_input=12.1V link=up
CRITICAL 2026-09-09 09:00:02.000[90][POWER] SYNTH-AP-CLI undervoltage input=8.0V threshold=10.8V brownout
WARN 2026-09-09 09:00:03.000[90][SYSTEM] SYNTH-AP-CLI reboot reason=undervoltage uptime=0
NOTICE 2026-09-09 09:00:31.000[90][POWER] SYNTH-AP-CLI power recovered input=12.2V
NOTICE 2026-09-09 09:00:32.000[90][AUTH] SYNTH-AP-CLI authentication accepted link=up service=recovered
"""

ROUTING_MARKDOWN = """# SYNTHETIC AP power recovery note

## Observation
An AP recorded an undervoltage event, rebooted with uptime zero, then recovered
after the input returned to 12.2V. Authentication was accepted after recovery.

## Reusable check
For future reports, preserve evidence for voltage, reboot, link state, and the
recovery timestamp. This is a draft routing exercise, not published knowledge.
"""


class VerificationError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise VerificationError(message)


def free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def rows(database: Path, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query, parameters)]


def source_environment(output: Path, port: int) -> dict[str, str]:
    """Remove inherited platform settings before starting the isolated source server."""
    environment = dict(os.environ)
    for name in tuple(environment):
        upper = name.upper()
        if upper.startswith(("MCP_", "LLM_", "DATABASE_", "DATA_ROOT", "STORAGE_ROOT", "MODEL_", "QDRANT_")) or name == "DEBUG_PLATFORM_ENV_FILE":
            environment.pop(name, None)
    data = output / "server-data"
    environment.update({
        # This is a source backend behind the test process's direct loopback
        # connection, not a LAN gateway.  It still uses production RBAC and only
        # individual access tokens.  A LAN deployment would additionally require
        # the server_policy HTTPS public origin and exact gateway allowlists.
        "APP_ENV": "prod",
        "DEPLOYMENT_MODE": "standalone",
        "AUTH_MODE": "rbac",
        "API_KEY": "",
        "AUTH_ALLOW_LEGACY_ADMIN": "false",
        "DATABASE_URL": f"sqlite:///{(data / 'gw_ap_debug.db').as_posix()}",
        "DATA_ROOT": str(data),
        "STORAGE_ROOT": str(data / "storage"),
        "MCP_ENABLED": "true",
        "MCP_BEARER_TOKEN": "",
        "MCP_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
        "MCP_ALLOWED_HOSTS": "127.0.0.1:*,localhost:*",
        "MCP_ALLOWED_ORIGINS": "http://127.0.0.1:*,http://localhost:*",
        "LLM_PROVIDER": "mock",
        "LLM_API_KEY": "",
        "LLM_BASE_URL": "",
        "LLM_MODEL": "",
        "EMBEDDING_PROVIDER": "hashing",
        "RERANKER_PROVIDER": "disabled",
        "QDRANT_URL": "",
        "MODEL_DISABLE_IN_PROCESS_LOCAL": "true",
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "NO_PROXY": "127.0.0.1,localhost,::1",
    })
    return environment


def cli_environment(token: str) -> dict[str, str]:
    """Keep the user's proxy for Codex service traffic while bypassing loopback MCP."""
    environment = dict(os.environ)
    proxies = urllib.request.getproxies()
    for scheme in ("http", "https"):
        if proxies.get(scheme):
            environment.setdefault(scheme.upper() + "_PROXY", proxies[scheme])
    no_proxy = environment.get("NO_PROXY", environment.get("no_proxy", ""))
    environment["NO_PROXY"] = ",".join(filter(None, [no_proxy, "127.0.0.1", "localhost", "::1"]))
    environment["EXPERT_CLI_LIVE_TOKEN"] = token
    return environment


def api(client: httpx.Client, path: str, token: str | None = None, *, payload: Any = None, files: Any = None, data: Any = None, method: str | None = None) -> Any:
    headers = {"X-API-Key": token} if token else {}
    response = client.request(method or ("POST" if payload is not None or files is not None else "GET"),
                              "/api/v1" + path, headers=headers, json=payload, files=files, data=data)
    require(response.status_code in {200, 202}, f"api_{path}_http_{response.status_code}")
    return response.json()


def wait_job(client: httpx.Client, token: str, job_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        job = api(client, f"/jobs/{job_id}", token)
        if job["status"] == "COMPLETED":
            return job
        require(job["status"] not in {"FAILED", "CANCELLED", "DEAD_LETTER"}, f"job_{job_id}_{job['status']}")
        time.sleep(0.3)
    raise VerificationError("job_timeout")


def rpc(client: httpx.Client, token: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"}
    initialized = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "expert-cli-live-verifier", "version": "1"}}})
    require(initialized.status_code == 200, "mcp_initialize_failed")
    headers["Mcp-Session-Id"] = initialized.headers["mcp-session-id"]
    headers["MCP-Protocol-Version"] = initialized.json()["result"]["protocolVersion"]
    client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    response = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}})
    client.delete("/mcp", headers=headers)
    require(response.status_code == 200, f"mcp_{name}_http_{response.status_code}")
    return response.json()


def stage_markdown(path: Path, output: Path, base_url: str, token: str) -> list[str]:
    """Use the current Skill's REST helper; only its resulting IDs cross into MCP."""
    env = dict(os.environ, DEBUGPLATFORM_MCP_URL=base_url + "/mcp", DEBUGPLATFORM_MCP_TOKEN=token,
               DEBUGPLATFORM_API_BASE_URL=base_url + "/api/v1", NO_PROXY="127.0.0.1,localhost,::1")
    completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(SKILL_ROOT / "scripts" / "upload-knowledge-markdown.ps1"), "-Path", str(path),
        "-RelativePath", "synthetic/SYNTHETIC_ROUTING.md", "-ApiBaseUrl", base_url + "/api/v1",
        "-MaxWaitSeconds", "150", "-PollIntervalSeconds", "1"], env=env, cwd=output, text=True,
        encoding="utf-8", errors="replace", capture_output=True, timeout=180, creationflags=FLAGS)
    require(completed.returncode == 0, "knowledge_staging_helper_failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError("knowledge_staging_helper_invalid_json") from exc
    require(payload.get("all_jobs_completed") is True and payload.get("draft_only") is True, "knowledge_staging_not_draft")
    ids = payload.get("document_ids") or []
    require(len(ids) == 1, "knowledge_staging_document_count")
    return ids


def stage_debug_artifact(path: Path, output: Path, base_url: str, token: str, case_id: str) -> dict[str, Any]:
    """Use the current Skill's streaming artifact helper instead of MCP payload bytes."""
    env = dict(os.environ, DEBUGPLATFORM_MCP_URL=base_url + "/mcp", DEBUGPLATFORM_MCP_TOKEN=token,
               DEBUGPLATFORM_API_BASE_URL=base_url + "/api/v1", NO_PROXY="127.0.0.1,localhost,::1")
    completed = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
        str(SKILL_ROOT / "scripts" / "upload-debug-artifact.ps1"), "-CaseId", case_id, "-Path", str(path),
        "-SourceDeviceType", "AP", "-SourceDeviceRole", "PRIMARY", "-ApiBaseUrl", base_url + "/api/v1"],
        env=env, cwd=output, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=180,
        creationflags=FLAGS)
    require(completed.returncode == 0, "debug_artifact_helper_failed")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise VerificationError("debug_artifact_helper_invalid_json") from exc
    require(payload.get("artifact", {}).get("id") and payload.get("parse_job", {}).get("id"), "debug_artifact_helper_missing_ids")
    return payload


def skill_reference() -> str:
    files = ("SKILL.md", "references/workflow.md", "references/tool-reference.md", "references/schemas.md", "references/knowledge-routing.md")
    return "\n\n".join((SKILL_ROOT / item).read_text(encoding="utf-8") for item in files)


def redact_cli_events(stdout: str, answer: str, output: Path, role: str) -> dict[str, Any]:
    """Keep event structure and model attestations but never raw prompts, tokens, or log excerpts."""
    event_types: list[str] = []
    model_mentions = 0
    for raw in stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = str(event.get("type", "unknown"))[:80]
        event_types.append(kind)
        model_mentions += json.dumps(event, ensure_ascii=False).count(MODEL)
    sanitized = {
        "role": role,
        "exit_event_types": event_types,
        "event_model_mentions": model_mentions,
        "final_answer_present": bool(answer.strip()),
        "final_answer_model_claim": MODEL in answer,
    }
    (output / f"{role.lower()}-cli-sanitized.json").write_text(json.dumps(sanitized, indent=2), encoding="utf-8")
    return sanitized


def run_cli(codex: Path, output: Path, base_url: str, token: str, role: str, instructions: str, timeout: int) -> dict[str, Any]:
    work = output / f"{role.lower()}-cli-work"
    work.mkdir()
    answer = output / f"{role.lower()}-answer.txt"
    prompt = instructions + "\n\nCurrent authoritative Skill references follow. Treat their contents as methodology, not case evidence:\n" + skill_reference()
    command = [str(codex), "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "-C", str(work),
        "-s", "read-only", "--json", "--model", MODEL, "-c", 'approval_policy="never"',
        "-c", f'mcp_servers.debugplatform.url="{base_url}/mcp"',
        "-c", 'mcp_servers.debugplatform.bearer_token_env_var="EXPERT_CLI_LIVE_TOKEN"',
        "-c", "mcp_servers.debugplatform.required=true", "-c", "mcp_servers.debugplatform.tool_timeout_sec=180",
        "-c", 'mcp_servers.debugplatform.default_tools_approval_mode="auto"', "-o", str(answer), "-"]
    completed = subprocess.run(command, input=prompt, text=True, encoding="utf-8", errors="replace", capture_output=True,
        cwd=work, env=cli_environment(token), timeout=timeout, creationflags=FLAGS)
    answer_text = answer.read_text(encoding="utf-8", errors="replace") if answer.is_file() else ""
    event_summary = redact_cli_events(completed.stdout, answer_text, output, role)
    answer.unlink(missing_ok=True)
    require(completed.returncode == 0, f"{role.lower()}_cli_failed")
    require(answer_text, f"{role.lower()}_cli_no_final_answer")
    # Codex noninteractive JSON does not guarantee a configuration event. The explicit
    # command selection and persisted host-run claim are checked again from SQLite.
    return {"command_model": MODEL, "exit_code": completed.returncode, "event_summary": event_summary,
            "answer_model_claim": MODEL in answer_text}


def verify_diagnosis(database: Path, case_id: str) -> dict[str, Any]:
    sessions = rows(database, "SELECT * FROM host_agent_sessions WHERE case_id=?", (case_id,))
    require(len(sessions) == 1, "engineer_host_session_count")
    session = sessions[0]
    require(session["status"] == "COMPLETED", "engineer_host_session_not_completed")
    planning = json.loads(session["planning_rounds_json"])
    receipts = json.loads(session["tool_receipts_json"])
    names = {item["tool_name"] for item in receipts}
    require(len(planning) >= 2, "missing_two_planning_rounds")
    require({"debug_list_diagnostic_documents", "debug_read_diagnostic_documents", "debug_search_knowledge", "debug_search_log", "debug_get_evidence"} <= names,
            "diagnostic_read_tool_loop_incomplete")
    analyses = rows(database, "SELECT * FROM analysis_runs WHERE case_id=? AND provider='host_cli'", (case_id,))
    require(len(analyses) == 1 and analyses[0]["status"] == "COMPLETED", "host_analysis_missing")
    diagnosis = json.loads(analyses[0]["result_json"])
    rendered = json.dumps(diagnosis).lower()
    require("undervoltage" in rendered or "brownout" in rendered, "causal_signal_missing")
    allowed = set(json.loads(session["allowed_evidence_ids_json"]))
    log_ids = {row["id"] for row in rows(database, "SELECT id FROM log_events WHERE case_id=?", (case_id,))}
    cited: set[str] = set()
    for fact in diagnosis.get("confirmed_facts", []):
        cited.update(fact.get("evidence_ids", []))
    for hypothesis in diagnosis.get("hypotheses", []):
        cited.update(hypothesis.get("supporting_evidence", []))
        cited.update(hypothesis.get("contradicting_evidence", []))
    require(cited and cited <= allowed and cited & log_ids, "diagnosis_citations_not_current_case")
    reports = rows(database, "SELECT * FROM reports WHERE analysis_run_id=?", (analyses[0]["id"],))
    require(reports, "diagnosis_report_missing")
    report_path = Path(reports[0]["stored_path"])
    if not report_path.is_absolute():
        report_path = database.parent / "storage" / report_path
    require(report_path.is_file() and hashlib.sha256(report_path.read_bytes()).hexdigest() == reports[0]["sha256"], "report_hash_mismatch")
    persisted = json.dumps(session, ensure_ascii=False)
    require(MODEL in persisted, "persisted_cli_model_claim_missing")
    return {"session_id": session["id"], "analysis_id": analyses[0]["id"], "report_id": reports[0]["id"],
            "planning_rounds": len(planning), "tool_receipts": sorted(names), "citation_count": len(cited)}


def run(codex: Path, output: Path, timeout: int) -> dict[str, Any]:
    require(codex.is_file(), "codex_executable_missing")
    require(ROOT.joinpath(".git").exists(), "workspace_not_repository")
    require(output.parent.name == "validation" and output.name.startswith("expert-cli-live-20260909-"), "unsafe_output_path")
    output.mkdir(parents=True, exist_ok=False)
    (output / "synthetic").mkdir()
    (output / "synthetic" / "SYNTHETIC_ROUTING.md").write_text(ROUTING_MARKDOWN, encoding="utf-8")
    (output / "synthetic" / "SYNTHETIC_AP.log").write_text(LOG, encoding="utf-8")
    summary: dict[str, Any] = {"status": "FAIL", "commit": os.popen("git rev-parse HEAD").read().strip(), "synthetic_only": True,
        "company_data_used": False, "requested_cli_model": MODEL, "global_cli_config_changed": False,
        "output": str(output), "backend_chat_request_count": None}
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    database = output / "server-data" / "gw_ap_debug.db"
    process: subprocess.Popen[bytes] | None = None
    server_log = None
    admin_token: str | None = None
    users: list[dict[str, Any]] = []
    started = time.monotonic()
    try:
        environment = source_environment(output, port)
        bootstrap = subprocess.run([str(ROOT / ".venv" / "Scripts" / "python.exe"), "-B", str(ROOT / "scripts" / "manage_users.py"),
            "create", "--username", "expert-cli-bootstrap", "--display-name", "Synthetic bootstrap administrator",
            "--role", "ADMIN", "--token-name", "expert-cli-live-bootstrap", "--expires-days", "1"], cwd=ROOT,
            env=environment, text=True, encoding="utf-8", errors="replace", capture_output=True, timeout=90, creationflags=FLAGS)
        require(bootstrap.returncode == 0, "individual_bootstrap_failed")
        admin_token = bootstrap.stdout.strip().splitlines()[-1]
        require(admin_token.startswith("gwdp_"), "individual_bootstrap_token_missing")
        server_log = (output / "source-server.log").open("wb")
        process = subprocess.Popen([str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"], cwd=ROOT / "backend",
            env=environment, stdout=server_log, stderr=server_log, creationflags=FLAGS)
        with httpx.Client(base_url=base_url, trust_env=False, timeout=90) as client:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                require(process.poll() is None, "isolated_source_server_exited")
                try:
                    if client.get("/api/v1/health/ready").json().get("ready"):
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.4)
            else:
                raise VerificationError("isolated_source_server_not_ready")
            summary["isolated_source_port"] = port
            expert = api(client, "/system/users", admin_token, payload={"username": "expert-cli-live", "display_name": "Synthetic Expert", "role": "EXPERT", "issue_token": True, "token_expires_days": 1})
            engineer = api(client, "/system/users", admin_token, payload={"username": "engineer-cli-live", "display_name": "Synthetic Engineer", "role": "ENGINEER", "issue_token": True, "token_expires_days": 1})
            users = [expert, engineer]
            method = api(client, "/knowledge", expert["raw_token"], payload={"title": "SYNTHETIC AP brownout SKILL", "content": METHOD,
                "source_type": "analysis_skill", "device_type": "AP", "trust_level": "HIGH"})
            adopted = api(client, f"/knowledge/{method['id']}/adopt-human-verified", expert["raw_token"], payload={"human_verified": True,
                "expected_lock_version": method["lock_version"], "content_sha256": hashlib.sha256(METHOD.encode()).hexdigest()})
            wait_job(client, expert["raw_token"], adopted["job"]["id"])
            case = api(client, "/cases", engineer["raw_token"], payload={"title": "SYNTHETIC engineer CLI brownout", "device_type": "AP",
                "description": "Synthetic-only expert CLI validation case", "model_egress_approved": False})
            artifact = stage_debug_artifact(output / "synthetic" / "SYNTHETIC_AP.log", output, base_url, engineer["raw_token"], case["id"])
            wait_job(client, engineer["raw_token"], artifact["parse_job"]["id"])
            require(rows(database, "SELECT count(*) AS n FROM log_events WHERE case_id=?", (case["id"],))[0]["n"] >= 5, "synthetic_log_not_parsed")

            document_ids = stage_markdown(output / "synthetic" / "SYNTHETIC_ROUTING.md", output, base_url, expert["raw_token"])
            denied = rpc(client, engineer["raw_token"], "debug_get_knowledge_routing_context", {"document_ids": document_ids, "consent_host_model_data": True})
            require(bool(denied.get("error") or denied.get("result", {}).get("isError")), "engineer_routing_permission_not_denied")

            engineer_prompt = f"""Perform a complete real synthetic GW/AP host diagnosis ONLY for case {case['id']}.
The user authorizes this exact synthetic case and its bounded data for this authenticated Codex CLI model, all necessary MCP diagnosis state changes, and HTML report generation. Use only debugplatform MCP tools; do not use shell, browsing, other services, or subagents. You are the sole reasoner and backend Chat must remain unused.

Call debug_status first and confirm host_cli execution. Then list cases, read this case context, begin one diagnosis with executor codex and client_model_claim exactly `{MODEL}`. Read every required method. Perform knowledge search, bounded log searches, and exact evidence reads. Submit at least two evidence-driven planning rounds with receipt call_id and accepted_arguments copied exactly from completed calls. Use the latest expected_version for each mutation. Test the voltage/reboot causal chain and the authentication/link alternatives. Finalize an evidence-grounded diagnosis and generate an HTML report. Do not stop at a plan. In the final answer state `selected_model={MODEL}` and give only case, session, analysis, report IDs and a concise synthetic cause."""
            summary["engineer_cli"] = run_cli(codex, output, base_url, engineer["raw_token"], "ENGINEER", engineer_prompt, timeout)
            summary["diagnosis"] = verify_diagnosis(database, case["id"])

            expert_prompt = f"""Perform real host-model Markdown routing only for staged document {document_ids[0]}.
The user authorizes this exact synthetic Markdown for the current authenticated Codex CLI model. Use only debugplatform MCP tools; do not use shell, browsing, other services, or subagents. Call debug_status first and verify host-model routing has backend Chat disabled. Call debug_get_knowledge_routing_context with consent_host_model_data=true. Read every required Markdown section page by page with debug_read_knowledge_sections. Select exactly one returned active leaf category. Apply one decision with exact lock version, content hash, every covered section ID, client_model_claim `{MODEL}`, and confirm_draft_update=true. Verify the response says draft_only and backend_chat_calls=0. Do not publish or review the document. In the final answer state `selected_model={MODEL}` and give the document ID, category, and DRAFT boundary."""
            summary["expert_cli"] = run_cli(codex, output, base_url, expert["raw_token"], "EXPERT", expert_prompt, timeout)
            routed = rows(database, "SELECT id,active,review_status,lock_version FROM knowledge_documents WHERE id=?", (document_ids[0],))
            require(len(routed) == 1 and not routed[0]["active"] and routed[0]["review_status"] == "DRAFT", "expert_routing_not_draft")
            summary["expert_routing"] = {"document_id": document_ids[0], "active": False, "review_status": "DRAFT", "engineer_mcp_denied": True}

            audit_rows = rows(database, "SELECT details_json FROM audit_events WHERE action='model.egress'")
            chat_calls = sum(1 for row in audit_rows if json.loads(row["details_json"]).get("task_type") == "chat")
            require(chat_calls == 0, "backend_chat_egress_detected")
            summary["backend_chat_request_count"] = 0
            summary["status"] = "PASS"
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        if server_log is not None:
            server_log.close()
        if users and database.is_file():
            # Tokens are revoked directly in the isolated database after the API process
            # stops, avoiding any reuse and retaining only hashed, revoked credentials.
            with closing(sqlite3.connect(database)) as db:
                user_ids = [item["user"]["id"] for item in users]
                if admin_token:
                    bootstrap_user = rows(database, "SELECT id FROM user_accounts WHERE username=?", ("expert-cli-bootstrap",))
                    user_ids.extend(row["id"] for row in bootstrap_user)
                marks = ",".join("?" for _ in user_ids)
                db.execute(f"UPDATE access_tokens SET revoked_at=CURRENT_TIMESTAMP WHERE user_id IN ({marks})", user_ids)
                db.commit()
            summary["synthetic_credentials_revoked"] = True
        if database.is_file():
            summary["persistent_database_reopened_read_only"] = bool(rows(database, "SELECT id FROM host_agent_sessions"))
        summary["duration_seconds"] = round(time.monotonic() - started, 2)
        # Never retain CLI stdout/stderr, prompts, bearer values, or full model replies.
        (output / "result.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--output", type=Path, default=ROOT / "artifacts" / "validation" / f"expert-cli-live-20260909-{uuid.uuid4().hex[:10]}")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    try:
        result = run(args.codex, args.output.resolve(), args.timeout)
    except Exception as error:
        result = {"status": "FAIL", "error_type": type(error).__name__, "error": str(error), "synthetic_only": True}
        args.output.resolve().mkdir(parents=True, exist_ok=True)
        (args.output.resolve() / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
