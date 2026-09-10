"""Automatic six-file update called by the installer, with the server stopped."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

PACKAGE = Path(__file__).resolve().parent
sys.path.insert(0, str(PACKAGE))
sys.path.insert(0, str(PACKAGE / "scripts"))


def apply(data, *, check_only=False):
    data = data.resolve()
    config_path = data / "config/server.json"
    if not (data / "data/gw_ap_debug.db").exists():
        print("[OK] First installation: six files will be imported automatically on first server start.", flush=True)
        return "FIRST_START"
    if not config_path.exists():
        if (data / "data/gw_ap_debug.db").exists():
            raise ValueError("Server database exists but its configuration is missing; no knowledge was changed")
        print("[OK] First installation: all six files are bundled and will be imported automatically on first server start.", flush=True)
        return "FIRST_START"
    from portable_server_config import load_server_config
    import server_admin
    import portable_launcher as launcher
    config = load_server_config(config_path)
    server_admin._validate_recovery_target(config, config_path)
    config.apply_environment()
    server_admin._prepare_existing_server_environment(config)
    with server_admin._stopped_server_lock(config):
        if check_only:
            print("[OK] Existing server is stopped; data update can proceed.", flush=True)
            return "STOPPED"
        from app.core.migrations import run_database_migrations
        from app.core.db import SessionLocal
        from app.services import knowledge_reset as reset
        from app.services.model_profiles import get_active_model_profile, MANAGED_LOCAL_PROVIDER
        from offline_skill_update import run_update
        # The pre-migration image is separate from the publication backup.
        archives = data / "knowledge-update-backups"
        archives.mkdir(exist_ok=True)
        reset.consistent_backup(data / "data/gw_ap_debug.db",
            archives / ("before-install-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".sqlite3"))
        run_database_migrations()
        def manager():
            specs = tuple(s for s in launcher.load_bundled_model_specs() if s.task_type == "embedding")
            if len(specs) != 1:
                raise ValueError("The complete bundled Embedding component is required")
            return launcher.BundledModelManager(specs, data_root=data / "data", timeout_seconds=600)
        with SessionLocal() as db:
            profile = get_active_model_profile("embedding", db)
            external = bool(profile and profile.mode == "api" and profile.provider != MANAGED_LOCAL_PROVIDER)
        result = run_update(data_root=data / "data", source_zip=PACKAGE / "bundled-knowledge/hilink-diag.zip",
            archive_root=archives, manager_factory=manager, allow_external=external)
        # A receipt binds this installation to the durable database publication.
        receipt = {"status": result["status"], "source_sha256": reset.read_bundle(
            PACKAGE / "bundled-knowledge/hilink-diag.zip")["source_sha256"],
            "package_version": json.loads((PACKAGE / "build-info.json").read_text(encoding="utf-8-sig"))["package_version"]}
        print("[OK] Installed network Skill: " + json.dumps(receipt), flush=True)
        return result["status"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    try:
        args = parser.parse_args()
        apply(args.data_root, check_only=args.check_only)
    except (OSError, ValueError, RuntimeError) as error:
        print("[GWAP_INSTALL_ERROR] Knowledge update did not complete: " + str(error), flush=True)
        raise SystemExit(1)
