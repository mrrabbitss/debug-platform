"""Verified full server backup/restore. Call only while both services are stopped."""
from dataclasses import replace
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import uuid

from app.services.backup import create_backup, restore_backup, inspect_backup, _extract_verified, sqlite_database_path
from portable_server_config import ServerConfig, caddy_configuration


def file_hash(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def full_backup(config, settings, archive: Path, package: Path):
    extra = {"server/package-manifest.json": package / "package-manifest.json"}
    for directory in ("config", "gateway"):
        for path in (config.root / directory).rglob("*"):
            if path.is_symlink():
                raise ValueError("Server backup cannot follow symbolic links")
            if path.is_file():
                extra[f"server/{path.relative_to(config.root).as_posix()}"] = path
    if config.tls_mode == "certificate":
        extra["server/tls/certificate.pem"] = Path(config.certificate_file)
        extra["server/tls/private-key.pem"] = Path(config.certificate_key_file)
    result = create_backup(database_url=settings.database_url, storage_root=settings.storage_root,
        model_secret_key_path=settings.model_secret_key_path, output_path=archive, extra_files=extra,
        server_metadata={"format": "gwap-full-server-v1", "data_root": str(config.root),
                         "package_sha256": file_hash(package / "package-manifest.json")})
    inspect_backup(archive)
    return {"verified": True, "archive": str(archive), "files": result["file_count"],
            "scope": "database, artifacts, model decryption key, server configuration, TLS identity",
            "program_and_gguf": "Retain the matching versioned server package"}


def full_restore(config, settings, archive: Path, package: Path, confirm: str):
    if confirm != "RESTORE":
        raise ValueError("Full restore requires RESTORE")
    manifest = inspect_backup(archive)
    server = manifest.get("server") or {}
    if server.get("format") != "gwap-full-server-v1":
        raise ValueError("This archive is not a complete server backup")
    if server.get("package_sha256") != file_hash(package / "package-manifest.json"):
        raise ValueError("Restore requires the exact matching server package; retain the old package when upgrading")
    rollback = config.root / "rollback" / ("server-" + uuid.uuid4().hex)
    rollback.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix="full-restore-", dir=config.root) as temporary:
        staging = Path(temporary)
        _extract_verified(archive, staging)
        source = staging / "server"
        restored = ServerConfig(**json.loads((source / "config/server.json").read_text(encoding="utf-8-sig")))
        restored = replace(restored, data_root=str(config.root))
        if restored.tls_mode == "certificate":
            certificates = source / "config/certificates"
            certificates.mkdir(exist_ok=True)
            shutil.copyfile(source / "tls/certificate.pem", certificates / "certificate.pem")
            shutil.copyfile(source / "tls/private-key.pem", certificates / "private-key.pem")
            restored = replace(restored, certificate_file=str(config.root / "config/certificates/certificate.pem"),
                               certificate_key_file=str(config.root / "config/certificates/private-key.pem"))
        (source / "config/server.json").write_text(json.dumps(restored.payload(), indent=2), encoding="utf-8")
        (source / "config/caddy.json").write_text(json.dumps(caddy_configuration(restored), indent=2), encoding="utf-8")
        env = source / "config/server.env"
        if env.exists():
            text = env.read_text(encoding="utf-8-sig")
            old = str(server["data_root"])
            text = text.replace(old, str(config.root)).replace(old.replace("\\", "/"), str(config.root).replace("\\", "/"))
            env.write_text(text, encoding="utf-8")
        switched = []
        try:
            for directory in ("config", "gateway"):
                target = config.root / directory
                if target.exists():
                    os.replace(target, rollback / directory)
                switched.append(directory)
                if (source / directory).exists():
                    os.replace(source / directory, target)
                else:
                    target.mkdir()
            business = restore_backup(archive_path=archive, database_path=sqlite_database_path(settings.database_url),
                storage_root=settings.storage_root, model_secret_key_path=settings.model_secret_key_path,
                rollback_root=config.root / "rollback", confirmation="RESTORE")
        except Exception:
            for directory in reversed(switched):
                target = config.root / directory
                if target.exists():
                    os.replace(target, rollback / (directory + "-failed"))
                if (rollback / directory).exists():
                    os.replace(rollback / directory, target)
            raise
    return {"restored": True, "server_config_rollback": str(rollback), "business": business,
            "public_url": restored.origin}


def backup_name(root: Path):
    return root / ("server-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8] + ".zip")
