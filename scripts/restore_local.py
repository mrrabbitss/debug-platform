import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services.backup import BackupError, restore_backup, sqlite_database_path  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Verify and restore a local backup, preserving previous data in a rollback directory."
    )
    result.add_argument("--archive", required=True, type=Path)
    result.add_argument("--confirm", required=True, help='Must be exactly "RESTORE"')
    return result


def main() -> int:
    args = parser().parse_args()
    settings = get_settings()
    print("The backend and frontend must be stopped before restore. The old data will be retained for rollback.")
    report = restore_backup(
        archive_path=args.archive,
        database_path=sqlite_database_path(settings.database_url),
        storage_root=settings.storage_root,
        model_secret_key_path=settings.model_secret_key_path,
        rollback_root=settings.data_root / "restore_rollbacks",
        confirmation=args.confirm,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("Restore completed. Start the platform normally so pending database migrations can run.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
