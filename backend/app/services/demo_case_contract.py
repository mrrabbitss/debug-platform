from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEMO_CASE_ID_PREFIX = "CASE-DEMO-AP-OFFLINE"
DEMO_DATASET_VERSION = "ap-frequent-offline-glm52-v1"
DEMO_METHOD_TREE_ID = "DEMO-METHOD-FAULT-TREE"
DEMO_METHOD_LOG_ID = "DEMO-METHOD-LOG-ANALYSIS"
DEMO_SNAPSHOT_FILENAME = "glm52_success_snapshot.json"
DEMO_HOST_METHOD_MANIFEST_FILENAME = "methods/manifest.json"
DEMO_HOST_METHOD_LOG_FILENAME = "methods/ap-offline-log-analysis.md"
DEMO_HOST_METHOD_TREE_FILENAME = "methods/ap-offline-fault-tree.md"
DEMO_SNAPSHOT_SHA256 = (
    "844e0ced48d69586b5393871e0f369be77c88ebf46786f4050dff09d78c71a29"
)
DEMO_HOST_METHOD_MANIFEST_SHA256 = (
    "f409404f61ec22596fd9f864fe4b0150c63213b86d7d4f8b1d9b0b2e0f76d24e"
)

_INTERNAL_SOURCE_ID_RE = re.compile(
    r"\b(?:CASE|ART|PRUN|LTRIAGE|RUN|ARUN|ATRACE|EVT|LEM|LEH|LEO|LDE|"
    r"LOCALDOC|MEM|MODEL|JOB|FTITEM)-[A-Za-z0-9_.:-]+\b"
)
_SECRET_RE = re.compile(
    r"(?i)(?:api[_ -]?key|authorization|bearer|ciphertext|password|secret|"
    r"sk-[a-z0-9]{16,})"
)
_PRIVATE_IPV4_RE = re.compile(
    r"\b(?:10\.(?:\d{1,3}\.){2}\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.(?:\d{1,3}\.)\d{1,3}|"
    r"192\.168\.(?:\d{1,3}\.)\d{1,3})\b"
)
_MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_ALLOWED_DEMO_MACS = {"02:00:00:00:00:33", "02:00:00:00:01:01"}


@dataclass(frozen=True)
class DemoFixture:
    filename: str
    device_type: str
    device_role: str
    sha256: str


DEMO_FIXTURES = (
    DemoFixture(
        filename="GW_collectDebuginfo_demo.txt",
        device_type="GW",
        device_role="PRIMARY",
        sha256="a8bdb3121c3093b038fa67e0ce103f0d5483fe6062ddd41518eb7befdc85f318",
    ),
    DemoFixture(
        filename="AP_collectDebuginfo_demo.txt",
        device_type="AP",
        device_role="SECONDARY",
        sha256="80a72a62e371b328c649569118cd7a688c472660ab29ec55cabae1b75ff539ed",
    ),
)


def resolve_demo_fixture_root() -> Path:
    service_path = Path(__file__).resolve()
    candidates = (
        service_path.parents[3] / "sample_data" / "demo_ap_frequent_offline",
        service_path.parents[2] / "demo_data" / "ap_frequent_offline",
    )
    required = [fixture.filename for fixture in DEMO_FIXTURES]
    required.extend((
        DEMO_SNAPSHOT_FILENAME,
        DEMO_HOST_METHOD_MANIFEST_FILENAME,
        DEMO_HOST_METHOD_LOG_FILENAME,
        DEMO_HOST_METHOD_TREE_FILENAME,
    ))
    for candidate in candidates:
        if all((candidate / filename).is_file() for filename in required):
            return candidate
    raise FileNotFoundError(
        "Bundled AP frequent-offline GLM demo is unavailable. "
        "Rebuild the portable package or restore sample_data/demo_ap_frequent_offline."
    )


def load_demo_fixtures() -> list[tuple[DemoFixture, Path, bytes, str]]:
    root = resolve_demo_fixture_root()
    loaded: list[tuple[DemoFixture, Path, bytes, str]] = []
    for fixture in DEMO_FIXTURES:
        path = root / fixture.filename
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != fixture.sha256:
            raise ValueError(f"Synthetic demo fixture integrity check failed: {fixture.filename}")
        text = raw.decode("utf-8", errors="strict")
        if not all(marker in text for marker in ("DEMO-", "SYNTHETIC-", "192.0.2.")):
            raise ValueError(f"Demo fixture lost its synthetic-data markers: {fixture.filename}")
        loaded.append((fixture, path, raw, text))
    return loaded


def load_demo_snapshot() -> dict[str, Any]:
    path = resolve_demo_fixture_root() / DEMO_SNAPSHOT_FILENAME
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != DEMO_SNAPSHOT_SHA256:
        raise ValueError("Recorded GLM demo snapshot integrity check failed")
    rendered = raw.decode("utf-8", errors="strict")
    if _SECRET_RE.search(rendered):
        raise ValueError("Recorded GLM demo snapshot secret scan failed")
    if _PRIVATE_IPV4_RE.search(rendered):
        raise ValueError("Recorded GLM demo snapshot contains a private IPv4 address")
    unexpected_macs = {
        item.casefold() for item in _MAC_RE.findall(rendered)
    } - _ALLOWED_DEMO_MACS
    if unexpected_macs:
        raise ValueError("Recorded GLM demo snapshot contains a non-synthetic MAC address")
    if _INTERNAL_SOURCE_ID_RE.search(rendered):
        raise ValueError("Recorded GLM demo snapshot contains unreplaced runtime identifiers")
    snapshot = json.loads(rendered)
    provenance = snapshot.get("provenance") or {}
    analysis = snapshot.get("analysis") or {}
    result = analysis.get("result") or {}
    if snapshot.get("schema_version") != 1:
        raise ValueError("Unsupported recorded GLM demo snapshot schema")
    if snapshot.get("dataset_version") != DEMO_DATASET_VERSION:
        raise ValueError("Recorded GLM demo dataset version mismatch")
    if (
        provenance.get("kind") != "recorded_real_model_run"
        or provenance.get("provider") != "openai_compatible"
        or provenance.get("model") != "glm-5.2"
        or provenance.get("endpoint_origin") != "https://wawapii.com"
        or analysis.get("provider") != "openai_compatible"
        or analysis.get("model") != "glm-5.2"
        or result.get("recorded_model_run") is not True
        or result.get("demo_snapshot") is not True
    ):
        raise ValueError("Recorded GLM demo provenance contract failed")
    expected_hashes = {fixture.filename: fixture.sha256 for fixture in DEMO_FIXTURES}
    if provenance.get("source_log_sha256") != expected_hashes:
        raise ValueError("Recorded GLM demo source-log hashes do not match bundled fixtures")
    if set((snapshot.get("triage") or {}).keys()) != {"GW", "AP"}:
        raise ValueError("Recorded GLM demo must contain GW and AP triage snapshots")
    if len(snapshot.get("source_evidence") or []) != 152:
        raise ValueError("Recorded GLM demo evidence catalog is incomplete")
    coverage = (result.get("diagnostic_planning") or {}).get("fault_tree_coverage") or {}
    if not (
        coverage.get("complete") is True
        and coverage.get("attempted") == 27
        and coverage.get("concluded") == 27
        and coverage.get("total") == 27
    ):
        raise ValueError("Recorded GLM demo fault-tree coverage is incomplete")
    usage = (analysis.get("agent_run") or {}).get("usage") or {}
    if usage.get("total_tokens") != 321_453:
        raise ValueError("Recorded GLM demo analysis usage does not match source run")
    return snapshot


def demo_method_catalog(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    source = snapshot or load_demo_snapshot()
    planning = ((source.get("analysis") or {}).get("result") or {}).get(
        "diagnostic_planning"
    ) or {}
    return [dict(item) for item in planning.get("method_usage") or []]
