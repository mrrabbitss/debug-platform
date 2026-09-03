from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any

from app.models import Artifact, Case
from app.services.diagnostic_scope import normalize_artifact_source
from app.services.storage import storage
from app.services.text_files import detect_text_encoding


MAX_LOCAL_IDENTITY_SCAN_LINES = 250_000
_COLON_MAC = re.compile(
    r"(?<![0-9a-f])([0-9a-f]{2}(?::[0-9a-f]{2}){5})(?![0-9a-f])",
    re.IGNORECASE,
)
_UDN_MAC_SUFFIX = re.compile(
    r"udn\[uuid:[^\]\r\n]*?([0-9a-f]{12})\]",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _IdentityLocation:
    artifact: Artifact
    line: int


def _scan_identity_locations(
    artifacts: list[Artifact],
) -> tuple[dict[str, list[_IdentityLocation]], dict[str, list[_IdentityLocation]]]:
    udn_values: dict[str, list[_IdentityLocation]] = {}
    mac_values: dict[str, list[_IdentityLocation]] = {}
    for artifact in artifacts:
        try:
            path = storage.resolve_path(artifact.stored_path)
        except ValueError:
            continue
        if not path.is_file():
            continue
        encoding = detect_text_encoding(path)
        if encoding is None:
            continue
        try:
            with path.open("r", encoding=encoding, errors="replace", newline=None) as handle:
                for line_number, line in enumerate(handle, start=1):
                    if line_number > MAX_LOCAL_IDENTITY_SCAN_LINES:
                        break
                    location = _IdentityLocation(artifact=artifact, line=line_number)
                    for match in _UDN_MAC_SUFFIX.finditer(line):
                        udn_values.setdefault(match.group(1).casefold(), []).append(location)
                    for match in _COLON_MAC.finditer(line):
                        normalized = match.group(1).replace(":", "").casefold()
                        mac_values.setdefault(normalized, []).append(location)
        except OSError:
            continue
    return udn_values, mac_values


def derive_case_local_evidence(
    case: Case,
    artifacts: list[Artifact],
) -> list[dict[str, Any]]:
    """Compare sensitive identities locally and expose only the boolean relation."""

    udn_values, mac_values = _scan_identity_locations(artifacts)
    if not udn_values or not set(udn_values).issubset(mac_values):
        return []
    result: list[dict[str, Any]] = []
    for position, value in enumerate(sorted(udn_values), start=1):
        udn_location = udn_values[value][0]
        mac_location = mac_values[value][0]
        digest = hashlib.sha256(
            (
                f"{case.id}\0{udn_location.artifact.id}\0{udn_location.line}\0"
                f"{mac_location.artifact.id}\0{mac_location.line}"
            ).encode("utf-8")
        ).hexdigest()[:20]
        result.append({
            "evidence_id": f"LDE-{digest}",
            "source_type": "local_derived_evidence",
            "artifact_id": udn_location.artifact.id,
            "source_file": udn_location.artifact.original_name,
            "line_start": udn_location.line,
            "line_end": udn_location.line,
            "event_code": "UDN_AP_MAC_MATCH",
            "content": (
                "Local deterministic UDN/AP MAC comparison succeeded; "
                "sensitive identifier values are withheld."
            ),
            "meaning": "本地确定性比较确认 UDN UUID 末 12 位与 AP MAC 一致。",
            "artifact_source": normalize_artifact_source(
                udn_location.artifact, case,
            ),
            "metadata": {
                "comparison_complete": True,
                "identity_position": position,
                "identity_count": len(udn_values),
                "udn_location": {
                    "artifact_id": udn_location.artifact.id,
                    "source_file": udn_location.artifact.original_name,
                    "line": udn_location.line,
                },
                "mac_location": {
                    "artifact_id": mac_location.artifact.id,
                    "source_file": mac_location.artifact.original_name,
                    "line": mac_location.line,
                },
            },
        })
    return result
