from __future__ import annotations

from typing import Any

from app.models import Artifact, Case


JOINT_DIAGNOSTIC_DEVICE_TYPES = frozenset({"GW", "AP", "GENERAL", "OTHER"})
VALID_ARTIFACT_DEVICE_TYPES = frozenset({"GW", "AP", "UNKNOWN"})
VALID_ARTIFACT_DEVICE_ROLES = frozenset({"PRIMARY", "SECONDARY", "UNKNOWN"})


def knowledge_matches_joint_diagnostic_scope(device_type: str | None) -> bool:
    """Include both sides of a GW/AP topology plus shared legacy knowledge."""
    if not device_type:
        return True
    return device_type.strip().upper() in JOINT_DIAGNOSTIC_DEVICE_TYPES


def normalize_artifact_source(
    artifact: Artifact,
    case: Case | None = None,
) -> dict[str, Any]:
    device_type = str(artifact.source_device_type or "UNKNOWN").strip().upper()
    role = str(artifact.source_device_role or "UNKNOWN").strip().upper()
    if device_type not in VALID_ARTIFACT_DEVICE_TYPES:
        device_type = "UNKNOWN"
    if role not in VALID_ARTIFACT_DEVICE_ROLES:
        role = "UNKNOWN"
    if device_type == "UNKNOWN" and case:
        case_type = str(case.device_type or "").strip().upper()
        if case_type in {"GW", "AP"}:
            device_type = case_type
    if role == "UNKNOWN":
        if device_type == "GW":
            role = "PRIMARY"
        elif device_type == "AP":
            role = "SECONDARY"
    return {
        "artifact_id": artifact.id,
        "original_name": artifact.original_name,
        "device_type": device_type,
        "device_role": role,
        "topology_relation": (
            "GW_PRIMARY" if device_type == "GW" and role == "PRIMARY"
            else "AP_SECONDARY" if device_type == "AP" and role == "SECONDARY"
            else "UNSPECIFIED"
        ),
    }
