from __future__ import annotations

import concurrent.futures
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "deploy" / "windows-portable" / "portable_mcp.py"
)
_ENV_NAMES = {
    "auth_mode", "api_key", "mcp_bearer_token", "mcp_enabled", "debugplatform_mcp_token",
}


@pytest.fixture
def portable_mcp(monkeypatch):
    for name in tuple(os.environ):
        if name.casefold() in _ENV_NAMES:
            monkeypatch.delenv(name)
    # Track the module's direct setenv so pytest restores the caller environment.
    monkeypatch.setenv("MCP_BEARER_TOKEN", "")
    monkeypatch.delenv("MCP_BEARER_TOKEN")
    spec = importlib.util.spec_from_file_location("portable_mcp_test", _MODULE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configured_local_key_is_shared_without_changing_user_settings(
    portable_mcp, tmp_path: Path, monkeypatch, capsys,
) -> None:
    env_path = tmp_path / ".env"
    original = (
        'AUTH_MODE=local\nAPI_KEY="fixture-api-key" # comment\n'
        'MCP_BEARER_TOKEN="different-mcp-key"\nHTTP_PROXY=http://proxy.invalid:8080\n'
        'LLM_MODEL=keep-my-model\n'
    )
    env_path.write_text(original, encoding="utf-8")
    monkeypatch.setenv("HTTPS_PROXY", "http://process-proxy.invalid:8080")
    before = dict(os.environ)
    assert portable_mcp.prepare_mcp_environment(tmp_path / "data", env_path) == "fixture-api-key"
    assert dict(os.environ) == before
    assert env_path.read_text(encoding="utf-8") == original
    assert not (tmp_path / "data").exists()
    assert capsys.readouterr() == ("", "")


def test_process_settings_override_dotenv_and_keep_explicit_token(
    portable_mcp, tmp_path: Path, monkeypatch,
) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("AUTH_MODE=rbac\nAPI_KEY=file-key\nMCP_BEARER_TOKEN=file-token\n")
    monkeypatch.setenv("AUTH_MODE", "local")
    monkeypatch.setenv("API_KEY", "")
    monkeypatch.setenv("MCP_BEARER_TOKEN", "process-token")
    before = dict(os.environ)
    assert portable_mcp.prepare_mcp_environment(tmp_path / "data", env_path) == "process-token"
    assert dict(os.environ) == before
    assert not (tmp_path / "data").exists()


def test_dotenv_case_and_quoted_value_match_settings(portable_mcp, tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("auth_mode=local\napi_key=\nmcp_bearer_token='file-token # kept'\n")
    assert portable_mcp.prepare_mcp_environment(tmp_path / "data", env_path) == "file-token # kept"
    assert os.environ["MCP_BEARER_TOKEN"] == "file-token # kept"
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("mode, api_key, personal, expected", [
    ("rbac", "legacy-admin", "", None),
    ("rbac", "legacy-admin", "personal-client-token", "personal-client-token"),
    ("api_key", "explicit-api-key", "", "explicit-api-key"),
    ("api_key", "", "", None),
    ("invalid-mode", "", "", None),
])
def test_other_modes_never_generate_or_borrow_static_admin_tokens(
    portable_mcp, tmp_path: Path, monkeypatch, mode, api_key, personal, expected,
) -> None:
    monkeypatch.setenv("AUTH_MODE", mode)
    monkeypatch.setenv("API_KEY", api_key)
    monkeypatch.setenv("MCP_BEARER_TOKEN", "existing-static-admin")
    monkeypatch.setenv("DEBUGPLATFORM_MCP_TOKEN", personal)
    before = dict(os.environ)
    assert portable_mcp.prepare_mcp_environment(tmp_path / "data", tmp_path / ".env") == expected
    assert dict(os.environ) == before
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("disabled", ["false", "False", "0", "off", "NO"])
def test_disabled_mcp_never_generates_token(portable_mcp, tmp_path: Path, monkeypatch, disabled):
    monkeypatch.setenv("MCP_ENABLED", disabled)
    before = dict(os.environ)
    assert portable_mcp.prepare_mcp_environment(tmp_path / "data", tmp_path / ".env") is None
    assert dict(os.environ) == before
    assert not (tmp_path / "data").exists()


@pytest.mark.skipif(os.name != "nt", reason="Real Windows CurrentUser DPAPI contract")
def test_dpapi_real_roundtrip_reuse_and_no_plaintext_changes(
    portable_mcp, tmp_path: Path, monkeypatch, capsys,
) -> None:
    data_root = tmp_path / "中文 path [state]" / "data"
    env_path = tmp_path / ".env"
    original = b"AUTH_MODE=local\nAPI_KEY=\nMCP_BEARER_TOKEN=\nLLM_MODEL=keep-my-model\n"
    env_path.write_bytes(original)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "existing.internal")
    before = dict(os.environ)
    first = portable_mcp.prepare_mcp_environment(data_root, env_path)
    assert first and len(first) == 64
    assert os.environ["MCP_BEARER_TOKEN"] == first
    stored = data_root / ".launcher" / "mcp-token.dpapi"
    encrypted = stored.read_bytes()
    assert first.encode() not in encrypted
    assert b"token" not in encrypted
    assert json.loads(portable_mcp._dpapi(encrypted, decrypt=True)) == {
        "schema_version": 1, "token": first,
    }
    monkeypatch.delenv("MCP_BEARER_TOKEN")
    assert portable_mcp.prepare_mcp_environment(data_root, env_path) == first
    assert stored.read_bytes() == encrypted
    assert dict(os.environ) == {**before, "MCP_BEARER_TOKEN": first}
    assert env_path.read_bytes() == original
    assert list(stored.parent.iterdir()) == [stored]
    assert capsys.readouterr() == ("", "")


@pytest.mark.skipif(os.name != "nt", reason="Real Windows CurrentUser DPAPI contract")
def test_invalid_saved_dpapi_is_not_silently_replaced(portable_mcp, tmp_path: Path) -> None:
    stored = tmp_path / "data" / ".launcher" / "mcp-token.dpapi"
    stored.parent.mkdir(parents=True)
    stored.write_bytes(b"not-a-valid-dpapi-credential")
    with pytest.raises(portable_mcp.PortableMCPError, match="Windows account"):
        portable_mcp.prepare_mcp_environment(tmp_path / "data", tmp_path / ".env")
    assert stored.read_bytes() == b"not-a-valid-dpapi-credential"
    assert "MCP_BEARER_TOKEN" not in os.environ


@pytest.mark.skipif(os.name != "nt", reason="Real Windows cross-process mutex and DPAPI")
def test_concurrent_web_and_cli_startup_reuse_one_atomic_credential(
    portable_mcp, tmp_path: Path,
) -> None:
    data_root = tmp_path / "concurrent"
    source = (
        "import hashlib, importlib.util, pathlib, sys; "
        "spec=importlib.util.spec_from_file_location('portable_mcp',sys.argv[1]); "
        "module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
        "token=module.prepare_mcp_environment(pathlib.Path(sys.argv[2]),pathlib.Path(sys.argv[3])); "
        "print(hashlib.sha256(token.encode()).hexdigest())"
    )

    def launch(_index):
        result = subprocess.run(
            [sys.executable, "-B", "-c", source, str(_MODULE_PATH), str(data_root), str(tmp_path / ".env")],
            check=False, capture_output=True, text=True, timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        assert result.returncode == 0, result.stderr
        assert not result.stderr
        return result.stdout.strip()

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        hashes = list(pool.map(launch, range(4)))
    token = portable_mcp.prepare_mcp_environment(data_root, tmp_path / ".env")
    assert hashes == [hashlib.sha256(token.encode()).hexdigest()] * 4
    assert len(list((data_root / ".launcher").iterdir())) == 1
