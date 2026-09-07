from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.diagnosis_contract import LLMDiagnosis
from app.services.diagnostic_planning_contract import PlanningRound

class _StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DebugStatusInput(_StrictInput):
    pass


class DebugListCasesInput(_StrictInput):
    query: str = Field(default="", max_length=255)
    limit: int = Field(default=20, ge=1, le=100)


class DebugCaseInput(_StrictInput):
    case_id: str = Field(min_length=1, max_length=40)


class DebugBeginHostDiagnosisInput(DebugCaseInput):
    executor: Literal["claude_code", "codex", "opencode", "other_cli"]
    client_model_claim: str = Field(default="unknown", min_length=1, max_length=512)
    skill_version: str = Field(default="gw-ap-debug@1.0.0", min_length=1, max_length=128)
    ttl_seconds: int = Field(default=14_400, ge=300, le=604_800)


class DebugHostRunInput(_StrictInput):
    session_id: str = Field(min_length=1, max_length=40)
    include_evidence: bool = False
    include_planning_payloads: bool = False


class _VersionedHostRunInput(_StrictInput):
    session_id: str = Field(min_length=1, max_length=40)
    expected_version: int = Field(ge=1)


class DebugListDiagnosticDocumentsInput(_VersionedHostRunInput):
    roles: list[str] = Field(default_factory=list, max_length=20)


class DebugReadDiagnosticDocumentsInput(_VersionedHostRunInput):
    document_ids: list[str] = Field(min_length=1, max_length=5_000)


class DebugSearchKnowledgeInput(_VersionedHostRunInput):
    query: str = Field(min_length=2, max_length=4_000)
    method_document_ids: list[str] = Field(default_factory=list, max_length=5_000)
    top_k: int = Field(default=10, ge=1, le=20)


class DebugSearchLogInput(_VersionedHostRunInput):
    keywords: list[str] = Field(default_factory=list, max_length=30)
    pattern_ids: list[str] = Field(default_factory=list, max_length=60)
    artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    method_document_ids: list[str] = Field(default_factory=list, max_length=5_000)
    top_k: int = Field(default=20, ge=1, le=100)


class DebugGetEvidenceInput(_VersionedHostRunInput):
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class DebugPlanningRoundInput(_VersionedHostRunInput):
    round_number: int = Field(ge=1, le=20)
    planning: PlanningRound


class DebugFinalizeDiagnosisInput(_VersionedHostRunInput):
    diagnosis: LLMDiagnosis


class DebugCancelHostRunInput(_VersionedHostRunInput):
    reason: str = Field(min_length=1, max_length=2_000)


class DebugGenerateReportInput(DebugCaseInput):
    analysis_id: str | None = Field(default=None, min_length=1, max_length=40)
    format: Literal["html", "pdf", "docx"] = "html"


class DebugOpenUIInput(_StrictInput):
    case_id: str | None = Field(default=None, min_length=1, max_length=40)
    analysis_id: str | None = Field(default=None, min_length=1, max_length=40)


class DebugKnowledgeRoutingContextInput(_StrictInput):
    document_ids: list[str] = Field(min_length=1, max_length=20)
    consent_host_model_data: Literal[True]


class DebugKnowledgeSectionsInput(_StrictInput):
    document_id: str = Field(min_length=1, max_length=40)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    offset: int = Field(default=0, ge=0, le=512)
    limit: int = Field(default=2, ge=1, le=2)
    consent_host_model_data: Literal[True]


class DebugKnowledgeRoutingDecision(_StrictInput):
    covered_section_ids: list[str] = Field(default_factory=list, max_length=512)
    document_id: str = Field(min_length=1, max_length=40)
    expected_lock_version: int = Field(ge=1)
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    category_id: str = Field(min_length=1, max_length=40)
    device_type: Literal["GW", "AP", "GENERAL", "OTHER"] | None = None
    module: str | None = Field(default=None, max_length=64)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=1_000)


class DebugApplyKnowledgeRoutingInput(_StrictInput):
    decisions: list[DebugKnowledgeRoutingDecision] = Field(min_length=1, max_length=20)
    client_model_claim: str = Field(default="unknown", min_length=1, max_length=512)
    confirm_draft_update: Literal[True]
