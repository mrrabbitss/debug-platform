from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


HostAgentSessionStatus = Literal[
    "CREATED",
    "METHODS_READ",
    "SEARCHING",
    "DRAFT_SUBMITTED",
    "VALIDATED",
    "REJECTED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
]

TERMINAL_HOST_AGENT_SESSION_STATUSES = frozenset({
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "EXPIRED",
})

Digest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-fA-F]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=128)]


class _StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HostAgentSessionCreate(_StrictContract):
    case_id: Annotated[str, Field(min_length=1, max_length=40)]
    executor: Annotated[str, Field(min_length=1, max_length=64)]
    client_model_claim: Annotated[str, Field(min_length=1, max_length=512)] = "unknown"
    prompt_version: Annotated[str, Field(min_length=1, max_length=128)]
    skill_version: Annotated[str, Field(min_length=1, max_length=128)]
    case_snapshot_hash: Digest
    parse_snapshot_hash: Digest
    method_snapshot_hash: Digest
    snapshot: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=14_400, ge=300, le=604_800)

    @field_validator(
        "case_snapshot_hash",
        "parse_snapshot_hash",
        "method_snapshot_hash",
    )
    @classmethod
    def normalize_digest(cls, value: str) -> str:
        return value.lower()


class HostAgentSessionStatusTransition(_StrictContract):
    expected_version: int = Field(ge=1)
    status: HostAgentSessionStatus
    reason: Annotated[str, Field(max_length=2000)] = ""
    lease_owner: Annotated[str | None, Field(max_length=160)] = None


class HostAgentSessionSnapshotUpdate(_StrictContract):
    expected_version: int = Field(ge=1)
    case_snapshot_hash: Digest
    parse_snapshot_hash: Digest
    method_snapshot_hash: Digest
    snapshot: dict[str, Any] = Field(default_factory=dict)
    lease_owner: Annotated[str | None, Field(max_length=160)] = None

    @field_validator(
        "case_snapshot_hash",
        "parse_snapshot_hash",
        "method_snapshot_hash",
    )
    @classmethod
    def normalize_digest(cls, value: str) -> str:
        return value.lower()


class HostAgentSessionCoverageUpdate(_StrictContract):
    expected_version: int = Field(ge=1)
    coverage_patch: dict[str, Any] = Field(default_factory=dict)
    allowed_evidence_ids: list[Identifier] = Field(default_factory=list, max_length=2000)
    lease_owner: Annotated[str | None, Field(max_length=160)] = None


class HostAgentPlanningRoundAppend(_StrictContract):
    expected_version: int = Field(ge=1)
    round_number: int = Field(ge=1, le=20)
    payload: dict[str, Any]
    summary: dict[str, Any] = Field(default_factory=dict)
    lease_owner: Annotated[str | None, Field(max_length=160)] = None


class HostAgentPlanningRound(_StrictContract):
    round_number: int = Field(ge=1, le=20)
    payload_hash: Digest
    payload: dict[str, Any]
    summary: dict[str, Any]
    recorded_at: datetime


class HostAgentEvidenceCacheUpdate(_StrictContract):
    expected_version: int = Field(ge=1)
    evidence: dict[Identifier, Any] = Field(default_factory=dict)
    lease_owner: Annotated[str | None, Field(max_length=160)] = None


class HostAgentToolReceiptInput(_StrictContract):
    expected_version: int = Field(ge=1)
    call_id: Identifier
    tool_name: Annotated[str, Field(min_length=1, max_length=128)]
    arguments_hash: Digest
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    evidence: dict[Identifier, Any] = Field(default_factory=dict)
    lease_owner: Annotated[str | None, Field(max_length=160)] = None

    @field_validator("arguments_hash")
    @classmethod
    def normalize_digest(cls, value: str) -> str:
        return value.lower()


class HostAgentToolReceipt(_StrictContract):
    call_id: Identifier
    tool_name: Annotated[str, Field(min_length=1, max_length=128)]
    arguments_hash: Digest
    evidence_ids: list[Identifier] = Field(default_factory=list, max_length=1000)
    recorded_at: datetime


class HostAgentSessionLeaseRequest(_StrictContract):
    expected_version: int = Field(ge=1)
    owner: Annotated[str, Field(min_length=1, max_length=160)]
    lease_seconds: int = Field(default=120, ge=15, le=3600)


class HostAgentSessionLeaseRelease(_StrictContract):
    expected_version: int = Field(ge=1)
    owner: Annotated[str, Field(min_length=1, max_length=160)]


class HostAgentSessionCancel(_StrictContract):
    expected_version: int = Field(ge=1)
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    lease_owner: Annotated[str | None, Field(max_length=160)] = None


class HostAgentSessionView(_StrictContract):
    id: str
    case_id: str
    agent_run_id: str
    executor: str
    client_model_claim: str
    client_model_claim_verified: bool = False
    prompt_version: str
    skill_version: str
    case_snapshot_hash: str
    parse_snapshot_hash: str
    method_snapshot_hash: str
    snapshot: dict[str, Any]
    coverage: dict[str, Any]
    planning_rounds: list[HostAgentPlanningRound]
    allowed_evidence_ids: list[str]
    tool_receipts: list[HostAgentToolReceipt]
    evidence_cache: dict[str, Any]
    status: HostAgentSessionStatus
    status_reason: str
    version: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    expires_at: datetime
    created_by: str | None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    completed_at: datetime | None
