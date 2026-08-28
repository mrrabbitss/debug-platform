from __future__ import annotations

import hashlib
import ipaddress
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit

import httpx

from app.core.config import get_settings
from app.core.utils import utcnow
from app.services.jobs import JobContext
from app.services.model_download_storage import (
    MANIFEST_MAX_BYTES,
    MODEL_DOWNLOAD_GENERATION_MANIFEST,
    MODEL_DOWNLOAD_GENERATIONS_DIRECTORY,
    MODEL_DOWNLOAD_MANIFEST,
    MODEL_DOWNLOAD_STAGING_DIRECTORY,
    ModelStorageError,
    activate_generation,
    active_directory,
    atomic_write_json,
    generation_directory,
    generation_identifier,
    manifest_is_complete,
    model_download_lock,
    publish_staging_generation,
    read_manifest,
    safe_relative_path,
    safe_target_path,
    staging_directory,
)
from app.services.model_transport import verified_ssl_context_without_revocation
from app.services.secrets import decrypt_secret


MODEL_DOWNLOAD_JOB_KIND = "download_model_files"
_CHUNK_SIZE = 1024 * 1024
_MANIFEST_MAX_BYTES = MANIFEST_MAX_BYTES


class ModelDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelDownloadSpec:
    model_id: str
    display_name: str
    task_type: str
    relative_directory: PurePosixPath


@dataclass(frozen=True)
class RemoteModelFile:
    relative_path: PurePosixPath
    size: int | None
    sha256: str | None


@dataclass(frozen=True)
class RemoteModelManifest:
    requested_revision: str
    resolved_revision: str
    files: tuple[RemoteModelFile, ...]


SUPPORTED_MODEL_DOWNLOADS: dict[str, ModelDownloadSpec] = {
    "BAAI/bge-base-zh-v1.5": ModelDownloadSpec(
        model_id="BAAI/bge-base-zh-v1.5",
        display_name="BGE Base 中文 Embedding",
        task_type="embedding",
        relative_directory=PurePosixPath("embedding/bge-base-zh-v1.5"),
    ),
    "Qwen/Qwen3-Reranker-0.6B": ModelDownloadSpec(
        model_id="Qwen/Qwen3-Reranker-0.6B",
        display_name="Qwen3 Reranker 0.6B",
        task_type="reranker",
        relative_directory=PurePosixPath("reranker/Qwen3-Reranker-0.6B"),
    ),
}


def _download_root() -> Path:
    configured = get_settings().model_download_root
    if configured is None:  # The Settings validator normally fills this value.
        configured = get_settings().data_root / "models"
    return configured.expanduser().resolve()


def _normalize_mirror_shape(value: str) -> str:
    cleaned = value.strip()
    if len(cleaned) > 2048 or any(
        character.isspace() or ord(character) == 127
        for character in cleaned
    ):
        raise ValueError("Model mirror URL is invalid")
    try:
        parsed = urlsplit(cleaned)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Model mirror URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Model mirror URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Model mirror URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Model mirror URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("Model mirror URL must not contain a query string or fragment")
    decoded_parts = [unquote(part) for part in parsed.path.split("/") if part]
    if any(part in {".", ".."} for part in decoded_parts):
        raise ValueError("Model mirror URL path is invalid")
    host = parsed.hostname.rstrip(".").lower()
    if host.startswith("metadata.") or host in {
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data.ec2.internal",
    }:
        raise ValueError("Cloud metadata endpoints cannot be used as model mirrors")
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = f"{rendered_host}:{port}" if port else rendered_host
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{netloc}{path}"


def allowed_model_mirrors() -> list[str]:
    configured = get_settings().model_download_mirror_entries
    mirrors: list[str] = []
    for entry in configured:
        normalized = _normalize_mirror_shape(entry)
        if normalized not in mirrors:
            mirrors.append(normalized)
    if not mirrors:
        raise ValueError("MODEL_DOWNLOAD_MIRRORS must contain at least one valid URL")
    return mirrors


def validate_model_mirror(value: str) -> str:
    normalized = _normalize_mirror_shape(value)
    if normalized not in allowed_model_mirrors():
        raise ValueError(
            "Model mirror is not enabled by MODEL_DOWNLOAD_MIRRORS"
        )
    return normalized


def validate_download_proxy(proxy_url: str | None) -> str:
    cleaned = (proxy_url or "").strip()
    if not cleaned:
        return ""
    if len(cleaned) > 2048 or any(
        character.isspace() or ord(character) == 127
        for character in cleaned
    ):
        raise ValueError("Model download proxy URL is invalid")
    try:
        parsed = urlsplit(cleaned)
        parsed.port
    except ValueError as exc:
        raise ValueError("Model download proxy URL is invalid") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Model download proxy URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Model download proxy URL must include a hostname")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(
            "Model download proxy URL must not contain a path, query string or fragment"
        )
    host = parsed.hostname.rstrip(".").lower()
    if host.startswith("metadata.") or host in {
        "metadata.google.internal",
        "metadata.azure.internal",
        "instance-data.ec2.internal",
    }:
        raise ValueError("Cloud metadata endpoints cannot be used as download proxies")
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        pass
    else:
        if (
            address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        ):
            raise ValueError("Model download proxy uses a blocked network address")
    return cleaned


def proxy_url_hint(proxy_url: str) -> str | None:
    if not proxy_url:
        return None
    parsed = urlsplit(proxy_url)
    if not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    rendered_host = f"[{host}]" if ":" in host else host
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme.lower()}://{rendered_host}{port}"


def _model_target(spec: ModelDownloadSpec) -> Path:
    root = _download_root()
    target = (root / Path(*spec.relative_directory.parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:  # pragma: no cover - catalog constants are static.
        raise ModelDownloadError("Model catalog target escapes the managed root") from exc
    return target


def _read_local_manifest(target: Path) -> dict[str, Any]:
    return read_manifest(target)


def _manifest_is_complete(target: Path, payload: dict[str, Any]) -> bool:
    return manifest_is_complete(target, payload)


def model_download_catalog() -> dict[str, Any]:
    root = _download_root()
    models: list[dict[str, Any]] = []
    for spec in SUPPORTED_MODEL_DOWNLOADS.values():
        target = _model_target(spec)
        payload = _read_local_manifest(target)
        active = active_directory(target, payload)
        complete = bool(payload) and _manifest_is_complete(active, payload)
        partial_files: list[Path] = []
        managed_files: list[Path] = []
        if target.is_dir():
            for path in target.rglob("*"):
                if not path.is_file() or path.name in {
                    MODEL_DOWNLOAD_MANIFEST,
                    MODEL_DOWNLOAD_GENERATION_MANIFEST,
                }:
                    continue
                if path.name.endswith(".partial"):
                    partial_files.append(path)
                elif MODEL_DOWNLOAD_GENERATIONS_DIRECTORY not in path.parts:
                    managed_files.append(path)
        manifest_files = payload.get("files") if complete else []
        file_count = len(manifest_files) if isinstance(manifest_files, list) else 0
        size_bytes = (
            sum(int(item.get("size") or 0) for item in manifest_files)
            if isinstance(manifest_files, list)
            else 0
        )
        has_staging = (target / MODEL_DOWNLOAD_STAGING_DIRECTORY).is_dir()
        state = (
            "READY"
            if complete
            else "PARTIAL"
            if managed_files or partial_files or has_staging
            else "NOT_DOWNLOADED"
        )
        models.append({
            "model_id": spec.model_id,
            "display_name": spec.display_name,
            "task_type": spec.task_type,
            "status": state,
            "target_directory": str(active if complete else target),
            "file_count": file_count,
            "partial_file_count": len(partial_files),
            "size_bytes": size_bytes,
            "revision": payload.get("requested_revision"),
            "resolved_revision": payload.get("resolved_revision"),
            "completed_at": payload.get("completed_at"),
        })
    return {
        "download_root": str(root),
        "mirrors": allowed_model_mirrors(),
        "models": models,
        "runtime_installed": False,
    }


def _safe_relative_path(value: str) -> PurePosixPath:
    try:
        return safe_relative_path(value)
    except ModelStorageError as exc:
        raise ModelDownloadError(str(exc)) from exc


def _safe_target_path(
    target_root: Path,
    relative: PurePosixPath,
    *,
    create_parent: bool,
) -> Path:
    try:
        return safe_target_path(
            target_root,
            relative,
            create_parent=create_parent,
        )
    except ModelStorageError as exc:
        raise ModelDownloadError(str(exc)) from exc


def build_model_download_client(
    proxy_url: str | None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    timeout = httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)
    kwargs: dict[str, Any] = {
        "timeout": timeout,
        "follow_redirects": True,
        "trust_env": False,
        "headers": {"Accept-Encoding": "identity"},
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
        kwargs["verify"] = verified_ssl_context_without_revocation()
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


def _safe_http_failure(exc: Exception, action: str, proxy_configured: bool) -> ModelDownloadError:
    route = " through the configured proxy" if proxy_configured else ""
    if isinstance(exc, httpx.ProxyError):
        message = f"Model {action}{route} failed because the proxy rejected the connection"
    elif isinstance(exc, httpx.TimeoutException):
        message = f"Model {action}{route} timed out"
    elif isinstance(exc, httpx.HTTPStatusError):
        message = f"Model {action} returned HTTP {exc.response.status_code}"
    elif isinstance(exc, httpx.RequestError):
        message = f"Model {action}{route} connection failed ({type(exc).__name__})"
    else:
        message = f"Model {action} failed ({type(exc).__name__})"
    return ModelDownloadError(message)


def _remote_sha256(item: dict[str, Any]) -> str | None:
    lfs = item.get("lfs") if isinstance(item.get("lfs"), dict) else {}
    value = str(lfs.get("sha256") or lfs.get("oid") or "").strip().lower()
    if value.startswith("sha256:"):
        value = value.removeprefix("sha256:")
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def _load_remote_manifest(
    client: httpx.Client,
    spec: ModelDownloadSpec,
    mirror_base: str,
    revision: str,
    *,
    proxy_configured: bool,
) -> RemoteModelManifest:
    model_path = quote(spec.model_id, safe="/")
    api_url = f"{mirror_base}/api/models/{model_path}"
    try:
        response = client.get(
            api_url,
            params={"revision": revision, "blobs": "true"},
        )
        response.raise_for_status()
    except Exception as exc:  # httpx subclasses differ by transport.
        raise _safe_http_failure(exc, "manifest request", proxy_configured) from None
    if len(response.content) > _MANIFEST_MAX_BYTES:
        raise ModelDownloadError("Remote model manifest exceeds the configured limit")
    try:
        payload = response.json()
    except ValueError:
        raise ModelDownloadError("Remote model manifest is not valid JSON") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("siblings"), list):
        raise ModelDownloadError("Remote model manifest does not contain a file list")

    settings = get_settings()
    files: list[RemoteModelFile] = []
    seen_paths: set[str] = set()
    known_total = 0
    for raw_item in payload["siblings"]:
        if not isinstance(raw_item, dict):
            raise ModelDownloadError("Remote model manifest contains an invalid file entry")
        relative = _safe_relative_path(str(raw_item.get("rfilename") or ""))
        normalized_path = relative.as_posix().casefold()
        if normalized_path in seen_paths:
            raise ModelDownloadError(
                "Remote model manifest contains duplicate file paths"
            )
        seen_paths.add(normalized_path)
        lfs = raw_item.get("lfs") if isinstance(raw_item.get("lfs"), dict) else {}
        raw_size = raw_item.get("size")
        if raw_size is None:
            raw_size = lfs.get("size")
        size = int(raw_size) if raw_size is not None else None
        if size is not None and (size < 0 or size > settings.model_download_max_file_bytes):
            raise ModelDownloadError("Remote model file exceeds the configured size limit")
        if size is not None:
            known_total += size
        files.append(RemoteModelFile(relative, size, _remote_sha256(raw_item)))
    if not files:
        raise ModelDownloadError("Remote model repository contains no downloadable files")
    if len(files) > settings.model_download_max_files:
        raise ModelDownloadError("Remote model repository contains too many files")
    if known_total > settings.model_download_max_total_bytes:
        raise ModelDownloadError("Remote model repository exceeds the configured total size limit")
    resolved_revision = str(payload.get("sha") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,64}", resolved_revision):
        raise ModelDownloadError(
            "Remote model manifest did not provide a valid immutable commit revision"
        )
    return RemoteModelManifest(
        requested_revision=revision,
        resolved_revision=resolved_revision,
        files=tuple(sorted(files, key=lambda item: item.relative_path.as_posix())),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _previous_file_map(
    payload: dict[str, Any],
    resolved_revision: str,
) -> dict[str, dict[str, Any]]:
    if payload.get("resolved_revision") != resolved_revision:
        return {}
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        return {}
    return {
        str(item.get("path")): item
        for item in raw_files
        if isinstance(item, dict) and item.get("path")
    }


def _existing_file_complete(
    path: Path,
    remote: RemoteModelFile,
    previous: dict[str, Any] | None,
) -> tuple[bool, str | None]:
    if not path.is_file():
        return False, None
    size = path.stat().st_size
    if remote.size is not None and size != remote.size:
        return False, None
    expected_hash = remote.sha256
    if expected_hash:
        actual_hash = _sha256_file(path)
        return actual_hash == expected_hash, actual_hash
    if previous and int(previous.get("size") or -1) == size:
        previous_hash = str(previous.get("sha256") or "")
        if re.fullmatch(r"[0-9a-f]{64}", previous_hash):
            actual_hash = _sha256_file(path)
            return actual_hash == previous_hash, actual_hash
    return False, None


def _finish_partial_file(
    partial: Path,
    final: Path,
    remote: RemoteModelFile,
) -> dict[str, Any]:
    settings = get_settings()
    size = partial.stat().st_size
    if size > settings.model_download_max_file_bytes:
        raise ModelDownloadError("Downloaded model file exceeds the configured size limit")
    if remote.size is not None and size != remote.size:
        raise ModelDownloadError(
            f"Downloaded file size is incomplete: {remote.relative_path.as_posix()}"
        )
    digest = _sha256_file(partial)
    if remote.sha256 and digest != remote.sha256:
        partial.unlink(missing_ok=True)
        raise ModelDownloadError(
            f"Downloaded file SHA-256 verification failed: {remote.relative_path.as_posix()}"
        )
    os.replace(partial, final)
    stat = final.stat()
    return {
        "path": remote.relative_path.as_posix(),
        "size": stat.st_size,
        "sha256": digest,
        "mtime_ns": stat.st_mtime_ns,
        "remote_sha256_verified": bool(remote.sha256),
    }


def _download_file(
    client: httpx.Client,
    context: JobContext,
    *,
    url: str,
    target_root: Path,
    remote: RemoteModelFile,
    progress_callback: Callable[[int], None],
    proxy_configured: bool,
) -> dict[str, Any]:
    final = _safe_target_path(target_root, remote.relative_path, create_parent=True)
    partial = final.with_name(final.name + ".partial")
    if partial.is_symlink():
        raise ModelDownloadError("Symbolic links are not allowed in managed model directories")

    for request_attempt in range(2):
        context.raise_if_cancelled()
        range_start = partial.stat().st_size if partial.is_file() else 0
        headers = {"Range": f"bytes={range_start}-"} if range_start else {}
        try:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code == 416:
                    if remote.size is not None and range_start == remote.size:
                        return _finish_partial_file(partial, final, remote)
                    partial.unlink(missing_ok=True)
                    if request_attempt == 0:
                        continue
                    raise ModelDownloadError("Remote server rejected the resume range")
                response.raise_for_status()
                if response.status_code == 206 and range_start:
                    content_range = response.headers.get("content-range", "")
                    if not content_range.lower().startswith(f"bytes {range_start}-"):
                        partial.unlink(missing_ok=True)
                        if request_attempt == 0:
                            continue
                        raise ModelDownloadError("Remote server returned an invalid resume range")
                    mode = "ab"
                    current_size = range_start
                else:
                    mode = "wb"
                    current_size = 0

                last_update = 0.0
                with partial.open(mode) as stream:
                    for chunk in response.iter_bytes(chunk_size=_CHUNK_SIZE):
                        if not chunk:
                            continue
                        stream.write(chunk)
                        current_size += len(chunk)
                        if current_size > get_settings().model_download_max_file_bytes:
                            raise ModelDownloadError(
                                "Downloaded model file exceeds the configured size limit"
                            )
                        now = time.monotonic()
                        if now - last_update >= 1.0:
                            progress_callback(current_size)
                            last_update = now
                    stream.flush()
                    os.fsync(stream.fileno())
                progress_callback(current_size)
                return _finish_partial_file(partial, final, remote)
        except ModelDownloadError:
            raise
        except Exception as exc:
            raise _safe_http_failure(exc, "file download", proxy_configured) from None
    raise ModelDownloadError("Unable to resume the model file download")


def _generation_payload(
    spec: ModelDownloadSpec,
    mirror_base: str,
    manifest: RemoteModelManifest,
    generation_id: str,
    files: list[dict[str, Any]],
    *,
    complete: bool,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "complete": complete,
        "generation_id": generation_id,
        "model_id": spec.model_id,
        "task_type": spec.task_type,
        "mirror": mirror_base,
        "requested_revision": manifest.requested_revision,
        "resolved_revision": manifest.resolved_revision,
        "completed_at": utcnow().isoformat() if complete else None,
        "files": files,
    }


def _download_result(
    spec: ModelDownloadSpec,
    active: Path,
    payload: dict[str, Any],
    proxy_url: str,
) -> dict[str, Any]:
    files = payload.get("files") if isinstance(payload.get("files"), list) else []
    return {
        "model_id": spec.model_id,
        "task_type": spec.task_type,
        "target_directory": str(active),
        "requested_revision": payload.get("requested_revision"),
        "resolved_revision": payload.get("resolved_revision"),
        "generation_id": payload.get("active_generation") or payload.get("generation_id"),
        "file_count": len(files),
        "size_bytes": sum(int(item.get("size") or 0) for item in files),
        "proxy_configured": bool(proxy_url),
        "proxy_hint": proxy_url_hint(proxy_url),
        "certificate_revocation_check_skipped": bool(proxy_url),
        "tls_certificate_verification": True,
        "runtime_installed": False,
    }


def _migrate_legacy_partial(
    target: Path,
    staging: Path,
    relative: PurePosixPath,
) -> None:
    payload = _read_local_manifest(target)
    if payload.get("active_generation"):
        return
    legacy_final = _safe_target_path(target, relative, create_parent=False)
    legacy_partial = legacy_final.with_name(legacy_final.name + ".partial")
    staged_final = _safe_target_path(staging, relative, create_parent=True)
    staged_partial = staged_final.with_name(staged_final.name + ".partial")
    if legacy_partial.is_file() and not staged_partial.exists():
        os.replace(legacy_partial, staged_partial)


def _download_locked(
    context: JobContext,
    *,
    spec: ModelDownloadSpec,
    target: Path,
    mirror_base: str,
    revision: str,
    proxy_url: str,
    client: httpx.Client,
) -> dict[str, Any]:
    context.update(2, "正在读取模型文件清单")
    remote_manifest = _load_remote_manifest(
        client,
        spec,
        mirror_base,
        revision,
        proxy_configured=bool(proxy_url),
    )
    generation_id = generation_identifier(
        spec.model_id,
        remote_manifest.resolved_revision,
        [
            (item.relative_path.as_posix(), item.size, item.sha256)
            for item in remote_manifest.files
        ],
    )
    generation = generation_directory(target, generation_id)
    existing_generation = read_manifest(
        generation,
        MODEL_DOWNLOAD_GENERATION_MANIFEST,
    )
    if (
        existing_generation.get("generation_id") == generation_id
        and manifest_is_complete(generation, existing_generation)
    ):
        pointer = _generation_payload(
            spec,
            mirror_base,
            remote_manifest,
            generation_id,
            list(existing_generation["files"]),
            complete=True,
        )
        try:
            active, published = activate_generation(
                target,
                generation_id,
                pointer,
            )
        except ModelStorageError as exc:
            raise ModelDownloadError(str(exc)) from exc
        context.update(99, "模型分代已校验并保持为当前版本")
        return _download_result(spec, active, published, proxy_url)

    staging = staging_directory(target, generation_id)
    staging.mkdir(parents=True, exist_ok=True)
    progress_manifest = read_manifest(staging, MODEL_DOWNLOAD_GENERATION_MANIFEST)
    previous = (
        _previous_file_map(progress_manifest, remote_manifest.resolved_revision)
        if progress_manifest.get("generation_id") == generation_id
        else {}
    )
    known_total = sum(item.size or 0 for item in remote_manifest.files)
    all_sizes_known = all(item.size is not None for item in remote_manifest.files)
    completed_bytes = 0
    completed_files: list[dict[str, Any]] = []
    settings = get_settings()

    for index, remote in enumerate(remote_manifest.files):
        context.raise_if_cancelled()
        relative_text = remote.relative_path.as_posix()
        final = _safe_target_path(staging, remote.relative_path, create_parent=True)
        complete, digest = _existing_file_complete(
            final,
            remote,
            previous.get(relative_text),
        )
        if complete:
            stat = final.stat()
            completed_bytes += stat.st_size
            completed_files.append({
                "path": relative_text,
                "size": stat.st_size,
                "sha256": digest or _sha256_file(final),
                "mtime_ns": stat.st_mtime_ns,
                "remote_sha256_verified": bool(remote.sha256),
            })
            progress = int(((index + 1) / len(remote_manifest.files)) * 98)
            context.update(max(2, min(99, progress)), f"已校验 {relative_text}")
            continue

        _migrate_legacy_partial(target, staging, remote.relative_path)
        partial = final.with_name(final.name + ".partial")
        if (
            final.is_file()
            and not partial.exists()
            and remote.size is not None
            and 0 < final.stat().st_size < remote.size
            and relative_text not in previous
        ):
            os.replace(final, partial)

        file_path = quote(relative_text, safe="/")
        revision_path = quote(remote_manifest.resolved_revision, safe="")
        url = (
            f"{mirror_base}/{quote(spec.model_id, safe='/')}"
            f"/resolve/{revision_path}/{file_path}"
        )

        def report(current_size: int) -> None:
            aggregate = completed_bytes + current_size
            if aggregate > settings.model_download_max_total_bytes:
                raise ModelDownloadError(
                    "Model download exceeds the configured total size limit"
                )
            if all_sizes_known and known_total:
                progress = 2 + int((aggregate / known_total) * 96)
            else:
                fraction = min(
                    1.0,
                    current_size / max(1, remote.size or current_size or 1),
                )
                progress = 2 + int(
                    ((index + fraction) / len(remote_manifest.files)) * 96
                )
            context.update(
                max(2, min(99, progress)),
                f"正在下载 {relative_text}",
            )

        downloaded = _download_file(
            client,
            context,
            url=url,
            target_root=staging,
            remote=remote,
            progress_callback=report,
            proxy_configured=bool(proxy_url),
        )
        completed_files.append(downloaded)
        completed_bytes += int(downloaded["size"])
        atomic_write_json(
            staging,
            MODEL_DOWNLOAD_GENERATION_MANIFEST,
            _generation_payload(
                spec,
                mirror_base,
                remote_manifest,
                generation_id,
                completed_files,
                complete=False,
            ),
        )

    context.raise_if_cancelled()
    payload = _generation_payload(
        spec,
        mirror_base,
        remote_manifest,
        generation_id,
        completed_files,
        complete=True,
    )
    try:
        active, published = publish_staging_generation(
            target,
            staging,
            generation_id,
            payload,
        )
    except ModelStorageError as exc:
        raise ModelDownloadError(str(exc)) from exc
    context.update(99, "模型分代已完整校验并原子发布")
    return _download_result(spec, active, published, proxy_url)


def download_model_repository(
    context: JobContext,
    *,
    model_id: str,
    mirror_base: str,
    revision: str,
    proxy_url: str | None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    spec = SUPPORTED_MODEL_DOWNLOADS.get(model_id)
    if not spec:
        raise ModelDownloadError("Unsupported model download")
    mirror_base = validate_model_mirror(mirror_base)
    proxy_url = validate_download_proxy(proxy_url)
    target = _model_target(spec)
    target.mkdir(parents=True, exist_ok=True)
    owned_client = client is None
    client = client or build_model_download_client(proxy_url)
    try:
        with model_download_lock(target, context):
            return _download_locked(
                context,
                spec=spec,
                target=target,
                mirror_base=mirror_base,
                revision=revision,
                proxy_url=proxy_url,
                client=client,
            )
    finally:
        if owned_client:
            client.close()


def download_model_job(
    context: JobContext,
    model_id: str,
    mirror_base: str,
    revision: str,
    proxy_url_ciphertext: str,
) -> dict[str, Any]:
    proxy_url = decrypt_secret(
        proxy_url_ciphertext,
        "model download proxy URL",
    )
    return download_model_repository(
        context,
        model_id=model_id,
        mirror_base=mirror_base,
        revision=revision,
        proxy_url=proxy_url,
    )
