from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias

from pydantic import BaseModel, ValidationError

from app.mcp.contracts import (
    MCPPrincipal,
    MCPToolCallContext,
    MCPToolDefinition,
    MCPToolError,
)


ToolInput: TypeAlias = BaseModel
ToolHandler: TypeAlias = Callable[
    [ToolInput, MCPToolCallContext],
    Any | Awaitable[Any],
]


@dataclass(frozen=True, slots=True)
class _RegisteredTool:
    definition: MCPToolDefinition
    input_model: type[BaseModel]
    handler: ToolHandler


class MCPToolRegistry:
    """Small injectable registry for typed Debug Platform tools.

    Domain modules can register Pydantic input/output models without depending on
    MCP protocol internals.  Case-level authorization remains in each handler.
    """

    def __init__(self) -> None:
        self._tools: dict[str, _RegisteredTool] = {}

    def register(
        self,
        *,
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler: ToolHandler,
        output_model: type[BaseModel] | None = None,
        title: str | None = None,
        annotations: Mapping[str, Any] | None = None,
    ) -> None:
        if name in self._tools:
            raise ValueError(f"MCP tool {name!r} is already registered")
        if not name or len(name) > 128:
            raise ValueError("MCP tool names must contain between 1 and 128 characters")
        if not description.strip():
            raise ValueError(f"MCP tool {name!r} must have a description")
        if not issubclass(input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic BaseModel subclass")
        if output_model is not None and not issubclass(output_model, BaseModel):
            raise TypeError("output_model must be a Pydantic BaseModel subclass")

        definition = MCPToolDefinition(
            name=name,
            title=title,
            description=description,
            input_schema=input_model.model_json_schema(),
            output_schema=output_model.model_json_schema() if output_model is not None else None,
            annotations=dict(annotations) if annotations is not None else None,
        )
        self._tools[name] = _RegisteredTool(
            definition=definition,
            input_model=input_model,
            handler=handler,
        )

    async def list_tools(self, principal: MCPPrincipal) -> Sequence[MCPToolDefinition]:
        del principal
        return tuple(tool.definition for tool in self._tools.values())

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: MCPToolCallContext,
    ) -> Any:
        registered = self._tools.get(name)
        if registered is None:
            raise MCPToolError(f"Unknown tool: {name}")
        try:
            validated = registered.input_model.model_validate(dict(arguments))
        except ValidationError as exc:
            raise MCPToolError(f"Invalid arguments for {name}: {exc}") from exc

        result = registered.handler(validated, context)
        if inspect.isawaitable(result):
            result = await result
        return result

