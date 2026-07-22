from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CaseCreate(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    device_type: Literal["GW", "AP", "OTHER"] = "GW"
    device_model: str | None = None
    firmware_version: str | None = None
    topology: str | None = None
    description: str = ""
    reproduction_steps: str | None = None
    issue_time: str | None = None


class CaseUpdate(BaseModel):
    title: str | None = None
    device_model: str | None = None
    firmware_version: str | None = None
    topology: str | None = None
    description: str | None = None
    reproduction_steps: str | None = None
    issue_time: str | None = None
    severity: str | None = None


class CaseOut(ORMModel):
    id: str
    title: str
    device_type: str
    device_model: str | None
    firmware_version: str | None
    topology: str | None
    description: str
    reproduction_steps: str | None
    issue_time: str | None
    status: str
    severity: str
    created_at: datetime
    updated_at: datetime


class ArtifactOut(ORMModel):
    id: str
    case_id: str | None
    kind: str
    original_name: str
    sha256: str
    size_bytes: int
    status: str
    metadata_json: str
    created_at: datetime


class LogEventOut(ORMModel):
    id: str
    source_file: str
    line_start: int
    line_end: int
    timestamp_raw: str | None
    timestamp_normalized: str | None
    level: str
    module: str
    component: str
    event_code: str
    message: str
    raw_text: str
    entities_json: str
    confidence: float


class KnowledgeCreate(BaseModel):
    title: str
    source_type: str = "document"
    device_type: str | None = None
    device_model: str | None = None
    firmware_range: str | None = None
    module: str | None = None
    trust_level: str = "MEDIUM"
    confidentiality: str = "INTERNAL"
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    category_id: str | None = None


class KnowledgeUpdate(BaseModel):
    title: str | None = None
    source_type: str | None = None
    device_type: str | None = None
    device_model: str | None = None
    firmware_range: str | None = None
    module: str | None = None
    trust_level: str | None = None
    confidentiality: str | None = None
    content: str | None = None
    metadata: dict[str, Any] | None = None
    category_id: str | None = None
    active: bool | None = None


class KnowledgeOut(ORMModel):
    id: str
    title: str
    source_type: str
    device_type: str | None
    device_model: str | None
    firmware_range: str | None
    module: str | None
    trust_level: str
    confidentiality: str
    active: bool
    category_id: str | None = None
    category_name: str | None = None
    chunk_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KnowledgeDetailOut(KnowledgeOut):
    content: str


class KnowledgeCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: str | None = None
    description: str = ""
    sort_order: int = 0


class KnowledgeCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    parent_id: str | None = None
    description: str | None = None
    sort_order: int | None = None
    active: bool | None = None


class KnowledgeCategoryOut(ORMModel):
    id: str
    name: str
    code: str
    parent_id: str | None
    description: str
    sort_order: int
    system: bool
    active: bool
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class ModelProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    task_type: Literal["chat", "embedding", "reranker"]
    mode: Literal["builtin", "local", "api"]
    provider: str
    model_name: str = ""
    base_url: str | None = None
    api_key: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ModelProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    mode: Literal["builtin", "local", "api"] | None = None
    provider: str | None = None
    model_name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    clear_api_key: bool = False
    config: dict[str, Any] | None = None
    enabled: bool | None = None


class ModelProfileOut(ORMModel):
    id: str
    name: str
    task_type: str
    mode: str
    provider: str
    model_name: str
    base_url: str | None
    api_key_configured: bool = False
    api_key_hint: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AnalysisOut(ORMModel):
    id: str
    case_id: str
    status: str
    provider: str
    model: str
    prompt_version: str
    result_json: str
    evidence_json: str
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class JobOut(ORMModel):
    id: str
    kind: str
    status: str
    progress: int
    message: str
    result_json: str
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=10000)


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class StaticAnalysisRequest(BaseModel):
    tools: list[Literal["cppcheck", "clang-tidy"]] = Field(default_factory=lambda: ["cppcheck"])


class PatchRequest(BaseModel):
    symbol_id: str
    instruction: str = "根据当前故障证据生成最小、安全、可审查的候选补丁"
