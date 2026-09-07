from __future__ import annotations

import importlib.util
import sys
import json
import subprocess

import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, Settings
from app.services.client_capabilities import client_capabilities


def load_config_module():
    path = PROJECT_ROOT / "deploy/windows-portable/portable_server_config.py"
    spec = importlib.util.spec_from_file_location("portable_server_config_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_server_profile_keeps_every_private_listener_behind_https(tmp_path):
    module = load_config_module()
    config = module.ServerConfig("https://debug.example.test", str(tmp_path), "GWAP-" + "a" * 32)
    env = config.environment()
    settings = Settings(_env_file=None, **{key.lower(): value for key, value in env.items() if key != "MODEL_CPU_THREADS"})
    assert settings.deployment_mode == "lan_server"
    assert settings.job_workers == 2
    assert settings.auth_mode == "rbac" and not settings.auth_allow_legacy_admin
    public = client_capabilities(settings)
    assert public["backend_chat_allowed"] is False
    assert public["server_id"] == config.server_id
    gateway = module.caddy_configuration(config)
    server = gateway["apps"]["http"]["servers"]["platform"]
    assert server["listen"] == ["0.0.0.0:443"]
    assert server["routes"][0]["handle"][-1]["upstreams"] == [{"dial": "127.0.0.1:18080"}]
    assert gateway["admin"]["disabled"] is True
    assert gateway["apps"]["pki"]["certificate_authorities"]["local"]["install_trust"] is False
    for secret in ("api_key", "mcp_bearer_token", "data_root", "model_secret_key"):
        assert secret not in public


@pytest.mark.parametrize("changes", [
    {"public_url": "http://debug.example.test"}, {"public_url": "https://name:secret@example.test"},
    {"public_url": "https://debug.example.test/a"}, {"public_url": "https://debug.example.test?x=1"},
    {"data_root": "relative"}, {"model_threads": 0}, {"job_workers": 99}, {"server_id": "changed"},
    {"tls_mode": "certificate"}, {"backend_port": 443},
])
def test_server_profile_rejects_unsafe_or_unbounded_values(tmp_path, changes):
    module = load_config_module()
    values = dict(public_url="https://debug.example.test", data_root=str(tmp_path), server_id="GWAP-" + "a" * 32)
    with pytest.raises(ValueError):
        module.ServerConfig(**{**values, **changes})


@pytest.mark.parametrize("changes", [
    {"auth_mode": "local"}, {"auth_allow_legacy_admin": True}, {"api_key": "synthetic"},
    {"mcp_bearer_token": "synthetic"}, {"mcp_allowed_hosts": "*"}, {"cors_origins": "*"},
    {"mcp_enabled": False}, {"trusted_hosts": "*"},
])
def test_backend_independently_enforces_server_policy(tmp_path, changes):
    module = load_config_module()
    config = module.ServerConfig("https://debug.example.test", str(tmp_path), "GWAP-" + "a" * 32)
    values = {key.lower(): value for key, value in config.environment().items() if key != "MODEL_CPU_THREADS"}
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{**values, **changes})


def test_client_info_is_accessible_to_engineer_but_status_remains_admin(tmp_path):
    from fastapi import HTTPException
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from starlette.requests import Request
    from app.services.access_control import authorize_request

    engine = create_engine("sqlite://")
    with Session(engine) as db:
        def request(path):
            return Request({"type": "http", "method": "GET", "path": path, "headers": []})
        authorize_request(db, request("/api/v1/system/client-info"), {"role": "ENGINEER", "id": "test"})
        with pytest.raises(HTTPException) as error:
            authorize_request(db, request("/api/v1/system/status"), {"role": "ENGINEER", "id": "test"})
        assert error.value.status_code == 403
    engine.dispose()


def test_caddy_accepts_actual_server_json_without_machine_trust(tmp_path):
    caddy = PROJECT_ROOT / "artifacts/build-cache/lan-runtime/caddy-2.11.4/caddy.exe"
    if not caddy.is_file():
        pytest.skip("Pinned Windows Caddy runtime not present; run LAN package smoke")
    module = load_config_module()
    config = module.ServerConfig("https://127.0.0.1:18443", str(tmp_path), "GWAP-" + "b" * 32, gateway_bind="127.0.0.1")
    path = tmp_path / "caddy.json"
    path.write_text(json.dumps(module.caddy_configuration(config)), encoding="utf-8")
    completed = subprocess.run([str(caddy), "validate", "--config", str(path)], capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
