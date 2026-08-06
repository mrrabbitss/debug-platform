from __future__ import annotations

import threading
from collections.abc import Callable
from enum import StrEnum
from time import monotonic, perf_counter
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.services.agent_trace import summary_hash
from app.services.agentic.timeouts import (
    PlannerTimedOut,
    ToolTimedOut,
    invoke_planner_with_timeout,
    invoke_tool_with_timeout,
)
from app.services.agentic.tools import (
    ToolContext,
    ToolNotAllowedError,
    ToolPermission,
    ToolRegistry,
    ToolRegistryError,
)


class AgentStopReason(StrEnum):
    COMPLETED = "COMPLETED"
    MAX_STEPS = "MAX_STEPS"
    MAX_HOPS = "MAX_HOPS"
    TOKEN_BUDGET = "TOKEN_BUDGET"
    COST_BUDGET = "COST_BUDGET"
    TIME_BUDGET = "TIME_BUDGET"
    CANCELLED = "CANCELLED"
    TOOL_NOT_ALLOWED = "TOOL_NOT_ALLOWED"
    WRITE_APPROVAL_REQUIRED = "WRITE_APPROVAL_REQUIRED"
    CIRCUIT_OPEN = "CIRCUIT_OPEN"
    TOOL_FAILED = "TOOL_FAILED"
    PLANNER_ERROR = "PLANNER_ERROR"
    FALLBACK_DETERMINISTIC = "FALLBACK_DETERMINISTIC"


class AgentBudget(BaseModel):
    max_steps: int = Field(default=8, ge=1, le=50)
    max_hops: int = Field(default=3, ge=0, le=10)
    max_tokens: int = Field(default=12_000, ge=1, le=2_000_000)
    max_cost: float = Field(default=1.0, ge=0, le=10_000)
    max_duration_ms: int = Field(default=30_000, ge=100, le=3_600_000)


class PlannerDecision(BaseModel):
    action: Literal["tool", "finish"]
    tool_name: str | None = Field(default=None, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    hop: int = Field(default=0, ge=0, le=100)
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0)
    stop_reason: str = Field(default="COMPLETED", max_length=128)


class AgentExecutionState(BaseModel):
    step: int = 0
    tokens: int = 0
    cost: float = 0.0
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    trajectory: list[dict[str, Any]] = Field(default_factory=list)


class AgentExecutionResult(BaseModel):
    status: Literal["COMPLETED", "FAILED", "CANCELLED", "FALLBACK"]
    stop_reason: AgentStopReason
    steps: int
    tokens: int
    cost: float
    duration_ms: int
    outputs: list[dict[str, Any]]
    trajectory: list[dict[str, Any]]
    fallback_result: dict[str, Any] | None = None


Planner = Callable[[AgentExecutionState], PlannerDecision | dict[str, Any]]
Fallback = Callable[[AgentExecutionState, AgentStopReason], dict[str, Any]]


class BoundedAgentExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        circuit_failure_threshold: int = 3,
        circuit_cooldown_seconds: float = 30.0,
        retry_base_seconds: float = 0.05,
    ) -> None:
        self.registry = registry
        self.circuit_failure_threshold = max(1, circuit_failure_threshold)
        self.circuit_cooldown_seconds = max(0.1, circuit_cooldown_seconds)
        self.retry_base_seconds = max(0.0, retry_base_seconds)
        self._circuit: dict[str, tuple[int, float]] = {}
        self._circuit_lock = threading.Lock()

    def _circuit_is_open(self, tool_name: str) -> bool:
        with self._circuit_lock:
            failures, opened_until = self._circuit.get(tool_name, (0, 0.0))
            if opened_until and monotonic() >= opened_until:
                self._circuit[tool_name] = (0, 0.0)
                return False
            return failures >= self.circuit_failure_threshold and opened_until > monotonic()

    def _record_tool_success(self, tool_name: str) -> None:
        with self._circuit_lock:
            self._circuit[tool_name] = (0, 0.0)

    def _record_tool_failure(self, tool_name: str) -> None:
        with self._circuit_lock:
            failures, _ = self._circuit.get(tool_name, (0, 0.0))
            failures += 1
            opened_until = (
                monotonic() + self.circuit_cooldown_seconds
                if failures >= self.circuit_failure_threshold
                else 0.0
            )
            self._circuit[tool_name] = (failures, opened_until)

    @staticmethod
    def _result(
        state: AgentExecutionState,
        *,
        status: Literal["COMPLETED", "FAILED", "CANCELLED", "FALLBACK"],
        stop_reason: AgentStopReason,
        started: float,
        fallback_result: dict[str, Any] | None = None,
    ) -> AgentExecutionResult:
        return AgentExecutionResult(
            status=status,
            stop_reason=stop_reason,
            steps=state.step,
            tokens=state.tokens,
            cost=round(state.cost, 8),
            duration_ms=int((perf_counter() - started) * 1000),
            outputs=state.outputs,
            trajectory=state.trajectory,
            fallback_result=fallback_result,
        )

    def _stop_or_fallback(
        self,
        state: AgentExecutionState,
        reason: AgentStopReason,
        started: float,
        fallback: Fallback | None,
    ) -> AgentExecutionResult:
        if fallback:
            fallback_result = fallback(state, reason)
            state.trajectory.append({
                "stage": "deterministic_fallback",
                "status": "COMPLETED",
                "reason": reason.value,
                "output_summary_hash": summary_hash(fallback_result),
            })
            return self._result(
                state,
                status="FALLBACK",
                stop_reason=AgentStopReason.FALLBACK_DETERMINISTIC,
                started=started,
                fallback_result=fallback_result,
            )
        status = "CANCELLED" if reason == AgentStopReason.CANCELLED else "FAILED"
        return self._result(
            state,
            status=status,
            stop_reason=reason,
            started=started,
        )

    def run(
        self,
        planner: Planner,
        *,
        context: ToolContext,
        budget: AgentBudget | None = None,
        fallback: Fallback | None = None,
    ) -> AgentExecutionResult:
        limits = budget or AgentBudget()
        state = AgentExecutionState()
        started = perf_counter()
        while True:
            elapsed_ms = int((perf_counter() - started) * 1000)
            if context.cancel_event.is_set():
                return self._stop_or_fallback(
                    state, AgentStopReason.CANCELLED, started, fallback
                )
            if elapsed_ms >= limits.max_duration_ms:
                return self._stop_or_fallback(
                    state, AgentStopReason.TIME_BUDGET, started, fallback
                )
            if state.step >= limits.max_steps:
                return self._stop_or_fallback(
                    state, AgentStopReason.MAX_STEPS, started, fallback
                )
            remaining_seconds = max(
                0.001,
                (limits.max_duration_ms - elapsed_ms) / 1000,
            )
            try:
                decision = PlannerDecision.model_validate(
                    invoke_planner_with_timeout(
                        planner,
                        state,
                        timeout_seconds=remaining_seconds,
                        context=context,
                    )
                )
            except PlannerTimedOut:
                return self._stop_or_fallback(
                    state, AgentStopReason.TIME_BUDGET, started, fallback
                )
            except (ValidationError, ValueError, TypeError, RuntimeError):
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback
                )
            except Exception:  # noqa: BLE001
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback
                )
            if int((perf_counter() - started) * 1000) >= limits.max_duration_ms:
                return self._stop_or_fallback(
                    state, AgentStopReason.TIME_BUDGET, started, fallback
                )
            if decision.action == "finish":
                return self._result(
                    state,
                    status="COMPLETED",
                    stop_reason=AgentStopReason.COMPLETED,
                    started=started,
                )
            if not decision.tool_name:
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback
                )
            if decision.hop > limits.max_hops:
                return self._stop_or_fallback(
                    state, AgentStopReason.MAX_HOPS, started, fallback
                )
            if state.tokens + decision.estimated_tokens > limits.max_tokens:
                return self._stop_or_fallback(
                    state, AgentStopReason.TOKEN_BUDGET, started, fallback
                )
            if state.cost + decision.estimated_cost > limits.max_cost:
                return self._stop_or_fallback(
                    state, AgentStopReason.COST_BUDGET, started, fallback
                )
            try:
                spec = self.registry.get(decision.tool_name, role=context.role)
            except (ToolRegistryError, ToolNotAllowedError):
                return self._stop_or_fallback(
                    state, AgentStopReason.TOOL_NOT_ALLOWED, started, fallback
                )
            if (
                spec.permission == ToolPermission.WRITE
                and spec.name not in context.approved_tools
            ):
                return self._stop_or_fallback(
                    state,
                    AgentStopReason.WRITE_APPROVAL_REQUIRED,
                    started,
                    fallback,
                )
            if self._circuit_is_open(spec.name):
                return self._stop_or_fallback(
                    state, AgentStopReason.CIRCUIT_OPEN, started, fallback
                )
            try:
                validated_input = spec.input_schema.model_validate(decision.arguments)
            except ValidationError:
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback
                )

            state.step += 1
            state.tokens += decision.estimated_tokens
            state.cost += decision.estimated_cost
            tool_started = perf_counter()
            final_error: Exception | None = None
            for attempt in range(spec.max_retries + 1):
                if context.cancel_event.is_set():
                    return self._stop_or_fallback(
                        state, AgentStopReason.CANCELLED, started, fallback
                    )
                try:
                    raw_output = invoke_tool_with_timeout(
                        spec,
                        context,
                        validated_input,
                        timeout_seconds=min(
                            spec.timeout_seconds,
                            max(
                                0.001,
                                (
                                    limits.max_duration_ms
                                    - int((perf_counter() - started) * 1000)
                                )
                                / 1000,
                            ),
                        ),
                    )
                    output = spec.output_schema.model_validate(raw_output)
                    self._record_tool_success(spec.name)
                    rendered = output.model_dump(mode="json")
                    state.outputs.append({
                        "tool_name": spec.name,
                        "output": rendered,
                    })
                    state.trajectory.append({
                        "stage": "tool",
                        "tool_name": spec.name,
                        "tool_permission": spec.permission.value,
                        "status": "COMPLETED",
                        "retry_count": attempt,
                        "duration_ms": int((perf_counter() - tool_started) * 1000),
                        "input_summary_hash": summary_hash(decision.arguments),
                        "output_summary_hash": summary_hash(rendered),
                    })
                    break
                except Exception as exc:  # noqa: BLE001
                    final_error = exc
                    self._record_tool_failure(spec.name)
                    if (
                        isinstance(exc, ToolTimedOut)
                        or attempt >= spec.max_retries
                        or not spec.idempotent
                    ):
                        state.trajectory.append({
                            "stage": "tool",
                            "tool_name": spec.name,
                            "tool_permission": spec.permission.value,
                            "status": "FAILED",
                            "retry_count": attempt,
                            "duration_ms": int(
                                (perf_counter() - tool_started) * 1000
                            ),
                            "input_summary_hash": summary_hash(decision.arguments),
                            "reason": type(exc).__name__,
                        })
                        break
                    delay = self.retry_base_seconds * (2 ** attempt)
                    if context.cancel_event.wait(delay):
                        return self._stop_or_fallback(
                            state, AgentStopReason.CANCELLED, started, fallback
                        )
            if final_error is not None and (
                not state.trajectory
                or state.trajectory[-1].get("status") == "FAILED"
            ):
                if int((perf_counter() - started) * 1000) >= limits.max_duration_ms:
                    return self._stop_or_fallback(
                        state, AgentStopReason.TIME_BUDGET, started, fallback
                    )
                return self._stop_or_fallback(
                    state, AgentStopReason.TOOL_FAILED, started, fallback
                )


def sequence_planner(decisions: list[PlannerDecision]) -> Planner:
    def planner(state: AgentExecutionState) -> PlannerDecision:
        if state.step >= len(decisions):
            return PlannerDecision(action="finish")
        return decisions[state.step]

    return planner
