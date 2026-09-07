"""Validate a real Windows package with a synthetic CLI and no Chat model calls.

The optional GGUF processes are real. All CLI settings, database contents and
logs are isolated in a temporary directory; only a content-free JSON result is
retained in artifacts. This is not a real CodeAgent inference acceptance test.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
FAKE_CLI = r"""$ErrorActionPreference = 'Stop'
if ($args -contains '--help') {
    Write-Output '--mcp-config --append-system-prompt'
    exit 0
}
$stage = 'arguments'
try {
    $items = @($args)
    if ($items.Count -ne 4 -or $items[0] -ne '--mcp-config' -or
            $items[2] -ne '--append-system-prompt' -or $items -contains '--strict-mcp-config') {
        throw 'Unexpected arguments'
    }
    $root = $env:GWAP_VERIFY_PACKAGE
    $skill = Join-Path $root 'agent-skills\gw-ap-debug\SKILL.md'
    if (-not $items[3].Contains($skill) -or -not [IO.File]::Exists($skill)) {
        throw 'Wrong package Skill'
    }
    if ((Get-Location).Path -ne $env:GWAP_VERIFY_WORKSPACE) { throw 'Wrong client working directory' }
    [IO.File]::WriteAllText((Join-Path (Get-Location).Path 'fixture-report.txt'),
        'Synthetic CLI report; no device or model content.', [Text.UTF8Encoding]::new($false))
    $config = [IO.File]::ReadAllText($items[1]) | ConvertFrom-Json
    if (@($config.mcpServers.PSObject.Properties).Count -ne 1 -or
            $config.mcpServers.'gw-ap-debug'.headers.Authorization -cne 'Bearer ${DEBUGPLATFORM_MCP_TOKEN}') {
        throw 'Wrong session configuration'
    }
    $stage = 'environment'
    $expected = $env:GWAP_VERIFY_EXPECTED_ENV | ConvertFrom-Json
    foreach ($property in $expected.PSObject.Properties) {
        if ([Environment]::GetEnvironmentVariable($property.Name, 'Process') -cne $property.Value) {
            throw 'Client environment changed'
        }
    }
    $stage = 'mcp'
    . (Join-Path $root 'scripts\codeagent_launcher_http.ps1')
    $uri = [Uri]$env:DEBUGPLATFORM_MCP_URL
    $status = Test-LauncherConnection $uri $env:DEBUGPLATFORM_MCP_TOKEN
    $stage = 'profiles'
    $headers = @{ Authorization = 'Bearer ' + $env:DEBUGPLATFORM_MCP_TOKEN }
    $response = Invoke-LauncherHttp GET ($uri.GetLeftPart([UriPartial]::Authority) + '/api/v1/system/models') $headers
    if ($response.status -ne 200) { throw 'Cannot read model profiles' }
    $profiles = $response.body | ConvertFrom-Json
    $active = @($profiles | Where-Object { $_.is_active -and $_.task_type -in @('embedding', 'reranker') })
    $sanitizedProfiles = @($active | ForEach-Object {
        [ordered]@{ task_type = $_.task_type; provider = $_.provider; enabled = [bool]$_.enabled }
    })
    $sidecarPorts = @($active | Where-Object { $_.provider -eq 'llama_cpp_local' } | ForEach-Object {
        ([Uri]$_.base_url).Port
    })
    $tokenBytes = [Text.Encoding]::UTF8.GetBytes($env:DEBUGPLATFORM_MCP_TOKEN)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $tokenHash = [BitConverter]::ToString($sha.ComputeHash($tokenBytes)) }
    finally { $sha.Dispose() }
    $record = [ordered]@{
        ok = $true; current_package_skill = $true; additive_mcp = $true
        client_environment_preserved = $true; session_file = $items[1]
        token_hash = $tokenHash; backend_chat_calls = $status.backend_chat_calls
        inference_owner = $status.inference_owner; backend_chat_allowed = $status.backend_chat_allowed
        active_retrieval_profiles = $sanitizedProfiles; sidecar_ports = $sidecarPorts
    }
    [IO.File]::WriteAllText($env:GWAP_VERIFY_RECORD, ($record | ConvertTo-Json -Depth 8),
        [Text.UTF8Encoding]::new($false))
    exit [int]$env:GWAP_VERIFY_EXIT
} catch {
    [IO.File]::WriteAllText($env:GWAP_VERIFY_RECORD,
        (@{ ok = $false; failed_stage = $stage } | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Write-Output '[ERROR] Synthetic CLI validation failed; see the sanitized verification result.'
    exit 93
}
"""


class VerificationError(RuntimeError):
    """Static reason code only, never a response body, token or local path."""


def require(condition: object, reason: str) -> None:
    if not condition:
        raise VerificationError(reason)


def open_port(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.4)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def wait_closed(ports: list[int], timeout: int = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(open_port(port) for port in ports):
            return
        time.sleep(0.2)
    raise VerificationError("owned_backend_or_sidecar_port_remained_open")


def wait_ready(process: subprocess.Popen, port: int, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        require(process.poll() is None, "web_backend_exited_before_readiness")
        try:
            with OPENER.open(f"http://127.0.0.1:{port}/api/v1/health/ready", timeout=2) as response:
                if response.status == 200 and json.load(response).get("ready") is True:
                    return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.4)
    raise VerificationError("web_readiness_timeout")


def load_job_class(package: Path):
    spec = importlib.util.spec_from_file_location("gwap_package_verify", package / "portable_launcher.py")
    require(spec is not None and spec.loader is not None, "package_launcher_missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.WindowsKillOnCloseJob


def supervise(job, process: subprocess.Popen) -> None:
    if not job.assign(process.pid):
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise VerificationError("cannot_supervise_test_process")


def fixture_environment(temporary: Path, package: Path) -> tuple[dict[str, str], dict[Path, bytes]]:
    environment = dict(os.environ)
    # Never let an existing platform/model credential or profile affect this fixture.
    for key in list(environment):
        if key.upper().startswith(("DEBUGPLATFORM_", "BUNDLED_GGUF_", "LLM_", "MCP_", "ANTHROPIC_")):
            environment.pop(key)
    config_directory = temporary / "synthetic client config"
    config_directory.mkdir()
    files = {
        config_directory / "settings.json": b'{"model":"fixture-model","theme":"dark"}',
        config_directory / "mcp.json": b'{"mcpServers":{"fixture-existing":{"command":"fixture-mcp"}}}',
    }
    for path, content in files.items():
        path.write_bytes(content)
    expected = {
        "CLAUDE_CONFIG_DIR": str(config_directory), "ANTHROPIC_MODEL": "synthetic-preserved-model",
        "ANTHROPIC_BASE_URL": "https://synthetic-model.example.invalid",
        "HTTP_PROXY": "http://127.0.0.1:19997", "HTTPS_PROXY": "http://127.0.0.1:19997",
        "ALL_PROXY": "http://127.0.0.1:19997", "NO_PROXY": "synthetic-company.example.invalid",
    }
    environment.update(expected)
    environment.update(
        APP_ENV="test", AUTH_MODE="local", API_KEY="", AUTH_ALLOW_LEGACY_ADMIN="true",
        MCP_ENABLED="true", MCP_BEARER_TOKEN="", LLM_PROVIDER="mock", LLM_API_KEY="",
        LLM_BASE_URL="", EMBEDDING_PROVIDER="hashing", RERANKER_PROVIDER="disabled",
        GWAP_VERIFY_PACKAGE=str(package), GWAP_VERIFY_EXPECTED_ENV=json.dumps(expected),
        PYTHONUTF8="1", PYTHONUNBUFFERED="1",
    )
    return environment, files


def run_client(
    package: Path, fixture: Path, environment: dict[str, str], data: Path, env_file: Path,
    port: int, no_local_retrieval: bool, timeout: int, job, *, exit_code: int, name: str,
) -> dict:
    record_path = fixture.parent / f"{name}-record.json"
    environment = {**environment, "GWAP_VERIFY_RECORD": str(record_path), "GWAP_VERIFY_EXIT": str(exit_code)}
    command = [
        str(package / "runtime/python/python.exe"), "-B", "-s", str(package / "portable_codeagent.py"),
        "--cli-command", str(fixture), "--port", str(port), "--data-root", str(data),
        "--env-file", str(env_file),
    ]
    if no_local_retrieval:
        command.append("--no-local-retrieval")
    with (fixture.parent / f"{name}-output.log").open("wb") as output:
        process = subprocess.Popen(
            command, cwd=package, env=environment, stdin=subprocess.DEVNULL, stdout=output,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        supervise(job, process)
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise VerificationError("packaged_client_timeout") from exc
    if record_path.is_file():
        record = json.loads(record_path.read_text(encoding="utf-8-sig"))
        require(record.get("ok"), "fake_cli_" + str(record.get("failed_stage", "failed")))
    else:
        raise VerificationError("packaged_client_did_not_run_fixture")
    require(code == exit_code, "client_exit_code_not_preserved")
    require(record.get("backend_chat_calls") == 0, "backend_chat_calls_not_zero")
    require(record.get("backend_chat_allowed") is False, "backend_chat_was_allowed")
    require(record.get("inference_owner") == "host_cli", "wrong_inference_owner")
    require(not Path(record["session_file"]).exists(), "temporary_mcp_session_remained")
    profiles = record["active_retrieval_profiles"]
    actual = {item["task_type"]: item["provider"] for item in profiles}
    expected = {"embedding": "hashing", "reranker": "disabled"} if no_local_retrieval else {
        "embedding": "llama_cpp_local", "reranker": "llama_cpp_local",
    }
    require(actual == expected, "unexpected_active_retrieval_profiles")
    if not no_local_retrieval:
        require(all(item["enabled"] for item in profiles), "managed_retrieval_profile_disabled")
        status = json.loads((data / "logs/local-models/status.json").read_text(encoding="utf-8"))
        require(
            {item["task_type"] for item in status["components"] if item["status"] == "ready"}
            == {"embedding", "reranker"}, "gguf_components_not_both_ready",
        )
    return record


def clean_temporary_files(data: Path) -> None:
    require(not list((data / ".launcher/codeagent/sessions").glob("*.json")), "session_json_leaked")
    require(not list((data / "logs/local-models").glob("api-key-*.txt")), "ephemeral_model_key_leaked")


def diagnose_startup(args: argparse.Namespace, results: list[dict]) -> None:
    """Compare Core startup with/without synthetic proxies, using stack snapshots."""
    require(os.name == "nt", "windows_required")
    package = args.package_root.expanduser().resolve()
    with tempfile.TemporaryDirectory(prefix="gwap-core-startup-diagnostic-") as temporary_name:
        temporary = Path(temporary_name)
        environment, _config = fixture_environment(temporary, package)
        special_stdin = args.raw_stdin_diagnostic or args.winapi_stdin_diagnostic or args.peek_stdin_diagnostic
        for proxy_enabled in ((True,) if special_stdin else (True, False)):
            case = temporary / ("synthetic-proxy" if proxy_enabled else "without-proxy")
            case.mkdir()
            current_environment = dict(environment)
            if not proxy_enabled:
                for name in list(current_environment):
                    if name.casefold() in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}:
                        current_environment.pop(name)
            data, env_file = case / "data", case / "platform.env"
            env_file.write_text("APP_ENV=test\nAUTH_MODE=local\nMCP_ENABLED=true\nLLM_PROVIDER=mock\n", encoding="utf-8")
            port = unused_port()
            bootstrap = (
                "import faulthandler,os,runpy,sys\n"
                "faulthandler.dump_traceback_later(15,repeat=True)\n"
            )
            if args.raw_stdin_diagnostic:
                bootstrap += (
                    "class RawManagedStdin:\n"
                    " def __init__(self, original):\n"
                    "  self.original=original; self.fd=original.fileno(); self.buffer=self\n"
                    " def readline(self): return os.read(self.fd,1)\n"
                    " def __getattr__(self,name): return getattr(self.original,name)\n"
                    "sys.stdin=RawManagedStdin(sys.stdin)\n"
                )
            if args.winapi_stdin_diagnostic:
                bootstrap += (
                    "import ctypes,msvcrt\n"
                    "from ctypes import wintypes\n"
                    "kernel=ctypes.WinDLL('kernel32',use_last_error=True)\n"
                    "kernel.ReadFile.argtypes=[wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),wintypes.LPVOID]\n"
                    "kernel.ReadFile.restype=wintypes.BOOL\n"
                    "class WinapiManagedStdin:\n"
                    " def __init__(self,original):\n"
                    "  self.original=original; self.handle=msvcrt.get_osfhandle(original.fileno()); self.buffer=self\n"
                    " def readline(self):\n"
                    "  buffer=ctypes.create_string_buffer(1); count=wintypes.DWORD()\n"
                    "  ok=kernel.ReadFile(self.handle,buffer,1,ctypes.byref(count),None)\n"
                    "  if not ok and ctypes.get_last_error() not in (109,232): raise OSError('Managed pipe read failed')\n"
                    "  return buffer.raw[:count.value]\n"
                    " def __getattr__(self,name): return getattr(self.original,name)\n"
                    "sys.stdin=WinapiManagedStdin(sys.stdin)\n"
                )
            if args.peek_stdin_diagnostic:
                bootstrap += (
                    "import ctypes,msvcrt,time\n"
                    "from ctypes import wintypes\n"
                    "kernel=ctypes.WinDLL('kernel32',use_last_error=True)\n"
                    "kernel.PeekNamedPipe.argtypes=[wintypes.HANDLE,wintypes.LPVOID,wintypes.DWORD,ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD),ctypes.POINTER(wintypes.DWORD)]\n"
                    "kernel.PeekNamedPipe.restype=wintypes.BOOL\n"
                    "class PeekManagedStdin:\n"
                    " def __init__(self,original):\n"
                    "  self.original=original; self.handle=msvcrt.get_osfhandle(original.fileno()); self.buffer=self\n"
                    " def readline(self):\n"
                    "  while True:\n"
                    "   count=wintypes.DWORD()\n"
                    "   ok=kernel.PeekNamedPipe(self.handle,None,0,None,ctypes.byref(count),None)\n"
                    "   if not ok and ctypes.get_last_error() in (109,232): return b''\n"
                    "   if not ok: raise OSError('Managed pipe probe failed')\n"
                    "   if count.value: return b'x'\n"
                    "   time.sleep(0.1)\n"
                    " def __getattr__(self,name): return getattr(self.original,name)\n"
                    "sys.stdin=PeekManagedStdin(sys.stdin)\n"
                )
            bootstrap += "entry=sys.argv.pop(1); runpy.run_path(entry,run_name='__main__')"
            command = [
                str(package / "runtime/python/python.exe"), "-B", "-s", "-u", "-c", bootstrap,
                str(package / "portable_launcher.py"), "--no-browser", "--managed-stdin",
                "--no-local-retrieval", "--port", str(port), "--data-root", str(data), "--env-file", str(env_file),
            ]
            job = load_job_class(package)()
            record = {
                "scenario": "core_startup_diagnostic", "synthetic_proxy": proxy_enabled,
                "raw_stdin_read": args.raw_stdin_diagnostic,
                "winapi_stdin_read": args.winapi_stdin_diagnostic,
                "peek_stdin_read": args.peek_stdin_diagnostic,
            }
            started = time.monotonic()
            with (case / "diagnostic.log").open("wb") as output:
                process = subprocess.Popen(
                    command, cwd=package, env=current_environment, stdin=subprocess.PIPE,
                    stdout=output, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
                )
                supervise(job, process)
                try:
                    wait_ready(process, port, min(args.timeout, 45))
                    record["ready"] = True
                except VerificationError as exc:
                    record.update(ready=False, error_code=str(exc))
                finally:
                    if process.poll() is None:
                        try:
                            process.stdin.write(b"stop\n")
                            process.stdin.close()
                            process.wait(timeout=20 if record.get("ready") else 5)
                        except (OSError, subprocess.TimeoutExpired):
                            pass
                    record["graceful_exit"] = process.poll() == 0
                    job.close()
                    process.wait(timeout=10)
            content = (case / "diagnostic.log").read_text(encoding="utf-8", errors="replace")
            record["startup_waiting"] = "Waiting for application startup" in content
            record["startup_complete"] = "Application startup complete" in content
            sections = re.split(r"(?m)(?=Thread 0x|Current thread 0x|Timeout \()", content)
            record["thread_stack_tops"] = [
                [
                    {"file": Path(filename).name, "line": int(line), "function": function.strip()}
                    for filename, line, function in re.findall(r'File "([^"\n]+)", line (\d+) in ([^\n]+)', section)[:10]
                ]
                for section in sections if re.search(r'File "', section)
            ][-12:]
            record["exception_types"] = re.findall(r"^([\w.]+(?:Error|Exception)):", content, re.MULTILINE)
            record["seconds"] = round(time.monotonic() - started, 2)
            record["port_closed"] = not open_port(port)
            results.append(record)
            print(json.dumps(record, ensure_ascii=True), flush=True)


def verify(args: argparse.Namespace, results: list[dict]) -> None:
    require(os.name == "nt", "windows_required")
    package = args.package_root.expanduser().resolve()
    for relative in ("portable_codeagent.py", "portable_launcher.py", "runtime/python/python.exe"):
        require((package / relative).is_file(), "incomplete_package")
    with tempfile.TemporaryDirectory(prefix="gwap-packaged-codeagent-") as temporary_name:
        temporary = Path(temporary_name)
        environment, config_files = fixture_environment(temporary, package)
        fixture = temporary / "synthetic codeagent 中文.ps1"
        fixture.write_text(FAKE_CLI, encoding="utf-8-sig")
        data, env_file = temporary / "data", temporary / "platform.env"
        environment["GWAP_VERIFY_WORKSPACE"] = str(data / "workspace")
        env_file.write_text("APP_ENV=test\nAUTH_MODE=local\nMCP_ENABLED=true\nLLM_PROVIDER=mock\n", encoding="utf-8")
        job = load_job_class(package)()
        web = None
        web_log = None
        try:
            port = unused_port()
            print("[VERIFY] Starting packaged CLI-owned backend scenario.", flush=True)
            started = time.monotonic()
            first = run_client(
                package, fixture, environment, data, env_file, port, args.no_local_retrieval,
                args.timeout, job, exit_code=23, name="cli-owned",
            )
            wait_closed([port, *first["sidecar_ports"]])
            clean_temporary_files(data)
            credential = data / ".launcher/mcp-token.dpapi"
            require(credential.is_file(), "shared_dpapi_credential_missing")
            saved_credential = credential.read_bytes()
            results.append({
                "scenario": "cli_owned_backend", "ok": True, "exit_code": 23,
                "backend_chat_calls": 0, "profiles": first["active_retrieval_profiles"],
                "owned_backend_and_sidecars_stopped": True, "temporary_files_cleaned": True,
                "seconds": round(time.monotonic() - started, 2),
            })
            print("[VERIFY] CLI-owned backend passed; starting Web-first reuse scenario.", flush=True)
            started = time.monotonic()
            command = [
                str(package / "runtime/python/python.exe"), "-B", "-s", str(package / "portable_launcher.py"),
                "--no-browser", "--managed-stdin", "--port", str(port), "--data-root", str(data),
                "--env-file", str(env_file),
            ]
            if args.no_local_retrieval:
                command.append("--no-local-retrieval")
            web_log = (temporary / "web-output.log").open("wb")
            web = subprocess.Popen(
                command, cwd=package, env=environment, stdin=subprocess.PIPE, stdout=web_log,
                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            supervise(job, web)
            wait_ready(web, port, args.timeout)
            second = run_client(
                package, fixture, environment, data, env_file, port, args.no_local_retrieval,
                args.timeout, job, exit_code=0, name="web-reused",
            )
            require(web.poll() is None and open_port(port), "cli_stopped_existing_web_backend")
            require(credential.read_bytes() == saved_credential, "shared_dpapi_credential_replaced")
            require(first["token_hash"] == second["token_hash"], "web_cli_did_not_share_token")
            require(all(path.read_bytes() == content for path, content in config_files.items()), "user_fixture_config_changed")
            require(not (package / ".venv").exists(), "package_created_source_venv")
            require((data / "workspace/fixture-report.txt").is_file(), "cli_report_not_in_external_workspace")
            require(not (package / "fixture-report.txt").exists(), "cli_report_polluted_package")
            web.stdin.write(b"stop\n")
            web.stdin.close()
            require(web.wait(timeout=35) == 0, "managed_web_exit_failed")
            wait_closed([port, *second["sidecar_ports"]])
            clean_temporary_files(data)
            results.append({
                "scenario": "web_first_cli_reuse", "ok": True, "exit_code": 0,
                "backend_chat_calls": 0, "profiles": second["active_retrieval_profiles"],
                "existing_web_preserved": True, "dpapi_token_reused": True,
                "synthetic_user_settings_unchanged": True, "client_environment_preserved": True,
                "cli_report_in_external_workspace": True,
                "managed_web_and_sidecars_stopped": True, "temporary_files_cleaned": True,
                "seconds": round(time.monotonic() - started, 2),
            })
        finally:
            if web is not None and web.poll() is None:
                try:
                    if web.stdin is not None and not web.stdin.closed:
                        web.stdin.write(b"stop\n")
                        web.stdin.close()
                    web.wait(timeout=25)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            job.close()
            if web is not None:
                try:
                    web.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
            if web_log is not None:
                web_log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--no-local-retrieval", action="store_true", help="Core smoke without GGUF loading")
    parser.add_argument("--diagnose-startup", action="store_true", help="Core-only proxy comparison with content-free stack snapshots")
    parser.add_argument("--raw-stdin-diagnostic", action="store_true", help="Diagnose with raw os.read instead of the buffered stdin reader")
    parser.add_argument("--winapi-stdin-diagnostic", action="store_true", help="Diagnose with WinAPI ReadFile instead of CRT stdin reads")
    parser.add_argument("--peek-stdin-diagnostic", action="store_true", help="Diagnose with nonblocking WinAPI PeekNamedPipe")
    parser.add_argument("--timeout", type=int, default=720)
    parser.add_argument("--output-directory", type=Path)
    args = parser.parse_args()
    require(args.timeout >= 30, "timeout_must_be_at_least_30_seconds")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = args.output_directory or ROOT / "artifacts/validation" / f"{timestamp}-packaged-codeagent"
    output.mkdir(parents=True, exist_ok=True)
    manifest = args.package_root.expanduser().resolve() / "package-manifest.json"
    summary = {
        "schema_version": 1, "started_at_utc": timestamp,
        "mode": "core" if args.no_local_retrieval else "real_gguf",
        "fake_cli": True, "real_codeagent_inference_tested": False, "chat_model_calls": 0,
        "scenarios": [],
        "package_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest.is_file() else None,
    }
    started = time.monotonic()
    try:
        if args.diagnose_startup:
            diagnose_startup(args, summary["scenarios"])
            summary["diagnostic_completed"] = True
            summary["ok"] = all(
                item.get("ready") and item.get("graceful_exit") and item.get("port_closed")
                for item in summary["scenarios"]
            )
        else:
            verify(args, summary["scenarios"])
            summary["ok"] = True
    except Exception as exc:
        summary["ok"] = False
        summary["error_code"] = str(exc) if isinstance(exc, VerificationError) else type(exc).__name__
    summary["seconds"] = round(time.monotonic() - started, 2)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
