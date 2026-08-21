from __future__ import annotations

from app.services.diagnostic_agent_budget import (
    NO_PROGRESS,
    TIME_BUDGET,
    TOKEN_BUDGET,
    TOOL_CALL_BUDGET,
    DiagnosticAgentBudget,
    DiagnosticAgentBudgetTracker,
)


def _coverage(*, attempted: int = 0, concluded: int = 0) -> dict:
    return {
        "attempted": attempted,
        "concluded": concluded,
        "status_counts": {
            "PENDING": max(0, 2 - concluded),
            "SUPPORTED": concluded,
            "EXCLUDED": 0,
            "INSUFFICIENT_EVIDENCE": 0,
        },
    }


def test_tracker_enforces_aggregate_model_token_budget() -> None:
    tracker = DiagnosticAgentBudgetTracker(DiagnosticAgentBudget(
        max_total_tokens=150,
    ))

    assert tracker.record_model_usage({
        "prompt_tokens": 80,
        "completion_tokens": 20,
    }) is None
    assert tracker.record_model_usage({"total_tokens": 50}) == TOKEN_BUDGET
    usage = tracker.snapshot()["usage"]
    assert usage["input_tokens"] == 80
    assert usage["output_tokens"] == 20
    assert usage["total_tokens"] == 150
    assert usage["tool_calls"] == 0


def test_tracker_enforces_wall_clock_and_tool_call_budgets() -> None:
    now = [10.0]
    tracker = DiagnosticAgentBudgetTracker(
        DiagnosticAgentBudget(max_duration_ms=1_000, max_total_tool_calls=1),
        time_source=lambda: now[0],
    )

    assert tracker.can_invoke_tool() is True
    tracker.record_tool_call()
    assert tracker.can_invoke_tool() is False
    assert tracker.stop_reason == TOOL_CALL_BUDGET

    fresh = DiagnosticAgentBudgetTracker(
        DiagnosticAgentBudget(max_duration_ms=1_000),
        time_source=lambda: now[0],
    )
    now[0] = 11.0
    assert fresh.check_limits() == TIME_BUDGET
    assert fresh.remaining_duration_ms == 0


def test_tracker_only_stops_after_consecutive_rounds_without_real_progress() -> None:
    tracker = DiagnosticAgentBudgetTracker(DiagnosticAgentBudget(
        max_stagnant_rounds=2,
    ))
    before = _coverage()

    assert tracker.record_round_progress(
        round_number=1,
        coverage_before=before,
        coverage_after=before,
        new_tool_calls=1,
        new_evidence_ids=0,
        new_queries=1,
    ) is None
    assert tracker.record_round_progress(
        round_number=2,
        coverage_before=before,
        coverage_after=before,
        new_tool_calls=0,
        new_evidence_ids=0,
        new_queries=0,
    ) is None
    assert tracker.record_round_progress(
        round_number=3,
        coverage_before=before,
        coverage_after=before,
        new_tool_calls=0,
        new_evidence_ids=0,
        new_queries=0,
    ) == NO_PROGRESS


def test_tracker_resets_stagnation_when_fault_tree_coverage_advances() -> None:
    tracker = DiagnosticAgentBudgetTracker(DiagnosticAgentBudget(
        max_stagnant_rounds=2,
    ))
    before = _coverage()
    tracker.record_round_progress(
        round_number=1,
        coverage_before=before,
        coverage_after=before,
        new_tool_calls=0,
        new_evidence_ids=0,
        new_queries=0,
    )

    assert tracker.record_round_progress(
        round_number=2,
        coverage_before=before,
        coverage_after=_coverage(attempted=1, concluded=1),
        new_tool_calls=0,
        new_evidence_ids=0,
        new_queries=0,
    ) is None
    assert tracker.stagnant_rounds == 0
