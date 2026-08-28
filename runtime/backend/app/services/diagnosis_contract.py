from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field


_EvidenceId = Annotated[str, Field(min_length=1, max_length=128)]


class LLMFact(BaseModel):
    model_config = ConfigDict(extra="ignore")

    statement: Annotated[str, Field(min_length=1, max_length=4_000)]
    evidence_ids: Annotated[list[_EvidenceId], Field(min_length=1, max_length=30)]


class LLMHypothesis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    rank: int = Field(default=0, ge=0, le=100)
    title: Annotated[str, Field(min_length=1, max_length=1_000)]
    description: Annotated[str, Field(min_length=1, max_length=8_000)]
    supporting_evidence: Annotated[list[_EvidenceId], Field(min_length=1, max_length=50)]
    contradicting_evidence: list[_EvidenceId] = Field(default_factory=list, max_length=50)
    confidence_score: float = Field(ge=0.0, le=1.0)
    confidence_level: Literal["LOW", "MEDIUM", "HIGH"]
    priority: Annotated[str, Field(pattern=r"^(P[0-3]|UNKNOWN)$")]
    needs_human_review: bool = True
    event_code: str | None = None


class LLMAction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    priority: Annotated[str, Field(pattern=r"^(P[0-3]|UNKNOWN)$")]
    action: Annotated[str, Field(min_length=1, max_length=4_000)]
    reason: Annotated[str, Field(min_length=1, max_length=4_000)]
    expected_result: Annotated[str, Field(min_length=1, max_length=4_000)]


class LLMFaultTreeConclusion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: _EvidenceId
    method_document_id: _EvidenceId
    status: Literal["SUPPORTED", "EXCLUDED", "INSUFFICIENT_EVIDENCE"]
    conclusion: Annotated[str, Field(min_length=1, max_length=4_000)]
    evidence_ids: list[_EvidenceId] = Field(default_factory=list, max_length=100)
    next_action: str = Field(default="", max_length=4_000)


class LLMDiagnosis(BaseModel):
    model_config = ConfigDict(extra="ignore")

    summary: Annotated[str, Field(min_length=1, max_length=8_000)]
    confirmed_facts: Annotated[list[LLMFact], Field(max_length=100)]
    hypotheses: Annotated[list[LLMHypothesis], Field(min_length=1, max_length=50)]
    recommended_actions: Annotated[list[LLMAction], Field(max_length=100)]
    missing_information: Annotated[list[str], Field(max_length=100)]
    suspected_modules: Annotated[list[str], Field(max_length=100)]
    limitations: Annotated[list[str], Field(max_length=100)]
    fault_tree_conclusions: list[LLMFaultTreeConclusion] = Field(
        default_factory=list, max_length=500,
    )


def validate_llm_diagnosis(
    payload: Any,
    valid_evidence_ids: set[str],
    required_fault_tree_items: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    parsed = LLMDiagnosis.model_validate(payload)
    referenced_ids: set[str] = set()
    for fact in parsed.confirmed_facts:
        referenced_ids.update(fact.evidence_ids)
    for hypothesis in parsed.hypotheses:
        referenced_ids.update(hypothesis.supporting_evidence)
        referenced_ids.update(hypothesis.contradicting_evidence)
    expected_items = required_fault_tree_items or {}
    conclusions = {item.item_id: item for item in parsed.fault_tree_conclusions}
    if len(conclusions) != len(parsed.fault_tree_conclusions):
        raise ValueError("Model returned duplicate fault-tree conclusions")
    if required_fault_tree_items is not None and set(conclusions) != set(expected_items):
        raise ValueError("Model did not conclude every required fault-tree item")
    for item_id, conclusion in conclusions.items():
        expected = expected_items.get(item_id)
        if expected is not None:
            if conclusion.method_document_id != expected.get("method_document_id"):
                raise ValueError("Model bound a fault-tree conclusion to the wrong method")
            if conclusion.status != expected.get("status"):
                raise ValueError("Model changed the evidence-gated fault-tree status")
        if conclusion.status in {"SUPPORTED", "EXCLUDED"} and not conclusion.evidence_ids:
            raise ValueError("Supported or excluded fault-tree conclusions require evidence")
        referenced_ids.update(conclusion.evidence_ids)
    unknown_ids = sorted(referenced_ids - valid_evidence_ids)
    if unknown_ids:
        preview = ", ".join(unknown_ids[:5])
        raise ValueError(f"Model cited unknown evidence IDs: {preview}")
    output = parsed.model_dump()
    output["hypotheses"].sort(key=lambda item: item["confidence_score"], reverse=True)
    for rank, hypothesis in enumerate(output["hypotheses"], start=1):
        hypothesis["rank"] = rank
        score = hypothesis["confidence_score"]
        hypothesis["confidence_level"] = (
            "HIGH" if score >= 0.78 else "MEDIUM" if score >= 0.5 else "LOW"
        )
    return output
