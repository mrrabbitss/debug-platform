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
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or get_settings().storage_root).resolve()
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

    def storage_key(self, path: Path) -> str:
        """Persist portable relative keys for files below the configured root.

        Absolute paths are retained only for legacy/test artifacts that live
        outside the managed storage root.
        """
        resolved = path.resolve()
        if resolved == self.root:
            return "."
        if self.root in resolved.parents:
            return resolved.relative_to(self.root).as_posix()
        return str(resolved)

    def resolve_path(self, value: str | Path) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path.resolve()
        resolved = (self.root / path).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ValueError("Stored path escapes the configured storage root")
        return resolved

    def _remove_managed_tree(self, path: Path) -> bool:
        resolved = path.resolve()
        if resolved == self.root or self.root not in resolved.parents:
            raise ValueError("Refusing to remove a path outside the managed storage root")
        if not resolved.exists():
            return False
        shutil.rmtree(resolved)
        return True

    def remove_artifact(self, artifact_id: str) -> bool:
        return self._remove_managed_tree(self.root / "artifacts" / artifact_id)

    def remove_repository(self, repository_id: str) -> bool:
        return self._remove_managed_tree(self.root / "repositories" / repository_id)

    def remove_case_reports(self, case_id: str) -> bool:
        return self._remove_managed_tree(self.root / "reports" / case_id)

    def cleanup_case(self, artifact_ids: list[str], repository_ids: list[str], case_id: str) -> list[str]:
        errors: list[str] = []
        for artifact_id in artifact_ids:
            try:
                self.remove_artifact(artifact_id)
            except (OSError, ValueError) as exc:
                errors.append(f"artifact {artifact_id}: {exc}")
        for repository_id in repository_ids:
            try:
                self.remove_repository(repository_id)
            except (OSError, ValueError) as exc:
                errors.append(f"repository {repository_id}: {exc}")
        try:
            self.remove_case_reports(case_id)
        except (OSError, ValueError) as exc:
            errors.append(f"reports {case_id}: {exc}")
        return errors

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
