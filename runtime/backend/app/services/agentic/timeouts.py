from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any

from app.services.agentic.tools import ToolContext


class ToolTimedOut(TimeoutError):
    pass


class PlannerTimedOut(TimeoutError):
    pass


def invoke_tool_with_timeout(
    spec: Any,
    context: ToolContext,
    validated_input: Any,
    *,
    timeout_seconds: float,
) -> Any:
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put((True, spec.handler(context, validated_input)))
        except Exception as exc:  # noqa: BLE001
            result_queue.put((False, exc))

    thread = threading.Thread(
        target=target,
        name=f"agent-tool-{spec.name[:32]}",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        context.cancel_event.set()
        raise ToolTimedOut(f"Tool {spec.name} exceeded {timeout_seconds:.2f}s")
    ok, value = result_queue.get_nowait()
    if not ok:
        raise value
    return value


def invoke_planner_with_timeout(
    planner: Callable[[Any], Any],
    state: Any,
    *,
    timeout_seconds: float,
    context: ToolContext,
) -> Any:
    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            result_queue.put((True, planner(state)))
        except Exception as exc:  # noqa: BLE001
            result_queue.put((False, exc))

    thread = threading.Thread(
        target=target,
        name="agent-planner",
        daemon=True,
    )
    thread.start()
    thread.join(timeout=timeout_seconds)
    if thread.is_alive():
        context.cancel_event.set()
        raise PlannerTimedOut(
            f"Planner exceeded remaining wall-clock budget ({timeout_seconds:.2f}s)"
        )
    ok, value = result_queue.get_nowait()
    if not ok:
        raise value
    return value
