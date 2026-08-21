"""Provider-observed execution accounting shared by agent runtimes."""

from __future__ import annotations

from collections.abc import Callable
from time import monotonic
from typing import Any

from app.services.agent_runtime.context import estimate_tokens


def merge_usage_totals(total: dict[str, int], usage: dict[str, Any]) -> None:
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(
        usage.get("completion_tokens") or usage.get("output_tokens") or 0
    )
    reported_total = int(usage.get("total_tokens") or 0)
    total["prompt_tokens"] += input_tokens
    total["completion_tokens"] += output_tokens
    total["total_tokens"] += reported_total or input_tokens + output_tokens
    total["cached_tokens"] = total.get("cached_tokens", 0) + int(
        usage.get("cached_tokens") or 0
    )
    total["reasoning_tokens"] = total.get("reasoning_tokens", 0) + int(
        usage.get("reasoning_tokens") or 0
    )


class ExecutionBudgetLedger:
    """Count observed model usage, tool output size, cost, calls, and time.

    Planner estimates are used only when the provider exposes no usage/cost.
    This prevents a planner from bypassing hard limits by returning zero while
    keeping deterministic test planners and non-provider integrations usable.
    """

    def __init__(
        self,
        *,
        max_tokens: int,
        max_cost: float,
        max_duration_ms: int,
        max_tool_calls: int,
        time_source: Callable[[], float] = monotonic,
    ) -> None:
        self.max_tokens = max_tokens
        self.max_cost = max_cost
        self.max_duration_ms = max_duration_ms
        self.max_tool_calls = max_tool_calls
        self._time_source = time_source
        self._started_at = time_source()
        self.input_tokens = 0
        self.cached_input_tokens = 0
        self.output_tokens = 0
        self.tool_output_tokens = 0
        self.total_tokens = 0
        self.cost = 0.0
        self.tool_calls = 0
        self.model_calls = 0
        self.usage_sources: dict[str, int] = {}

    @property
    def duration_ms(self) -> int:
        return max(0, int((self._time_source() - self._started_at) * 1000))

    @property
    def remaining_duration_ms(self) -> int:
        return max(0, self.max_duration_ms - self.duration_ms)

    def _record_source(self, source: str) -> None:
        self.usage_sources[source] = self.usage_sources.get(source, 0) + 1

    def record_model_usage(
        self,
        usage: dict[str, Any] | None,
        *,
        fallback_payload: Any = None,
        fallback_tokens: int = 0,
        reported_cost: float | None = None,
        fallback_cost: float = 0.0,
    ) -> dict[str, Any]:
        usage = usage or {}
        input_tokens = int(
            usage.get("prompt_tokens") or usage.get("input_tokens") or 0
        )
        output_tokens = int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
        reported_total = int(usage.get("total_tokens") or 0)
        observed_total = reported_total or input_tokens + output_tokens
        if observed_total > 0:
            source = "provider_reported"
        else:
            observed_total = max(
                int(fallback_tokens or 0),
                estimate_tokens(fallback_payload) if fallback_payload is not None else 0,
            )
            output_tokens = observed_total
            source = "runtime_estimate"
        cached_tokens = int(usage.get("cached_tokens") or 0)
        cost_value = (
            float(reported_cost)
            if reported_cost is not None
            else float(usage.get("cost") or fallback_cost or 0.0)
        )
        self.input_tokens += input_tokens
        self.cached_input_tokens += cached_tokens
        self.output_tokens += output_tokens
        self.total_tokens += observed_total
        self.cost += max(0.0, cost_value)
        self.model_calls += 1
        self._record_source(source)
        return {
            "source": source,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": observed_total,
            "cached_tokens": cached_tokens,
            "cost": max(0.0, cost_value),
        }

    def record_tool_output(self, output: Any) -> int:
        tokens = estimate_tokens(output)
        self.tool_output_tokens += tokens
        self.total_tokens += tokens
        return tokens

    def can_invoke_tool(self) -> bool:
        return self.tool_calls < self.max_tool_calls

    def record_tool_call(self) -> None:
        self.tool_calls += 1

    def limit_reason(
        self,
        *,
        token_reason: str,
        cost_reason: str,
        time_reason: str,
        tool_reason: str,
    ) -> str | None:
        if self.duration_ms >= self.max_duration_ms:
            return time_reason
        if self.total_tokens >= self.max_tokens:
            return token_reason
        if self.cost >= self.max_cost and self.max_cost > 0:
            return cost_reason
        if self.tool_calls >= self.max_tool_calls:
            return tool_reason
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "usage": {
                "input_tokens": self.input_tokens,
                "cached_input_tokens": self.cached_input_tokens,
                "output_tokens": self.output_tokens,
                "tool_output_tokens": self.tool_output_tokens,
                "total_tokens": self.total_tokens,
                "cost": round(self.cost, 8),
                "model_calls": self.model_calls,
                "tool_calls": self.tool_calls,
                "duration_ms": self.duration_ms,
                "usage_sources": dict(sorted(self.usage_sources.items())),
            },
            "remaining": {
                "tokens": max(0, self.max_tokens - self.total_tokens),
                "cost": round(max(0.0, self.max_cost - self.cost), 8),
                "tool_calls": max(0, self.max_tool_calls - self.tool_calls),
                "duration_ms": self.remaining_duration_ms,
            },
        }
