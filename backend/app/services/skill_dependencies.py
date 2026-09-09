"""Resolve reviewed Skill references inside the immutable knowledge bundle."""
import posixpath
from urllib.parse import unquote, urlsplit

from app.core.utils import json_loads


def _resolve_dependency(source_path, reference, path_map, by_id):
    if isinstance(reference, dict):
        if reference.get("external"):
            return [], None
        path = reference.get("path") or reference.get("reference", "")
        identifiers = reference.get("document_ids") or ([reference["document_id"]] if reference.get("document_id") else [])
        if identifiers:
            return [key for key in identifiers if key in by_id], path if any(key not in by_id for key in identifiers) else None
    else:
        parsed = urlsplit(str(reference))
        if not parsed.path or parsed.scheme or parsed.netloc:
            return [], None
        path = posixpath.normpath(posixpath.join(posixpath.dirname(source_path), unquote(parsed.path)))
    if not isinstance(path, str) or path.startswith(("../", "/")) or path == "..":
        return [], str(reference)
    targets = path_map.get(path, [])
    return targets, None if targets else path


def bundle_dependencies(document, available):
    metadata = json_loads(document.metadata_json, {})
    bundle = metadata.get("bundle_id")
    if not bundle:
        return [], []
    path_map, by_id = {}, {doc.id: doc for doc in available}
    for doc in available:
        values = json_loads(doc.metadata_json, {})
        if values.get("bundle_id") == bundle:
            for path in values.get("source_paths", []):
                path_map.setdefault(path, []).append(doc.id)
    manifest = metadata.get("bundle_manifest", [])
    for item in manifest:
        target_ids = item.get("document_ids") or ([item["document_id"]] if item.get("document_id") else [])
        valid = [key for key in target_ids if key in by_id]
        if valid:
            path_map[item["path"]] = valid
    dependencies, missing = [], []
    source_paths = set(metadata.get("source_paths", []))
    for item in manifest:
        if item.get("path") not in source_paths:
            continue
        for reference in item.get("references", []):
            targets, unresolved = _resolve_dependency(item["path"], reference, path_map, by_id)
            if unresolved:
                missing.append(unresolved)
            dependencies.extend(key for key in targets if key != document.id)
    return list(dict.fromkeys(dependencies)), list(dict.fromkeys(missing))


def expand_bundle_documents(selected, available):
    by_id = {doc.id: doc for doc in available}
    result, seen = list(selected), {doc.id for doc in selected}
    for doc in result:
        dependencies, _ = bundle_dependencies(doc, available)
        for key in dependencies:
            if key not in seen:
                seen.add(key)
                result.append(by_id[key])
    return result
