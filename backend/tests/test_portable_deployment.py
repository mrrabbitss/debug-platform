import importlib.util
import os
import sys
import threading
import types
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import PROJECT_ROOT
from app.services.static_frontend import mount_static_frontend


def _load_portable_launcher():
    path = PROJECT_ROOT / "deploy" / "windows-portable" / "portable_launcher.py"
    spec = importlib.util.spec_from_file_location("portable_launcher_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_static_frontend_serves_assets_and_vue_routes(tmp_path: Path) -> None:
    frontend = tmp_path / "web"
    assets = frontend / "assets"
    assets.mkdir(parents=True)
    (frontend / "index.html").write_text(
        '<!doctype html><div id="app"></div>', encoding="utf-8"
    )
    (assets / "app.js").write_text("console.log('portable')", encoding="utf-8")

    app = FastAPI()

    @app.get("/api/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok"}

    mount_static_frontend(app, frontend)
    with TestClient(app) as client:
        assert client.get("/").status_code == 200
        assert 'id="app"' in client.get("/cases/CASE-portable").text
        assert "portable" in client.get("/assets/app.js").text
        assert client.get("/assets/missing.js").status_code == 404
        assert client.get("/api/ping").json() == {"status": "ok"}


def test_static_frontend_fails_fast_when_build_is_incomplete(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="index.html"):
        mount_static_frontend(FastAPI(), tmp_path)


def test_portable_build_excludes_native_model_runtime_and_has_real_smoke() -> None:
    build = (PROJECT_ROOT / "scripts" / "build_windows_portable.ps1").read_text(
        encoding="utf-8"
    )
    verify = (PROJECT_ROOT / "scripts" / "verify_windows_portable.ps1").read_text(
        encoding="utf-8"
    )
    launcher = (
        PROJECT_ROOT / "deploy" / "windows-portable" / "portable_launcher.py"
    ).read_text(encoding="utf-8")
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "windows-portable.yml"
    ).read_text(encoding="utf-8")

    assert 'find_spec(\'torch\') is None' in build
    assert 'find_spec(\'sentence_transformers\') is None' in build
    assert "backend[local-models]" not in build
    assert "--target" in build and "Lib\\site-packages" in build
    assert "._pth" in build and '"import site"' in build
    assert '"-B"' in build and '"-s"' in build
    assert "source_dirty" in build
    assert "agent-skills\\gw-ap-debug" in build
    assert "install_agent_skill_mcp.ps1" in build
    assert "install_agent_skill_mcp.bat" in build
    assert "agent_skill_bundled = $true" in build
    assert "mcp_installer_bundled = $true" in build
    assert 'file_hash.ps1' in build and "Get-Sha256Hex" in build
    assert "Get-FileHash" not in build
    assert "STATIC_FRONTEND_ROOT" in launcher
    assert "DEBUG_PLATFORM_ENV_FILE" in launcher
    assert "MODEL_DISABLE_IN_PROCESS_LOCAL" in launcher
    assert "MCP_PUBLIC_BASE_URL" in launcher
    assert "verify_package_manifest" in launcher
    assert "sys.flags.isolated" in launcher
    assert "package-manifest.json" in build
    assert "/api/v1/health/ready" in verify
    assert "/cases/portable-smoke-route" in verify
    assert "agent-skills\\gw-ap-debug\\SKILL.md" in verify
    assert "upload-knowledge-markdown.ps1" in verify
    assert "install_agent_skill_mcp.ps1" in verify
    assert "install_agent_skill_mcp.bat" in verify
    assert "Packaged Skill/MCP installer dry-run failed" in verify
    assert "Packaged Markdown knowledge helper dry-run failed" in verify
    assert "WindowStyle Hidden" in verify
    assert '-B -s "portable_launcher.py"' in verify
    assert "workflow_dispatch" in workflow
    assert 'tags:' in workflow and '"v*"' in workflow


def test_portable_launcher_uses_the_actual_listening_port_for_mcp(monkeypatch) -> None:
    launcher = _load_portable_launcher()
    monkeypatch.setenv("MCP_PUBLIC_BASE_URL", "http://127.0.0.1:8000")

    public_base_url = launcher.configure_server_environment("127.0.0.1", 18080)

    assert public_base_url == "http://127.0.0.1:18080"
    assert os.environ["MCP_PUBLIC_BASE_URL"] == public_base_url
    assert os.environ["CORS_ORIGINS"] == (
        "http://127.0.0.1:18080,http://localhost:18080"
    )


def test_removed_model_installer_cannot_pollute_platform_environment() -> None:
    removed = (
        "install_local_models.bat",
        "install_local_models.ps1",
        "hf_model_tools.ps1",
        "check_hf_model_access.bat",
        "check_hf_model_access.ps1",
        "validate_local_models.py",
        "verify_local_models.py",
    )
    for name in removed:
        assert not (PROJECT_ROOT / "scripts" / name).exists()

    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "/A.py" in gitignore
    assert "/*.rar" in gitignore


@pytest.mark.skipif(os.name != "nt", reason="Win11 native pipe and CRT contract")
@pytest.mark.parametrize("signal", [b"stop\n", b""])
def test_managed_pipe_allows_stdio_inspection_and_handles_stop_or_eof(signal):
    launcher = _load_portable_launcher()
    read_fd, write_fd = os.pipe()
    server = types.SimpleNamespace(should_exit=False)
    thread = threading.Thread(target=launcher._watch_parent_pipe, args=(server, read_fd), daemon=True)
    thread.start()
    try:
        # DLL startup may inspect stdin while the watcher is active. No blocking
        # read/CRT descriptor lock is allowed here.
        assert not os.isatty(read_fd)
        if signal:
            os.write(write_fd, signal)
        os.close(write_fd)
        write_fd = -1
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert server.should_exit is True
    finally:
        server.should_exit = True
        if write_fd >= 0:
            os.close(write_fd)
        thread.join(timeout=5)
        os.close(read_fd)
