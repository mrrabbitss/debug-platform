"""Shared timing and budget policy for executable quality evaluators."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter
from typing import Any, Protocol


class EvaluationOutcomeLike(Protocol):
    metrics: dict[str, Any]
    failures: list[str]


def run_timed_check(
    name: str,
    evaluator: Callable[[], EvaluationOutcomeLike],
    *,
    max_duration_ms: int | None = None,
    enforce_duration_budget: bool = True,
) -> dict[str, Any]:
    started = perf_counter()
    try:
        outcome = evaluator()
        failures = list(outcome.failures)
        metrics = outcome.metrics
    except Exception as exc:  # noqa: BLE001 - one evaluator must not abort the suite
        failures = [f"{type(exc).__name__}: {exc}"]
        metrics = {}
    duration_ms = round((perf_counter() - started) * 1000, 3)
    if (
        enforce_duration_budget
        and max_duration_ms is not None
        and duration_ms > max_duration_ms
    ):
        failures.append(
            f"duration {duration_ms} ms exceeded budget {max_duration_ms} ms"
        )
    return {
        "name": name,
        "status": "PASS" if not failures else "FAIL",
        "duration_ms": duration_ms,
        "budget_ms": max_duration_ms,
        "duration_budget_enforced": enforce_duration_budget,
        "metrics": metrics,
        "failures": failures,
    }
