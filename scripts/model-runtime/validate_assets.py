#!/usr/bin/env python3
"""Validate the pinned GGUF retrieval assets and generated installer lock.

The validator is intentionally standard-library only so that it can run before
the platform Python environment exists.  It never downloads files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlparse


LLAMA_TAG = "b10729"
LLAMA_REVISION = "458681e1d5d4a29a1463c4732e03226cf384b997"
LLAMA_ARCHIVE_SIZE = 18_367_763
LLAMA_ARCHIVE_SHA256 = "628ad9516f13c1b01727657987abdf57c019f58af074e6f3e5ec36996188f6cd"
BGE_REVISION = "f03589ceff5aac7111bd60cfc7d497ca17ecac65"
QWEN_REVISION = "e61197ed45024b0ed8a2d74b80b4d909f1255473"
QWEN_GGUF_REVISION = "a02f48bb4f057028298c21fa033da2b30d7742d5"
QWEN_GGUF_SIZE = 639_153_184
QWEN_GGUF_SHA256 = "22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48"
MSVC_RUNTIME_ID = "microsoft-vc143-crt-x64-app-local"
MSVC_RUNTIME_VERSION = "14.44.35211.0"
MSVC_SOURCE_SIZE = 3_224_191
MSVC_SOURCE_SHA256 = "4aaf54db0bfc9435f7c3660e1a00237a4b556042bfeea64bde44c2e0194e6ee5"
MSVC_SOURCE_URL = (
    "https://download.visualstudio.microsoft.com/download/pr/"
    "45d3b8dd-bced-4b37-9974-142f748d710c/"
    f"{MSVC_SOURCE_SHA256}/Microsoft.VC.14.44.17.14.CRT.Redist.X64.base.vsix"
)
MSVC_ARCHIVE_PREFIX = (
    "Contents/VC/Redist/MSVC/14.44.35112/x64/Microsoft.VC143.CRT/"
)
MSVC_LICENSE_ID = "LicenseRef-Microsoft-Visual-Studio-2022-Redistributable"
MSVC_LICENSE_URL = (
    "https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution"
)
MSVC_SIGNER_SUBJECT = "Microsoft Windows Software Compatibility Publisher"
MSVC_RUNTIME_FILES = {
    "concrt140.dll": (324_208, "2405355f0a58067b258f8df33c327e3a3d716eaac5a3a5aebb757842d85bd376"),
    "msvcp140.dll": (557_728, "0f885b509a685d2bbfa652fed26b5fb31d88fbdab0a978c641d1c7b8aa460aa9"),
    "msvcp140_1.dll": (35_952, "bfad5aef4c63a669e3c140655cdfdf395b6c979b400a447bd5dcb65ed8826c3d"),
    "msvcp140_2.dll": (280_200, "3ea06f0ee098b4823cb79599df3780e7f23cce52c19aac31d2a0d47efe33a5e9"),
    "msvcp140_atomic_wait.dll": (50_304, "640b2aefced484d0368eea5bdd06addd0658a3a70a49256e560d6923b404a479"),
    "msvcp140_codecvt_ids.dll": (31_872, "f2069a52880ec885ee7f0511186100eb7fada0411a2b4948fafea7735b878a18"),
    "vccorlib140.dll": (352_384, "19839407c3fdbc824e5bce189bf68ddf8097f12ec28b757797ffa0415c144ddd"),
    "vcruntime140.dll": (124_544, "d5e4d9a3e835fa679450145d6a7d94e36573a509317111904d9b3712c30d9066"),
    "vcruntime140_1.dll": (49_792, "1f2d41c4aa5db0bc33ebf7b66d72943a817d7ce6cbe880502a9403823633093f"),
    "vcruntime140_threads.dll": (38_528, "219915cf20822f34d5e7c1fdd4e21ae7f3396881096c51036225fb8f84b47afa"),
}
ALLOWED_DOWNLOAD_HOSTS = frozenset(
    {
        "download.visualstudio.microsoft.com",
        "github.com",
        "raw.githubusercontent.com",
        "huggingface.co",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


class ValidationError(ValueError):
    """Raised for a release-asset contract violation."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"JSON root must be an object: {path}")
    return value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _safe_relative(value: Any, label: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), f"{label} must be a string")
    normalized = value.replace("\\", "/")
    candidate = PurePosixPath(normalized)
    _require(not candidate.is_absolute(), f"{label} must be relative: {value}")
    _require(not re.match(r"^[A-Za-z]:", normalized), f"{label} must not contain a drive")
    _require(all(part not in {"", ".", ".."} for part in candidate.parts), f"{label} is unsafe")
    return candidate.as_posix()


def safe_child(root: Path, relative: str, label: str = "path") -> Path:
    normalized = _safe_relative(relative, label)
    root_resolved = root.resolve()
    result = (root_resolved / Path(*PurePosixPath(normalized).parts)).resolve()
    try:
        result.relative_to(root_resolved)
    except ValueError as exc:
        raise ValidationError(f"{label} escapes its root: {relative}") from exc
    return result


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def pe_import_names(path: Path) -> set[str]:
    """Read the normal PE import table without relying on the target machine DLL set."""

    try:
        data = path.read_bytes()

        def unpack(fmt: str, offset: int) -> tuple[Any, ...]:
            size = struct.calcsize(fmt)
            if offset < 0 or offset + size > len(data):
                raise ValidationError(f"truncated PE structure in {path}")
            return struct.unpack_from(fmt, data, offset)

        _require(len(data) >= 64 and data[:2] == b"MZ", f"not a PE binary: {path}")
        pe_offset = unpack("<I", 0x3C)[0]
        _require(data[pe_offset:pe_offset + 4] == b"PE\0\0", f"invalid PE signature: {path}")
        coff_offset = pe_offset + 4
        section_count = unpack("<H", coff_offset + 2)[0]
        optional_size = unpack("<H", coff_offset + 16)[0]
        optional_offset = coff_offset + 20
        magic = unpack("<H", optional_offset)[0]
        if magic == 0x20B:
            data_directories_offset = optional_offset + 112
        elif magic == 0x10B:
            data_directories_offset = optional_offset + 96
        else:
            raise ValidationError(f"unsupported PE optional-header format in {path}")
        import_rva, _import_size = unpack("<II", data_directories_offset + 8)
        if import_rva == 0:
            return set()
        size_of_headers = unpack("<I", optional_offset + 60)[0]
        section_offset = optional_offset + optional_size
        sections: list[tuple[int, int, int, int]] = []
        for index in range(section_count):
            entry_offset = section_offset + index * 40
            virtual_size, virtual_address, raw_size, raw_offset = unpack(
                "<IIII", entry_offset + 8
            )
            sections.append((virtual_address, virtual_size, raw_size, raw_offset))

        def rva_to_offset(rva: int) -> int:
            if rva < size_of_headers:
                _require(rva < len(data), f"PE header RVA is out of range in {path}")
                return rva
            for virtual_address, virtual_size, raw_size, raw_offset in sections:
                span = max(virtual_size, raw_size)
                if virtual_address <= rva < virtual_address + span:
                    offset = raw_offset + (rva - virtual_address)
                    _require(offset < len(data), f"PE section RVA is out of range in {path}")
                    return offset
            raise ValidationError(f"PE import RVA cannot be mapped in {path}")

        result: set[str] = set()
        descriptor_offset = rva_to_offset(import_rva)
        for descriptor_index in range(4096):
            offset = descriptor_offset + descriptor_index * 20
            original_first_thunk, timestamp, forwarder_chain, name_rva, first_thunk = unpack(
                "<IIIII", offset
            )
            if not any((original_first_thunk, timestamp, forwarder_chain, name_rva, first_thunk)):
                break
            name_offset = rva_to_offset(name_rva)
            end = data.find(b"\0", name_offset, min(len(data), name_offset + 512))
            _require(end >= 0, f"unterminated PE import name in {path}")
            try:
                name = data[name_offset:end].decode("ascii").lower()
            except UnicodeDecodeError as exc:
                raise ValidationError(f"non-ASCII PE import name in {path}") from exc
            _require(bool(name), f"empty PE import name in {path}")
            result.add(name)
        else:
            raise ValidationError(f"unbounded PE import table in {path}")
        return result
    except OSError as exc:
        raise ValidationError(f"cannot inspect PE imports for {path}: {exc}") from exc


def _validate_sha(value: Any, label: str) -> None:
    _require(isinstance(value, str) and SHA256_RE.fullmatch(value) is not None,
             f"{label} must be 64 lowercase hexadecimal characters")


def _validate_revision(value: Any, label: str) -> None:
    _require(isinstance(value, str) and REVISION_RE.fullmatch(value) is not None,
             f"{label} must be a pinned 40-character commit")


def _validate_download(item: dict[str, Any], label: str) -> None:
    for key in ("url", "relative_path", "size_bytes", "sha256"):
        _require(key in item, f"{label} is missing {key}")
    parsed = urlparse(str(item["url"]))
    _require(parsed.scheme == "https", f"{label}.url must use HTTPS")
    _require(parsed.hostname in ALLOWED_DOWNLOAD_HOSTS,
             f"{label}.url host is not allowlisted: {parsed.hostname}")
    _safe_relative(item["relative_path"], f"{label}.relative_path")
    _require(isinstance(item["size_bytes"], int) and item["size_bytes"] > 0,
             f"{label}.size_bytes must be positive")
    _validate_sha(item["sha256"], f"{label}.sha256")


def _items_for_download(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = [
        ("runtime.windows_archive", manifest["runtime"]["windows_archive"])
    ]
    for index, dependency in enumerate(manifest["native_dependencies"]):
        items.append(
            (f"native_dependencies[{index}].source_package", dependency["source_package"])
        )
    for model in manifest["models"]:
        for index, source in enumerate(model.get("source_files", [])):
            items.append((f"{model['id']}.source_files[{index}]", source))
        artifact = model["artifact"]
        if "url" in artifact:
            items.append((f"{model['id']}.artifact", artifact))
    for index, license_asset in enumerate(manifest["licenses"]):
        items.append((f"licenses[{index}]", license_asset))
    return items


def _fallback_items(manifest: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    reranker = _model_by_task(manifest, "reranker")
    fallback = reranker.get("fallback_build")
    if not isinstance(fallback, dict):
        return []
    return [
        (f"{reranker['id']}.fallback_build.source_files[{index}]", source)
        for index, source in enumerate(fallback.get("source_files", []))
    ]


def _model_by_task(manifest: dict[str, Any], task: str) -> dict[str, Any]:
    matches = [model for model in manifest["models"] if model.get("task_type") == task]
    _require(len(matches) == 1, f"models must contain exactly one {task}")
    return matches[0]


def _contains_sequence(arguments: Iterable[Any], sequence: list[str]) -> bool:
    values = [str(value) for value in arguments]
    width = len(sequence)
    return any(values[index:index + width] == sequence for index in range(len(values) - width + 1))


def _native_dependency(manifest: dict[str, Any]) -> dict[str, Any]:
    dependencies = manifest.get("native_dependencies")
    _require(
        isinstance(dependencies, list) and len(dependencies) == 1,
        "manifest must contain exactly one pinned native runtime dependency",
    )
    dependency = dependencies[0]
    _require(isinstance(dependency, dict), "native dependency must be an object")
    return dependency


def _validate_native_dependency(manifest: dict[str, Any]) -> None:
    dependency = _native_dependency(manifest)
    _require(dependency.get("id") == MSVC_RUNTIME_ID, "unexpected native runtime dependency")
    _require(
        dependency.get("version") == MSVC_RUNTIME_VERSION,
        "unexpected Microsoft VC runtime version",
    )
    source = dependency.get("source_package")
    _require(isinstance(source, dict), "native runtime source_package must be an object")
    _validate_download(source, "native runtime source_package")
    _require(source["url"] == MSVC_SOURCE_URL, "Microsoft VC runtime URL is not immutable")
    _require(
        source["relative_path"]
        == "downloads/Microsoft.VC.14.44.17.14.CRT.Redist.X64.base.vsix",
        "unexpected Microsoft VC runtime cache path",
    )
    _require(source["size_bytes"] == MSVC_SOURCE_SIZE, "unexpected VC runtime VSIX size")
    _require(
        source["sha256"] == MSVC_SOURCE_SHA256,
        "unexpected VC runtime VSIX SHA-256",
    )
    license_info = dependency.get("license")
    _require(isinstance(license_info, dict), "native runtime license must be an object")
    _require(
        license_info
        == {
            "id": MSVC_LICENSE_ID,
            "source_url": MSVC_LICENSE_URL,
            "redistribution": "app_local_unmodified",
            "notice_required": True,
        },
        "Microsoft VC runtime redistribution terms are incomplete",
    )
    _require(
        dependency.get("signer_subject_contains") == MSVC_SIGNER_SUBJECT,
        "Microsoft VC runtime signer contract is missing",
    )

    entries = dependency.get("files")
    _require(
        isinstance(entries, list) and len(entries) == len(MSVC_RUNTIME_FILES),
        "Microsoft VC143 app-local release CRT file set is incomplete",
    )
    observed: set[str] = set()
    for index, entry in enumerate(entries):
        _require(isinstance(entry, dict), f"native runtime files[{index}] must be an object")
        name = PurePosixPath(str(entry.get("archive_path") or "")).name.lower()
        _require(name in MSVC_RUNTIME_FILES, f"unexpected Microsoft VC runtime file: {name}")
        _require(name not in observed, f"duplicate Microsoft VC runtime file: {name}")
        observed.add(name)
        expected_size, expected_sha256 = MSVC_RUNTIME_FILES[name]
        _require(
            entry.get("archive_path") == f"{MSVC_ARCHIVE_PREFIX}{name}",
            f"Microsoft VC runtime file must come from the release CRT directory: {name}",
        )
        _require(
            "debug_nonredist" not in str(entry.get("archive_path") or "").lower(),
            "debug_nonredist files must never be extracted",
        )
        _require(entry.get("cache_path") == f"llama/{name}", f"unsafe CRT cache path: {name}")
        _require(
            entry.get("install_path") == f"runtime/llama/{name}",
            f"CRT must be deployed app-local beside llama-server.exe: {name}",
        )
        _require(entry.get("size_bytes") == expected_size, f"unexpected size for {name}")
        _require(entry.get("sha256") == expected_sha256, f"unexpected SHA-256 for {name}")
    _require(
        observed == set(MSVC_RUNTIME_FILES),
        "Microsoft VC143 app-local release CRT names are incomplete",
    )


def validate_manifest(manifest: dict[str, Any], *, strict_release: bool = False) -> list[str]:
    warnings: list[str] = []
    required = {"schema_version", "bundle_id", "bundle_status", "target", "runtime",
                "native_dependencies",
                "models", "licenses", "release_gates"}
    _require(required <= manifest.keys(), f"manifest is missing: {sorted(required - manifest.keys())}")
    _require(manifest["schema_version"] == 1, "unsupported manifest schema_version")
    _require(manifest["target"] == {"os": "windows", "architecture": "x86_64", "backend": "cpu"},
             "first release target must be Windows x64 CPU")

    runtime = manifest["runtime"]
    _require(runtime.get("id") == "llama.cpp", "runtime.id must be llama.cpp")
    _require(runtime.get("tag") == LLAMA_TAG, f"runtime.tag must be {LLAMA_TAG}")
    _validate_revision(runtime.get("revision"), "runtime.revision")
    _require(runtime["revision"] == LLAMA_REVISION, "unexpected llama.cpp revision")
    archive = runtime.get("windows_archive")
    _require(isinstance(archive, dict), "runtime.windows_archive must be an object")
    _validate_download(archive, "runtime.windows_archive")
    _require(archive["size_bytes"] == LLAMA_ARCHIVE_SIZE, "unexpected llama.cpp archive size")
    _require(archive["sha256"] == LLAMA_ARCHIVE_SHA256, "unexpected llama.cpp archive SHA-256")
    includes = runtime.get("archive_include")
    _require(isinstance(includes, list) and set(includes) == {"llama-server.exe", "*.dll", "LICENSE-LLVM-OpenMP"},
             "runtime.archive_include must select server, every DLL and LICENSE-LLVM-OpenMP")
    _validate_native_dependency(manifest)

    models = manifest["models"]
    _require(isinstance(models, list) and len(models) == 2,
             "manifest must contain exactly one embedding and one reranker")
    embedding = _model_by_task(manifest, "embedding")
    reranker = _model_by_task(manifest, "reranker")

    _require(embedding.get("id") == "BAAI/bge-base-zh-v1.5", "unexpected embedding model")
    _require(embedding.get("distribution") == "build_from_source", "BGE must be built from pinned source")
    _require(embedding["upstream"].get("revision") == BGE_REVISION, "unexpected BGE revision")
    _require(len(embedding.get("source_files", [])) >= 10, "BGE source snapshot is incomplete")
    embedding_artifact = embedding["artifact"]
    _require(embedding_artifact.get("format") == "gguf", "BGE artifact must be GGUF")
    _require(embedding_artifact.get("quantization") == "F16", "BGE quality baseline must be F16")
    _require(embedding_artifact.get("relative_path") == "models/bge-base-zh-v1.5-f16.gguf",
             "BGE cache path must match the installer contract")
    conversion = embedding.get("conversion")
    _require(isinstance(conversion, dict), "BGE conversion recipe is required")
    _require(conversion.get("tool_revision") == LLAMA_REVISION,
             "BGE converter must use the pinned llama.cpp revision")
    _require(_contains_sequence(conversion.get("command", []), ["--outtype", "f16"]),
             "BGE conversion must request f16")
    _require(conversion.get("pooling") == "cls" and conversion.get("normalization") == "l2",
             "BGE runtime must use CLS pooling and L2 normalization")
    embedding_args = embedding["server"].get("arguments", [])
    for pair in (["--pooling", "cls"], ["--embd-normalize", "2"], ["--ctx-size", "512"]):
        _require(_contains_sequence(embedding_args, pair), f"BGE server arguments are missing {pair}")
    _require("--embedding" in embedding_args and "--no-webui" in embedding_args,
             "BGE server must enable embeddings and disable the Web UI")

    _require(reranker.get("id") == "Qwen/Qwen3-Reranker-0.6B", "unexpected reranker model")
    _require(reranker.get("distribution") == "trusted_prebuilt", "Qwen must use the trusted prebuilt GGUF")
    _require(reranker["upstream"].get("revision") == QWEN_REVISION, "unexpected Qwen upstream revision")
    qwen_artifact = reranker["artifact"]
    _validate_download(qwen_artifact, "Qwen artifact")
    _require(QWEN_GGUF_REVISION in qwen_artifact["url"], "Qwen GGUF URL is not revision pinned")
    _require(qwen_artifact["size_bytes"] == QWEN_GGUF_SIZE, "unexpected Qwen GGUF size")
    _require(qwen_artifact["sha256"] == QWEN_GGUF_SHA256, "unexpected Qwen GGUF SHA-256")
    reranker_args = reranker["server"].get("arguments", [])
    for pair in (["--pooling", "rank"], ["--ctx-size", "8192"], ["--parallel", "1"]):
        _require(_contains_sequence(reranker_args, pair), f"Qwen server arguments are missing {pair}")
    _require("--reranking" in reranker_args and "--no-webui" in reranker_args,
             "Qwen server must enable reranking and disable the Web UI")
    fallback = reranker.get("fallback_build")
    _require(isinstance(fallback, dict), "Qwen must define an official-source conversion fallback")
    _require(fallback.get("tool_revision") == LLAMA_REVISION,
             "Qwen fallback converter must use the pinned llama.cpp revision")
    _require(len(fallback.get("source_files", [])) >= 8, "Qwen fallback source snapshot is incomplete")
    _require(_contains_sequence(fallback.get("convert_command", []), ["--outtype", "f16"]),
             "Qwen fallback must first convert an F16 GGUF")
    _require("Q8_0" in fallback.get("quantize_command", []),
             "Qwen fallback must quantize to Q8_0")
    _require(fallback["artifact"].get("quantization") == "Q8_0",
             "Qwen fallback artifact must be Q8_0")
    for label, item in _fallback_items(manifest):
        _validate_download(item, label)
        _require(QWEN_REVISION in item["url"], f"{label} is not pinned to the official Qwen revision")

    seen_paths: set[str] = set()
    for label, item in _items_for_download(manifest):
        _validate_download(item, label)
        relative = _safe_relative(item["relative_path"], f"{label}.relative_path")
        _require(relative not in seen_paths, f"duplicate download path: {relative}")
        seen_paths.add(relative)

    _require(len(manifest["licenses"]) >= 3, "runtime and both model licenses must be pinned")
    for index, license_asset in enumerate(manifest["licenses"]):
        _require(license_asset.get("spdx") in {"MIT", "Apache-2.0"},
                 f"licenses[{index}].spdx is unsupported")

    bge_sha = embedding_artifact.get("sha256")
    if bge_sha is None:
        warnings.append("BGE F16 output SHA-256 is pending the first controlled build.")
    else:
        _validate_sha(bge_sha, "BGE artifact.sha256")
    if embedding.get("quality_status") != "verified" or reranker.get("quality_status") != "verified":
        warnings.append("GGUF quality equivalence is not yet marked verified.")
    if manifest.get("bundle_status") not in {"release_candidate", "released"}:
        warnings.append("Bundle remains experimental_unverified.")
    if strict_release:
        _require(bge_sha is not None, "strict release requires the generated BGE SHA-256")
        _require(embedding["artifact"].get("verification_status") == "quality_verified",
                 "strict release requires BGE quality_verified")
        _require(all(model.get("quality_status") == "verified" for model in models),
                 "strict release requires both model quality gates")
        _require(manifest.get("bundle_status") in {"release_candidate", "released"},
                 "strict release requires release_candidate or released status")
    return warnings


def validate_asset_root(manifest: dict[str, Any], root: Path) -> None:
    reranker = _model_by_task(manifest, "reranker")
    qwen_primary = reranker["artifact"]
    qwen_primary_label = f"{reranker['id']}.artifact"
    for label, item in _items_for_download(manifest):
        if label == qwen_primary_label:
            continue
        path = safe_child(root, item["relative_path"], label)
        _require(path.is_file(), f"missing pinned asset: {path}")
        _require(path.stat().st_size == item["size_bytes"],
                 f"size mismatch for {path}: expected {item['size_bytes']}, got {path.stat().st_size}")
        _require(sha256_file(path) == item["sha256"], f"SHA-256 mismatch for {path}")

    embedding = _model_by_task(manifest, "embedding")
    artifact = embedding["artifact"]
    path = safe_child(root, artifact["relative_path"], "BGE artifact")
    _require(path.is_file(), f"missing generated BGE artifact: {path}")
    size = path.stat().st_size
    limits = artifact["expected_size_bytes"]
    _require(limits["minimum"] <= size <= limits["maximum"],
             f"generated BGE size {size} is outside {limits['minimum']}..{limits['maximum']}")
    if artifact.get("sha256") is not None:
        _require(sha256_file(path) == artifact["sha256"], "generated BGE SHA-256 mismatch")

    qwen_path = safe_child(root, qwen_primary["relative_path"], qwen_primary_label)
    if qwen_path.is_file():
        _require(qwen_path.stat().st_size == qwen_primary["size_bytes"], "Qwen primary size mismatch")
        _require(sha256_file(qwen_path) == qwen_primary["sha256"], "Qwen primary SHA-256 mismatch")
    else:
        fallback = reranker["fallback_build"]
        for label, item in _fallback_items(manifest):
            source_path = safe_child(root, item["relative_path"], label)
            _require(source_path.is_file() and source_path.stat().st_size == item["size_bytes"] and
                     sha256_file(source_path) == item["sha256"], f"invalid Qwen fallback source: {label}")
        fallback_artifact = fallback["artifact"]
        fallback_path = safe_child(root, fallback_artifact["relative_path"], "Qwen fallback artifact")
        limits = fallback_artifact["expected_size_bytes"]
        _require(fallback_path.is_file(), "neither Qwen primary nor source-converted fallback exists")
        _require(limits["minimum"] <= fallback_path.stat().st_size <= limits["maximum"],
                 "Qwen fallback output size is outside its release range")

    runtime_root = safe_child(root, "llama", "llama runtime")
    _require((runtime_root / "llama-server.exe").is_file(), "llama-server.exe was not extracted")
    _require((runtime_root / "LICENSE-LLVM-OpenMP").is_file(), "LICENSE-LLVM-OpenMP was not extracted")
    dlls = sorted(runtime_root.rglob("*.dll"))
    _require(bool(dlls), "llama.cpp runtime has no DLLs")
    _require(not any(re.search(r"cuda|cublas|hipblas|vulkan|sycl", dll.name, re.I) for dll in dlls),
             "GPU DLL leaked into the CPU runtime")
    native_dependency = _native_dependency(manifest)
    for index, entry in enumerate(native_dependency["files"]):
        path = safe_child(root, entry["cache_path"], f"native runtime files[{index}]")
        _require(path.is_file(), f"missing app-local Microsoft VC runtime: {path}")
        _require(path.stat().st_size == entry["size_bytes"], f"VC runtime size mismatch: {path}")
        _require(sha256_file(path) == entry["sha256"], f"VC runtime SHA-256 mismatch: {path}")

    imported_names: set[str] = set()
    for binary in [(runtime_root / "llama-server.exe"), *dlls]:
        imported_names.update(pe_import_names(binary))
    imported_msvc = {
        name
        for name in imported_names
        if re.fullmatch(r"(?:concrt|msvcp|vccorlib|vcruntime)\d[^/\\]*\.dll", name)
    }
    _require(
        {"msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll"} <= imported_msvc,
        "pinned llama.cpp import scan did not observe the expected VC143 runtime dependencies",
    )
    local_dll_names = {path.name.lower() for path in dlls}
    _require(
        imported_msvc <= local_dll_names,
        "clean-system VC runtime closure is incomplete; missing app-local imports: "
        f"{sorted(imported_msvc - local_dll_names)}",
    )


def validate_component_lock(lock: dict[str, Any], root: Path) -> None:
    _require(lock.get("schema_version") == 1, "unsupported component lock schema")
    _require(lock.get("target") == "windows-x64", "component lock target must be windows-x64")
    runtime = lock.get("runtime")
    _require(isinstance(runtime, dict), "component lock runtime must be an object")
    _require(runtime.get("version") == LLAMA_TAG, "component lock has wrong llama.cpp version")
    _require(runtime.get("revision") == LLAMA_REVISION, "component lock has wrong llama.cpp revision")
    _require(runtime.get("cache_directory") == "llama", "runtime.cache_directory must be llama")
    native_dependencies = lock.get("native_dependencies")
    _require(
        isinstance(native_dependencies, list) and len(native_dependencies) == 1,
        "component lock must contain the pinned Microsoft VC runtime dependency",
    )
    native_dependency = native_dependencies[0]
    _require(isinstance(native_dependency, dict), "native dependency lock must be an object")
    _require(native_dependency.get("id") == MSVC_RUNTIME_ID, "component lock has wrong VC runtime")
    _require(
        native_dependency.get("version") == MSVC_RUNTIME_VERSION,
        "component lock has wrong VC runtime version",
    )
    _require(
        native_dependency.get("source_url") == MSVC_SOURCE_URL
        and native_dependency.get("source_size") == MSVC_SOURCE_SIZE
        and native_dependency.get("source_sha256") == MSVC_SOURCE_SHA256,
        "component lock has incomplete VC runtime source provenance",
    )
    _require(
        native_dependency.get("license") == MSVC_LICENSE_ID
        and native_dependency.get("license_url") == MSVC_LICENSE_URL
        and native_dependency.get("redistribution") == "app_local_unmodified",
        "component lock has incomplete VC runtime license provenance",
    )
    _require(
        native_dependency.get("signer_subject_contains") == MSVC_SIGNER_SUBJECT,
        "component lock has wrong VC runtime signer contract",
    )
    models = lock.get("models")
    _require(isinstance(models, list) and len(models) == 2, "component lock must have two models")
    _require({model.get("task_type") for model in models} == {"embedding", "reranker"},
             "component lock must have embedding and reranker")
    files = lock.get("files")
    _require(isinstance(files, list) and files, "component lock files must not be empty")

    cache_paths: set[str] = set()
    install_paths: set[str] = set()
    for index, entry in enumerate(files):
        _require(isinstance(entry, dict), f"files[{index}] must be an object")
        cache_path = _safe_relative(entry.get("cache_path"), f"files[{index}].cache_path")
        install_path = _safe_relative(entry.get("install_path"), f"files[{index}].install_path")
        _require(cache_path not in cache_paths, f"duplicate cache_path: {cache_path}")
        _require(install_path not in install_paths, f"duplicate install_path: {install_path}")
        cache_paths.add(cache_path)
        install_paths.add(install_path)
        _require(isinstance(entry.get("size"), int) and entry["size"] > 0,
                 f"files[{index}].size must be positive")
        _validate_sha(entry.get("sha256"), f"files[{index}].sha256")
        path = safe_child(root, cache_path, f"files[{index}].cache_path")
        _require(path.is_file(), f"component file is missing: {path}")
        _require(path.stat().st_size == entry["size"], f"component size mismatch: {cache_path}")
        _require(sha256_file(path) == entry["sha256"], f"component SHA-256 mismatch: {cache_path}")

    executable = _safe_relative(runtime.get("executable_install_path"), "runtime executable")
    runtime_license = _safe_relative(runtime.get("license_install_path"), "runtime license")
    _require(executable in install_paths, "runtime executable is not in files[]")
    _require(runtime_license in install_paths, "runtime license is not in files[]")
    expected_native_paths = {f"runtime/llama/{name}" for name in MSVC_RUNTIME_FILES}
    locked_native_paths = native_dependency.get("file_install_paths")
    _require(
        isinstance(locked_native_paths, list)
        and set(locked_native_paths) == expected_native_paths,
        "component lock does not identify the complete app-local VC143 runtime",
    )
    _require(
        expected_native_paths <= install_paths,
        "component lock files[] omits an app-local VC143 runtime DLL",
    )
    for model in models:
        model_file = _safe_relative(model.get("file_install_path"), "model file")
        license_file = _safe_relative(model.get("license_install_path"), "model license")
        _require(model_file in install_paths, f"model file is not in files[]: {model_file}")
        _require(license_file in install_paths, f"model license is not in files[]: {license_file}")
        _require(isinstance(model.get("server_arguments"), list) and model["server_arguments"],
                 f"model {model.get('id')} lacks server_arguments")

    runtime_dir = safe_child(root, runtime["cache_directory"], "runtime cache directory")
    actual_dlls = {
        path.relative_to(root).as_posix().lower() for path in runtime_dir.rglob("*.dll")
    }
    locked_dlls = {path.lower() for path in cache_paths if path.lower().endswith(".dll")}
    _require(actual_dlls == locked_dlls,
             f"every runtime DLL must be locked; missing={sorted(actual_dlls - locked_dlls)}, "
             f"extra={sorted(locked_dlls - actual_dlls)}")


def known_download_size(manifest: dict[str, Any]) -> int:
    return sum(item["size_bytes"] for _, item in _items_for_download(manifest))


def main(argv: list[str] | None = None) -> int:
    script_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=script_dir / "model-assets.json")
    parser.add_argument("--asset-root", type=Path, help="also verify every cached asset")
    parser.add_argument("--component-lock", type=Path, help="also verify installer lock and files")
    parser.add_argument("--strict-release", action="store_true",
                        help="require recorded generated hash and completed quality gates")
    args = parser.parse_args(argv)
    try:
        manifest = _read_json(args.manifest.resolve())
        warnings = validate_manifest(manifest, strict_release=args.strict_release)
        if args.asset_root:
            validate_asset_root(manifest, args.asset_root.resolve())
        if args.component_lock:
            _require(args.asset_root is not None, "--component-lock requires --asset-root")
            lock = _read_json(args.component_lock.resolve())
            validate_component_lock(lock, args.asset_root.resolve())
    except ValidationError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    byte_count = known_download_size(manifest)
    print(f"[OK] Pinned manifest contract is valid ({byte_count:,} known download bytes).")
    if args.asset_root:
        print(f"[OK] Cached files are size/hash verified under {args.asset_root.resolve()}.")
    if args.component_lock:
        print(f"[OK] Installer component lock is complete and verified: {args.component_lock.resolve()}")
    for warning in warnings:
        print(f"[WARN] {warning}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
