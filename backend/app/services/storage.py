import shutil
from pathlib import Path
from typing import BinaryIO

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.utils import sha256_file


def normalize_debug_log_filename(filename: str | None) -> tuple[str, str]:
    original_name = Path((filename or "collectDebuginfo").replace("\\", "/")).name
    normalized_name = original_name if Path(original_name).suffix else f"{original_name}.txt"
    return original_name, normalized_name


class StorageService:
    def __init__(self) -> None:
        self.root = get_settings().storage_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def artifact_dir(self, artifact_id: str) -> Path:
        path = self.root / "artifacts" / artifact_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def repository_dir(self, repository_id: str) -> Path:
        path = self.root / "repositories" / repository_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def report_dir(self, case_id: str) -> Path:
        path = self.root / "reports" / case_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def save_upload(
        self,
        upload: UploadFile,
        artifact_id: str,
        target_name: str | None = None,
    ) -> tuple[Path, int, str]:
        target_dir = self.artifact_dir(artifact_id)
        safe_name = Path(target_name or upload.filename or "upload.bin").name
        target = target_dir / safe_name
        max_size = get_settings().max_upload_bytes
        size = 0
        with target.open("wb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > max_size:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise ValueError(f"Upload exceeds configured limit: {max_size} bytes")
                output.write(chunk)
        return target, size, sha256_file(target)

    def copy_stream(self, stream: BinaryIO, target: Path) -> tuple[int, str]:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as output:
            shutil.copyfileobj(stream, output)
        return target.stat().st_size, sha256_file(target)


storage = StorageService()
