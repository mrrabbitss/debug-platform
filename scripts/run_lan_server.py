"""Run the existing LAN server package interactively, without installing services."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def exclusive_runner(data):
    data.mkdir(parents=True, exist_ok=True)
    with (data / "script-runner.lock").open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("Server or backup is already running. Stop it first.") from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def stop(process, managed=False):
    if process.poll() is not None:
        return
    if managed:
        process.stdin.close()
    else:
        process.terminate()
    try:
        process.wait(timeout=45 if managed else 15)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)
        raise RuntimeError("Server shutdown timed out; inspect the log before restarting.")


def run(args):
    package, data = args.package.resolve(), args.data_root.resolve()
    python = package / "runtime/python/python.exe"
    if not python.is_file():
        raise RuntimeError(f"Server runtime not found: {package}")
    environment = {k: v for k, v in os.environ.items() if not k.startswith(
        ("MCP_", "BUNDLED_", "LLM_", "DATABASE_", "AUTH_", "SERVER_", "MODEL_"))}
    environment.update(PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    deployment_path = package / "deployment.json"
    deployment = json.loads(deployment_path.read_text(encoding="utf-8-sig")) if deployment_path.is_file() else {}
    if deployment.get("simple_engineer_login") is True:
        environment.update(SIMPLE_ENGINEER_LOGIN="true",
                           SIMPLE_LOGIN_CA_FILE=str(data / "gateway/pki/authorities/local/root.crt"))
    with exclusive_runner(data):
        logs = data / "logs"
        logs.mkdir(exist_ok=True)
        with (logs / "script-server.log").open("ab") as log:
            def admin(*arguments):
                subprocess.run([str(python), "-B", "-s", str(package / "server_admin.py"), *map(str, arguments)],
                    cwd=package, env=environment, stdout=log, stderr=log, check=True, timeout=300,
                    creationflags=subprocess.CREATE_NO_WINDOW)

            config_path = data / "config/server.json"
            if not config_path.is_file():
                if args.backup:
                    raise RuntimeError("No configured server to back up.")
                address = args.public_url or deployment.get("server_url") or input("Server HTTPS address (example https://192.168.1.100:8443): ").strip()
                admin("configure", "--public-url", address, "--data-root", data, "--backend-port", args.backend_port)
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if args.public_url and args.public_url.rstrip("/") != config["public_url"].rstrip("/"):
                raise RuntimeError("The address differs from saved configuration. Existing settings were not replaced.")
            for host, port in (("127.0.0.1", config["backend_port"]),
                               (config["gateway_bind"], urlsplit(config["public_url"]).port or 443)):
                with socket.socket() as probe:
                    probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                    probe.bind((host, port))
            if args.backup:
                archive = data / "backups" / f"server-{datetime.now():%Y%m%d-%H%M%S-%f}.zip"
                archive.parent.mkdir(exist_ok=True)
                admin("backup", "--config", config_path, "--archive", archive)
                print(f"Backup verified: {archive}", flush=True)
                return
            database = data / "data/gw_ap_debug.db"
            needs_admin = not database.exists()
            if database.exists():
                connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                try:
                    needs_admin = connection.execute("SELECT count(*) FROM user_accounts").fetchone()[0] == 0
                finally:
                    connection.close()
            if needs_admin:
                admin("initialize-admin", "--config", config_path)
            processes = []
            try:
                for command in (
                    [str(python), "-B", "-s", str(package / "portable_launcher.py"), "--server-config", str(config_path),
                     "--managed-stdin", "--no-browser"],
                    [str(package / "server-runtime/caddy.exe"), "run", "--config", str(data / "config/caddy.json")],
                ):
                    processes.append(subprocess.Popen(command, cwd=package, env=environment,
                        stdin=subprocess.PIPE, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW))
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                ready, started = False, time.monotonic()
                while all(p.poll() is None for p in processes):
                    if not ready:
                        try:
                            with opener.open(f"http://127.0.0.1:{config['backend_port']}/api/v1/health/ready", timeout=2) as response:
                                ready = bool(json.load(response).get("ready"))
                        except OSError:
                            pass
                        if ready:
                            share = data / "client-files"
                            share.mkdir(exist_ok=True)
                            (share / "server-address.txt").write_text(config["public_url"] + "\n", encoding="utf-8")
                            certificate = data / "gateway/pki/authorities/local/root.crt"
                            if config["tls_mode"] == "internal":
                                shutil.copyfile(certificate, share / "root.crt")
                                digest = hashlib.sha256(certificate.read_bytes()).hexdigest()
                                (share / "root.crt.sha256").write_text(digest + "\n", encoding="ascii")
                            print(f"READY: {config['public_url']}\nData: {data}\nClient files: {share}\n"
                                  f"Initial admin token: {data / 'config/bootstrap-token.txt'}\n"
                                  "Keep this window open. Press Ctrl+C to stop.", flush=True)
                    if not ready and time.monotonic() - started > 420:
                        raise RuntimeError("Startup timed out. Inspect logs/script-server.log.")
                    if ready and args.run_seconds and time.monotonic() - started >= args.run_seconds:
                        return
                    time.sleep(0.5)
                raise RuntimeError("A server process exited. Inspect logs/script-server.log.")
            except KeyboardInterrupt:
                print("Stopping server...", flush=True)
            finally:
                try:
                    if processes:
                        stop(processes[0], managed=True)
                finally:
                    for process in processes[1:]:
                        stop(process)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=ROOT / "artifacts/lan/server-pilot-20260907")
    parser.add_argument("--data-root", type=Path, default=ROOT / "artifacts/lan/server-data")
    parser.add_argument("--public-url")
    parser.add_argument("--backend-port", type=int, default=18080)
    parser.add_argument("--backup", action="store_true")
    parser.add_argument("--run-seconds", type=int, default=0, help=argparse.SUPPRESS)
    try:
        run(parser.parse_args())
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"[ERROR] {error}")
        raise SystemExit(1)
