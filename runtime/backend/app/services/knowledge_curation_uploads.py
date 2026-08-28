from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.utils import new_id
from app.services.knowledge_curation_common import CurationConflict, CurationError
from app.services.storage import StorageService, storage


WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def normalize_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip("/")
    if not normalized or "\x00" in normalized:
        raise CurationError("Folder contains an empty or invalid file path")
    path = PurePosixPath(normalized)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise CurationError(f"Unsafe folder path: {value}")
    if len(path.parts) > get_settings().max_archive_depth:
        raise CurationError(f"Folder path is too deep: {value}")
    if len(normalized) > 1000:
        raise CurationError(f"Folder path is too long: {value[:120]}")
    for part in path.parts:
        if any(character in part for character in '<>:"|?*'):
            raise CurationError(f"Folder path contains unsupported characters: {value}")
        if part.rstrip(" .") != part:
            raise CurationError(f"Folder path has a trailing dot or space: {value}")
        if part.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES:
            raise CurationError(f"Folder path uses a reserved Windows name: {value}")
    return path.as_posix()


def _classify_source_role(relative_path: str) -> str:
    value = relative_path.casefold()
    if any(token in value for token in ("solution", "resolve", "fix", "解决", "方案", "修复")):
        return "solution"
    if any(token in value for token in ("analysis", "rootcause", "root_cause", "分析", "根因", "定位")):
        return "analysis"
    if any(token in value for token in ("error", "failure", "issue", "故障", "错误", "异常")):
        return "error"
    if any(token in value for token in ("log", "trace", "debug", "日志")):
        return "log"
    return "context"


async def persist_curation_uploads(
    session_id: str,
    uploads: list[UploadFile],
    relative_paths: list[str],
    *,
    storage_service: StorageService = storage,
) -> list[dict[str, Any]]:
    settings = get_settings()
    if not uploads:
        raise CurationError("Select at least one source file")
    if len(uploads) > settings.curation_max_files:
        raise CurationError(
            f"Folder contains more than {settings.curation_max_files} files"
        )
    if relative_paths and len(relative_paths) != len(uploads):
        raise CurationError("Folder path list does not match the uploaded files")

    curation_root = storage_service.root / "curations"
    curation_root.mkdir(parents=True, exist_ok=True)
    final_dir = curation_root / session_id
    staging_dir = curation_root / f".{session_id}.uploading-{uuid.uuid4().hex}"
    if final_dir.exists():
        raise CurationConflict("Curation source directory already exists")
    staging_dir.mkdir(parents=True, exist_ok=False)
    total_bytes = 0
    seen_paths: set[str] = set()
    manifest: list[dict[str, Any]] = []
    try:
        for index, upload in enumerate(uploads, start=1):
            candidate = (
                relative_paths[index - 1]
                if relative_paths
                else upload.filename or f"source-{index}.txt"
            )
            relative_path = normalize_relative_path(candidate)
            folded = relative_path.casefold()
            if folded in seen_paths:
                raise CurationError(f"Folder contains a duplicate path: {relative_path}")
            seen_paths.add(folded)
            target = staging_dir / "sources" / Path(*PurePosixPath(relative_path).parts)
            _, size, digest = await storage_service.save_upload_to_path(
                upload,
                target,
                max_size=settings.curation_max_file_bytes,
            )
            total_bytes += size
            if total_bytes > settings.curation_max_total_bytes:
                raise CurationError(
                    "Folder exceeds the configured total upload limit: "
                    f"{settings.curation_max_total_bytes} bytes"
                )
            manifest.append({
                "id": new_id("KSRC"),
                "source_ref": f"SRC-{index:04d}",
                "relative_path": relative_path,
                "staged_path": target,
                "sha256": digest,
                "size_bytes": size,
                "media_type": upload.content_type,
                "source_role": _classify_source_role(relative_path),
            })
        os.replace(staging_dir, final_dir)
        for item in manifest:
            final_path = final_dir / "sources" / Path(
                *PurePosixPath(item["relative_path"]).parts
            )
            item["stored_path"] = storage_service.storage_key(final_path)
            item.pop("staged_path", None)
        return manifest
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        if final_dir.exists():
            shutil.rmtree(final_dir)
        raise
