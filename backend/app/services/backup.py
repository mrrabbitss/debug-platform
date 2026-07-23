import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from sqlalchemy.engine import make_url


BACKUP_FORMAT = "gw-ap-debug-platform"
BACKUP_VERSION = 1
DATABASE_ARCHIVE_PATH = "database/database.sqlite3"
SECRET_ARCHIVE_PATH = "secrets/model_secret.key"
MANIFEST_ARCHIVE_PATH = "manifest.json"
MAX_BACKUP_FILES = 100_000
MAX_BACKUP_BYTES = 128 * 1024 * 1024 * 1024


class BackupError(ValueError):
    pass


def sqlite_database_path(database_url: str) -> Path:
    url = make_url(database_url)
    if url.drivername != "sqlite":
        raise BackupError(
            "The built-in backup supports SQLite only. Use pg_dump/pg_restore for PostgreSQL."
        )
    if not url.database or url.database == ":memory:":
        raise BackupError("An on-disk SQLite database is required for backup or restore")
    return Path(url.database).expanduser().resolve()


def _sha256_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _write_file_to_zip(archive: zipfile.ZipFile, source: Path, archive_path: str) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with source.open("rb") as input_file, archive.open(archive_path, "w") as output_file:
        while chunk := input_file.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            output_file.write(chunk)
    return {"path": archive_path, "size": size, "sha256": digest.hexdigest()}


def _snapshot_sqlite(source: Path, target: Path) -> None:
    if not source.is_file():
        raise BackupError(f"SQLite database not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        closing(sqlite3.connect(str(source), timeout=30)) as source_db,
        closing(sqlite3.connect(str(target))) as target_db,
    ):
        source_db.backup(target_db)
        result = target_db.execute("PRAGMA integrity_check").fetchone()
        if not result or result[0] != "ok":
            raise BackupError("SQLite snapshot failed its integrity check")


def _storage_files(storage_root: Path):
    if not storage_root.exists():
        return
    if not storage_root.is_dir():
        raise BackupError(f"Storage root is not a directory: {storage_root}")
    for source in sorted(storage_root.rglob("*")):
        if source.is_symlink():
            raise BackupError(f"Refusing to back up a symbolic link: {source}")
        if source.is_file():
            relative = source.relative_to(storage_root).as_posix()
            yield source, f"storage/{relative}"


def create_backup(
    *,
    database_url: str,
    storage_root: Path,
    model_secret_key_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    database_path = sqlite_database_path(database_url)
    storage = storage_root.expanduser().resolve()
    secret = model_secret_key_path.expanduser().resolve()
    output = output_path.expanduser().resolve()
    if output == storage or storage in output.parents:
        raise BackupError("Backup output must be outside the storage directory")
    if database_path == storage or storage in database_path.parents:
        raise BackupError("The database file must be outside the storage directory")
    if secret == storage or storage in secret.parents:
        raise BackupError("The model secret key must be outside the storage directory")
    if output.exists():
        raise BackupError(f"Backup output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    entries: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="gw-ap-backup-") as temp_dir:
            snapshot = Path(temp_dir) / "database.sqlite3"
            _snapshot_sqlite(database_path, snapshot)
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                entries.append(_write_file_to_zip(archive, snapshot, DATABASE_ARCHIVE_PATH))
                for source, archive_path in _storage_files(storage):
                    entries.append(_write_file_to_zip(archive, source, archive_path))
                    if len(entries) > MAX_BACKUP_FILES:
                        raise BackupError(f"Backup contains more than {MAX_BACKUP_FILES} files")
                secret_included = secret.is_file()
                if secret_included:
                    if secret.is_symlink():
                        raise BackupError("Refusing to back up a symbolic-link model secret key")
                    entries.append(_write_file_to_zip(
                        archive,
                        secret,
                        SECRET_ARCHIVE_PATH,
                    ))
                total_bytes = sum(int(entry["size"]) for entry in entries)
                if total_bytes > MAX_BACKUP_BYTES:
                    raise BackupError(f"Backup exceeds the {MAX_BACKUP_BYTES}-byte safety limit")
                manifest = {
                    "format": BACKUP_FORMAT,
                    "version": BACKUP_VERSION,
                    "created_at": datetime.now(UTC).isoformat(),
                    "database": "sqlite",
                    "storage_included": True,
                    "model_secret_key_included": secret_included,
                    "contains_environment_file": False,
                    "sensitive": True,
                    "file_count": len(entries),
                    "total_uncompressed_bytes": total_bytes,
                    "files": entries,
                }
                archive.writestr(
                    MANIFEST_ARCHIVE_PATH,
                    json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
                )
        if output.exists():
            raise BackupError(f"Backup output appeared while backup was running: {output}")
        os.replace(partial, output)
        return {**manifest, "output_path": str(output), "archive_bytes": output.stat().st_size}
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _validated_archive_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    for info in archive.infolist():
        name = info.filename
        path = PurePosixPath(name)
        if (
            not name
            or "\\" in name
            or name.startswith("/")
            or path.is_absolute()
            or ".." in path.parts
            or any(part in {"", "."} for part in path.parts)
        ):
            raise BackupError(f"Unsafe path in backup archive: {name!r}")
        if info.is_dir():
            continue
        if name in members:
            raise BackupError(f"Duplicate path in backup archive: {name}")
        members[name] = info
    if len(members) > MAX_BACKUP_FILES + 1:
        raise BackupError("Backup archive contains too many files")
    return members


def inspect_backup(archive_path: Path, *, verify_hashes: bool = True) -> dict[str, Any]:
    archive_file = archive_path.expanduser().resolve()
    if not archive_file.is_file():
        raise BackupError(f"Backup archive not found: {archive_file}")
    try:
        with zipfile.ZipFile(archive_file, "r") as archive:
            members = _validated_archive_members(archive)
            manifest_info = members.get(MANIFEST_ARCHIVE_PATH)
            if not manifest_info or manifest_info.file_size > 10 * 1024 * 1024:
                raise BackupError("Backup manifest is missing or too large")
            try:
                manifest = json.loads(archive.read(manifest_info).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BackupError("Backup manifest is not valid UTF-8 JSON") from exc
            if manifest.get("format") != BACKUP_FORMAT or manifest.get("version") != BACKUP_VERSION:
                raise BackupError("Unsupported backup format or version")
            files = manifest.get("files")
            if not isinstance(files, list):
                raise BackupError("Backup manifest files list is invalid")
            expected: dict[str, dict[str, Any]] = {}
            total_size = 0
            for item in files:
                if not isinstance(item, dict):
                    raise BackupError("Backup manifest contains an invalid file entry")
                name = item.get("path")
                size = item.get("size")
                digest = item.get("sha256")
                if (
                    not isinstance(name, str)
                    or name == MANIFEST_ARCHIVE_PATH
                    or name in expected
                    or not isinstance(size, int)
                    or size < 0
                    or not isinstance(digest, str)
                    or len(digest) != 64
                ):
                    raise BackupError("Backup manifest contains an invalid file entry")
                expected[name] = item
                total_size += size
            if total_size > MAX_BACKUP_BYTES:
                raise BackupError("Backup exceeds the extraction safety limit")
            actual_names = set(members) - {MANIFEST_ARCHIVE_PATH}
            if actual_names != set(expected):
                raise BackupError("Backup members do not match the manifest")
            if DATABASE_ARCHIVE_PATH not in expected:
                raise BackupError("Backup does not contain a database snapshot")
            for name, item in expected.items():
                info = members[name]
                if info.file_size != item["size"]:
                    raise BackupError(f"Size mismatch for backup member: {name}")
                if verify_hashes:
                    with archive.open(info, "r") as stream:
                        digest, size = _sha256_stream(stream)
                    if size != item["size"] or not hmac.compare_digest(digest, item["sha256"]):
                        raise BackupError(f"Checksum mismatch for backup member: {name}")
            if manifest.get("file_count") != len(expected):
                raise BackupError("Backup manifest file count is inconsistent")
            if manifest.get("total_uncompressed_bytes") != total_size:
                raise BackupError("Backup manifest byte count is inconsistent")
            return manifest
    except zipfile.BadZipFile as exc:
        raise BackupError("Backup archive is not a valid ZIP file") from exc


def _extract_verified(archive_path: Path, destination: Path) -> dict[str, Any]:
    manifest = inspect_backup(archive_path, verify_hashes=False)
    expected = {item["path"]: item for item in manifest["files"]}
    with zipfile.ZipFile(archive_path, "r") as archive:
        members = _validated_archive_members(archive)
        for name, item in expected.items():
            target = destination.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            size = 0
            with archive.open(members[name], "r") as source, target.open("xb") as output:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > item["size"]:
                        raise BackupError(f"Expanded backup member exceeds its declared size: {name}")
                    digest.update(chunk)
                    output.write(chunk)
            if size != item["size"] or not hmac.compare_digest(digest.hexdigest(), item["sha256"]):
                raise BackupError(f"Checksum mismatch while extracting backup member: {name}")
    return manifest


def _check_sqlite_integrity(database_path: Path) -> None:
    try:
        with closing(sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True)) as db:
            result = db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise BackupError("Restored SQLite snapshot cannot be opened") from exc
    if not result or result[0] != "ok":
        raise BackupError("Restored SQLite snapshot failed its integrity check")


def _safe_restore_targets(
    database_path: Path,
    storage_root: Path,
    model_secret_key_path: Path,
    rollback_root: Path,
) -> tuple[Path, Path, Path, Path]:
    database = database_path.expanduser().resolve()
    storage = storage_root.expanduser().resolve()
    secret = model_secret_key_path.expanduser().resolve()
    rollback = rollback_root.expanduser().resolve()
    if storage == Path(storage.anchor) or rollback == Path(rollback.anchor):
        raise BackupError("Refusing to restore into a filesystem root")
    if database == storage or storage in database.parents:
        raise BackupError("The database file must not be inside the restored storage directory")
    if secret == storage or storage in secret.parents:
        raise BackupError("The model secret key must not be inside the restored storage directory")
    if rollback == storage or storage in rollback.parents:
        raise BackupError("Rollback storage must not be inside the restored storage directory")
    return database, storage, secret, rollback


def restore_backup(
    *,
    archive_path: Path,
    database_path: Path,
    storage_root: Path,
    model_secret_key_path: Path,
    rollback_root: Path,
    confirmation: str,
) -> dict[str, Any]:
    if confirmation != "RESTORE":
        raise BackupError('Restore requires the exact confirmation text "RESTORE"')
    database, storage, secret, rollback_base = _safe_restore_targets(
        database_path,
        storage_root,
        model_secret_key_path,
        rollback_root,
    )
    restore_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:8]}"
    rollback = rollback_base / restore_id
    rollback.mkdir(parents=True, exist_ok=False)
    prepared_database = database.parent / f".{database.name}.{restore_id}.restore"
    prepared_storage = storage.parent / f".{storage.name}.{restore_id}.restore"
    prepared_secret = secret.parent / f".{secret.name}.{restore_id}.restore"
    old_database = rollback / "database.sqlite3"
    old_storage = rollback / "storage"
    old_secret = rollback / "model_secret.key"
    database_existed = database.exists()
    storage_existed = storage.exists()
    secret_existed = secret.exists()
    database_replaced = False
    storage_moved = False
    storage_replaced = False
    secret_replaced = False
    try:
        with tempfile.TemporaryDirectory(prefix="gw-ap-restore-") as temp_dir:
            staging = Path(temp_dir)
            manifest = _extract_verified(archive_path.expanduser().resolve(), staging)
            staged_database = staging / DATABASE_ARCHIVE_PATH
            staged_storage = staging / "storage"
            staged_storage.mkdir(exist_ok=True)
            staged_secret = staging / SECRET_ARCHIVE_PATH
            _check_sqlite_integrity(staged_database)

            database.parent.mkdir(parents=True, exist_ok=True)
            storage.parent.mkdir(parents=True, exist_ok=True)
            secret.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staged_database, prepared_database)
            shutil.copytree(staged_storage, prepared_storage)
            if staged_secret.is_file():
                shutil.copy2(staged_secret, prepared_secret)

        if database_existed:
            shutil.copy2(database, old_database)
        os.replace(prepared_database, database)
        database_replaced = True

        if storage_existed:
            os.replace(storage, old_storage)
            storage_moved = True
        os.replace(prepared_storage, storage)
        storage_replaced = True

        if prepared_secret.exists():
            if secret_existed:
                shutil.copy2(secret, old_secret)
            os.replace(prepared_secret, secret)
            secret_replaced = True

        return {
            "restored": True,
            "backup_created_at": manifest["created_at"],
            "file_count": manifest["file_count"],
            "rollback_path": str(rollback),
            "model_secret_key_restored": secret_replaced,
        }
    except Exception:
        try:
            if secret_replaced:
                if secret_existed and old_secret.exists():
                    shutil.copy2(old_secret, prepared_secret)
                    os.replace(prepared_secret, secret)
                else:
                    secret.unlink(missing_ok=True)
            if storage_replaced:
                if storage.exists():
                    os.replace(storage, prepared_storage)
            if storage_moved and old_storage.exists():
                os.replace(old_storage, storage)
            if database_replaced:
                if database_existed and old_database.exists():
                    shutil.copy2(old_database, prepared_database)
                    os.replace(prepared_database, database)
                else:
                    database.unlink(missing_ok=True)
        finally:
            if prepared_database.exists():
                prepared_database.unlink(missing_ok=True)
            if prepared_storage.exists():
                shutil.rmtree(prepared_storage)
            if prepared_secret.exists():
                prepared_secret.unlink(missing_ok=True)
        raise
