"""Transport contracts kept independent from the legacy knowledge schemas."""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ContributionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["CREATE", "UPDATE", "DELETE"] = "CREATE"
    content_kind: Literal["KNOWLEDGE", "SKILL"] = "KNOWLEDGE"
    target_document_id: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, min_length=1, max_length=512)
    content: str | None = Field(default=None, max_length=500_000)
    source_type: str | None = Field(default=None, max_length=64)
    category_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)
    source_curation_id: str | None = Field(default=None, max_length=40)


class ContributionVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)


class ContributionUpdate(ContributionVersion):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    content: str | None = Field(default=None, max_length=500_000)
    category_id: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] | None = None
    confidentiality: Literal["PUBLIC", "INTERNAL", "RESTRICTED"] | None = None
    comment: str = Field(default="", max_length=4000)


class ContributionReview(ContributionVersion):
    expected_content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    action: Literal["APPROVE", "REJECT", "RETURN"]
    comment: str = Field(default="", max_length=4000)


class ContributionChat(ContributionVersion):
    instruction: str = Field(min_length=1, max_length=16_000)
    model_profile_id: str | None = Field(default=None, max_length=40)
    consent_model_egress: bool = True


class ContributionRefinement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assistant_message: str = Field(min_length=1, max_length=20_000)
    title: str = Field(min_length=1, max_length=512)
    revised_markdown: str = Field(min_length=1, max_length=500_000)
    change_summary: str = Field(default="AI review correction", max_length=2000)
