"""Offline operations for a stopped machine-level server, using its bundled Python."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import secrets
import socket
import sys
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, timedelta
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portable_launcher import ensure_local_environment, validate_layout, verify_package_manifest
from portable_server_config import ServerConfig, caddy_configuration, load_server_config

PACKAGE_ROOT = Path(__file__).resolve().parent
INITIAL_BOOTSTRAP_DAYS = 7
RECOVERY_TOKEN_DAYS = 7
MAX_RECOVERY_TOKEN_DAYS = 30


def write_service_xml(config: ServerConfig, package_root: Path, *, gateway: bool) -> Path:
    service_id = "GWAPGateway" if gateway else "GWAPBackend"
    service = ET.Element("service")
    values = {
        "id": service_id, "name": f"GW/AP {'HTTPS Gateway' if gateway else 'Platform Backend'}",
        "description": "GW/AP LAN platform, managed independently of interactive CLI windows",
        "executable": str(package_root / ("server-runtime/caddy.exe" if gateway else "runtime/python/python.exe")),
        "arguments": (f'run --config "{config.root / "config/caddy.json"}"' if gateway else
                      f'-B -s "{package_root / "portable_launcher.py"}" --server-config "{config.root / "config/server.json"}"'),
        "workingdirectory": str(package_root), "startmode": "Automatic", "delayedAutoStart": "true",
        "stoptimeout": "45 sec", "logpath": str(config.root / "logs" / service_id),
    }
    for key, value in values.items():
        ET.SubElement(service, key).text = value
    account = ET.SubElement(service, "serviceaccount")
    ET.SubElement(account, "username").text = "NT AUTHORITY\\LocalService"
    ET.SubElement(service, "onfailure", action="restart", delay="15 sec")
    ET.SubElement(service, "onfailure", action="restart", delay="30 sec")
    ET.SubElement(service, "resetfailure").text = "1 hour"
    log = ET.SubElement(service, "log", mode="roll-by-size")
    ET.SubElement(log, "sizeThreshold").text = "10240"
    ET.SubElement(log, "keepFiles").text = "8"
    if gateway:
        ET.SubElement(service, "depend").text = "GWAPBackend"
    folder = config.root / "services"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{service_id}.xml"
    ET.indent(service)
    ET.ElementTree(service).write(path, encoding="utf-8", xml_declaration=True)
    return path


def configure(args) -> dict:
    root = args.data_root.resolve()
    path = root / "config/server.json"
    if path.exists() or (root / "data/gw_ap_debug.db").exists():
        raise ValueError("Existing server data must use the upgrade/restore workflow; it was not overwritten")
    config = ServerConfig(public_url=args.public_url, data_root=str(root), server_id="GWAP-" + uuid.uuid4().hex,
                          tls_mode="certificate" if args.certificate else "internal",
                          certificate_file=str(args.certificate.resolve()) if args.certificate else "",
                          certificate_key_file=str(args.certificate_key.resolve()) if args.certificate_key else "",
                          backend_port=args.backend_port)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config.payload(), indent=2), encoding="utf-8")
    (root / "config/caddy.json").write_text(json.dumps(caddy_configuration(config), indent=2), encoding="utf-8")
    for folder in ("logs", "gateway", "backups", "services"):
        (root / folder).mkdir(exist_ok=True)
    for gateway in (False, True):
        write_service_xml(config, PACKAGE_ROOT, gateway=gateway)
    return {"configured": True, "server_id": config.server_id, "public_url": config.origin, "config": str(path)}


def initialize_admin(config: ServerConfig) -> dict:
    from app.core.db import SessionLocal
    from app.core.migrations import run_database_migrations
    from app.core.utils import new_id
    from app.models import AccessToken, UserAccount
    from app.services.access_control import token_digest
    from sqlalchemy import select

    run_database_migrations()
    with SessionLocal() as db:
        if db.scalar(select(UserAccount.id).limit(1)):
            raise ValueError("Users already exist; refusing to create or replace an administrator")
        path = config.root / "config/bootstrap-token.txt"
        if path.exists():
            raw_token = _read_valid_credential(path)
            if db.scalar(select(AccessToken.id).where(AccessToken.token_hash == token_digest(raw_token))):
                raise ValueError("Bootstrap credential is already registered but no user account is available; refusing to replace it")
            user = UserAccount(id=new_id("USR"), username="admin", display_name="Platform administrator", role="ADMIN")
            db.add(user)
            db.flush()
            _register_file_token(
                db,
                user,
                raw_token=raw_token,
                name="installation-bootstrap",
                expires_days=INITIAL_BOOTSTRAP_DAYS,
                audit_action="auth.installation_bootstrap_resumed",
            )
            return {
                "administrator": user.username,
                "bootstrap_token_file": str(path),
                "expires_days": INITIAL_BOOTSTRAP_DAYS,
                "next": "Recovered the existing administrator-only bootstrap credential without replacing it. Use it before it expires, create a personal administrator token, then revoke the bootstrap token.",
            }
        user = UserAccount(id=new_id("USR"), username="admin", display_name="Platform administrator", role="ADMIN")
        db.add(user)
        db.flush()
        _issue_file_token(
            db,
            user,
            path=path,
            name="installation-bootstrap",
            expires_days=INITIAL_BOOTSTRAP_DAYS,
            audit_action="auth.installation_bootstrap_issued",
        )
        return {
            "administrator": user.username,
            "bootstrap_token_file": str(path),
            "expires_days": INITIAL_BOOTSTRAP_DAYS,
            "next": "Use the administrator-only file before it expires, create a personal administrator token, then revoke the bootstrap token.",
        }


def _write_new_credential(path: Path, raw_token: str) -> None:
    """Persist a secret before committing its usable hash, without replacing a file."""
    created = False
    try:
        with path.open("x", encoding="utf-8") as stream:
            created = True
            stream.write(raw_token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        if created:
            try:
                path.unlink()
            except OSError as cleanup_error:
                raise RuntimeError(
                    "Credential file write failed and its partial file could not be removed; restrict access to the file before retrying."
                ) from cleanup_error
        raise


def _read_valid_credential(path: Path) -> str:
    try:
        raw_token = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise ValueError("Existing credential file cannot be read; it was not replaced") from error
    if not raw_token.startswith("gwdp_") or len(raw_token) < 40:
        raise ValueError("Existing credential file is not a valid platform credential; it was not replaced")
    return raw_token


def _register_file_token(
    db,
    user,
    *,
    raw_token: str,
    name: str,
    expires_days: int,
    audit_action: str,
    replaced_credential_state: str | None = None,
):
    """Commit the hash of an already durable credential without persisting its value."""
    from app.core.utils import json_dumps, new_id, utcnow
    from app.models import AccessToken, AuditEvent
    from app.services.access_control import token_digest

    token = AccessToken(
        id=new_id("TOK"),
        user_id=user.id,
        name=name,
        token_hash=token_digest(raw_token),
        token_hint=f"gwdp_...{raw_token[-6:]}",
        expires_at=utcnow() + timedelta(days=expires_days),
    )
    details = {"user_id": user.id, "username": user.username, "name": name,
               "expires_days": expires_days, "credential_file_written": True}
    if replaced_credential_state:
        details["replaced_credential_state"] = replaced_credential_state
    db.add(token)
    db.add(AuditEvent(
        id=new_id("AUD"),
        actor_id=user.id,
        actor_type="offline_recovery",
        action=audit_action,
        resource_type="access_token",
        resource_id=token.id,
        details_json=json_dumps(details),
    ))
    db.commit()
    return token


def _issue_file_token(
    db,
    user,
    *,
    path: Path,
    name: str,
    expires_days: int,
    audit_action: str,
    replaced_credential_state: str | None = None,
):
    """Create one file-backed credential or leave neither a usable token nor an account change."""
    if not user.active:
        raise ValueError("Cannot issue a token for an inactive user")
    raw_token = f"gwdp_{secrets.token_urlsafe(32)}"
    _write_new_credential(path, raw_token)
    try:
        return _register_file_token(
            db,
            user,
            raw_token=raw_token,
            name=name,
            expires_days=expires_days,
            audit_action=audit_action,
            replaced_credential_state=replaced_credential_state,
        )
    except Exception:
        db.rollback()
        try:
            path.unlink()
        except OSError as cleanup_error:
            raise RuntimeError(
                "Credential issuance did not commit and its unusable file could not be removed; restrict access to the file before retrying."
            ) from cleanup_error
        raise


def _discard_unusable_recovery_credential(db, user, path: Path) -> str | None:
    """Remove only a stale recovery file belonging to the selected administrator."""
    from app.core.utils import utcnow
    from app.models import AccessToken
    from app.services.access_control import token_digest
    from sqlalchemy import select

    if not path.exists():
        return None
    raw_token = _read_valid_credential(path)
    token = db.scalar(select(AccessToken).where(AccessToken.token_hash == token_digest(raw_token)))
    if token is None:
        state = "unregistered"
    else:
        if token.user_id != user.id or token.name != "offline-admin-recovery":
            raise ValueError("Existing recovery credential belongs to another account or purpose; it was not replaced")
        expires_at = token.expires_at
        if expires_at and (expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=UTC)) <= utcnow():
            state = "expired"
        elif token.revoked_at is not None:
            state = "revoked"
        else:
            raise ValueError("A recovery credential already exists, is still usable, and was not replaced")
    try:
        path.unlink()
    except OSError as error:
        raise RuntimeError("Existing unusable recovery credential could not be removed; no replacement was made") from error
    return state


def _select_recovery_administrator(db, username: str | None):
    from app.models import UserAccount
    from sqlalchemy import select

    administrators = db.scalars(select(UserAccount).where(
        UserAccount.role == "ADMIN", UserAccount.active.is_(True),
    ).order_by(UserAccount.username)).all()
    if username:
        user = next((item for item in administrators if item.username == username), None)
        if not user:
            raise ValueError("The requested username is not an active administrator; no token was issued")
        return user
    if len(administrators) != 1:
        raise ValueError(
            "Recovery requires exactly one active administrator. Supply --username with an exact active administrator username; no token was issued."
        )
    return administrators[0]


def recover_admin_access(config: ServerConfig, *, username: str | None, expires_days: int, session_factory=None) -> dict:
    """Issue a temporary local recovery token without creating or changing accounts."""
    from app.core.db import SessionLocal

    if not 1 <= expires_days <= MAX_RECOVERY_TOKEN_DAYS:
        raise ValueError(f"Recovery token lifetime must be between 1 and {MAX_RECOVERY_TOKEN_DAYS} days")
    path = config.root / "config/admin-recovery-token.txt"
    with (session_factory or SessionLocal)() as db:
        user = _select_recovery_administrator(db, username)
        replaced_credential_state = _discard_unusable_recovery_credential(db, user, path)
        _issue_file_token(
            db,
            user,
            path=path,
            name="offline-admin-recovery",
            expires_days=expires_days,
            audit_action="auth.admin_recovery_token_issued",
            replaced_credential_state=replaced_credential_state,
        )
    return {
        "administrator": user.username,
        "recovery_token_file": str(path),
        "expires_days": expires_days,
        "next": "Read the file only on this Windows account, sign in through the administrator entry, create a personal administrator token, then revoke the recovery token.",
    }


def _validate_recovery_target(config: ServerConfig, config_path: Path) -> None:
    if config_path.resolve() != (config.root / "config/server.json").resolve():
        raise ValueError("Recovery configuration does not belong to the configured data root")
    database = config.root / "data/gw_ap_debug.db"
    if not database.is_file():
        raise ValueError("Configured server database was not found; recovery will not initialize a new server")
    if not config.env_file.is_file():
        raise ValueError("Configured server environment was not found; start and stop the configured server once before recovery")


def _prepare_existing_server_environment(config: ServerConfig) -> None:
    """Set the portable paths only after validating every recovery input already exists."""
    data = config.root / "data"
    storage = data / "storage"
    if not storage.is_dir():
        raise ValueError("Configured server storage was not found; recovery will not create a new data root")
    os.environ.update({
        "DEBUG_PLATFORM_ENV_FILE": str(config.env_file),
        "DATABASE_URL": f"sqlite:///{(data / 'gw_ap_debug.db').resolve().as_posix()}",
        "DATA_ROOT": str(data),
        "STORAGE_ROOT": str(storage),
        "STATIC_FRONTEND_ROOT": str((PACKAGE_ROOT / "web").resolve()),
        "MODEL_DISABLE_IN_PROCESS_LOCAL": "true",
        "PYTHONUTF8": "1",
    })
    backend = str((PACKAGE_ROOT / "app/backend").resolve())
    if backend not in sys.path:
        sys.path.insert(0, backend)


def _server_port_is_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


@contextmanager
def _stopped_server_lock(config: ServerConfig):
    """Use the interactive runner lock and port probes before touching SQLite."""
    import msvcrt

    lock_path = config.root / "script-runner.lock"
    with lock_path.open("a+b") as lock:
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError("The interactive server or a backup is running. Stop it before administrator recovery.") from None
        try:
            public_port = urlsplit(config.origin).port or 443
            if _server_port_is_open(config.backend_port) or _server_port_is_open(public_port):
                raise RuntimeError("The configured server appears to be running. Stop it before administrator recovery.")
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def operate(args) -> dict:
    validate_layout()
    verify_package_manifest()
    if args.action == "configure":
        return configure(args)
    config = load_server_config(args.config.resolve())
    config.apply_environment()
    if args.action == "recover-admin":
        _validate_recovery_target(config, args.config.resolve())
        _prepare_existing_server_environment(config)
        with _stopped_server_lock(config):
            return recover_admin_access(config, username=args.username, expires_days=args.expires_days)
    ensure_local_environment(config.root / "data", config.env_file)
    if args.action == "initialize-admin":
        return initialize_admin(config)
    if args.action == "rewrite-services":
        for gateway in (False, True):
            write_service_xml(config, PACKAGE_ROOT, gateway=gateway)
        return {"configured_package": str(PACKAGE_ROOT), "services_started": False}
    from app.core.config import get_settings
    from server_backup import full_backup, full_restore
    settings = get_settings()
    if args.action == "backup":
        return full_backup(config, settings, args.archive, PACKAGE_ROOT)
    if args.action == "restore":
        result = full_restore(config, settings, args.archive, PACKAGE_ROOT, args.confirm)
        for gateway in (False, True):
            write_service_xml(load_server_config(args.config.resolve()), PACKAGE_ROOT, gateway=gateway)
        return result
    raise ValueError("Unsupported offline action")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    setup = sub.add_parser("configure")
    setup.add_argument("--public-url", required=True)
    setup.add_argument("--data-root", type=Path, required=True)
    setup.add_argument("--backend-port", type=int, default=18080)
    setup.add_argument("--certificate", type=Path)
    setup.add_argument("--certificate-key", type=Path)
    for action in ("initialize-admin", "backup", "restore", "rewrite-services", "recover-admin"):
        command = sub.add_parser(action)
        command.add_argument("--config", type=Path, required=True)
        if action in {"backup", "restore"}:
            command.add_argument("--archive", type=Path, required=True)
        if action == "restore":
            command.add_argument("--confirm", choices=["RESTORE"], required=True)
        if action == "recover-admin":
            command.add_argument("--username")
            command.add_argument("--expires-days", type=int, default=RECOVERY_TOKEN_DAYS)
    args = parser.parse_args(argv)
    if args.action == "configure" and bool(args.certificate) != bool(args.certificate_key):
        parser.error("Certificate and private key must be supplied together")
    try:
        print(json.dumps(operate(args), indent=2, default=str))
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print(f"[ERROR] {error}")
        return 1


if __name__ == "__main__":
    os.environ["PYTHONUTF8"] = "1"
    raise SystemExit(main())
