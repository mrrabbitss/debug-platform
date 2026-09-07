"""Offline operations for a stopped machine-level server, using its bundled Python."""
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portable_launcher import ensure_local_environment, validate_layout, verify_package_manifest
from portable_server_config import ServerConfig, caddy_configuration, load_server_config

PACKAGE_ROOT = Path(__file__).resolve().parent


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
    from app.models import UserAccount
    from app.services.access_control import issue_access_token
    from sqlalchemy import select

    run_database_migrations()
    with SessionLocal() as db:
        if db.scalar(select(UserAccount.id).limit(1)):
            raise ValueError("Users already exist; refusing to create or replace an administrator")
        user = UserAccount(id=new_id("USR"), username="admin", display_name="Platform administrator", role="ADMIN")
        db.add(user)
        db.flush()
        token, raw = issue_access_token(db, user, name="installation-bootstrap", expires_days=1)
        path = config.root / "config/bootstrap-token.txt"
        with path.open("x", encoding="utf-8") as stream:
            stream.write(raw + "\n")
        return {"administrator": user.username, "bootstrap_token_file": str(path), "expires_days": 1,
                "next": "Use the administrator-only file to sign in, create personal accounts/tokens, then revoke the bootstrap token."}


def operate(args) -> dict:
    validate_layout()
    verify_package_manifest()
    if args.action == "configure":
        return configure(args)
    config = load_server_config(args.config.resolve())
    config.apply_environment()
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
    for action in ("initialize-admin", "backup", "restore", "rewrite-services"):
        command = sub.add_parser(action)
        command.add_argument("--config", type=Path, required=True)
        if action in {"backup", "restore"}:
            command.add_argument("--archive", type=Path, required=True)
        if action == "restore":
            command.add_argument("--confirm", choices=["RESTORE"], required=True)
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
