from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Generic, TypeVar

from pydantic import BaseModel


class ToolPermission(StrEnum):
    READ = "READ"
    WRITE = "WRITE"


class ToolRegistryError(ValueError):
    pass


class ToolNotAllowedError(ToolRegistryError):
    pass


InputModel = TypeVar("InputModel", bound=BaseModel)
OutputModel = TypeVar("OutputModel", bound=BaseModel)


@dataclass
class ToolContext:
    role: str
    case_id: str | None = None
    approved_tools: frozenset[str] = frozenset()
    cancel_event: threading.Event = field(default_factory=threading.Event)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolSpec(Generic[InputModel, OutputModel]):
    name: str
    description: str
    input_schema: type[InputModel]
    output_schema: type[OutputModel]
    handler: Callable[[ToolContext, InputModel], OutputModel | dict[str, Any]]
    permission: ToolPermission = ToolPermission.READ
    allowed_roles: frozenset[str] = frozenset({"ADMIN", "ENGINEER", "VIEWER"})
    idempotent: bool = True
    max_retries: int = 1
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 128:
            raise ValueError("Tool name must contain 1-128 characters")
        if self.max_retries < 0 or self.max_retries > 5:
            raise ValueError("Tool retries must be between 0 and 5")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 3600:
            raise ValueError("Tool timeout must be between 0 and 3600 seconds")
        if self.permission == ToolPermission.WRITE and self.idempotent is False:
            object.__setattr__(self, "max_retries", 0)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec[Any, Any]] = {}

    def register(self, spec: ToolSpec[Any, Any]) -> None:
        if spec.name in self._tools:
            raise ToolRegistryError(f"Tool is already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str, *, role: str) -> ToolSpec[Any, Any]:
        spec = self._tools.get(name)
        if not spec:
            raise ToolRegistryError(f"Unknown tool: {name}")
        if role not in spec.allowed_roles:
            raise ToolNotAllowedError(
                f"Role {role} is not allowed to use tool {name}"
            )
        return spec

    def manifest(self, *, role: str) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "permission": spec.permission.value,
                "idempotent": spec.idempotent,
                "input_schema": spec.input_schema.model_json_schema(),
                "output_schema": spec.output_schema.model_json_schema(),
            }
            for spec in sorted(self._tools.values(), key=lambda item: item.name)
            if role in spec.allowed_roles
        ]
