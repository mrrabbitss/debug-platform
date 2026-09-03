"""Windows launcher black-box checks, without a model account or a real CLI."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "scripts" / "start_codeagent.ps1"
POWERSHELL = shutil.which("powershell.exe")
pytestmark = pytest.mark.skipif(
    os.name != "nt" or not POWERSHELL, reason="Windows PowerShell and DPAPI launcher"
)


def _unused_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def _environment(tmp_path: Path) -> dict[str, str]:
    environment = os.environ.copy()
    for name in (
        "DEBUGPLATFORM_MCP_URL", "DEBUGPLATFORM_MCP_TOKEN", "DEBUGPLATFORM_API_BASE_URL",
        "MCP_BEARER_TOKEN", "MCP_PUBLIC_BASE_URL", "STATIC_FRONTEND_ROOT",
    ):
        environment.pop(name, None)
    environment.update(
        DATABASE_URL="sqlite:///" + (tmp_path / "isolated.db").as_posix(),
        DATA_ROOT=str(tmp_path / "data"),
        STORAGE_ROOT=str(tmp_path / "storage"),
        APP_ENV="test", AUTH_MODE="local", API_KEY="", AUTH_ALLOW_LEGACY_ADMIN="true",
        MCP_ENABLED="true", MCP_BEARER_TOKEN="", LLM_PROVIDER="mock",
        LLM_API_KEY="", LLM_BASE_URL="",
        EMBEDDING_PROVIDER="hashing", RERANKER_PROVIDER="disabled",
        GWAP_TEST_CLI_RECORD=str(tmp_path / "client-record.json"),
        ANTHROPIC_MODEL="launcher-test-preserve-model",
    )
    return environment


def _run(
    environment: dict[str, str], state: Path, *arguments: str, timeout: int = 150,
    launcher: Path = LAUNCHER, program_files: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
    ]
    if program_files is not None:
        # PS 5.1 resets ProgramFiles to the known folder during process startup.
        # Override only inside this isolated test process, not in production code.
        parameters: dict[str, object] = {"StateDirectory": str(state)}
        iterator = iter(arguments)
        for argument in iterator:
            key = argument.removeprefix("-")
            parameters[key] = True if key in {"DryRun", "Check", "Configure"} else next(iterator)
        environment = {
            **environment, "GWAP_TEST_PROGRAM_FILES": str(program_files),
            "GWAP_TEST_LAUNCH_PARAMETERS": json.dumps(parameters),
            "GWAP_TEST_LAUNCHER": str(launcher),
        }
        command += [
            "-Command",
            "$env:ProgramFiles = $env:GWAP_TEST_PROGRAM_FILES; $launchParameters = @{}; "
            "($env:GWAP_TEST_LAUNCH_PARAMETERS | ConvertFrom-Json).PSObject.Properties | "
            "ForEach-Object { $launchParameters[$_.Name] = $_.Value }; "
            "& $env:GWAP_TEST_LAUNCHER @launchParameters; exit $LASTEXITCODE",
        ]
    else:
        command += ["-File", str(launcher), "-StateDirectory", str(state), *arguments]
    return subprocess.run(
        command,
        cwd=state.parent, env=environment, capture_output=True,
        text=True, encoding="utf-8-sig", errors="replace", timeout=timeout, check=False,
    )


def _fake_cli(tmp_path: Path, extension: str = "ps1") -> Path:
    folder = tmp_path / "Program Files 中文 [tools]" / "CodeAgentCLI"
    folder.mkdir(parents=True)
    path = folder / "codeagent.ps1"
    path.write_text(
        """$ErrorActionPreference = 'Stop'
if ($args -contains '--help') {
    Write-Output '--mcp-config --strict-mcp-config --append-system-prompt'
    exit 0
}
if ($args -contains '--version') { Write-Output 'codeagent-test 1.0'; exit 0 }
$items = @($args)
$configIndex = [Array]::IndexOf($items, '--mcp-config')
if ($configIndex -lt 0) { throw 'No MCP config argument' }
$config = Get-Content -Raw -LiteralPath $items[$configIndex + 1] | ConvertFrom-Json
$token = $env:DEBUGPLATFORM_MCP_TOKEN
$sha = [Security.Cryptography.SHA256]::Create()
try {
    $digest = [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($token)))
} finally { $sha.Dispose() }
$record = [ordered]@{
    arguments = $items
    cwd = (Get-Location).Path
    mcp_config = $config
    token_present = -not [string]::IsNullOrWhiteSpace($token)
    token_hash = $digest
    model = $env:ANTHROPIC_MODEL
    mcp_url = $env:DEBUGPLATFORM_MCP_URL
}
[IO.File]::WriteAllText($env:GWAP_TEST_CLI_RECORD, ($record | ConvertTo-Json -Depth 12),
    [Text.UTF8Encoding]::new($false))
if ($env:GWAP_TEST_CLI_EXIT) { exit [int]$env:GWAP_TEST_CLI_EXIT }
exit 0
""",
        encoding="utf-8-sig",
    )
    if extension == "cmd":
        shim = folder / "codeagent.cmd"
        shim.write_bytes(
            b'@echo off\r\nif "%~1"=="--help" echo harmless CLI help warning 1>&2\r\n'
            b'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass '
            b'-File "%~dp0codeagent.ps1" %*\r\nexit /b %errorlevel%\r\n'
        )
        return shim
    return path


def _assert_ok(result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 0, result.stdout + result.stderr


def test_codeagent_dry_run_is_non_mutating(tmp_path: Path) -> None:
    state = tmp_path / "state not created"
    result = _run(
        _environment(tmp_path), state, "-DryRun", "-CliCommand", "missing-codeagent-test",
    )
    _assert_ok(result)
    assert not state.exists()
    assert not (tmp_path / "isolated.db").exists()
    assert not (tmp_path / "client-record.json").exists()


def test_codeagent_refuses_insecure_remote_url(tmp_path: Path) -> None:
    state = tmp_path / "state"
    result = _run(
        _environment(tmp_path), state,
        "-McpUrl", "http://192.0.2.1/mcp", "-DryRun",
    )
    assert result.returncode != 0
    assert "HTTPS" in result.stdout + result.stderr
    assert not state.exists()


def test_codeagent_native_stderr_warning_uses_exit_code(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    environment.update(
        GWAP_TEST_NATIVE_PYTHON=sys.executable,
        GWAP_TEST_NATIVE_SUPPORT=str(ROOT / "scripts" / "codeagent_launcher_support.ps1"),
        GWAP_TEST_NATIVE_LOG=str(tmp_path / "native-warning.log"),
        GWAP_TEST_NATIVE_SUCCESS="import sys; sys.stderr.write('harmless bootstrap warning'); print('completed')",
        GWAP_TEST_NATIVE_FAILURE="import sys; sys.stderr.write('expected failure'); sys.exit(17)",
    )
    result = subprocess.run(
        [
            str(POWERSHELL), "-NoLogo", "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-Command",
            "$ErrorActionPreference = 'Stop'; . $env:GWAP_TEST_NATIVE_SUPPORT; "
            "$ok = Invoke-LauncherNative -Path $env:GWAP_TEST_NATIVE_PYTHON "
            "-Arguments @('-c', $env:GWAP_TEST_NATIVE_SUCCESS) -LogPath $env:GWAP_TEST_NATIVE_LOG; "
            "$failed = Invoke-LauncherNative -Path $env:GWAP_TEST_NATIVE_PYTHON "
            "-Arguments @('-c', $env:GWAP_TEST_NATIVE_FAILURE) -LogPath $env:GWAP_TEST_NATIVE_LOG -Append; "
            "[ordered]@{ ok = $ok.exit_code; failed = $failed.exit_code; "
            "policy = [string]$ErrorActionPreference } | ConvertTo-Json -Compress",
        ],
        cwd=tmp_path, env=environment, capture_output=True,
        text=True, encoding="utf-8-sig", errors="replace", timeout=30, check=False,
    )
    _assert_ok(result)
    record = json.loads(result.stdout)
    assert record == {"ok": 0, "failed": 17, "policy": "Stop"}
    log = (tmp_path / "native-warning.log").read_text(encoding="utf-16")
    assert "harmless bootstrap warning" in log
    assert "completed" in log
    assert "expected failure" in log


def test_codeagent_check_uses_real_backend_without_cli(tmp_path: Path) -> None:
    state = tmp_path / "state"
    port = _unused_port()
    result = _run(
        _environment(tmp_path), state, "-Check",
        "-McpUrl", f"http://127.0.0.1:{port}/mcp",
    )
    _assert_ok(result)
    assert "host_cli" in result.stdout
    assert "backend_chat_calls" in result.stdout
    assert (tmp_path / "isolated.db").exists()
    assert not (tmp_path / "client-record.json").exists()
    assert not _port_open(port), "The launcher must stop only its owned backend on exit"


@pytest.mark.parametrize("entry,explicit_path", [("ps1", True), ("cmd", True), ("cmd", False)])
def test_codeagent_arbitrary_path_and_second_launch_reuse_configuration(
    tmp_path: Path, entry: str, explicit_path: bool,
) -> None:
    state = tmp_path / "启动配置 with spaces"
    environment = _environment(tmp_path)
    cli = _fake_cli(tmp_path, entry)
    port = _unused_port()
    url = f"http://127.0.0.1:{port}/mcp"
    cli_arguments = ["-CliCommand", str(cli)] if explicit_path else []
    first = _run(
        environment, state, *cli_arguments, "-McpUrl", url,
        program_files=cli.parent.parent if not explicit_path else None,
    )
    _assert_ok(first)
    record_path = tmp_path / "client-record.json"
    first_record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    assert first_record["token_present"]
    assert first_record["mcp_url"] == url
    assert first_record["model"] == "launcher-test-preserve-model"
    assert Path(first_record["cwd"]).resolve() == ROOT
    config = first_record["mcp_config"]["mcpServers"]["gw-ap-debug"]
    assert config["url"] == url
    assert config["headers"]["Authorization"] == "Bearer ${DEBUGPLATFORM_MCP_TOKEN}"
    assert "--strict-mcp-config" in first_record["arguments"]
    assert "--model" not in first_record["arguments"]
    assert "--dangerously-skip-permissions" not in first_record["arguments"]
    assert not _port_open(port)

    settings = json.loads((state / "config.json").read_text(encoding="utf-8-sig"))
    assert settings["mcp_url"] == url
    assert Path(settings["cli_command"]).resolve() == cli
    encrypted_token = (state / "token.dpapi").read_bytes()
    assert encrypted_token
    second = _run(environment, state)
    _assert_ok(second)
    second_record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    assert second_record["token_hash"] == first_record["token_hash"]
    assert second_record["mcp_url"] == url
    assert not _port_open(port)
    if entry == "ps1":
        new_port = _unused_port()
        changed = _run(environment, state, "-McpUrl", f"http://127.0.0.1:{new_port}/mcp")
        _assert_ok(changed)
        changed_record = json.loads(record_path.read_text(encoding="utf-8-sig"))
        assert changed_record["token_hash"] != first_record["token_hash"]
        assert not _port_open(new_port)


def test_codeagent_propagates_client_failure_and_cleans_owned_backend(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    environment["GWAP_TEST_CLI_EXIT"] = "23"
    port = _unused_port()
    result = _run(
        environment, tmp_path / "state", "-CliCommand", str(_fake_cli(tmp_path)),
        "-McpUrl", f"http://127.0.0.1:{port}/mcp",
    )
    assert result.returncode == 23, result.stdout + result.stderr
    assert not _port_open(port)


def test_codeagent_does_not_replace_foreign_port_owner(tmp_path: Path) -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        result = _run(
            _environment(tmp_path), tmp_path / "state", "-Check",
            "-McpUrl", f"http://127.0.0.1:{port}/mcp",
            "-BackendStartupTimeoutSeconds", "10", timeout=30,
        )
        assert result.returncode != 0
        assert listener.fileno() != -1
    assert not (tmp_path / "isolated.db").exists()


def test_codeagent_reuses_existing_backend_without_changing_auth(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    port = _unused_port()
    base = f"http://127.0.0.1:{port}"
    fixture_token = "synthetic-launcher-reuse-token"
    environment.update(
        AUTH_MODE="api_key", API_KEY=fixture_token,
        MCP_BEARER_TOKEN="", MCP_PUBLIC_BASE_URL=base,
        DEBUGPLATFORM_MCP_TOKEN=fixture_token,
    )
    with (tmp_path / "fixture-backend.log").open("w", encoding="utf-8") as log:
        server = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "app.main:app",
                "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=ROOT / "backend", env=environment, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                assert server.poll() is None, "Isolated fixture backend failed to start"
                try:
                    with opener.open(base + "/api/v1/health", timeout=1) as response:
                        if json.load(response).get("status") == "ok":
                            break
                except (OSError, ValueError):
                    time.sleep(0.2)
            else:
                pytest.fail("Isolated fixture backend did not become healthy")

            state = tmp_path / "existing-state"
            result = _run(environment, state, "-Check", "-McpUrl", base + "/mcp")
            _assert_ok(result)
            assert server.poll() is None, "Launcher killed a server it did not start"
            assert fixture_token not in result.stdout + result.stderr
            for path in state.rglob("*"):
                if path.is_file():
                    assert fixture_token.encode() not in path.read_bytes()

            relocated = tmp_path / "搬迁项目 with spaces"
            for relative in (
                "start_codeagent.bat", "scripts/start_codeagent.ps1",
                "scripts/codeagent_launcher_support.ps1", "scripts/codeagent_launcher_http.ps1",
            ):
                destination = relocated / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / relative, destination)
            shutil.copytree(ROOT / ".claude" / "skills", relocated / ".claude" / "skills")
            moved_result = _run(
                environment, tmp_path / "relocated-state", "-McpUrl", base + "/mcp",
                "-CliCommand", str(_fake_cli(tmp_path)),
                launcher=relocated / "scripts" / "start_codeagent.ps1",
            )
            _assert_ok(moved_result)
            moved_record = json.loads((tmp_path / "client-record.json").read_text(encoding="utf-8-sig"))
            assert Path(moved_record["cwd"]).resolve() == relocated
            prompt_index = moved_record["arguments"].index("--append-system-prompt") + 1
            assert str(relocated) in moved_record["arguments"][prompt_index]
            assert server.poll() is None

            environment["DEBUGPLATFORM_MCP_TOKEN"] = "synthetic-wrong-token"
            rejected = _run(
                environment, tmp_path / "bad-credentials", "-Check", "-McpUrl", base + "/mcp",
            )
            assert rejected.returncode != 0
            assert server.poll() is None
        finally:
            if server.poll() is None:
                server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
