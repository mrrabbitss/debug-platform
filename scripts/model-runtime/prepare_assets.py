#!/usr/bin/env python3
# ruff: noqa: E402 -- prevent bytecode before importing the sibling validator
"""Prepare a verified Win11 CPU GGUF cache for the offline installer.

No network or filesystem mutation occurs unless an action flag is supplied.
The default invocation prints a deterministic plan.  Large files are never
written inside the Git worktree unless --cache-root explicitly points there.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse, urlunparse

sys.dont_write_bytecode = True

from validate_assets import (
    LLAMA_REVISION,
    LLAMA_TAG,
    MSVC_RUNTIME_FILES,
    QWEN_GGUF_REVISION,
    QWEN_GGUF_SHA256,
    QWEN_REVISION,
    ValidationError,
    _items_for_download,
    _model_by_task,
    _read_json,
    _require,
    _safe_relative,
    known_download_size,
    safe_child,
    sha256_file,
    validate_component_lock,
    validate_manifest,
)


CHUNK_SIZE = 8 * 1024 * 1024
DEFAULT_CACHE = Path("artifacts/build-cache/windows-x64")


def _format_bytes(value: int) -> str:
    return f"{value / (1024 ** 3):.3f} GiB ({value:,} bytes)"


def _fallback_files(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    reranker = _model_by_task(manifest, "reranker")
    fallback = reranker.get("fallback_build")
    if not isinstance(fallback, dict):
        return []
    return [
        (f"{reranker['id']}.fallback_build.source_files[{index}]", item)
        for index, item in enumerate(fallback.get("source_files", []))
    ]


def _default_downloads(
    manifest: dict[str, Any], *, qwen_from_source: bool
) -> list[tuple[str, dict[str, Any]]]:
    items = _items_for_download(manifest)
    if not qwen_from_source:
        return items
    reranker = _model_by_task(manifest, "reranker")
    primary_label = f"{reranker['id']}.artifact"
    return [(label, item) for label, item in items if label != primary_label]


def _rewrite_hf_endpoint(url: str, endpoint: str | None) -> str:
    if not endpoint:
        return url
    parsed = urlparse(url)
    if parsed.hostname != "huggingface.co":
        return url
    endpoint_parsed = urlparse(endpoint.rstrip("/"))
    _require(endpoint_parsed.scheme == "https" and bool(endpoint_parsed.netloc),
             "--hf-endpoint must be an HTTPS origin")
    return urlunparse((endpoint_parsed.scheme, endpoint_parsed.netloc, parsed.path,
                       parsed.params, parsed.query, parsed.fragment))


def _verify_file(path: Path, item: dict[str, Any], label: str) -> bool:
    if not path.is_file():
        return False
    actual_size = path.stat().st_size
    _require(actual_size == item["size_bytes"],
             f"existing {label} has size {actual_size}, expected {item['size_bytes']}: {path}")
    _require(sha256_file(path) == item["sha256"], f"existing {label} has the wrong SHA-256: {path}")
    return True


def download_file(
    item: dict[str, Any],
    label: str,
    root: Path,
    *,
    hf_endpoint: str | None,
    offline: bool,
) -> Path:
    destination = safe_child(root, item["relative_path"], label)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _verify_file(destination, item, label):
        print(f"[REUSE] {label}: {destination}")
        return destination

    partial = destination.with_name(destination.name + ".partial")
    start = partial.stat().st_size if partial.is_file() else 0
    if start > item["size_bytes"]:
        partial.unlink()
        start = 0
    elif start == item["size_bytes"]:
        if sha256_file(partial) == item["sha256"]:
            os.replace(partial, destination)
            print(f"[RECOVER] Completed partial verified for {label}: {destination}")
            return destination
        partial.unlink()
        start = 0
    _require(not offline, f"offline cache is missing {label}: {destination}")
    url = _rewrite_hf_endpoint(item["url"], hf_endpoint)
    headers = {"User-Agent": "debug-platform-model-assets/1"}
    if start:
        headers["Range"] = f"bytes={start}-"
    request = urllib.request.Request(url, headers=headers)
    print(f"[DOWNLOAD] {label}: {url}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status = getattr(response, "status", response.getcode())
            append = bool(start and status == 206)
            if start and not append:
                start = 0
            mode = "ab" if append else "wb"
            with partial.open(mode) as target:
                completed = start
                while chunk := response.read(CHUNK_SIZE):
                    target.write(chunk)
                    completed += len(chunk)
                    print(f"\r  {completed:,}/{item['size_bytes']:,} bytes", end="", flush=True)
    except (OSError, urllib.error.URLError) as exc:
        print()
        raise ValidationError(f"download failed for {label}: {exc}") from exc
    print()
    _require(partial.stat().st_size == item["size_bytes"],
             f"downloaded {label} has the wrong byte size")
    _require(sha256_file(partial) == item["sha256"],
             f"downloaded {label} failed SHA-256 verification")
    os.replace(partial, destination)
    return destination


def download_items(
    items: Iterable[tuple[str, dict[str, Any]]],
    root: Path,
    *,
    hf_endpoint: str | None,
    offline: bool,
) -> None:
    for label, item in items:
        download_file(item, label, root, hf_endpoint=hf_endpoint, offline=offline)


def extract_runtime(manifest: dict[str, Any], root: Path) -> None:
    archive_item = manifest["runtime"]["windows_archive"]
    archive_path = safe_child(root, archive_item["relative_path"], "llama.cpp archive")
    _require(_verify_file(archive_path, archive_item, "llama.cpp archive"),
             "llama.cpp archive is missing")
    runtime_root = safe_child(root, "llama", "runtime output")
    nonce = uuid.uuid4().hex
    staging = safe_child(root, f"llama.installing-{nonce}", "runtime staging")
    backup = safe_child(root, f"llama.backup-{nonce}", "runtime backup")
    staging.mkdir(parents=True, exist_ok=False)
    previous_moved = False
    published = False
    selected: dict[str, zipfile.ZipInfo] = {}
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in archive.infolist():
                if entry.is_dir():
                    continue
                archive_path_value = PurePosixPath(entry.filename.replace("\\", "/"))
                _require(
                    not archive_path_value.is_absolute()
                    and ".." not in archive_path_value.parts,
                    f"unsafe path in llama.cpp archive: {entry.filename}",
                )
                name = archive_path_value.name
                if (
                    name == "llama-server.exe"
                    or name == "LICENSE-LLVM-OpenMP"
                    or name.lower().endswith(".dll")
                ):
                    _require(
                        name.lower() not in selected,
                        f"duplicate runtime basename in archive: {name}",
                    )
                    selected[name.lower()] = entry
            _require("llama-server.exe" in selected, "archive lacks llama-server.exe")
            _require("license-llvm-openmp" in selected, "archive lacks LICENSE-LLVM-OpenMP")
            _require(any(name.endswith(".dll") for name in selected), "archive lacks runtime DLLs")
            for key, entry in sorted(selected.items()):
                destination_name = (
                    "LICENSE-LLVM-OpenMP"
                    if key == "license-llvm-openmp"
                    else PurePosixPath(entry.filename).name
                )
                destination = staging / destination_name
                temporary = destination.with_name(destination.name + ".partial")
                with archive.open(entry) as source, temporary.open("wb") as target:
                    shutil.copyfileobj(source, target, CHUNK_SIZE)
                os.replace(temporary, destination)

        dependencies = manifest["native_dependencies"]
        _require(
            isinstance(dependencies, list) and len(dependencies) == 1,
            "exactly one native runtime dependency is required",
        )
        native = dependencies[0]
        native_source = native["source_package"]
        native_archive_path = safe_child(
            root,
            native_source["relative_path"],
            "Microsoft VC runtime VSIX",
        )
        _require(
            _verify_file(native_archive_path, native_source, "Microsoft VC runtime VSIX"),
            "Microsoft VC runtime VSIX is missing",
        )
        expected_native_names = set(MSVC_RUNTIME_FILES)
        extracted_native_names: set[str] = set()
        with zipfile.ZipFile(native_archive_path) as archive:
            archive_entries = {
                entry.filename.replace("\\", "/"): entry
                for entry in archive.infolist()
                if not entry.is_dir()
            }
            for index, item in enumerate(native["files"]):
                source_name = str(item["archive_path"])
                normalized = PurePosixPath(source_name.replace("\\", "/"))
                _require(
                    not normalized.is_absolute()
                    and ".." not in normalized.parts
                    and "debug_nonredist" not in normalized.as_posix().lower(),
                    f"unsafe native runtime archive path: {source_name}",
                )
                entry = archive_entries.get(normalized.as_posix())
                _require(entry is not None, f"VC runtime VSIX lacks {source_name}")
                destination_name = normalized.name.lower()
                _require(
                    destination_name in expected_native_names
                    and destination_name not in extracted_native_names,
                    f"unexpected or duplicate VC runtime file: {destination_name}",
                )
                _require(
                    destination_name not in selected,
                    f"llama.cpp archive unexpectedly contains {destination_name}",
                )
                destination = staging / destination_name
                temporary = destination.with_name(destination.name + ".partial")
                with archive.open(entry) as source, temporary.open("wb") as target:
                    shutil.copyfileobj(source, target, CHUNK_SIZE)
                _require(
                    temporary.stat().st_size == item["size_bytes"],
                    f"VC runtime file has the wrong size: {destination_name}",
                )
                _require(
                    sha256_file(temporary) == item["sha256"],
                    f"VC runtime file failed SHA-256 verification: {destination_name}",
                )
                os.replace(temporary, destination)
                extracted_native_names.add(destination_name)
        _require(
            extracted_native_names == expected_native_names,
            "Microsoft VC143 release CRT extraction is incomplete",
        )
        _require(
            not any("debug_nonredist" in path.as_posix().lower() for path in staging.rglob("*")),
            "debug_nonredist content leaked into the runtime staging directory",
        )

        if runtime_root.exists():
            os.replace(runtime_root, backup)
            previous_moved = True
        os.replace(staging, runtime_root)
        published = True
        if previous_moved:
            shutil.rmtree(backup)
        print(
            "[OK] Atomically published llama-server.exe, LICENSE-LLVM-OpenMP, "
            f"{sum(key.endswith('.dll') for key in selected)} llama.cpp DLLs and "
            f"{len(extracted_native_names)} app-local Microsoft VC143 CRT DLLs."
        )
    except Exception:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if published and runtime_root.exists():
            shutil.rmtree(runtime_root, ignore_errors=True)
        if previous_moved and backup.exists() and not runtime_root.exists():
            os.replace(backup, runtime_root)
        raise


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    printable = subprocess.list2cmdline(command)
    print(f"[RUN] {printable}")
    completed = subprocess.run(command, cwd=cwd, check=False)
    _require(completed.returncode == 0, f"command failed with exit code {completed.returncode}: {printable}")


def _checkout_converter(root: Path, *, offline: bool) -> Path:
    source = safe_child(root, "build/llama.cpp", "converter source")
    git = shutil.which("git")
    _require(git is not None, "Git is required to prepare the pinned converter")
    if not (source / ".git").is_dir():
        _require(not offline, f"offline converter checkout is missing: {source}")
        source.parent.mkdir(parents=True, exist_ok=True)
        _run([git, "init", str(source)])
        _run([git, "-C", str(source), "remote", "add", "origin",
              "https://github.com/ggml-org/llama.cpp.git"])
    current = subprocess.run([git, "-C", str(source), "rev-parse", "HEAD"],
                             capture_output=True, text=True, check=False)
    if current.returncode != 0 or current.stdout.strip() != LLAMA_REVISION:
        _require(not offline, f"offline converter checkout is not {LLAMA_REVISION}")
        _run([git, "-C", str(source), "fetch", "--depth", "1", "origin", LLAMA_REVISION])
        _run([git, "-C", str(source), "checkout", "--detach", "FETCH_HEAD"])
    verified = subprocess.run([git, "-C", str(source), "rev-parse", "HEAD"],
                              capture_output=True, text=True, check=False)
    _require(verified.returncode == 0 and verified.stdout.strip() == LLAMA_REVISION,
             "converter checkout revision verification failed")
    return source


def _conversion_python(root: Path, source: Path, requirements_path: str, *, offline: bool) -> Path:
    _require(sys.version_info[:2] == (3, 12),
             f"conversion requires Python 3.12, current is {sys.version_info.major}.{sys.version_info.minor}")
    environment = safe_child(root, "build/convert-venv", "conversion venv")
    python = environment / "Scripts" / "python.exe"
    if not python.is_file():
        _require(not offline, f"offline conversion venv is missing: {python}")
        _run([sys.executable, "-m", "venv", str(environment)])
    requirements = safe_child(source, requirements_path, "converter requirements")
    _require(requirements.is_file(), f"pinned converter requirements are missing: {requirements}")
    if not offline:
        _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(requirements)])
    return python


def _expand_command(command: list[str], replacements: dict[str, str], python: Path) -> list[str]:
    expanded = [replacements.get(value, value) for value in command]
    if expanded and expanded[0] == "python":
        expanded[0] = str(python)
    return expanded


def convert_bge(manifest: dict[str, Any], root: Path, *, offline: bool) -> Path:
    model = _model_by_task(manifest, "embedding")
    for label, item in [(f"BGE source {index}", item) for index, item in enumerate(model["source_files"])]:
        _require(_verify_file(safe_child(root, item["relative_path"], label), item, label),
                 f"missing BGE source file: {item['relative_path']}")
    recipe = model["conversion"]
    source = _checkout_converter(root, offline=offline)
    python = _conversion_python(root, source, recipe["requirements_path"], offline=offline)
    source_directory = safe_child(root, "sources/bge-base-zh-v1.5", "BGE source directory")
    artifact = safe_child(root, model["artifact"]["relative_path"], "BGE artifact")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    temporary = artifact.with_name(artifact.stem + ".partial.gguf")
    replacements = {
        "{source_directory}": str(source_directory),
        "{artifact_path}": str(temporary),
    }
    command = _expand_command(recipe["command"], replacements, python)
    _run(command, cwd=source)
    limits = model["artifact"]["expected_size_bytes"]
    _require(temporary.is_file() and limits["minimum"] <= temporary.stat().st_size <= limits["maximum"],
             "BGE converter produced no file or an unexpected byte size")
    actual_sha256 = sha256_file(temporary)
    expected_sha256 = model["artifact"].get("sha256")
    _require(expected_sha256 is None or actual_sha256 == expected_sha256,
             "BGE converter output does not match the release-locked SHA-256")
    os.replace(temporary, artifact)
    print(f"[OK] BGE F16: {artifact} ({artifact.stat().st_size:,} bytes, SHA-256 {actual_sha256})")
    return artifact


def _extract_quantizer(manifest: dict[str, Any], root: Path) -> Path:
    archive_item = manifest["runtime"]["windows_archive"]
    archive_path = safe_child(root, archive_item["relative_path"], "llama.cpp archive")
    _require(_verify_file(archive_path, archive_item, "llama.cpp archive"), "llama.cpp archive is missing")
    output = safe_child(root, "build/quantize", "quantizer directory")
    output.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as archive:
        wanted = [entry for entry in archive.infolist() if not entry.is_dir() and
                  (PurePosixPath(entry.filename).name == "llama-quantize.exe" or
                   PurePosixPath(entry.filename).name.lower().endswith(".dll"))]
        _require(any(PurePosixPath(entry.filename).name == "llama-quantize.exe" for entry in wanted),
                 "llama.cpp archive lacks llama-quantize.exe")
        for entry in wanted:
            name = PurePosixPath(entry.filename).name
            destination = output / name
            with archive.open(entry) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, CHUNK_SIZE)
    runtime_root = safe_child(root, "llama", "prepared llama runtime")
    _require(
        runtime_root.is_dir(),
        "the app-local VC runtime must be extracted before the Qwen fallback quantizer",
    )
    native_dependency = manifest["native_dependencies"][0]
    for item in native_dependency["files"]:
        source = safe_child(root, item["cache_path"], "app-local VC runtime")
        _require(
            source.is_file()
            and source.stat().st_size == item["size_bytes"]
            and sha256_file(source) == item["sha256"],
            f"invalid app-local VC runtime for quantizer: {source}",
        )
        shutil.copy2(source, output / source.name)
    return output / "llama-quantize.exe"


def convert_qwen_fallback(manifest: dict[str, Any], root: Path, *, offline: bool) -> Path:
    model = _model_by_task(manifest, "reranker")
    fallback = model["fallback_build"]
    for index, item in enumerate(fallback["source_files"]):
        label = f"Qwen fallback source {index}"
        _require(_verify_file(safe_child(root, item["relative_path"], label), item, label),
                 f"missing Qwen fallback source: {item['relative_path']}")
    source = _checkout_converter(root, offline=offline)
    python = _conversion_python(root, source, fallback["requirements_path"], offline=offline)
    source_directory = safe_child(root, "sources/qwen3-reranker-0.6b", "Qwen source directory")
    f16_artifact = safe_child(root, "build/qwen3-reranker-0.6b-f16.gguf", "Qwen F16 intermediate")
    artifact = safe_child(root, fallback["artifact"]["relative_path"], "Qwen fallback artifact")
    artifact.parent.mkdir(parents=True, exist_ok=True)
    convert_command = _expand_command(
        fallback["convert_command"],
        {"{source_directory}": str(source_directory), "{f16_artifact_path}": str(f16_artifact)},
        python,
    )
    _run(convert_command, cwd=source)
    quantizer = _extract_quantizer(manifest, root)
    temporary = artifact.with_name(artifact.stem + ".partial.gguf")
    replacements = {
        "llama-quantize.exe": str(quantizer),
        "{f16_artifact_path}": str(f16_artifact),
        "{artifact_path}": str(temporary),
    }
    _run(_expand_command(fallback["quantize_command"], replacements, python), cwd=quantizer.parent)
    limits = fallback["artifact"]["expected_size_bytes"]
    _require(temporary.is_file() and limits["minimum"] <= temporary.stat().st_size <= limits["maximum"],
             "Qwen fallback converter produced no file or an unexpected byte size")
    os.replace(temporary, artifact)
    print(f"[OK] Qwen source fallback Q8_0: {artifact} (SHA-256 {sha256_file(artifact)})")
    return artifact


def _component_file(root: Path, cache_path: str, install_path: str) -> dict[str, Any]:
    path = safe_child(root, cache_path, "component cache path")
    _require(path.is_file(), f"component file is missing: {path}")
    return {
        "cache_path": _safe_relative(cache_path, "component cache path"),
        "install_path": _safe_relative(install_path, "component install path"),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def write_component_lock(
    manifest: dict[str, Any],
    root: Path,
    output: Path,
    *,
    prefer_qwen_source: bool = False,
) -> None:
    embedding = _model_by_task(manifest, "embedding")
    reranker = _model_by_task(manifest, "reranker")
    bge_path = embedding["artifact"]["relative_path"]
    bge_file = safe_child(root, bge_path, "BGE artifact")
    _require(bge_file.is_file(), "BGE F16 must be converted before writing the component lock")
    bge_limits = embedding["artifact"]["expected_size_bytes"]
    _require(bge_limits["minimum"] <= bge_file.stat().st_size <= bge_limits["maximum"],
             "BGE F16 output size is outside its release range")
    expected_bge_sha256 = embedding["artifact"].get("sha256")
    _require(expected_bge_sha256 is None or sha256_file(bge_file) == expected_bge_sha256,
             "BGE F16 output does not match the release-locked SHA-256")

    primary = reranker["artifact"]
    primary_path = safe_child(root, primary["relative_path"], "Qwen primary artifact")
    if (not prefer_qwen_source and primary_path.is_file() and
            primary_path.stat().st_size == primary["size_bytes"] and
            sha256_file(primary_path) == QWEN_GGUF_SHA256):
        qwen_cache_path = primary["relative_path"]
        qwen_revision = QWEN_GGUF_REVISION
        qwen_source_url = "https://huggingface.co/ggml-org/Qwen3-reranker-0.6B-Q8_0-GGUF"
    else:
        fallback = reranker["fallback_build"]["artifact"]
        fallback_path = safe_child(root, fallback["relative_path"], "Qwen fallback artifact")
        limits = fallback["expected_size_bytes"]
        _require(fallback_path.is_file() and limits["minimum"] <= fallback_path.stat().st_size <= limits["maximum"],
                 "neither the pinned Qwen GGUF nor a locally converted fallback is valid")
        for index, item in enumerate(reranker["fallback_build"]["source_files"]):
            _require(_verify_file(safe_child(root, item["relative_path"], f"Qwen source {index}"), item,
                                  f"Qwen source {index}"), "Qwen fallback provenance is incomplete")
        qwen_cache_path = fallback["relative_path"]
        qwen_revision = QWEN_REVISION
        qwen_source_url = reranker["upstream"]["repository"]

    files: list[dict[str, Any]] = []
    runtime_root = safe_child(root, "llama", "runtime cache")
    _require(runtime_root.is_dir(), "llama.cpp runtime must be extracted before locking")
    runtime_files = sorted(
        [runtime_root / "llama-server.exe", runtime_root / "LICENSE-LLVM-OpenMP", *runtime_root.rglob("*.dll")],
        key=lambda path: path.as_posix().lower(),
    )
    _require(all(path.is_file() for path in runtime_files), "runtime extraction is incomplete")
    for path in runtime_files:
        relative = path.relative_to(root).as_posix()
        if path.name == "LICENSE-LLVM-OpenMP":
            install = "licenses/llama.cpp/LICENSE-LLVM-OpenMP"
        else:
            install = f"runtime/llama/{path.relative_to(runtime_root).as_posix()}"
        files.append(_component_file(root, relative, install))

    files.extend([
        _component_file(root, "licenses/llama.cpp-LICENSE.txt", "licenses/llama.cpp/LICENSE"),
        _component_file(root, bge_path, "models/embedding/bge-base-zh-v1.5-f16.gguf"),
        _component_file(root, "licenses/MIT.txt", "licenses/models/bge-base-zh-v1.5/LICENSE"),
        _component_file(root, qwen_cache_path, "models/reranker/qwen3-reranker-0.6b-q8_0.gguf"),
        _component_file(root, "licenses/Apache-2.0.txt", "licenses/models/qwen3-reranker-0.6b/LICENSE"),
    ])
    native_dependency = manifest["native_dependencies"][0]
    native_source = native_dependency["source_package"]
    lock: dict[str, Any] = {
        "schema_version": 1,
        "bundle_id": manifest["bundle_id"],
        "target": "windows-x64",
        "runtime": {
            "name": "llama.cpp CPU runtime",
            "backend": "cpu",
            "version": LLAMA_TAG,
            "revision": LLAMA_REVISION,
            "source_url": f"https://github.com/ggml-org/llama.cpp/releases/tag/{LLAMA_TAG}",
            "license": "MIT",
            "cache_directory": "llama",
            "license_install_path": "licenses/llama.cpp/LICENSE",
            "executable_install_path": "runtime/llama/llama-server.exe",
        },
        "native_dependencies": [
            {
                "id": native_dependency["id"],
                "version": native_dependency["version"],
                "source_url": native_source["url"],
                "source_size": native_source["size_bytes"],
                "source_sha256": native_source["sha256"],
                "license": native_dependency["license"]["id"],
                "license_url": native_dependency["license"]["source_url"],
                "redistribution": native_dependency["license"]["redistribution"],
                "signer_subject_contains": native_dependency["signer_subject_contains"],
                "file_install_paths": [
                    item["install_path"] for item in native_dependency["files"]
                ],
            }
        ],
        "models": [
            {
                "id": "bge-base-zh-v1.5-gguf",
                "task_type": "embedding",
                "model_name": embedding["id"],
                "revision": embedding["upstream"]["revision"],
                "source_url": embedding["upstream"]["repository"],
                "license": "MIT",
                "license_install_path": "licenses/models/bge-base-zh-v1.5/LICENSE",
                "file_install_path": "models/embedding/bge-base-zh-v1.5-f16.gguf",
                "base_path": "/v1",
                "health_path": "/health",
                "server_arguments": ["--embedding", "--pooling", "cls", "--embd-normalize", "2",
                                     "--ctx-size", "512", "--batch-size", "512", "--ubatch-size", "512",
                                     "--no-webui"],
            },
            {
                "id": "qwen3-reranker-0.6b-gguf",
                "task_type": "reranker",
                "model_name": reranker["id"],
                "revision": qwen_revision,
                "source_url": qwen_source_url,
                "upstream_model_revision": reranker["upstream"]["revision"],
                "license": "Apache-2.0",
                "license_install_path": "licenses/models/qwen3-reranker-0.6b/LICENSE",
                "file_install_path": "models/reranker/qwen3-reranker-0.6b-q8_0.gguf",
                "base_path": "/v1",
                "health_path": "/health",
                "server_arguments": ["--reranking", "--pooling", "rank", "--ctx-size", "8192",
                                     "--parallel", "1", "--batch-size", "512", "--ubatch-size", "512",
                                     "--no-webui"],
            },
        ],
        "files": files,
    }
    validate_component_lock(lock, root)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".partial")
    temporary.write_text(json.dumps(lock, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    print(f"[OK] Installer component lock: {output}")


def print_plan(manifest: dict[str, Any], root: Path) -> None:
    normal = known_download_size(manifest)
    fallback = sum(item["size_bytes"] for _, item in _fallback_files(manifest))
    embedding_artifact = _model_by_task(manifest, "embedding")["artifact"]
    embedding_size = embedding_artifact["expected_size_bytes"]
    print("Debug Platform full-GGUF retrieval pack (Win11 x64 CPU)")
    print(f"  Cache root: {root}")
    print(f"  Default verified downloads: {_format_bytes(normal)}")
    print(f"  Optional official-Qwen source fallback: +{_format_bytes(fallback)}")
    if (embedding_artifact.get("sha256") is not None and
            embedding_size["minimum"] == embedding_size["maximum"]):
        print(
            "  Locked BGE F16: "
            f"{embedding_size['minimum']:,} bytes, SHA-256 {embedding_artifact['sha256']}"
        )
    else:
        print(
            "  Generated BGE F16 estimate: "
            f"{embedding_size['minimum']:,}-{embedding_size['maximum']:,} bytes"
        )
    print("  Qwen Q8_0: 639,153,184 bytes (trusted ggml-org prebuilt)")
    print("  Recommended temporary free space: 5 GiB default; 8 GiB with source fallback")
    print("  Runtime: llama.cpp b10729, commit " + LLAMA_REVISION)
    print("  No action was taken. Use --all for the default build or individual flags.")


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=script_dir / "model-assets.json")
    parser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--hf-endpoint", help="optional HTTPS Hugging Face mirror origin")
    parser.add_argument("--offline", action="store_true", help="reuse cache only; prohibit network/bootstrap")
    parser.add_argument("--download", action="store_true", help="download and verify default pinned inputs")
    parser.add_argument("--extract-runtime", action="store_true")
    parser.add_argument("--convert-bge", action="store_true")
    parser.add_argument("--qwen-from-source", action="store_true",
                        help="download official Qwen source and build Q8_0 instead of relying on the prebuilt")
    parser.add_argument("--write-component-lock", action="store_true")
    parser.add_argument("--component-lock", type=Path,
                        help="output path; default is <cache-root>/components.lock.json")
    parser.add_argument("--all", action="store_true",
                        help="download, extract, convert BGE and write components.lock.json")
    args = parser.parse_args(argv)
    try:
        manifest = _read_json(args.manifest.resolve())
        for warning in validate_manifest(manifest):
            print(f"[WARN] {warning}")
        root = args.cache_root.resolve()
        actions = any((args.download, args.extract_runtime, args.convert_bge,
                       args.qwen_from_source, args.write_component_lock, args.all))
        if not actions:
            print_plan(manifest, root)
            return 0
        if args.offline and args.download:
            print("[INFO] --offline --download verifies/reuses the cache without network access.")
        root.mkdir(parents=True, exist_ok=True)
        if args.download or args.all:
            download_items(_default_downloads(manifest, qwen_from_source=args.qwen_from_source), root,
                           hf_endpoint=args.hf_endpoint, offline=args.offline)
        if args.qwen_from_source:
            download_items(_fallback_files(manifest), root,
                           hf_endpoint=args.hf_endpoint, offline=args.offline)
        if args.extract_runtime or args.all or args.qwen_from_source:
            extract_runtime(manifest, root)
        if args.convert_bge or args.all:
            convert_bge(manifest, root, offline=args.offline)
        if args.qwen_from_source:
            convert_qwen_fallback(manifest, root, offline=args.offline)
        if args.write_component_lock or args.all:
            output = (args.component_lock.resolve() if args.component_lock else root / "components.lock.json")
            write_component_lock(
                manifest,
                root,
                output,
                prefer_qwen_source=args.qwen_from_source,
            )
    except (ValidationError, KeyError, TypeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
