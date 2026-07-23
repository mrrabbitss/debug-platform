import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.services.backup import BackupError, create_backup  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Create a verified SQLite, storage and model-key backup archive."
    )
    result.add_argument("--output", type=Path, help="Destination .zip (must not already exist)")
    return result


def main() -> int:
    args = parser().parse_args()
    settings = get_settings()
    output = args.output or (
        PROJECT_ROOT
        / "backups"
        / f"debug-platform-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.zip"
    )
    print("For a database/storage-consistent backup, stop the local backend before running this command.")
    report = create_backup(
        database_url=settings.database_url,
        storage_root=settings.storage_root,
        model_secret_key_path=settings.model_secret_key_path,
        output_path=output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("This archive contains internal data and may contain the model-key encryption secret. Store it securely.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BackupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
