import yaml

from app.core.config import PROJECT_ROOT


def test_compose_declares_postgresql_qdrant_and_loopback_ports() -> None:
    compose_path = PROJECT_ROOT / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    assert {"postgres", "qdrant", "backend", "frontend"} <= set(services)
    assert services["backend"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert services["backend"]["environment"]["QDRANT_URL"].endswith("http://qdrant:6333}")
    assert services["backend"]["ports"][0].startswith("127.0.0.1:")
    assert services["frontend"]["ports"][0].startswith("127.0.0.1:")
    assert services["backend"]["env_file"][0]["required"] is False


def test_backend_image_contains_migration_configuration() -> None:
    dockerfile = (PROJECT_ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY pyproject.toml alembic.ini ./" in dockerfile
    assert "/api/v1/health/ready" in dockerfile


def test_ci_workflow_exists() -> None:
    workflow = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    assert workflow.is_file()
    content = workflow.read_text(encoding="utf-8")
    assert "windows-latest" in content
    assert "RUN_EXTERNAL_SERVICE_TESTS" in content
    assert "runtime_smoke.ps1" in content


def test_vscode_client_uses_secret_storage_and_accepts_extensionless_logs() -> None:
    extension_source = (
        PROJECT_ROOT / "vscode-extension" / "src" / "extension.ts"
    ).read_text(encoding="utf-8")
    manifest = (
        PROJECT_ROOT / "vscode-extension" / "package.json"
    ).read_text(encoding="utf-8")
    assert "context.secrets.store('gwap.apiKey'" in extension_source
    assert "All files (including extensionless logs)" in extension_source
    assert "'.env', '.env.*'" in extension_source
    assert "gwap.setCredential" in manifest
