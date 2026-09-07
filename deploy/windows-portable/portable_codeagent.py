# ruff: noqa: E402 -- disable bytecode before loading the bundled runtime
"""Run an installed CodeAgent against the self-contained Web/MCP package."""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

import argparse
import importlib.util
import json
import os
import subprocess
import time
import urllib.error
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent


def load_launcher():
    spec = importlib.util.spec_from_file_location(
        "gwap_portable_runtime", PACKAGE_ROOT / "portable_launcher.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("The portable launcher is missing.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def parse_args(argv: list[str] | None, launcher) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Start CodeAgent plus the bundled backend and optional GGUF models."
    )
    parser.add_argument("--cli-command", "-CliCommand", default="")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--data-root", type=Path, default=launcher.DEFAULT_DATA_ROOT)
    parser.add_argument("--env-file", type=Path, default=launcher.DEFAULT_ENV_PATH)
    parser.add_argument("--check", "-Check", action="store_true")
    parser.add_argument("--configure", "-Configure", action="store_true")
    parser.add_argument("--dry-run", "-DryRun", action="store_true")
    parser.add_argument("--no-local-retrieval", action="store_true")
    parser.add_argument("--model-start-timeout", type=int, default=240)
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 10 <= args.model_start_timeout <= 1800:
        parser.error("--model-start-timeout must be between 10 and 1800")
    return args


def powershell_arguments(args: argparse.Namespace, state_root: Path) -> list[str]:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    command = [
        str(system_root / "System32/WindowsPowerShell/v1.0/powershell.exe"),
        "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", str(PACKAGE_ROOT / "scripts/start_codeagent.ps1"),
        "-ConnectOnly", "-StateDirectory", str(state_root),
        "-WorkingDirectory", str(args.data_root.expanduser().resolve() / "workspace"),
        "-McpUrl", f"http://127.0.0.1:{args.port}/mcp",
    ]
    if args.cli_command:
        command += ["-CliCommand", args.cli_command]
    if args.check:
        command += ["-Check"]
    elif args.configure:
        command += ["-Configure"]
    return command


def backend_arguments(args: argparse.Namespace) -> list[str]:
    command = [
        str(PACKAGE_ROOT / "runtime/python/python.exe"), "-B", "-s", "-u",
        str(PACKAGE_ROOT / "portable_launcher.py"), "--no-browser", "--managed-stdin",
        "--port", str(args.port), "--data-root", str(args.data_root.expanduser().resolve()),
        "--env-file", str(args.env_file.expanduser().resolve()),
        "--model-start-timeout", str(args.model_start_timeout),
    ]
    if args.no_local_retrieval:
        command.append("--no-local-retrieval")
    return command


def wait_until_ready(launcher, process, port: int, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{port}/api/v1/health/ready"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise launcher.PortableLayoutError(
                "The bundled backend stopped before readiness; see the launcher log."
            )
        try:
            with launcher._LOOPBACK_HTTP_OPENER.open(url, timeout=2) as response:
                if response.status == 200 and json.load(response).get("ready") is True:
                    return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.4)
    raise launcher.PortableLayoutError("The bundled backend readiness check timed out.")


def stop_owned_backend(process, job) -> None:
    # The job contains only the backend created here, never an existing listener
    # or CodeAgent itself. Its descendants include the owned GGUF sidecars.
    if process is not None and process.poll() is None:
        try:
            if process.stdin is not None:
                process.stdin.write(b"stop\n")
                process.stdin.close()
            process.wait(timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            pass
    job.close()
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def main(argv: list[str] | None = None) -> int:
    launcher = load_launcher()
    args = parse_args(argv, launcher)
    state_root = args.data_root.expanduser().resolve() / ".launcher" / "codeagent"
    if args.dry_run:
        print(json.dumps({
            "dry_run": True, "package_root": str(PACKAGE_ROOT),
            "mcp_url": f"http://127.0.0.1:{args.port}/mcp",
            "state_directory": str(state_root), "cli_command": args.cli_command,
            "mcp_mode": "additive", "changes_user_cli_config": False,
            "requires_system_python_or_node": False, "writes_files": False,
            "starts_backend": False,
        }, ensure_ascii=True, indent=2))
        return 0

    owned = None
    log_stream = None
    job = launcher.WindowsKillOnCloseJob()
    try:
        launcher.validate_layout()
        launcher.verify_package_manifest()
        data_root, env_path = launcher.ensure_local_environment(args.data_root, args.env_file)
        sys.path.insert(0, str(PACKAGE_ROOT))
        from portable_mcp import prepare_mcp_environment

        token = prepare_mcp_environment(data_root, env_path)
        client_environment = dict(os.environ)
        if token and not client_environment.get("DEBUGPLATFORM_MCP_TOKEN"):
            client_environment["DEBUGPLATFORM_MCP_TOKEN"] = token
        state_root.mkdir(parents=True, exist_ok=True)
        workspace = data_root / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            launcher.check_port_available("127.0.0.1", args.port)
            port_available = True
        except launcher.PortableLayoutError:
            port_available = False
        if port_available:
            log_path = state_root / f"backend-{os.getpid()}.log"
            log_stream = log_path.open("wb")
            owned = subprocess.Popen(
                backend_arguments(args), cwd=str(PACKAGE_ROOT),
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                stdin=subprocess.PIPE, stdout=log_stream, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if not job.assign(owned.pid):
                raise launcher.PortableLayoutError(
                    "Windows could not supervise the backend process tree; startup stopped."
                )
            print(f"[INFO] Starting the bundled backend and retrieval models. Log: {log_path}")
            wait_until_ready(launcher, owned, args.port, 2 * args.model_start_timeout + 90)
        else:
            print("[INFO] Checking the existing service; it will be left running on exit.")
        print(f"[INFO] Web: http://127.0.0.1:{args.port}/")
        # This shared entry checks REST identity/auth AND MCP before starting the
        # client. ConnectOnly forbids falling back to source pip/venv bootstrap.
        result = subprocess.run(
            powershell_arguments(args, state_root), cwd=str(workspace),
            env=client_environment, check=False,
        )
        return result.returncode
    except (OSError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        stop_owned_backend(owned, job)
        if owned is not None and owned.poll() is not None:
            # A forced stop during model loading cannot execute the child's
            # finally block. Remove only this owned process's ephemeral key.
            key_path = (
                args.data_root.expanduser().resolve() / "logs" / "local-models"
                / f"api-key-{owned.pid}.txt"
            )
            try:
                key_path.unlink(missing_ok=True)
            except OSError:
                print("[WARN] Could not remove the stopped backend's temporary model key.")
        if log_stream is not None:
            log_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
