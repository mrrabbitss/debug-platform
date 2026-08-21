"""Shared hard budgets and stagnation tracking for the diagnostic tool Agent."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.services.agent_runtime.budget import ExecutionBudgetLedger


TOKEN_BUDGET = "TOKEN_BUDGET"
TIME_BUDGET = "TIME_BUDGET"
TOOL_CALL_BUDGET = "TOOL_CALL_BUDGET"
NO_PROGRESS = "NO_PROGRESS"


class DiagnosticAgentBudget(BaseModel):
    """Limits for one comprehensive-diagnosis planning run.

    Twenty rounds remain the exhaustive upper bound. The additional limits keep
    one slow or repetitive provider run from consuming the whole analysis-job
    timeout while preserving the existing deterministic fallback.
    """

    max_rounds: int = Field(default=20, ge=2, le=20)
    min_rounds: int = Field(default=2, ge=1, le=20)
    max_tool_calls_per_round: int = Field(default=4, ge=1, le=4)
    max_total_tool_calls: int = Field(default=80, ge=1, le=80)
    max_total_tokens: int = Field(default=2_000_000, ge=1, le=100_000_000)
    max_duration_ms: int = Field(
        default=3 * 60 * 60 * 1000,
        ge=1_000,
        le=4 * 60 * 60 * 1000,
    )
    max_stagnant_rounds: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def validate_round_bounds(self) -> "DiagnosticAgentBudget":
        if self.min_rounds > self.max_rounds:
            raise ValueError("min_rounds cannot exceed max_rounds")
        return self


class DiagnosticAgentBudgetTracker:
    """Accumulate provider usage and detect genuinely stagnant rounds."""

    def __init__(
        self,
        budget: DiagnosticAgentBudget,
        *,
        time_source: Callable[[], float] = monotonic,
    ) -> None:
        self.budget = budget
        self.ledger = ExecutionBudgetLedger(
            max_tokens=budget.max_total_tokens,
            max_cost=0.0,
            max_duration_ms=budget.max_duration_ms,
            max_tool_calls=budget.max_total_tool_calls,
            time_source=time_source,
        )
        self.rounds_completed = 0
        self.stagnant_rounds = 0
        self.stop_reason: str | None = None

    @property
    def duration_ms(self) -> int:
        return self.ledger.duration_ms

    @property
    def remaining_duration_ms(self) -> int:
        return self.ledger.remaining_duration_ms

    @property
    def input_tokens(self) -> int:
        return self.ledger.input_tokens

    @property
    def output_tokens(self) -> int:
        return self.ledger.output_tokens

    @property
    def total_tokens(self) -> int:
        return self.ledger.total_tokens

    @property
    def tool_calls(self) -> int:
        return self.ledger.tool_calls

    def check_limits(self) -> str | None:
        self.stop_reason = self.ledger.limit_reason(
            token_reason=TOKEN_BUDGET,
            cost_reason=TOKEN_BUDGET,
            time_reason=TIME_BUDGET,
            tool_reason=TOOL_CALL_BUDGET,
        )
        return self.stop_reason

    def record_model_usage(self, usage: dict[str, Any]) -> str | None:
        self.ledger.record_model_usage(usage)
        return self.check_limits()

    def record_tool_output(self, output: Any) -> str | None:
        self.ledger.record_tool_output(output)
        return self.check_limits()

    def can_invoke_tool(self) -> bool:
        if not self.ledger.can_invoke_tool():
            self.stop_reason = TOOL_CALL_BUDGET
            return False
        return True

    def record_tool_call(self) -> None:
        self.ledger.record_tool_call()

    @staticmethod
    def _coverage_signature(snapshot: dict[str, Any]) -> tuple[Any, ...]:
        counts = snapshot.get("status_counts", {})
        return (
            int(snapshot.get("attempted") or 0),
            int(snapshot.get("concluded") or 0),
            *(int(counts.get(status) or 0) for status in (
                "PENDING",
                "SUPPORTED",
                "EXCLUDED",
                "INSUFFICIENT_EVIDENCE",
            )),
        )

    def record_round_progress(
        self,
        *,
        round_number: int,
        coverage_before: dict[str, Any],
        coverage_after: dict[str, Any],
        new_tool_calls: int,
        new_evidence_ids: int,
        new_queries: int,
    ) -> str | None:
        self.rounds_completed = max(self.rounds_completed, round_number)
        progressed = any((
            self._coverage_signature(coverage_before)
            != self._coverage_signature(coverage_after),
            new_tool_calls > 0,
            new_evidence_ids > 0,
            new_queries > 0,
        ))
        self.stagnant_rounds = 0 if progressed else self.stagnant_rounds + 1
        limit_reason = self.check_limits()
        if limit_reason:
            return limit_reason
        if (
            round_number >= self.budget.min_rounds
            and self.stagnant_rounds >= self.budget.max_stagnant_rounds
        ):
            self.stop_reason = NO_PROGRESS
        return self.stop_reason

    def snapshot(self) -> dict[str, Any]:
        ledger_snapshot = self.ledger.snapshot()
        return {
            "limits": self.budget.model_dump(mode="json"),
            "usage": {
                **ledger_snapshot["usage"],
                "rounds": self.rounds_completed,
                "stagnant_rounds": self.stagnant_rounds,
            },
            "remaining": {
                **ledger_snapshot["remaining"],
            },
            "stop_reason": self.stop_reason,
        }


def budget_failure_details(
    reason: str,
    snapshot: dict[str, Any],
    *,
    finish_reason: str | None,
) -> dict[str, Any]:
    messages = {
        TOKEN_BUDGET: "Diagnostic planning reached the aggregate Token budget",
        TIME_BUDGET: "Diagnostic planning reached the wall-clock budget",
        TOOL_CALL_BUDGET: "Diagnostic planning reached the read-only tool-call budget",
        NO_PROGRESS: (
            "Diagnostic planning stopped after consecutive rounds without new "
            "coverage, evidence, queries, or unique tool calls"
        ),
    }
    return {
        "code": reason,
        "message": messages.get(reason, "Diagnostic planning budget stopped the run"),
        "field_path": "agent_budget",
        "error_type": (
            "AgentStagnationError" if reason == NO_PROGRESS
            else "AgentBudgetExceeded"
        ),
        "finish_reason": finish_reason,
        "budget": snapshot,
    }


def configured_diagnostic_agent_budget() -> DiagnosticAgentBudget:
    """Build the runtime budget from validated application settings."""
    from app.core.config import get_settings

    settings = get_settings()
    return DiagnosticAgentBudget(
        max_total_tokens=settings.diagnostic_agent_max_total_tokens,
        max_duration_ms=settings.diagnostic_agent_max_duration_seconds * 1000,
        max_total_tool_calls=settings.diagnostic_agent_max_total_tool_calls,
        max_stagnant_rounds=settings.diagnostic_agent_max_stagnant_rounds,
    )
