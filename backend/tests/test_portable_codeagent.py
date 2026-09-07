"""Package orchestration without a real model account or CLI installation."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "portable_codeagent_test", ROOT / "deploy/windows-portable/portable_codeagent.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_portable_dry_run_does_not_touch_disk_or_start_processes(tmp_path, monkeypatch, capsys):
    client = _load()
    monkeypatch.setattr(subprocess, "Popen", Mock(side_effect=AssertionError("process")))
    state = tmp_path / "never-created"
    assert client.main(["-DryRun", "--data-root", str(state)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mcp_mode"] == "additive"
    assert payload["requires_system_python_or_node"] is False
    assert not state.exists()


def test_portable_arguments_use_only_bundled_python_and_connect_only(tmp_path):
    client = _load()
    launcher = client.load_launcher()
    args = client.parse_args([
        "--cli-command", str(tmp_path / "中文 [app]" / "codeagent.cmd"),
        "--port", "18081", "--data-root", str(tmp_path / "data"),
        "--env-file", str(tmp_path / "settings.env"), "--no-local-retrieval", "-Check",
    ], launcher)
    backend = client.backend_arguments(args)
    assert backend[0].endswith("runtime\\python\\python.exe") if os.name == "nt" else (
        backend[0].endswith("runtime/python/python.exe")
    )
    assert "--no-browser" in backend and "--no-local-retrieval" in backend
    assert "-u" in backend  # isolated Python ignores PYTHONUNBUFFERED
    command = client.powershell_arguments(args, tmp_path / "state")
    assert "-ConnectOnly" in command and "-Check" in command
    assert command[command.index("-WorkingDirectory") + 1] == str((tmp_path / "data/workspace").resolve())
    assert "-CliCommand" in command and args.cli_command in command
    assert "http://127.0.0.1:18081/mcp" in command
    assert "--strict-mcp-config" not in command


@pytest.fixture
def orchestration(tmp_path, monkeypatch):
    client = _load()
    launcher = client.load_launcher()
    launcher.validate_layout = Mock()
    launcher.verify_package_manifest = Mock()
    launcher.ensure_local_environment = Mock(return_value=(tmp_path / "data", tmp_path / ".env"))
    launcher.check_port_available = Mock()
    job = Mock()
    job.assign.return_value = True
    launcher.WindowsKillOnCloseJob = Mock(return_value=job)
    monkeypatch.setattr(client, "load_launcher", lambda: launcher)
    monkeypatch.setattr(client, "wait_until_ready", Mock())
    token_module = types.ModuleType("portable_mcp")
    token_module.prepare_mcp_environment = Mock(return_value="synthetic-local-token")
    monkeypatch.setitem(sys.modules, "portable_mcp", token_module)
    process = Mock()
    process.pid = 17891
    process.poll.return_value = None
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(subprocess, "run", Mock(return_value=types.SimpleNamespace(returncode=23)))
    monkeypatch.delenv("DEBUGPLATFORM_MCP_TOKEN", raising=False)
    return client, launcher, job, process, ["--data-root", str(tmp_path / "data")]


def test_owned_backend_cleanup_and_cli_exit_are_preserved(orchestration):
    client, launcher, job, process, args = orchestration
    assert client.main(args) == 23
    launcher.verify_package_manifest.assert_called_once()
    client.wait_until_ready.assert_called_once()
    job.assign.assert_called_once_with(process.pid)
    job.close.assert_called_once()
    process.terminate.assert_called_once()
    assert process.wait.call_count == 2
    process.stdin.write.assert_called_once_with(b"stop\n")
    command = subprocess.run.call_args.args[0]
    assert "-ConnectOnly" in command
    assert "--strict-mcp-config" not in command
    assert subprocess.run.call_args.kwargs["env"]["DEBUGPLATFORM_MCP_TOKEN"] == "synthetic-local-token"


def test_existing_backend_is_reused_and_user_cli_environment_is_not_overridden(
    orchestration, monkeypatch,
):
    client, launcher, job, process, args = orchestration
    launcher.check_port_available.side_effect = launcher.PortableLayoutError("busy")
    values = {
        "HTTP_PROXY": "http://localhost:19999", "HTTPS_PROXY": "http://localhost:19999",
        "NO_PROXY": "example.invalid", "CLAUDE_CONFIG_DIR": r"C:\synthetic-user\.cac",
        "ANTHROPIC_MODEL": "keep-user-model", "DEBUGPLATFORM_MCP_TOKEN": "explicit-token",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    assert client.main(args) == 23
    subprocess.Popen.assert_not_called()
    client.wait_until_ready.assert_not_called()
    process.terminate.assert_not_called()
    inherited = subprocess.run.call_args.kwargs["env"]
    assert {key: inherited[key] for key in values} == values
    assert {key: os.environ[key] for key in values} == values
    job.close.assert_called_once()


def test_readiness_failure_never_starts_cli_and_cleans_own_backend(orchestration):
    client, launcher, job, process, args = orchestration
    client.wait_until_ready.side_effect = launcher.PortableLayoutError("not ready")
    assert client.main(args) == 1
    subprocess.run.assert_not_called()
    job.close.assert_called_once()
    process.terminate.assert_called_once()


def test_job_attachment_failure_fails_closed_without_starting_cli(orchestration):
    client, launcher, job, process, args = orchestration
    job.assign.return_value = False
    assert client.main(args) == 1
    subprocess.run.assert_not_called()
    process.terminate.assert_called_once()


def test_package_build_includes_both_no_dependency_entrypoints():
    build = (ROOT / "scripts/build_windows_portable.ps1").read_text(encoding="utf-8-sig")
    for name in (
        "portable_codeagent.py", "portable_mcp.py", "start_codeagent.bat",
        "start_codeagent.ps1", "codeagent_launcher_support.ps1", "codeagent_launcher_http.ps1",
    ):
        assert name in build
    bat = (ROOT / "deploy/windows-portable/start_codeagent.bat").read_text(encoding="utf-8")
    assert '"runtime\\python\\python.exe" -B -s "portable_codeagent.py" %*' in bat
    assert "pip" not in bat


def test_parent_and_backend_expand_user_paths_identically():
    client = _load()
    args = client.parse_args([
        "--data-root", "~/gwap-synthetic-data", "--env-file", "~/gwap-synthetic.env",
    ], client.load_launcher())
    command = client.backend_arguments(args)
    assert command[command.index("--data-root") + 1] == str(args.data_root.expanduser().resolve())
    assert command[command.index("--env-file") + 1] == str(args.env_file.expanduser().resolve())


def test_cleanup_removes_only_exited_owned_backend_key(orchestration):
    client, launcher, job, process, args = orchestration
    key_root = Path(args[1]) / "logs" / "local-models"
    key_root.mkdir(parents=True)
    own_key = key_root / f"api-key-{process.pid}.txt"
    other_key = key_root / "api-key-99999.txt"
    own_key.write_text("synthetic-owned", encoding="utf-8")
    other_key.write_text("synthetic-other", encoding="utf-8")
    process.poll.return_value = 0
    assert client.main(args) == 23
    assert not own_key.exists()
    assert other_key.read_text(encoding="utf-8") == "synthetic-other"
