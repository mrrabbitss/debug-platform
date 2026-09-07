"""Project a verified full offline bundle inside an unpublished installer staging tree.

No network or model calls are made. The source manifest remains available for
provenance; the installed manifest covers exactly the selected bytes and metadata.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any


SELECTIONS = {
    "Full": {"embedding", "reranker"},
    "Core": set(),
    "Embedding": {"embedding"},
    "Reranker": {"reranker"},
}
MANIFEST = "package-manifest.json"
SOURCE_MANIFEST = "source-package-manifest.json"
SELECTION_FILE = "installation-selection.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"Invalid installer metadata: {path.name}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"Invalid installer metadata object: {path.name}")
    return result


def _safe_file(root: Path, relative: Any) -> Path:
    if (
        not isinstance(relative, str)
        or not relative
        or "\\" in relative
        or ":" in relative
        or "\x00" in relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ValueError("Unsafe path in the source package manifest")
    target = root.joinpath(*PurePosixPath(relative).parts)
    if not target.resolve().is_relative_to(root):
        raise ValueError("A package manifest path escapes the staging tree")
    for item in (target, *target.parents):
        if item == root:
            break
        if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
            raise ValueError("Package links/reparse points are not allowed")
    return target


def _verified_entries(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    manifest = _read_json(root / MANIFEST)
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("Unsupported source package manifest schema")
    entries: dict[str, dict[str, Any]] = {}
    seen_casefold: set[str] = set()
    for entry in manifest["files"]:
        if not isinstance(entry, dict):
            raise ValueError("Invalid source package manifest entry")
        target = _safe_file(root, entry.get("path"))
        relative = entry["path"]
        if relative.casefold() in seen_casefold or relative.casefold() == MANIFEST:
            raise ValueError("Duplicate/reserved path in source package manifest")
        size, digest = entry.get("size"), entry.get("sha256")
        if (
            type(size) is not int or size < 0
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("Invalid source package size/SHA-256 contract")
        if not target.is_file() or target.stat().st_size != size or _sha256(target) != digest:
            raise ValueError(f"Source package integrity failed: {relative}")
        seen_casefold.add(relative.casefold())
        entries[relative] = copy.deepcopy(entry)
    actual: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("Package links/reparse points are not allowed")
        if path.is_file() and path.name != MANIFEST:
            actual.add(path.relative_to(root).as_posix())
    if actual != set(entries):
        raise ValueError("Source package contains unexpected or missing files")
    return manifest, entries


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )


def project_components(package_root: Path, selection: str) -> dict[str, Any]:
    if selection not in SELECTIONS:
        raise ValueError("Choose Full, Core, Embedding, or Reranker")
    # This helper never modifies a published install, arbitrary directory or user data.
    if package_root.is_symlink() or (
        hasattr(package_root, "is_junction") and package_root.is_junction()
    ):
        raise ValueError("Installer staging root must not be a link")
    root = package_root.resolve(strict=True)
    if not re.fullmatch(r"GWAPDebugPlatform\.installing-[0-9a-f]{32}", root.name):
        raise ValueError("Component selection requires an unpublished installer staging directory")
    manifest, entries = _verified_entries(root)
    for required in ("model-components.json", "build-info.json", "component_selection.py"):
        if required not in entries:
            raise ValueError(f"The offline source bundle is incomplete: {required}")
    components = _read_json(root / "model-components.json")
    available = components.get("components")
    if components.get("schema_version") != 1 or not isinstance(available, list):
        raise ValueError("Unsupported source model components schema")
    by_task: dict[str, dict[str, Any]] = {}
    for component in available:
        task = component.get("task_type") if isinstance(component, dict) else None
        if task not in {"embedding", "reranker"} or task in by_task:
            raise ValueError("Invalid or duplicate source model component")
        model = component.get("model")
        executable = component.get("executable")
        if (
            model not in entries or executable not in entries
            or not model.startswith(f"models/{task}/")
            or not executable.startswith("runtime/llama/")
        ):
            raise ValueError("Model component is not covered by the source manifest")
        by_task[task] = component
    selected = SELECTIONS[selection]
    if not selected.issubset(by_task):
        raise ValueError("Selected components are absent; use the original full Setup/ZIP to add them")
    build_info = _read_json(root / "build-info.json")
    source_manifest_bytes = (root / MANIFEST).read_bytes()
    source_manifest_path = root / SOURCE_MANIFEST
    original_manifest_digest = _sha256(
        source_manifest_path if SOURCE_MANIFEST in entries else root / MANIFEST
    )
    omitted: list[str] = []
    for relative in entries:
        lowered = relative.casefold()
        if (
            (lowered.startswith("models/embedding/") and "embedding" not in selected)
            or (lowered.startswith("models/reranker/") and "reranker" not in selected)
            or (lowered.startswith("runtime/llama/") and not selected)
        ):
            omitted.append(relative)
    # All validation above is read-only. Only declared staging files can be removed.
    for relative in omitted:
        _safe_file(root, relative).unlink()
        entries.pop(relative)
    for directory in sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    if SOURCE_MANIFEST not in entries:
        source_manifest_path.write_bytes(source_manifest_bytes)
    components["components"] = [by_task[task] for task in sorted(selected)]
    _write_json(root / "model-components.json", components)
    build_info.update({
        "local_model_runtime_bundled": bool(selected),
        "default_embedding": "bundled_gguf" if "embedding" in selected else "hashing",
        "default_reranker": "bundled_gguf" if "reranker" in selected else "disabled",
        "installed_component_selection": selection,
    })
    _write_json(root / "build-info.json", build_info)
    selection_record = {
        "schema_version": 1,
        "selection": selection,
        "core_required": True,
        "installed_tasks": sorted(selected),
        "source_bundle_id": components.get("bundle_id"),
        "source_manifest_sha256": original_manifest_digest,
        "omitted_files": sorted(omitted),
        "add_components_requires_original_bundle": True,
    }
    _write_json(root / SELECTION_FILE, selection_record)
    for relative in ("model-components.json", "build-info.json", SOURCE_MANIFEST, SELECTION_FILE):
        path = root / relative
        entries[relative] = {
            "path": relative, "size": path.stat().st_size, "sha256": _sha256(path),
        }
    manifest["files"] = [entries[relative] for relative in sorted(entries)]
    _write_json(root / MANIFEST, manifest)
    return selection_record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    parser.add_argument("--selection", choices=tuple(SELECTIONS), required=True)
    args = parser.parse_args()
    result = project_components(args.package_root, args.selection)
    print(f"[OK] Verified component selection: {result['selection']} (Core always installed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
