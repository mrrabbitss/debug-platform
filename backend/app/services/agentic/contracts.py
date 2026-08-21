"""Typed contracts for the bounded Agent executor."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


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
    depends_on_step: int | None = Field(default=None, ge=1, le=50)
    # Compatibility hints only. Runtime-observed usage and dependency depth are
    # authoritative whenever they are available.
    hop: int = Field(default=0, ge=0, le=100)
    estimated_tokens: int = Field(default=0, ge=0)
    estimated_cost: float = Field(default=0.0, ge=0)
    stop_reason: str = Field(default="COMPLETED", max_length=128)


class AgentExecutionState(BaseModel):
    step: int = 0
    tokens: int = 0
    cost: float = 0.0
    planner_tokens: int = 0
    tool_output_tokens: int = 0
    causal_depth: int = 0
    step_depths: list[int] = Field(default_factory=list)
    usage_sources: dict[str, int] = Field(default_factory=dict)
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    trajectory: list[dict[str, Any]] = Field(default_factory=list)


class AgentExecutionResult(BaseModel):
    status: Literal["COMPLETED", "FAILED", "CANCELLED", "FALLBACK"]
    stop_reason: AgentStopReason
    steps: int
    tokens: int
    cost: float
    planner_tokens: int
    tool_output_tokens: int
    causal_depth: int
    duration_ms: int
    outputs: list[dict[str, Any]]
    trajectory: list[dict[str, Any]]
    budget: dict[str, Any]
    fallback_result: dict[str, Any] | None = None


Planner = Callable[[AgentExecutionState], PlannerDecision | dict[str, Any]]
Fallback = Callable[[AgentExecutionState, AgentStopReason], dict[str, Any]]
