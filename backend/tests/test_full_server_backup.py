"""Restore actual knowledge/case rows, source files and TLS identity from a full archive."""
import importlib
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest
from app.core.config import PROJECT_ROOT


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "deploy/windows-portable"))
    monkeypatch.syspath_prepend(str(PROJECT_ROOT / "deploy/windows-server"))
    module = importlib.import_module("server_backup")
    config_module = importlib.import_module("portable_server_config")
    root = tmp_path / "server"
    (root / "config").mkdir(parents=True)
    (root / "gateway/pki").mkdir(parents=True)
    (root / "gateway/pki/private.key").write_bytes(b"synthetic-ca-key")
    config = config_module.ServerConfig("https://debug.example.test", str(root), "GWAP-" + "a" * 32)
    (root / "config/server.json").write_text(json.dumps(config.payload()), encoding="utf-8")
    (root / "config/server.env").write_text(f"DATA_ROOT={root / 'data'}\n", encoding="utf-8")
    data = root / "data"
    (data / "storage").mkdir(parents=True)
    (data / "storage/source.txt").write_text("synthetic case evidence", encoding="utf-8")
    (data / "model.key").write_bytes(b"synthetic-decryption-material")
    database = data / "cases.db"
    with closing(sqlite3.connect(database)) as db:
        db.executescript("CREATE TABLE knowledge_documents(id TEXT,content TEXT); CREATE TABLE cases(id TEXT,title TEXT);"
                         "INSERT INTO knowledge_documents VALUES('DOC-1','confirmed solution'); INSERT INTO cases VALUES('CASE-1','AP offline');")
    package = tmp_path / "package"
    package.mkdir()
    (package / "package-manifest.json").write_text('{"synthetic":true}', encoding="utf-8")
    settings = SimpleNamespace(database_url=f"sqlite:///{database.as_posix()}", storage_root=data / "storage",
                               model_secret_key_path=data / "model.key")
    return module, config, settings, package, database


def test_full_backup_restores_knowledge_cases_files_and_server_identity(bundle, tmp_path):
    module, config, settings, package, database = bundle
    archive = tmp_path / "full.zip"
    assert module.full_backup(config, settings, archive, package)["verified"]
    with closing(sqlite3.connect(database)) as db:
        db.execute("DELETE FROM knowledge_documents")
        db.execute("DELETE FROM cases")
        db.commit()
    (settings.storage_root / "source.txt").write_text("corrupted", encoding="utf-8")
    (config.root / "gateway/pki/private.key").write_bytes(b"wrong-key")
    assert module.full_restore(config, settings, archive, package, "RESTORE")["restored"]
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT content FROM knowledge_documents").fetchone()[0] == "confirmed solution"
        assert db.execute("SELECT title FROM cases").fetchone()[0] == "AP offline"
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert (settings.storage_root / "source.txt").read_text() == "synthetic case evidence"
    assert (config.root / "gateway/pki/private.key").read_bytes() == b"synthetic-ca-key"
    assert settings.model_secret_key_path.read_bytes() == b"synthetic-decryption-material"


def test_mismatched_program_is_rejected_before_any_restore(bundle, tmp_path):
    module, config, settings, package, database = bundle
    archive = tmp_path / "full.zip"
    module.full_backup(config, settings, archive, package)
    (package / "package-manifest.json").write_text('{"newer":true}')
    before = (config.root / "config/server.json").read_bytes()
    with pytest.raises(ValueError, match="matching server package"):
        module.full_restore(config, settings, archive, package, "RESTORE")
    assert (config.root / "config/server.json").read_bytes() == before
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT count(*) FROM cases").fetchone()[0] == 1
