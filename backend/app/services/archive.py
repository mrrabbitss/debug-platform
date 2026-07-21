import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings
from app.services.text_files import looks_like_text_file


class UnsafeArchiveError(ValueError):
    pass


@dataclass
class ExtractManifest:
    root: str
    files: list[dict] = field(default_factory=list)
    total_bytes: int = 0
    skipped: list[dict] = field(default_factory=list)


def _safe_target(root: Path, member_name: str) -> Path:
    normalized = member_name.replace("\\", "/")
    if normalized.startswith("/") or "\x00" in normalized:
        raise UnsafeArchiveError(f"Unsafe absolute or invalid path: {member_name}")
    target = (root / normalized).resolve()
    root_resolved = root.resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise UnsafeArchiveError(f"Archive path escapes destination: {member_name}")
    if len(Path(normalized).parts) > get_settings().max_archive_depth:
        raise UnsafeArchiveError(f"Archive path too deep: {member_name}")
    return target


def _check_limits(manifest: ExtractManifest, file_size: int) -> None:
    settings = get_settings()
    if len(manifest.files) >= settings.max_archive_files:
        raise UnsafeArchiveError("Archive contains too many files")
    if file_size > settings.max_single_file_bytes:
        raise UnsafeArchiveError("Archive member exceeds maximum single-file size")
    if manifest.total_bytes + file_size > settings.max_extracted_bytes:
        raise UnsafeArchiveError("Archive exceeds maximum extracted size")


def extract_archive(source: Path, destination: Path) -> ExtractManifest:
    destination.mkdir(parents=True, exist_ok=True)
    manifest = ExtractManifest(root=str(destination))
    lower = source.name.lower()
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                target = _safe_target(destination, info.filename)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                mode = info.external_attr >> 16
                if mode and (mode & 0o170000) == 0o120000:
                    manifest.skipped.append({"name": info.filename, "reason": "symlink"})
                    continue
                _check_limits(manifest, info.file_size)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source_handle, target.open("wb") as output:
                    shutil.copyfileobj(source_handle, output, length=1024 * 1024)
                manifest.total_bytes += target.stat().st_size
                manifest.files.append({"path": str(target.relative_to(destination)), "size": target.stat().st_size})
        return manifest

    if tarfile.is_tarfile(source):
        with tarfile.open(source, mode="r:*") as archive:
            for member in archive.getmembers():
                target = _safe_target(destination, member.name)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    manifest.skipped.append({"name": member.name, "reason": "non-regular-file"})
                    continue
                _check_limits(manifest, member.size)
                file_obj = archive.extractfile(member)
                if file_obj is None:
                    manifest.skipped.append({"name": member.name, "reason": "unreadable"})
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with file_obj, target.open("wb") as output:
                    shutil.copyfileobj(file_obj, output, length=1024 * 1024)
                os.chmod(target, 0o600)
                manifest.total_bytes += target.stat().st_size
                manifest.files.append({"path": str(target.relative_to(destination)), "size": target.stat().st_size})
        return manifest

    known_log_suffix = lower.endswith((
        ".log", ".txt", ".json", ".jsonl", ".xml", ".conf", ".cfg", ".out", ".err", ".trace",
        ".ini", ".status", ".info", ".dump",
    ))
    if known_log_suffix or (not source.suffix and looks_like_text_file(source)):
        _check_limits(manifest, source.stat().st_size)
        target = _safe_target(destination, source.name)
        shutil.copy2(source, target)
        manifest.total_bytes = target.stat().st_size
        manifest.files.append({"path": target.name, "size": target.stat().st_size})
        return manifest

    raise UnsafeArchiveError("Unsupported archive or text log file type")
