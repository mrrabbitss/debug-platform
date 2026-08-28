"""Small state helpers shared by bounded execution loops."""

from __future__ import annotations

from typing import Any

from app.services.agent_runtime.budget import ExecutionBudgetLedger


def sync_execution_state(state: Any, ledger: ExecutionBudgetLedger) -> None:
    state.tokens = ledger.total_tokens
    state.planner_tokens = ledger.input_tokens + ledger.output_tokens
    state.tool_output_tokens = ledger.tool_output_tokens
    state.cost = ledger.cost
    state.usage_sources = dict(ledger.usage_sources)


def causal_depth(step_depths: list[int], depends_on_step: int | None) -> int:
    if depends_on_step is None:
        return 1
    if depends_on_step > len(step_depths):
        raise ValueError("Planner referenced a future or missing dependency step")
    return step_depths[depends_on_step - 1] + 1
