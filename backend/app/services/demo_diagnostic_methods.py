from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from app.core.utils import json_loads
from app.models import Artifact, Case
from app.services.demo_case_contract import (
    DEMO_CASE_ID_PREFIX,
    DEMO_DATASET_VERSION,
    DEMO_FIXTURES,
    DEMO_HOST_METHOD_LOG_FILENAME,
    DEMO_HOST_METHOD_MANIFEST_FILENAME,
    DEMO_HOST_METHOD_MANIFEST_SHA256,
    DEMO_HOST_METHOD_TREE_FILENAME,
    resolve_demo_fixture_root,
)
from app.services.diagnostic_methods import DiagnosticMethodDocument, DiagnosticPattern
from app.services.fault_tree_coverage import FaultTreeCoverageItem


DEMO_HOST_METHOD_LOG_ID = "DEMO-HOST-METHOD-LOG-ANALYSIS-V1"
DEMO_HOST_METHOD_TREE_ID = "DEMO-HOST-METHOD-FAULT-TREE-V1"
DEMO_HOST_METHOD_BUNDLE_VERSION = "ap-frequent-offline-host-methods-v1"

_DEMO_CASE_ID = re.compile(
    rf"{re.escape(DEMO_CASE_ID_PREFIX)}-[0-9a-f]{{10}}"
)
_EXPECTED_DOCUMENTS = {
    DEMO_HOST_METHOD_LOG_ID: {
        "filename": DEMO_HOST_METHOD_LOG_FILENAME,
        "title": "AP 频繁离线演示日志分析（公开合成）",
        "source_type": "analysis_skill",
        "role": "LOG_ANALYSIS_METHOD",
        "sha256": "d999b853c7b142d796d2361f519bdefeb168293881a4ce3390abee2147159a40",
    },
    DEMO_HOST_METHOD_TREE_ID: {
        "filename": DEMO_HOST_METHOD_TREE_FILENAME,
        "title": "AP 频繁离线演示故障树（公开合成）",
        "source_type": "fault_tree",
        "role": "FAULT_TREE",
        "sha256": "e3793d888d0059ffbbfeb297612a9f31ee3dcf99d7299677bf3b57b9b0f04099",
    },
}
_EXPECTED_ROOT_LABELS = {"场景1", "场景2", "场景3"}
_EXPECTED_CATEGORY_COUNTS = Counter({
    "FLOW_STEP": 13,
    "ROOT_CAUSE": 3,
    "DECISION": 11,
})
_REQUIRED_PATTERN_TEXTS = {
    "LAN3 REACHABLE",
    "curTime[",
    "Recv Offline Event, src=CtrlPointVerify",
    "Error sending alive advertisements : -5",
    "process udm died unexpectedly with signal 11",
    "udp packet loss",
}


class BundledDemoMethodError(ValueError):
    """The immutable method bundle or its demo-case binding is invalid."""


def _safe_method_path(filename: Any):
    relative = PurePosixPath(str(filename or ""))
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise BundledDemoMethodError("Bundled demo method path is invalid")
    root = resolve_demo_fixture_root().resolve()
    path = root.joinpath(*relative.parts).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise BundledDemoMethodError("Bundled demo method escaped its fixture root") from exc
    return path


def _load_manifest() -> dict[str, Any]:
    path = _safe_method_path(DEMO_HOST_METHOD_MANIFEST_FILENAME)
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != DEMO_HOST_METHOD_MANIFEST_SHA256:
        raise BundledDemoMethodError("Bundled demo method manifest integrity check failed")
    try:
        manifest = json.loads(raw.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundledDemoMethodError("Bundled demo method manifest is invalid") from exc
    if (
        manifest.get("schema_version") != 1
        or manifest.get("bundle_version") != DEMO_HOST_METHOD_BUNDLE_VERSION
        or manifest.get("dataset_version") != DEMO_DATASET_VERSION
        or manifest.get("scope") != "bundled_demo_case_only"
        or manifest.get("expected_fault_tree_items") != 27
        or set(manifest.get("required_root_labels") or []) != _EXPECTED_ROOT_LABELS
    ):
        raise BundledDemoMethodError("Bundled demo method manifest contract failed")
    return manifest


def load_bundled_demo_diagnostic_methods() -> list[DiagnosticMethodDocument]:
    """Load integrity-pinned, redistributable methods for the synthetic demo only."""

    manifest = _load_manifest()
    entries = manifest.get("documents")
    if not isinstance(entries, list) or len(entries) != len(_EXPECTED_DOCUMENTS):
        raise BundledDemoMethodError("Bundled demo method catalog is incomplete")

    methods: list[DiagnosticMethodDocument] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise BundledDemoMethodError("Bundled demo method entry is invalid")
        document_id = str(entry.get("id") or "")
        expected = _EXPECTED_DOCUMENTS.get(document_id)
        if expected is None or document_id in seen_ids:
            raise BundledDemoMethodError("Bundled demo method identity is invalid")
        seen_ids.add(document_id)
        for key in ("filename", "title", "source_type", "role", "sha256"):
            if entry.get(key) != expected[key]:
                raise BundledDemoMethodError(
                    f"Bundled demo method contract changed: {document_id}:{key}"
                )
        if (
            entry.get("version") != 1
            or entry.get("device_type") != "GENERAL"
            or entry.get("module") != "demo.ap_frequent_offline"
        ):
            raise BundledDemoMethodError(
                f"Bundled demo method scope changed: {document_id}"
            )

        path = _safe_method_path(entry["filename"])
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise BundledDemoMethodError(
                f"Bundled demo method integrity check failed: {document_id}"
            )
        try:
            content = raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise BundledDemoMethodError(
                f"Bundled demo method encoding is invalid: {document_id}"
            ) from exc
        content = content.replace("\r\n", "\n").replace("\r", "\n")
        if "[SYNTHETIC DEMO METHOD]" not in content:
            raise BundledDemoMethodError(
                f"Bundled demo method lost its synthetic marker: {document_id}"
            )
        content_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if content_sha256 != expected["sha256"]:
            raise BundledDemoMethodError(
                f"Bundled demo method normalized hash changed: {document_id}"
            )
        methods.append(DiagnosticMethodDocument(
            id=document_id,
            title=str(entry["title"]),
            source_type=str(entry["source_type"]),
            version=1,
            device_type="GENERAL",
            module="demo.ap_frequent_offline",
            content=content,
            content_sha256=content_sha256,
            role=str(entry["role"]),
        ))

    if seen_ids != set(_EXPECTED_DOCUMENTS):
        raise BundledDemoMethodError("Bundled demo method catalog changed")
    return methods


def load_bundled_demo_methods_for_case(
    case: Case,
    artifacts: list[Artifact],
) -> list[DiagnosticMethodDocument] | None:
    """Return case-scoped methods, or None when this is an ordinary case.

    A case using the reserved demo ID namespace must satisfy the full artifact
    contract. It fails closed instead of silently falling back to global methods.
    """

    if _DEMO_CASE_ID.fullmatch(str(case.id or "")) is None:
        return None
    expected_by_name = {fixture.filename: fixture for fixture in DEMO_FIXTURES}
    if len(artifacts) != len(expected_by_name):
        raise BundledDemoMethodError("Bundled demo case artifact set is incomplete")
    actual_names = {str(artifact.original_name) for artifact in artifacts}
    if actual_names != set(expected_by_name):
        raise BundledDemoMethodError("Bundled demo case artifact names changed")

    for artifact in artifacts:
        fixture = expected_by_name[str(artifact.original_name)]
        metadata = json_loads(artifact.metadata_json, {})
        demo = metadata.get("demo_fixture") if isinstance(metadata, dict) else None
        if not isinstance(demo, dict) or (
            artifact.status != "PARSED"
            or not artifact.active_parse_run_id
            or artifact.sha256 != fixture.sha256
            or artifact.source_device_type != fixture.device_type
            or artifact.source_device_role != fixture.device_role
            or demo.get("dataset_version") != DEMO_DATASET_VERSION
            or demo.get("synthetic") is not True
            or demo.get("source_sha256") != fixture.sha256
        ):
            raise BundledDemoMethodError(
                f"Bundled demo case artifact contract failed: {fixture.filename}"
            )
    return load_bundled_demo_diagnostic_methods()


def validate_bundled_demo_method_compilation(
    methods: list[DiagnosticMethodDocument],
    patterns: list[DiagnosticPattern],
    fault_tree_items: list[FaultTreeCoverageItem],
) -> None:
    """Fail closed when the case-scoped method semantics drift."""

    if len(methods) != 2 or {method.id for method in methods} != set(_EXPECTED_DOCUMENTS):
        raise BundledDemoMethodError("Bundled demo requires exactly two diagnostic methods")
    if Counter(method.role for method in methods) != Counter({
        "LOG_ANALYSIS_METHOD": 1,
        "FAULT_TREE": 1,
    }):
        raise BundledDemoMethodError("Bundled demo diagnostic method roles changed")
    for method in methods:
        if method.content_sha256 != _EXPECTED_DOCUMENTS[method.id]["sha256"]:
            raise BundledDemoMethodError("Bundled demo diagnostic method hash changed")

    category_counts = Counter(item.category for item in fault_tree_items)
    root_labels = {
        item.label for item in fault_tree_items if item.category == "ROOT_CAUSE"
    }
    if (
        len(fault_tree_items) != 27
        or category_counts != _EXPECTED_CATEGORY_COUNTS
        or root_labels != _EXPECTED_ROOT_LABELS
        or any(
            item.method_document_id != DEMO_HOST_METHOD_TREE_ID
            for item in fault_tree_items
        )
    ):
        raise BundledDemoMethodError("Bundled demo fault-tree compilation contract failed")
    pattern_texts = {pattern.text for pattern in patterns}
    if not _REQUIRED_PATTERN_TEXTS.issubset(pattern_texts):
        raise BundledDemoMethodError("Bundled demo diagnostic Pattern contract failed")
