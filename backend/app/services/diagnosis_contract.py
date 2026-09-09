from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.services.report_contract import REFERENCE, report_references, structured_template, validate_report_markdown


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
    report_markdown: str = Field(default="", max_length=100000)
    suggested_problem_category: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")] | None = None
    category_reason: str | None = Field(default=None, min_length=1, max_length=4000)

    @model_validator(mode="after")
    def category_suggestion(self):
        if bool(self.suggested_problem_category) != bool(self.category_reason):
            raise ValueError("Suggested problem category and category reason must be supplied together")
        if self.suggested_problem_category == "unknown":
            raise ValueError("Leave an unsupported category suggestion empty")
        return self

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
    *,
    template_snapshot: dict[str, Any] | None = None,
    case_evidence_ids: set[str] | None = None,
    report_template: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate before persistence using the fixed template and case-only receipts.

    Callers resolve_configuration first and pass config['report_template'] as
    report_template (template_snapshot is an alias). Omission preserves legacy
    free-form report structure.
    Knowledge/method IDs may be in valid_evidence_ids but never case_evidence_ids.
    """
    if report_template is not None:
        if template_snapshot is not None and template_snapshot != report_template:
            raise ValueError("Conflicting report template snapshots")
        template_snapshot = report_template
    parsed = LLMDiagnosis.model_validate(payload)
    if parsed.report_markdown and structured_template(template_snapshot) and case_evidence_ids is None:
        raise ValueError("Templated reports require the explicit case_evidence_ids allowlist")
    report_ids = valid_evidence_ids if case_evidence_ids is None else case_evidence_ids & valid_evidence_ids
    validate_report_markdown(parsed.report_markdown, report_ids, template_snapshot)
    if parsed.category_reason:
        references = report_references(parsed.category_reason)
        if not references or set(REFERENCE.findall(parsed.category_reason)) - report_ids:
            raise ValueError("Category suggestion requires valid case evidence references")
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
