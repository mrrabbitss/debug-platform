import json

from app.core.config import PROJECT_ROOT


def test_windows_startup_is_local_only_and_rejects_foreign_backend() -> None:
    script = (PROJECT_ROOT / "scripts" / "start_local.bat").read_text(encoding="utf-8")
    bootstrap = (PROJECT_ROOT / "scripts" / "bootstrap_local.bat").read_text(encoding="utf-8")

    assert "--host 127.0.0.1" in script
    assert "GW/AP Intelligent Debug Platform" in script
    assert "Port 8000 is occupied by another service" in script
    assert "existing backend will be reused" not in script
    assert "py -3 -c" in bootstrap
    assert "python -c" in bootstrap


def test_frontend_development_server_is_local_only() -> None:
    package = json.loads((PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    vite_config = (PROJECT_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")

    assert package["scripts"]["dev"] == "vite"
    assert "host: '127.0.0.1'" in vite_config


def test_embedding_job_polling_uses_backend_terminal_status() -> None:
    settings_view = (PROJECT_ROOT / "frontend" / "src" / "views" / "SettingsView.vue").read_text(
        encoding="utf-8"
    )

    assert "['COMPLETED', 'FAILED', 'CANCELLED']" in settings_view
    assert "['SUCCEEDED', 'FAILED']" not in settings_view


def test_windows_doctor_checks_versions_dependencies_ports_and_privacy() -> None:
    batch = (PROJECT_ROOT / "scripts" / "doctor_local.bat").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "scripts" / "doctor_local.ps1").read_text(encoding="utf-8")

    assert "-ExecutionPolicy Bypass" in batch
    assert "Python 3.11+" in script
    assert "Node.js 20.19+ or 22.12+" in script
    assert ".local_dependency_stamp" in script
    assert "127.0.0.1:8000" in script
    assert "127.0.0.1:5173" not in script  # The port is inspected without making an HTTP request.
    assert "API keys, database contents, and log contents were not read" in script


def test_runtime_smoke_uses_isolated_database_and_alternate_ports() -> None:
    batch = (PROJECT_ROOT / "scripts" / "runtime_smoke.bat").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "scripts" / "runtime_smoke.ps1").read_text(encoding="utf-8")

    assert "-ExecutionPolicy Bypass" in batch
    assert "gw-ap-runtime-smoke-" in script
    assert "sqlite:///" in script
    assert "VITE_BACKEND_PROXY" in script
    assert '$env:AUTH_MODE = "local"' in script
    assert "18000" in script and "15173" in script
    assert 'WindowStyle = "Hidden"' in script
    assert "Runtime smoke test" in script
    assert "Stop-Process" in script


def test_local_model_installer_uses_project_layout_and_hf_mirror() -> None:
    batch = (PROJECT_ROOT / "scripts" / "install_local_models.bat").read_text(encoding="utf-8")
    script = (PROJECT_ROOT / "scripts" / "install_local_models.ps1").read_text(encoding="utf-8")
    validator = (PROJECT_ROOT / "scripts" / "validate_local_models.py").read_text(
        encoding="utf-8"
    )
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "-ExecutionPolicy Bypass" in batch
    assert "https://hf-mirror.com" in script
    assert 'foreach ($folder in @("inference", "reranker", "embedding"))' in script
    assert "$env:HF_ENDPOINT = $normalizedMirror" in script
    assert "HF_HUB_DOWNLOAD_TIMEOUT" in script
    assert "& $hfCli download $Repository" in script
    assert "--local-dir $Destination" in script
    assert '"embedding\\bge-base-zh-v1.5"' in script
    assert '"reranker\\Qwen3-Reranker-0.6B"' in script
    assert script.index("$env:HF_ENDPOINT = $normalizedMirror") < script.index(
        'Invoke-HfDownload `\n            -Repository "BAAI/bge-base-zh-v1.5"'
    )
    assert "verify_local_models.py" in script
    assert "BAAI/bge-base-zh-v1.5" in validator
    assert "Qwen/Qwen3-Reranker-0.6B" in validator
    assert "/models/" in gitignore
