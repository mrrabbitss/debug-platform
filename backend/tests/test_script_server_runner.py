"""Interactive server startup must not race backups or replace saved settings."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner():
    if os.name != "nt":
        pytest.skip("Windows interactive server runner")
    path = Path(__file__).resolve().parents[2] / "scripts/run_lan_server.py"
    spec = importlib.util.spec_from_file_location("script_server_runner_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_running_server_excludes_backup_and_releases_lock(runner, tmp_path):
    with runner.exclusive_runner(tmp_path):
        with pytest.raises(RuntimeError, match="already running"):
            with runner.exclusive_runner(tmp_path):
                pytest.fail("Concurrent maintenance acquired the server lock")
    with runner.exclusive_runner(tmp_path):
        pass


def test_new_address_cannot_replace_existing_server(runner, tmp_path):
    package = tmp_path / "package"
    python = package / "runtime/python/python.exe"
    python.parent.mkdir(parents=True)
    python.touch()
    data = tmp_path / "business"
    config = data / "config/server.json"
    config.parent.mkdir(parents=True)
    original = json.dumps({"public_url": "https://original.example.test"})
    config.write_text(original)
    args = SimpleNamespace(package=package, data_root=data, public_url="https://replacement.example.test", backup=False)
    with pytest.raises(RuntimeError, match="not replaced"):
        runner.run(args)
    assert config.read_text() == original
