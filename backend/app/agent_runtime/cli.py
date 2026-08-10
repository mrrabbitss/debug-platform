from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from app.agent_runtime.client import RuntimeClient, RuntimeClientError
from app.core.config import PROJECT_ROOT, get_settings


def emit(value: Any, human: bool = False) -> None:
    if human and isinstance(value, dict):
        for key, item in value.items():
            print(f"{key}: {item}")
        return
    print(json.dumps(value, ensure_ascii=False, indent=2 if human else None, default=str))


def _runtime_data_root() -> Path:
    explicit = os.environ.get("DATA_ROOT_PATH")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        return (Path(os.environ["LOCALAPPDATA"]) / "GWAPDebug" / "data").resolve()
    return get_settings().data_root


def runtime_pid_file() -> Path:
    path = _runtime_data_root() / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path / "agent-runtime.pid"


def runtime_log_file() -> Path:
    data_root = _runtime_data_root()
    path = data_root.parent / "logs" if data_root.name.lower() == "data" else data_root / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path / "agent-runtime.log"


def start_runtime(args: argparse.Namespace) -> dict[str, Any]:
    client = RuntimeClient(args.runtime_url)
    try:
        status = client.status()
        return {"started": False, "reason": "already_running", **status}
    except RuntimeClientError:
        pass
    settings = get_settings()
    env = os.environ.copy()
    env.setdefault("AGENT_MODE", "external")
    env.setdefault("AGENT_RUNTIME_PORT", str(args.port or settings.agent_runtime_port))
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        runtime_root = Path(os.environ["LOCALAPPDATA"]) / "GWAPDebug"
        data_root = runtime_root / "data"
        storage_root = runtime_root / "storage"
        data_root.mkdir(parents=True, exist_ok=True)
        storage_root.mkdir(parents=True, exist_ok=True)
        env.setdefault("DATA_ROOT_PATH", str(data_root))
        env.setdefault("DATABASE_URL", f"sqlite:///{(data_root / 'gw_ap_debug.db').as_posix()}")
        env.setdefault("STORAGE_ROOT", str(storage_root))
        env.setdefault("SERVE_FRONTEND", "true")
        env.setdefault("FRONTEND_DIST", str(PROJECT_ROOT / "frontend" / "dist"))
        env.setdefault("MODEL_ROOTS", str(PROJECT_ROOT / "models"))
        env.setdefault(
            "CORS_ORIGINS",
            f"http://127.0.0.1:{env['AGENT_RUNTIME_PORT']},http://localhost:{env['AGENT_RUNTIME_PORT']}",
        )
    port = int(env["AGENT_RUNTIME_PORT"])
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        settings.agent_runtime_host,
        "--port",
        str(port),
    ]
    log_path = runtime_log_file()
    with log_path.open("ab") as log:
        kwargs: dict[str, Any] = {
            "cwd": str(PROJECT_ROOT),
            "env": env,
            "stdout": log,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        else:
            kwargs["start_new_session"] = True
        process = subprocess.Popen(cmd, **kwargs)
    runtime_pid_file().write_text(str(process.pid), encoding="ascii")
    target = RuntimeClient(f"http://{settings.agent_runtime_host}:{port}")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            return {"started": True, "pid": process.pid, "log": str(log_path), **target.status()}
        except RuntimeClientError:
            if process.poll() is not None:
                break
            time.sleep(0.25)
    raise RuntimeError(f"Runtime did not become healthy; inspect {log_path}")


def stop_runtime() -> dict[str, Any]:
    pid_file = runtime_pid_file()
    if not pid_file.is_file():
        return {"stopped": False, "reason": "pid_file_missing"}
    pid = int(pid_file.read_text(encoding="ascii").strip())
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False, capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    pid_file.unlink(missing_ok=True)
    return {"stopped": True, "pid": pid}


def add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--runtime-url", default=os.environ.get("GWAP_RUNTIME_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--human", action="store_true")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gwap", description="GW/AP Debug Agent Runtime CLI")
    add_common(p)
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("start")
    s.add_argument("--port", type=int, default=None)
    sub.add_parser("stop")
    sub.add_parser("status")
    sub.add_parser("doctor")
    o = sub.add_parser("open")
    o.add_argument("--case-id")

    models = sub.add_parser("models").add_subparsers(dest="models_command", required=True)
    models.add_parser("list")
    ms = models.add_parser("scan")
    ms.add_argument("--no-llm", action="store_true")
    mv = models.add_parser("validate")
    mv.add_argument("candidate_id")
    mv.add_argument("--device", default="cpu")
    ma = models.add_parser("activate")
    ma.add_argument("candidate_id")
    ma.add_argument("--device", default="cpu")
    ma.add_argument("--force", action="store_true")

    c = sub.add_parser("case-create")
    c.add_argument("title")
    c.add_argument("--device-type", default="GW", choices=["GW", "AP", "OTHER"])
    c.add_argument("--description", default="")
    i = sub.add_parser("ingest")
    i.add_argument("case_id")
    i.add_argument("file")
    i.add_argument("--no-wait", action="store_true")
    i.add_argument("--timeout", type=int, default=900)
    j = sub.add_parser("job-wait")
    j.add_argument("job_id")
    j.add_argument("--timeout", type=int, default=900)
    e = sub.add_parser("evidence")
    e.add_argument("case_id")
    e.add_argument("--query", default="")
    e.add_argument("--top-k", type=int, default=12)
    e.add_argument("--max-hops", type=int, default=2)
    q = sub.add_parser("search")
    q.add_argument("case_id")
    q.add_argument("query")
    q.add_argument("--top-k", type=int, default=12)
    q.add_argument("--max-hops", type=int, default=2)
    w = sub.add_parser("workspace-attach")
    w.add_argument("case_id")
    w.add_argument("path")
    w.add_argument("--name")
    w.add_argument("--no-index", action="store_true")
    w.add_argument("--no-wait", action="store_true")
    w.add_argument("--timeout", type=int, default=1200)
    d = sub.add_parser("diagnose")
    d.add_argument("case_id")
    d.add_argument("--no-wait", action="store_true")
    d.add_argument("--timeout", type=int, default=1200)
    r = sub.add_parser("report")
    r.add_argument("case_id")
    r.add_argument("--format", default="html", choices=["html", "pdf", "docx"])
    return p


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "start":
            result = start_runtime(args)
        elif args.command == "stop":
            result = stop_runtime()
        else:
            client = RuntimeClient(args.runtime_url)
            if args.command == "status":
                result = client.status()
            elif args.command == "doctor":
                status = client.status()
                skill = Path.home() / ".claude" / "skills" / "gw-ap-debug" / "SKILL.md"
                result = {
                    "runtime": status,
                    "python": sys.version.split()[0],
                    "skill_installed": skill.is_file(),
                    "skill_path": str(skill),
                    "model_roots": [str(x) for x in get_settings().model_root_paths],
                }
            elif args.command == "open":
                result = client.open_ui(args.case_id)
            elif args.command == "models":
                if args.models_command == "list":
                    result = client.get("/system/local-models")
                elif args.models_command == "scan":
                    result = client.post(
                        "/system/local-models/scan", params={"use_llm": str(not args.no_llm).lower()}, json={}
                    )
                elif args.models_command == "validate":
                    result = client.post(
                        f"/system/local-models/{args.candidate_id}/validate", json={"device": args.device}
                    )
                else:
                    result = client.post(
                        f"/system/local-models/{args.candidate_id}/activate",
                        json={"device": args.device, "force": args.force},
                    )
            elif args.command == "case-create":
                result = client.create_case(
                    {"title": args.title, "device_type": args.device_type, "description": args.description}
                )
            elif args.command == "ingest":
                result = client.ingest(args.case_id, args.file)
                if not args.no_wait:
                    result["parse_result"] = client.wait_job(result["job"]["id"], args.timeout)
            elif args.command == "job-wait":
                result = client.wait_job(args.job_id, args.timeout)
            elif args.command == "evidence":
                result = client.evidence_bundle(
                    args.case_id,
                    {
                        "query": args.query or None,
                        "top_k": args.top_k,
                        "max_hops": args.max_hops,
                        "modules": None,
                    },
                )
            elif args.command == "search":
                result = client.search(
                    args.case_id,
                    {"query": args.query, "top_k": args.top_k, "max_hops": args.max_hops, "modules": None},
                )
            elif args.command == "workspace-attach":
                result = {"workspace": client.attach_workspace(args.case_id, args.path, args.name)}
                if not args.no_index:
                    job = client.index_workspace(result["workspace"]["id"])
                    result["index_job"] = job
                    if not args.no_wait:
                        result["index_result"] = client.wait_job(job["job_id"], args.timeout)
            elif args.command == "diagnose":
                result = {"job": client.diagnose(args.case_id)}
                if not args.no_wait:
                    result["job_result"] = client.wait_job(result["job"]["id"], args.timeout)
                    result["analysis"] = client.latest_analysis(args.case_id)
            elif args.command == "report":
                result = client.generate_report(args.case_id, args.format)
            else:
                raise RuntimeError("Unknown command")
        emit(result, args.human)
    except (RuntimeClientError, RuntimeError, ValueError, OSError) as exc:
        print(
            json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
