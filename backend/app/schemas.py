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
    owner_id: str | None
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
    expected_lock_version: int = Field(ge=1)


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
    review_status: str
    version: int
    lock_version: int
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_comment: str | None
    published_at: datetime | None
    category_id: str | None = None
    category_name: str | None = None
    chunk_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class KnowledgeDetailOut(KnowledgeOut):
    content: str


class KnowledgeReviewAction(BaseModel):
    expected_lock_version: int = Field(ge=1)
    comment: str | None = Field(default=None, max_length=4000)


class KnowledgeRollbackRequest(BaseModel):
    expected_lock_version: int = Field(ge=1)
    change_summary: str = Field(default="", max_length=512)


class KnowledgeCurationChatRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=20_000)
    expected_draft_version: int = Field(ge=1)


class KnowledgeCurationDraftUpdate(BaseModel):
    markdown: str = Field(min_length=20, max_length=500_000)
    title: str | None = Field(default=None, max_length=512)
    change_summary: str = Field(default="手工修订案例草稿", max_length=512)
    expected_draft_version: int = Field(ge=1)


class KnowledgeCurationConfirmRequest(BaseModel):
    expected_draft_version: int = Field(ge=1)


class KnowledgeCurationRetryRequest(BaseModel):
    model_profile_id: str | None = Field(default=None, max_length=40)
    consent_model_egress: bool = False


class KnowledgeCurationRestoreRequest(BaseModel):
    expected_draft_version: int = Field(ge=1)


class KnowledgeRevisionOut(BaseModel):
    id: str
    document_id: str
    version: int
    content_hash: str
    change_summary: str
    created_by: str | None
    created_at: datetime
    snapshot: dict[str, Any]


class DiagnosisFeedbackCreate(BaseModel):
    analysis_run_id: str
    verdict: Literal["CORRECT", "PARTIAL", "INCORRECT"]
    root_cause_correct: bool | None = None
    evidence_correct: bool | None = None
    comment: str = Field(default="", max_length=20000)
    corrections: dict[str, Any] = Field(default_factory=dict)


class DiagnosisFeedbackReview(BaseModel):
    action: Literal["APPROVE", "REJECT"]
    comment: str | None = Field(default=None, max_length=4000)


class DomainGraphSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=10000)
    top_k: int = Field(default=12, ge=1, le=50)
    max_hops: int = Field(default=2, ge=0, le=3)


class EvaluationDatasetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    description: str = Field(default="", max_length=20000)


class EvaluationDatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    description: str | None = Field(default=None, max_length=20000)
    active: bool | None = None


class EvaluationCaseCreate(BaseModel):
    case_id: str
    query: str = Field(min_length=2, max_length=10000)
    expected_evidence_ids: list[str] = Field(default_factory=list, max_length=200)
    expected_root_causes: list[str] = Field(default_factory=list, max_length=50)
    modules: list[
        Literal["knowledge", "domain_graph", "code", "commit", "memory"]
    ] = Field(
        default_factory=lambda: ["knowledge", "domain_graph"],
        min_length=1,
    )
    top_k: int = Field(default=10, ge=1, le=50)
    max_hops: int = Field(default=2, ge=0, le=3)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvaluationCaseUpdate(BaseModel):
    case_id: str | None = None
    query: str | None = Field(default=None, min_length=2, max_length=10000)
    expected_evidence_ids: list[str] | None = Field(default=None, max_length=200)
    expected_root_causes: list[str] | None = Field(default=None, max_length=50)
    modules: list[
        Literal["knowledge", "domain_graph", "code", "commit", "memory"]
    ] | None = Field(default=None, min_length=1)
    top_k: int | None = Field(default=None, ge=1, le=50)
    max_hops: int | None = Field(default=None, ge=0, le=3)
    metadata: dict[str, Any] | None = None


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
    model_profile_id: str | None
    model_config_json: str
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
    idempotency_key: str | None
    attempt: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    deadline_at: datetime | None
    timeout_seconds: int
    resource_limits_json: str
    dead_letter_at: datetime | None
    dead_letter_reason: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class RepositoryImportOut(BaseModel):
    repository_id: str
    artifact_id: str
    job: JobOut


class KnowledgeImportOut(BaseModel):
    document_id: str
    artifact_id: str
    job: JobOut


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=10000)


class ChatResponse(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class AgenticSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=10000)
    top_k: int = Field(default=12, ge=1, le=50)
    max_hops: int = Field(default=2, ge=0, le=3)
    modules: list[
        Literal["knowledge", "domain_graph", "code", "commit", "memory"]
    ] | None = None


class StaticAnalysisRequest(BaseModel):
    tools: list[Literal["cppcheck", "clang-tidy"]] = Field(default_factory=lambda: ["cppcheck"])


class PatchRequest(BaseModel):
    symbol_id: str
    instruction: str = "根据当前故障证据生成最小、安全、可审查的候选补丁"


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{2,128}$")
    display_name: str = Field(min_length=1, max_length=255)
    role: Literal["ADMIN", "ENGINEER", "VIEWER"] = "VIEWER"
    issue_token: bool = True
    token_name: str = Field(default="initial", min_length=1, max_length=255)
    token_expires_days: int | None = Field(default=90, ge=1, le=3650)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=255)
    role: Literal["ADMIN", "ENGINEER", "VIEWER"] | None = None
    active: bool | None = None


class AccessTokenCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=255)
    expires_days: int | None = Field(default=90, ge=1, le=3650)


class CaseMemberUpdate(BaseModel):
    permission: Literal["EDITOR", "VIEWER"] = "VIEWER"
