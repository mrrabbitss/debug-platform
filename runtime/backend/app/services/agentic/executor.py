from __future__ import annotations

import threading
from time import monotonic, perf_counter
from typing import Any, Literal

from pydantic import ValidationError

from app.services.agent_trace import summary_hash
from app.services.agent_runtime.budget import ExecutionBudgetLedger
from app.services.agent_runtime.execution import causal_depth, sync_execution_state
from app.services.agentic.contracts import (
    AgentBudget,
    AgentExecutionResult,
    AgentExecutionState,
    AgentStopReason,
    Fallback,
    Planner,
    PlannerDecision,
)
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
        budget_snapshot: dict[str, Any] | None = None,
    ) -> AgentExecutionResult:
        return AgentExecutionResult(
            status=status,
            stop_reason=stop_reason,
            steps=state.step,
            tokens=state.tokens,
            cost=round(state.cost, 8),
            planner_tokens=state.planner_tokens,
            tool_output_tokens=state.tool_output_tokens,
            causal_depth=state.causal_depth,
            duration_ms=int((perf_counter() - started) * 1000),
            outputs=state.outputs,
            trajectory=state.trajectory,
            budget=budget_snapshot or {},
            fallback_result=fallback_result,
        )

    def _stop_or_fallback(
        self,
        state: AgentExecutionState,
        reason: AgentStopReason,
        started: float,
        fallback: Fallback | None,
        ledger: ExecutionBudgetLedger,
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
                budget_snapshot=ledger.snapshot(),
            )
        status = "CANCELLED" if reason == AgentStopReason.CANCELLED else "FAILED"
        return self._result(
            state,
            status=status,
            stop_reason=reason,
            started=started,
            budget_snapshot=ledger.snapshot(),
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
        ledger = ExecutionBudgetLedger(
            max_tokens=limits.max_tokens,
            max_cost=limits.max_cost,
            max_duration_ms=limits.max_duration_ms,
            max_tool_calls=limits.max_steps,
        )
        while True:
            elapsed_ms = int((perf_counter() - started) * 1000)
            if context.cancel_event.is_set():
                return self._stop_or_fallback(
                    state, AgentStopReason.CANCELLED, started, fallback, ledger
                )
            if elapsed_ms >= limits.max_duration_ms:
                return self._stop_or_fallback(
                    state, AgentStopReason.TIME_BUDGET, started, fallback, ledger
                )
            if state.step >= limits.max_steps:
                return self._stop_or_fallback(
                    state, AgentStopReason.MAX_STEPS, started, fallback, ledger
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
                    state, AgentStopReason.TIME_BUDGET, started, fallback, ledger
                )
            except (ValidationError, ValueError, TypeError, RuntimeError):
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback, ledger
                )
            except Exception:  # noqa: BLE001
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback, ledger
                )
            if int((perf_counter() - started) * 1000) >= limits.max_duration_ms:
                return self._stop_or_fallback(
                    state, AgentStopReason.TIME_BUDGET, started, fallback, ledger
                )
            planner_usage = getattr(planner, "last_usage", {}) or {}
            reported_cost = getattr(planner, "last_cost", None)
            ledger.record_model_usage(
                planner_usage,
                fallback_payload=decision.model_dump(mode="json"),
                fallback_tokens=decision.estimated_tokens,
                reported_cost=reported_cost,
                fallback_cost=decision.estimated_cost,
            )
            sync_execution_state(state, ledger)
            if state.tokens >= limits.max_tokens:
                return self._stop_or_fallback(
                    state, AgentStopReason.TOKEN_BUDGET, started, fallback, ledger
                )
            if limits.max_cost > 0 and state.cost >= limits.max_cost:
                return self._stop_or_fallback(
                    state, AgentStopReason.COST_BUDGET, started, fallback, ledger
                )
            if decision.action == "finish":
                return self._result(
                    state,
                    status="COMPLETED",
                    stop_reason=AgentStopReason.COMPLETED,
                    started=started,
                    budget_snapshot=ledger.snapshot(),
                )
            if not decision.tool_name:
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback, ledger
                )
            try:
                step_depth = causal_depth(
                    state.step_depths, decision.depends_on_step
                )
            except ValueError:
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback, ledger
                )
            if step_depth > limits.max_hops:
                return self._stop_or_fallback(
                    state, AgentStopReason.MAX_HOPS, started, fallback, ledger
                )
            try:
                spec = self.registry.get(decision.tool_name, role=context.role)
            except (ToolRegistryError, ToolNotAllowedError):
                return self._stop_or_fallback(
                    state, AgentStopReason.TOOL_NOT_ALLOWED, started, fallback, ledger
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
                    ledger,
                )
            if self._circuit_is_open(spec.name):
                return self._stop_or_fallback(
                    state, AgentStopReason.CIRCUIT_OPEN, started, fallback, ledger
                )
            try:
                validated_input = spec.input_schema.model_validate(decision.arguments)
            except ValidationError:
                return self._stop_or_fallback(
                    state, AgentStopReason.PLANNER_ERROR, started, fallback, ledger
                )

            state.step += 1
            state.step_depths.append(step_depth)
            state.causal_depth = max(state.causal_depth, step_depth)
            ledger.record_tool_call()
            tool_started = perf_counter()
            final_error: Exception | None = None
            for attempt in range(spec.max_retries + 1):
                if context.cancel_event.is_set():
                    return self._stop_or_fallback(
                        state, AgentStopReason.CANCELLED, started, fallback, ledger
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
                    output_tokens = ledger.record_tool_output(rendered)
                    sync_execution_state(state, ledger)
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
                        "causal_depth": step_depth,
                        "depends_on_step": decision.depends_on_step,
                        "planner_advisory_hop": decision.hop,
                        "usage_source": next(
                            reversed(state.usage_sources), "runtime_estimate"
                        ),
                        "tool_output_tokens": output_tokens,
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
                            state, AgentStopReason.CANCELLED, started, fallback, ledger
                        )
            if state.tokens >= limits.max_tokens:
                return self._stop_or_fallback(
                    state, AgentStopReason.TOKEN_BUDGET, started, fallback, ledger
                )
            if final_error is not None and (
                not state.trajectory
                or state.trajectory[-1].get("status") == "FAILED"
            ):
                if int((perf_counter() - started) * 1000) >= limits.max_duration_ms:
                    return self._stop_or_fallback(
                        state, AgentStopReason.TIME_BUDGET, started, fallback, ledger
                    )
                return self._stop_or_fallback(
                    state, AgentStopReason.TOOL_FAILED, started, fallback, ledger
                )


def sequence_planner(decisions: list[PlannerDecision]) -> Planner:
    def planner(state: AgentExecutionState) -> PlannerDecision:
        if state.step >= len(decisions):
            return PlannerDecision(action="finish")
        return decisions[state.step]

    return planner
