import threading
import time

from pydantic import BaseModel

from app.services.agentic.executor import (
    AgentBudget,
    AgentStopReason,
    BoundedAgentExecutor,
    PlannerDecision,
    sequence_planner,
)
from app.services.agentic.tools import (
    ToolContext,
    ToolPermission,
    ToolRegistry,
    ToolSpec,
)


class QueryInput(BaseModel):
    query: str


class CountOutput(BaseModel):
    count: int


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="knowledge.search",
        description="Read-only knowledge lookup",
        input_schema=QueryInput,
        output_schema=CountOutput,
        handler=lambda context, value: {"count": len(value.query)},
        permission=ToolPermission.READ,
        allowed_roles=frozenset({"ADMIN", "ENGINEER", "VIEWER"}),
    ))
    registry.register(ToolSpec(
        name="knowledge.publish",
        description="Publish reviewed knowledge",
        input_schema=QueryInput,
        output_schema=CountOutput,
        handler=lambda context, value: {"count": 1},
        permission=ToolPermission.WRITE,
        allowed_roles=frozenset({"ADMIN"}),
        idempotent=False,
    ))
    return registry


def test_bounded_executor_runs_typed_read_tool_and_records_hashes() -> None:
    executor = BoundedAgentExecutor(_registry())
    result = executor.run(
        sequence_planner([PlannerDecision(
            action="tool",
            tool_name="knowledge.search",
            arguments={"query": "AUTH"},
            hop=1,
            estimated_tokens=25,
            estimated_cost=0.01,
        )]),
        context=ToolContext(role="ENGINEER", case_id="CASE-one"),
    )

    assert result.status == "COMPLETED"
    assert result.stop_reason == AgentStopReason.COMPLETED
    assert result.steps == 1
    assert result.outputs[0]["output"] == {"count": 4}
    assert len(result.trajectory[0]["input_summary_hash"]) == 64
    assert "AUTH" not in str(result.trajectory)


def test_write_tool_requires_role_and_explicit_approval() -> None:
    decision = PlannerDecision(
        action="tool",
        tool_name="knowledge.publish",
        arguments={"query": "draft"},
    )
    executor = BoundedAgentExecutor(_registry())
    denied_role = executor.run(
        sequence_planner([decision]),
        context=ToolContext(role="ENGINEER"),
    )
    assert denied_role.stop_reason == AgentStopReason.TOOL_NOT_ALLOWED

    missing_approval = executor.run(
        sequence_planner([decision]),
        context=ToolContext(role="ADMIN"),
    )
    assert missing_approval.stop_reason == AgentStopReason.WRITE_APPROVAL_REQUIRED

    approved = executor.run(
        sequence_planner([decision]),
        context=ToolContext(
            role="ADMIN",
            approved_tools=frozenset({"knowledge.publish"}),
        ),
    )
    assert approved.status == "COMPLETED"


def test_executor_enforces_hop_token_and_step_budgets() -> None:
    executor = BoundedAgentExecutor(_registry())
    hop = executor.run(
        sequence_planner([
            PlannerDecision(
                action="tool",
                tool_name="knowledge.search",
                arguments={"query": "root"},
                hop=99,
            ),
            PlannerDecision(
                action="tool",
                tool_name="knowledge.search",
                arguments={"query": "dependent"},
                depends_on_step=1,
                hop=0,
            ),
        ]),
        context=ToolContext(role="VIEWER"),
        budget=AgentBudget(max_hops=1),
    )
    assert hop.stop_reason == AgentStopReason.MAX_HOPS
    assert hop.steps == 1
    assert hop.causal_depth == 1
    assert hop.trajectory[0]["planner_advisory_hop"] == 99

    tokens = executor.run(
        sequence_planner([PlannerDecision(
            action="tool",
            tool_name="knowledge.search",
            arguments={"query": "x"},
            estimated_tokens=11,
        )]),
        context=ToolContext(role="VIEWER"),
        budget=AgentBudget(max_tokens=10),
    )
    assert tokens.stop_reason == AgentStopReason.TOKEN_BUDGET

    repeated = PlannerDecision(
        action="tool",
        tool_name="knowledge.search",
        arguments={"query": "x"},
    )
    steps = executor.run(
        lambda state: repeated,
        context=ToolContext(role="VIEWER"),
        budget=AgentBudget(max_steps=2),
    )
    assert steps.stop_reason == AgentStopReason.MAX_STEPS
    assert steps.steps == 2

    cost = executor.run(
        sequence_planner([PlannerDecision(
            action="tool",
            tool_name="knowledge.search",
            arguments={"query": "x"},
            estimated_cost=0.11,
        )]),
        context=ToolContext(role="VIEWER"),
        budget=AgentBudget(max_cost=0.1),
    )
    assert cost.stop_reason == AgentStopReason.COST_BUDGET


def test_executor_prefers_provider_usage_over_planner_estimates() -> None:
    class UsageReportingPlanner:
        last_usage = {
            "prompt_tokens": 30,
            "completion_tokens": 20,
            "total_tokens": 50,
        }
        last_cost = 0.25

        def __call__(self, state):
            return PlannerDecision(
                action="tool",
                tool_name="knowledge.search",
                arguments={"query": "x"},
                estimated_tokens=0,
                estimated_cost=0,
            )

    result = BoundedAgentExecutor(_registry()).run(
        UsageReportingPlanner(),
        context=ToolContext(role="VIEWER"),
        budget=AgentBudget(max_tokens=40, max_cost=1),
    )

    assert result.stop_reason == AgentStopReason.TOKEN_BUDGET
    assert result.tokens == 50
    assert result.steps == 0
    assert result.budget["usage"]["usage_sources"] == {"provider_reported": 1}


def test_planner_and_tool_are_bounded_by_wall_clock() -> None:
    executor = BoundedAgentExecutor(_registry())

    def slow_planner(state):
        time.sleep(0.2)
        return PlannerDecision(action="finish")

    planner_timeout = executor.run(
        slow_planner,
        context=ToolContext(role="VIEWER"),
        budget=AgentBudget(max_duration_ms=100),
    )
    assert planner_timeout.stop_reason == AgentStopReason.TIME_BUDGET
    assert planner_timeout.duration_ms < 180

    registry = ToolRegistry()

    def slow_tool(context, value):
        context.cancel_event.wait(0.2)
        return {"count": 1}

    registry.register(ToolSpec(
        name="slow.read",
        description="Synthetic slow read",
        input_schema=QueryInput,
        output_schema=CountOutput,
        handler=slow_tool,
        timeout_seconds=0.05,
        max_retries=0,
    ))
    tool_timeout = BoundedAgentExecutor(registry).run(
        sequence_planner([PlannerDecision(
            action="tool",
            tool_name="slow.read",
            arguments={"query": "x"},
        )]),
        context=ToolContext(role="VIEWER"),
    )
    assert tool_timeout.stop_reason == AgentStopReason.TOOL_FAILED


def test_retry_circuit_cancel_and_deterministic_fallback() -> None:
    attempts = 0

    def fail(context, value):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("synthetic failure")

    registry = ToolRegistry()
    registry.register(ToolSpec(
        name="unstable.read",
        description="Synthetic unstable read",
        input_schema=QueryInput,
        output_schema=CountOutput,
        handler=fail,
        max_retries=1,
    ))
    executor = BoundedAgentExecutor(
        registry,
        circuit_failure_threshold=2,
        retry_base_seconds=0,
    )
    decision = PlannerDecision(
        action="tool",
        tool_name="unstable.read",
        arguments={"query": "x"},
    )
    fallback = executor.run(
        sequence_planner([decision]),
        context=ToolContext(role="ENGINEER"),
        fallback=lambda state, reason: {"baseline": True, "reason": reason.value},
    )
    assert attempts == 2
    assert fallback.status == "FALLBACK"
    assert fallback.stop_reason == AgentStopReason.FALLBACK_DETERMINISTIC
    assert fallback.fallback_result["baseline"] is True

    circuit = executor.run(
        sequence_planner([decision]),
        context=ToolContext(role="ENGINEER"),
    )
    assert circuit.stop_reason == AgentStopReason.CIRCUIT_OPEN

    cancelled = threading.Event()
    cancelled.set()
    result = BoundedAgentExecutor(_registry()).run(
        sequence_planner([]),
        context=ToolContext(role="VIEWER", cancel_event=cancelled),
    )
    assert result.status == "CANCELLED"
    assert result.stop_reason == AgentStopReason.CANCELLED
