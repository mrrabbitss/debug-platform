"""Crash-safe storage and locking for managed model weight downloads."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Protocol


MODEL_DOWNLOAD_MANIFEST = ".debug-platform-model.json"
MODEL_DOWNLOAD_GENERATION_MANIFEST = ".debug-platform-generation.json"
MODEL_DOWNLOAD_GENERATIONS_DIRECTORY = ".generations"
MODEL_DOWNLOAD_STAGING_DIRECTORY = ".staging"
MODEL_DOWNLOAD_QUARANTINE_DIRECTORY = ".quarantine"
MANIFEST_MAX_BYTES = 8 * 1024 * 1024

_WINDOWS_RESERVED_NAMES = {
    "aux",
    "clock$",
    "con",
    "nul",
    "prn",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
_MANAGED_NAMES = {
    MODEL_DOWNLOAD_MANIFEST,
    MODEL_DOWNLOAD_GENERATION_MANIFEST,
    MODEL_DOWNLOAD_GENERATIONS_DIRECTORY,
    MODEL_DOWNLOAD_STAGING_DIRECTORY,
    MODEL_DOWNLOAD_QUARANTINE_DIRECTORY,
}
_MANAGED_NAMES_CASEFOLD = {name.casefold() for name in _MANAGED_NAMES}
_THREAD_LOCKS: dict[str, threading.Lock] = {}
_THREAD_LOCKS_GUARD = threading.Lock()


class DownloadLockContext(Protocol):
    def update(self, progress: int, message: str) -> None: ...

    def raise_if_cancelled(self) -> None: ...


class ModelStorageError(RuntimeError):
    pass


def safe_relative_path(value: str) -> PurePosixPath:
    if not value or len(value) > 1024 or "\\" in value or "\x00" in value:
        raise ModelStorageError("Remote model manifest contains an unsafe file path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ModelStorageError("Remote model manifest contains an unsafe file path")
    for part in path.parts:
        stem = part.split(".", 1)[0].casefold()
        if (
            part.casefold() in _MANAGED_NAMES_CASEFOLD
            or part.endswith((" ", "."))
            or ":" in part
            or any(ord(character) < 32 for character in part)
            or stem in _WINDOWS_RESERVED_NAMES
        ):
            raise ModelStorageError("Remote model manifest contains an unsafe file path")
    if path.name.casefold().endswith(".partial"):
        raise ModelStorageError("Remote model manifest uses a reserved file name")
    return path


def safe_target_path(
    target_root: Path,
    relative: PurePosixPath,
    *,
    create_parent: bool,
) -> Path:
    resolved_root = target_root.resolve()
    candidate = target_root.joinpath(*relative.parts)
    if create_parent:
        candidate.parent.mkdir(parents=True, exist_ok=True)
    resolved_parent = candidate.parent.resolve()
    try:
        resolved_parent.relative_to(resolved_root)
    except ValueError as exc:
        raise ModelStorageError(
            "Model file path escapes the managed target directory"
        ) from exc
    target = resolved_parent / candidate.name
    if target.is_symlink():
        raise ModelStorageError(
            "Symbolic links are not allowed in managed model directories"
        )
    return target


def read_manifest(directory: Path, name: str = MODEL_DOWNLOAD_MANIFEST) -> dict[str, Any]:
    manifest_path = directory / name
    try:
        if (
            not manifest_path.is_file()
            or manifest_path.stat().st_size > MANIFEST_MAX_BYTES
        ):
            return {}
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_is_complete(
    directory: Path,
    payload: dict[str, Any],
    *,
    force_hash: bool = False,
) -> bool:
    files = payload.get("files")
    if payload.get("complete") is False or not isinstance(files, list) or not files:
        return False
    for item in files:
        if not isinstance(item, dict):
            return False
        try:
            relative = safe_relative_path(str(item.get("path") or ""))
            expected_size = int(item["size"])
            expected_hash = str(item["sha256"]).lower()
            path = safe_target_path(directory, relative, create_parent=False)
        except (KeyError, OSError, TypeError, ValueError, ModelStorageError):
            return False
        if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
            return False
        try:
            stat = path.stat()
        except OSError:
            return False
        if not path.is_file() or stat.st_size != expected_size:
            return False
        recorded_mtime = item.get("mtime_ns")
        unchanged_since_verified = (
            isinstance(recorded_mtime, int) and recorded_mtime == stat.st_mtime_ns
        )
        if (force_hash or not unchanged_since_verified) and _sha256_file(path) != expected_hash:
            return False
    return True


def generation_identifier(
    model_id: str,
    resolved_revision: str,
    files: list[tuple[str, int | None, str | None]],
) -> str:
    payload = json.dumps(
        {
            "model_id": model_id,
            "resolved_revision": resolved_revision,
            "files": files,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def generation_directory(target: Path, generation_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", generation_id):
        raise ModelStorageError("Managed model generation ID is invalid")
    root = (target / MODEL_DOWNLOAD_GENERATIONS_DIRECTORY).resolve()
    generation = (root / generation_id).resolve()
    if generation.parent != root or generation.is_symlink():
        raise ModelStorageError("Managed model generation path is unsafe")
    return generation


def staging_directory(target: Path, generation_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{64}", generation_id):
        raise ModelStorageError("Managed model generation ID is invalid")
    root = (target / MODEL_DOWNLOAD_STAGING_DIRECTORY).resolve()
    staging = (root / generation_id).resolve()
    if staging.parent != root or staging.is_symlink():
        raise ModelStorageError("Managed model staging path is unsafe")
    return staging


def active_directory(target: Path, payload: dict[str, Any]) -> Path:
    generation_id = payload.get("active_generation")
    if generation_id is None:
        return target
    try:
        return generation_directory(target, str(generation_id))
    except ModelStorageError:
        return target / MODEL_DOWNLOAD_GENERATIONS_DIRECTORY / "invalid"


def atomic_write_json(directory: Path, name: str, payload: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    temporary = directory / f".{name}.{uuid.uuid4().hex}.tmp"
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, destination)


def _unexpected_generation_files(
    directory: Path,
    payload: dict[str, Any],
) -> list[str]:
    expected = {
        str(item["path"])
        for item in payload.get("files", [])
        if isinstance(item, dict) and item.get("path")
    }
    expected.add(MODEL_DOWNLOAD_GENERATION_MANIFEST)
    return sorted(
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.relative_to(directory).as_posix() not in expected
    )


def activate_generation(
    target: Path,
    generation_id: str,
    payload: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    generation = generation_directory(target, generation_id)
    existing = read_manifest(generation, MODEL_DOWNLOAD_GENERATION_MANIFEST)
    if (
        existing.get("generation_id") != generation_id
        or not manifest_is_complete(generation, existing, force_hash=True)
    ):
        raise ModelStorageError("Managed model generation failed integrity validation")
    published = {**payload, "complete": True, "active_generation": generation_id}
    atomic_write_json(target, MODEL_DOWNLOAD_MANIFEST, published)
    return generation, published


def publish_staging_generation(
    target: Path,
    staging: Path,
    generation_id: str,
    payload: dict[str, Any],
) -> tuple[Path, dict[str, Any]]:
    published = {
        **payload,
        "schema_version": 2,
        "complete": True,
        "generation_id": generation_id,
    }
    atomic_write_json(staging, MODEL_DOWNLOAD_GENERATION_MANIFEST, published)
    if not manifest_is_complete(staging, published, force_hash=True):
        raise ModelStorageError("Downloaded model generation failed integrity validation")
    unexpected = _unexpected_generation_files(staging, published)
    if unexpected:
        raise ModelStorageError(
            f"Downloaded model generation contains unexpected files: {unexpected[0]}"
        )

    generation = generation_directory(target, generation_id)
    generation.parent.mkdir(parents=True, exist_ok=True)
    if generation.exists():
        existing = read_manifest(generation, MODEL_DOWNLOAD_GENERATION_MANIFEST)
        if (
            existing.get("generation_id") == generation_id
            and manifest_is_complete(generation, existing, force_hash=True)
        ):
            shutil.rmtree(staging)
        else:
            quarantine_root = target / MODEL_DOWNLOAD_QUARANTINE_DIRECTORY
            quarantine_root.mkdir(parents=True, exist_ok=True)
            quarantine = quarantine_root / f"{generation_id}-{time.time_ns()}"
            os.replace(generation, quarantine)
            os.replace(staging, generation)
    else:
        os.replace(staging, generation)

    pointer = {**published, "active_generation": generation_id}
    atomic_write_json(target, MODEL_DOWNLOAD_MANIFEST, pointer)
    return generation, pointer


def _thread_lock(lock_path: Path) -> threading.Lock:
    key = str(lock_path.resolve()).casefold()
    with _THREAD_LOCKS_GUARD:
        return _THREAD_LOCKS.setdefault(key, threading.Lock())


def _try_file_lock(stream) -> bool:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
    import fcntl

    try:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _unlock_file(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def model_download_lock(
    target: Path,
    context: DownloadLockContext,
) -> Iterator[None]:
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{target.name}.download.lock"
    local_lock = _thread_lock(lock_path)
    last_update = 0.0
    while not local_lock.acquire(timeout=0.25):
        context.raise_if_cancelled()
        now = time.monotonic()
        if now - last_update >= 1.0:
            context.update(1, "正在等待同一模型的下载任务完成")
            last_update = now
    stream = None
    file_locked = False
    try:
        stream = lock_path.open("a+b")
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        while not _try_file_lock(stream):
            context.raise_if_cancelled()
            now = time.monotonic()
            if now - last_update >= 1.0:
                context.update(1, "正在等待其他平台实例完成模型下载")
                last_update = now
            time.sleep(0.25)
        file_locked = True
        yield
    finally:
        if stream is not None:
            if file_locked:
                _unlock_file(stream)
            stream.close()
        local_lock.release()
