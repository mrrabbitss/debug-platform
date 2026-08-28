"""Schema and distribution gate for the synthetic Agent scenario matrix."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EXPECTED_METRICS = {
    "citation_accuracy_min",
    "evidence_precision_min",
    "tool_precision_min",
    "tool_redundancy_max",
    "context_occupancy_max",
}


def evaluate_scenario_matrix(path: Path) -> tuple[dict[str, Any], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases", [])
    gates = payload.get("quality_gates", {})
    profiles = payload.get("expectation_profiles", {})
    failures: list[str] = []
    minimum = int(gates.get("minimum_scenarios") or 30)
    if len(cases) < minimum:
        failures.append(f"scenario matrix has {len(cases)} cases; minimum is {minimum}")
    if not payload.get("privacy", {}).get("synthetic_only"):
        failures.append("scenario matrix must declare synthetic_only=true")
    if payload.get("privacy", {}).get("contains_company_data"):
        failures.append("scenario matrix must not contain company data")
    identifiers = [str(item.get("id") or "") for item in cases]
    if len(set(identifiers)) != len(identifiers) or not all(identifiers):
        failures.append("scenario IDs must be non-empty and unique")

    required_values = gates.get("required_values", {})
    dimension_coverage: dict[str, list[str]] = {}
    for dimension, expected in required_values.items():
        actual = sorted({str(item.get(dimension) or "") for item in cases})
        dimension_coverage[dimension] = actual
        missing = set(map(str, expected)).difference(actual)
        if missing:
            failures.append(
                f"dimension {dimension} is missing values {sorted(missing)}"
            )

    for item in cases:
        case_id = str(item.get("id") or "<missing>")
        profile_id = str(item.get("expectation_profile") or "")
        profile = profiles.get(profile_id)
        if not isinstance(profile, dict):
            failures.append(f"{case_id}: unknown expectation profile {profile_id}")
            continue
        missing_metrics = EXPECTED_METRICS.difference(profile)
        if missing_metrics:
            failures.append(
                f"{case_id}: expectation profile lacks {sorted(missing_metrics)}"
            )
        for metric in EXPECTED_METRICS:
            value = profile.get(metric)
            if not isinstance(value, (int, float)) or not 0 <= float(value) <= 1:
                failures.append(f"{case_id}: {metric} must be within 0..1")
        if not item.get("expected_modules"):
            failures.append(f"{case_id}: expected_modules cannot be empty")
        if not item.get("expected_root_cause"):
            failures.append(f"{case_id}: expected_root_cause cannot be empty")

    return {
        "scenario_count": len(cases),
        "minimum_scenarios": minimum,
        "expectation_profile_count": len(profiles),
        "dimension_coverage": dimension_coverage,
        "phase": "distribution_contract",
    }, failures
