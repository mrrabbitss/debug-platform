"""Inventory, preview or explicitly queue a one-time SQLite Skill reset.

Default command is read-only inventory. No import-time application initialization.
Never imports main.py, migrates a database, starts a worker, or extracts a ZIP.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _path_settings(path):
    """Read only selected filesystem settings; never print endpoint/key contents."""
    result = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.partition("=")
            if separator and name.strip() in {"DATA_ROOT", "STORAGE_ROOT", "DATABASE_URL"}:
                result[name.strip()] = value.strip().strip("\"'")
    return result


def inventory() -> list[dict]:
    """Bounded likely-root inspection, file metadata and SQLite counts only."""
    candidates = [(REPO / "backend/data", "repository default")]
    if os.environ.get("LOCALAPPDATA"):
        local = Path(os.environ["LOCALAPPDATA"])
        candidates.extend([(local / "GWAPDebugServer", "installed server"),
            (local / "GWAPDebugServer/data", "installed server nested data"),
            (local / "GWAPDebugPlatform/data", "portable default")])
    for name in ("GWAP_SERVER_DATA_ROOT", "DATA_ROOT"):
        raw = os.environ.get(name)
        if raw and Path(raw).is_absolute():
            candidates.append((Path(raw), name))
            candidates.append((Path(raw) / "data", name + " nested data"))
    env_files = [REPO / ".env"]
    if os.environ.get("DEBUG_PLATFORM_ENV_FILE"):
        env_files.append(Path(os.environ["DEBUG_PLATFORM_ENV_FILE"]))
    for root, _ in list(candidates):
        env_files.append(root / "config/server.env")
        config = root / "config/server.json"
        if config.is_file():
            try:
                value = json.loads(config.read_text(encoding="utf-8-sig")).get("data_root")
                if isinstance(value, str) and Path(value).is_absolute():
                    candidates.append((Path(value), "saved server configuration"))
                    candidates.append((Path(value) / "data", "saved server configuration nested data"))
            except (ValueError, OSError):
                pass
    for env_file in env_files:
        try:
            values = _path_settings(env_file)
        except (ValueError, OSError):
            continue
        for key in ("DATA_ROOT", "DATABASE_URL"):
            value = values.get(key, "")
            if key == "DATABASE_URL":
                if not value.startswith("sqlite:///") or "?" in value:
                    continue
                value = value[len("sqlite:///"):]
            if value:
                path = Path(value)
                if not path.is_absolute():
                    path = REPO / "backend" / path
                candidates.append((path.parent if key == "DATABASE_URL" else path, "selected local environment path"))
    results, seen = [], set()
    allowed_counts = ("knowledge_documents", "knowledge_chunks", "knowledge_revisions", "knowledge_publications",
        "cases", "analysis_runs", "user_accounts", "model_profiles", "audit_events", "workbench_records")
    for root, origin in candidates:
        root = root.resolve()
        if str(root).casefold() in seen:
            continue
        seen.add(str(root).casefold())
        value = {"data_root": str(root), "source": origin, "exists": root.is_dir(), "databases": []}
        for name in ("gw_ap_debug.db",):
            database = root / name
            if not database.is_file():
                continue
            details = {"path": str(database), "bytes": database.stat().st_size,
                "wal_present": Path(str(database) + "-wal").exists()}
            try:
                with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=2) as db:
                    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    details["counts"] = {t: db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
                        for t in allowed_counts if t in tables}
                    if "alembic_version" in tables:
                        details["schema_revision"] = [r[0] for r in db.execute("SELECT version_num FROM alembic_version")]
            except sqlite3.Error:
                details["read_error"] = "Database busy or unavailable; no content read or printed"
            value["databases"].append(details)
        results.append(value)
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "preview", "confirm", "status"), nargs="?", default="inventory")
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--database", type=Path, help="Existing SQLite file; defaults to DATA_ROOT/gw_ap_debug.db")
    parser.add_argument("--source-zip", type=Path)
    parser.add_argument("--env-file", type=Path, help="Optional existing server environment file; never printed")
    parser.add_argument("--archive-root", type=Path, help="Trusted operator-selected archive directory outside Git")
    parser.add_argument("--actor", help="Existing ADMIN/EXPERT user ID, or configured local-development identity")
    parser.add_argument("--operation-id")
    parser.add_argument("--expected-source-sha256")
    parser.add_argument("--expected-preview-hash")
    parser.add_argument("--confirm", action="store_true", help="Explicitly approve the displayed complete manifest")
    parser.add_argument("--allow-model-egress", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "inventory":
        print(json.dumps(inventory(), indent=2, ensure_ascii=True))
        return 0
    if not args.data_root or not args.data_root.is_absolute() or not args.data_root.is_dir():
        parser.error("--data-root must name an explicit existing absolute directory")
    root = args.data_root.resolve(strict=True)
    database = args.database or root / "gw_ap_debug.db"
    if (not database.is_absolute() or not database.is_file() or database.is_symlink()
            or not database.resolve().is_relative_to(root)):
        parser.error("--database must be an existing regular SQLite file inside --data-root")
    if not (root / "storage").is_dir():
        parser.error("Expected existing storage directory; this helper does not initialize an installation")
    if not args.actor or not args.operation_id:
        parser.error("--actor and --operation-id are required")
    if args.command in {"preview", "confirm"} and (not args.source_zip or not args.source_zip.is_absolute()):
        parser.error("--source-zip must be an explicit absolute ZIP path")
    if args.env_file:
        if not args.env_file.is_absolute() or not args.env_file.is_file():
            parser.error("--env-file must be an existing absolute file")
        os.environ["DEBUG_PLATFORM_ENV_FILE"] = str(args.env_file)
    os.environ.update(DATA_ROOT=str(root), DATABASE_URL="sqlite:///" + database.as_posix(), STORAGE_ROOT=str(root / "storage"))
    sys.path.insert(0, str(REPO / "backend"))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.db import configure_sqlite_engine
    from app.services import knowledge_reset as service
    from app.models import Job
    read_only = args.command in {"preview", "status"}
    uri = database.resolve().as_uri() + ("?mode=ro" if read_only else "?mode=rw")
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, timeout=30))
    if not read_only:
        configure_sqlite_engine(engine)
    try:
        with sessionmaker(engine, expire_on_commit=False)() as db:
            service.require_manager(db, args.actor)
            if args.command == "preview":
                result = service.preview_reset(db, operation_id=args.operation_id, data_root=root,
                    source_zip=args.source_zip, actor=args.actor)
            elif args.command == "confirm":
                if not (args.confirm and args.expected_source_sha256 and args.expected_preview_hash):
                    parser.error("confirm requires --confirm and both hashes from the exact preview")
                row, job = service.confirm_reset(db, operation_id=args.operation_id, data_root=root, source_zip=args.source_zip,
                    actor=args.actor, expected_source_sha256=args.expected_source_sha256,
                    expected_preview_hash=args.expected_preview_hash, confirmed=args.confirm,
                    model_egress_approved=args.allow_model_egress, archive_root=args.archive_root)
                result = {"operation": service.operation_payload(row), "job_id": job.id, "job_status": job.status,
                    "dispatch": "Queued durably. A server with the integrated knowledge_reset handler must run it."}
            else:
                service.verified_target(db, root)
                row, value = service.read_operation(db, args.operation_id)
                job = db.get(Job, value["job_id"])
                result = {"operation": service.operation_payload(row), "job_status": job.status if job else None}
            print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
        return 0
    except service.ResetError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        print("Operation could not complete. No source contents or credentials were printed.", file=sys.stderr)
        return 2
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
