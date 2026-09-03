from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


class KeywordProposal(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keyword: Annotated[str, Field(min_length=2, max_length=256)]
    reason: Annotated[str, Field(min_length=1, max_length=1000)]
    relevance: float = Field(default=0.8, ge=0.0, le=1.0)


class LogTriagePlan(BaseModel):
    model_config = ConfigDict(extra="ignore")

    read_document_ids: list[str] = Field(default_factory=list, max_length=5000)
    selected_pattern_ids: list[str] = Field(default_factory=list, max_length=5000)
    additional_keywords: list[KeywordProposal] = Field(
        default_factory=list,
        max_length=500,
    )
    hypotheses: list[str] = Field(default_factory=list, max_length=100)
    screening_steps: list[str] = Field(default_factory=list, max_length=200)
    missing_information: list[str] = Field(default_factory=list, max_length=100)
    stop_conditions: list[str] = Field(default_factory=list, max_length=100)
    rationale: str = Field(default="", max_length=20_000)


def normalize_log_triage_plan(raw: Any) -> dict[str, Any]:
    """Accept common JSON-mode variations without weakening evidence gates."""
    if not isinstance(raw, dict):
        return raw
    # Some OpenAI-compatible models interpret ``schema_name`` as a requested
    # root property. Unwrap that presentation-only envelope while keeping the
    # same Pydantic and evidence-ID validation in the planner.
    wrapped = raw.get("log_triage_plan")
    if len(raw) == 1 and isinstance(wrapped, dict):
        raw = wrapped
    normalized = dict(raw)
    rationale = normalized.get("rationale")
    legacy_plan = normalized.get("plan")
    if not isinstance(rationale, str) and isinstance(legacy_plan, str):
        normalized["rationale"] = legacy_plan
    if not normalized.get("screening_steps") and isinstance(legacy_plan, str):
        normalized["screening_steps"] = [legacy_plan]

    proposals: list[Any] = []
    for item in normalized.get("additional_keywords") or []:
        if isinstance(item, str):
            keyword = item.strip()
            if keyword:
                proposals.append({
                    "keyword": keyword,
                    "reason": "模型根据案例现象与方法文档补充的字面量关键词",
                    "relevance": 0.8,
                })
        else:
            proposals.append(item)
    normalized["additional_keywords"] = proposals
    return normalized
