import json
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest

from app.services.backup import BackupError, create_backup, inspect_backup, restore_backup


def _database(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE sample (value TEXT NOT NULL)")
        db.execute("INSERT INTO sample (value) VALUES (?)", (value,))
        db.commit()


def _read_value(path: Path) -> str:
    with closing(sqlite3.connect(path)) as db:
        return str(db.execute("SELECT value FROM sample").fetchone()[0])


def test_backup_round_trip_verifies_hashes_and_retains_rollback(tmp_path: Path) -> None:
    source_database = tmp_path / "source" / "debug.db"
    source_storage = tmp_path / "source" / "storage"
    source_secret = tmp_path / "source" / "model_secret.key"
    source_storage.mkdir(parents=True)
    (source_storage / "artifacts" / "ART-1").mkdir(parents=True)
    (source_storage / "artifacts" / "ART-1" / "log.txt").write_text(
        "NOTICE 2026-03-02 03:29:17.483\n",
        encoding="utf-8",
    )
    source_secret.write_text("fernet-key", encoding="utf-8")
    _database(source_database, "backup-value")
    archive = tmp_path / "backup.zip"

    report = create_backup(
        database_url=f"sqlite:///{source_database.as_posix()}",
        storage_root=source_storage,
        model_secret_key_path=source_secret,
        output_path=archive,
    )
    manifest = inspect_backup(archive)
    assert report["output_path"] == str(archive.resolve())
    assert manifest["format"] == "gw-ap-debug-platform"
    assert manifest["contains_environment_file"] is False
    assert manifest["sensitive"] is True
    with zipfile.ZipFile(archive) as zipped:
        assert ".env" not in zipped.namelist()

    target_database = tmp_path / "target" / "debug.db"
    target_storage = tmp_path / "target" / "storage"
    target_secret = tmp_path / "target" / "model_secret.key"
    target_storage.mkdir(parents=True)
    (target_storage / "old.txt").write_text("old-storage", encoding="utf-8")
    target_secret.write_text("old-key", encoding="utf-8")
    _database(target_database, "old-value")

    restored = restore_backup(
        archive_path=archive,
        database_path=target_database,
        storage_root=target_storage,
        model_secret_key_path=target_secret,
        rollback_root=tmp_path / "rollbacks",
        confirmation="RESTORE",
    )
    assert restored["restored"] is True
    assert _read_value(target_database) == "backup-value"
    assert (target_storage / "artifacts" / "ART-1" / "log.txt").is_file()
    assert not (target_storage / "old.txt").exists()
    assert target_secret.read_text(encoding="utf-8") == "fernet-key"
    rollback = Path(restored["rollback_path"])
    assert _read_value(rollback / "database.sqlite3") == "old-value"
    assert (rollback / "storage" / "old.txt").read_text(encoding="utf-8") == "old-storage"
    assert (rollback / "model_secret.key").read_text(encoding="utf-8") == "old-key"


def test_backup_rejects_tampering_and_restore_requires_confirmation(tmp_path: Path) -> None:
    database = tmp_path / "debug.db"
    storage = tmp_path / "storage"
    storage.mkdir()
    (storage / "log.txt").write_text("original", encoding="utf-8")
    _database(database, "value")
    archive = tmp_path / "backup.zip"
    create_backup(
        database_url=f"sqlite:///{database.as_posix()}",
        storage_root=storage,
        model_secret_key_path=tmp_path / "missing.key",
        output_path=archive,
    )

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive, "r") as source, zipfile.ZipFile(tampered, "w") as target:
        for info in source.infolist():
            content = source.read(info)
            if info.filename == "storage/log.txt":
                content = b"tampered"
            target.writestr(info, content)
    with pytest.raises(BackupError, match="Checksum mismatch"):
        inspect_backup(tampered)

    with pytest.raises(BackupError, match="confirmation"):
        restore_backup(
            archive_path=archive,
            database_path=tmp_path / "restored.db",
            storage_root=tmp_path / "restored-storage",
            model_secret_key_path=tmp_path / "restored.key",
            rollback_root=tmp_path / "rollbacks",
            confirmation="yes",
        )


def test_backup_manifest_rejects_unlisted_archive_member(tmp_path: Path) -> None:
    archive = tmp_path / "bad.zip"
    manifest = {
        "format": "gw-ap-debug-platform",
        "version": 1,
        "file_count": 0,
        "total_uncompressed_bytes": 0,
        "files": [],
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("manifest.json", json.dumps(manifest))
        zipped.writestr("../escape.txt", "unsafe")
    with pytest.raises(BackupError, match="Unsafe path"):
        inspect_backup(archive)


def test_backup_output_cannot_be_written_inside_source_storage(tmp_path: Path) -> None:
    database = tmp_path / "debug.db"
    storage = tmp_path / "storage"
    storage.mkdir()
    _database(database, "value")
    with pytest.raises(BackupError, match="outside the storage"):
        create_backup(
            database_url=f"sqlite:///{database.as_posix()}",
            storage_root=storage,
            model_secret_key_path=tmp_path / "model_secret.key",
            output_path=storage / "recursive-backup.zip",
        )
