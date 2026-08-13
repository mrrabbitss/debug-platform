from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import Case
from app.services.diagnostic_methods import DiagnosticMethodDocument


MAX_QUERIES_PER_ROUND = 4
_CASE_TERM_STOPWORDS = frozenset({
    "ap", "gw", "问题", "故障", "设备", "当前", "出现", "情况", "频繁",
})


class PlannedCheck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    check_id: Annotated[str, Field(min_length=1, max_length=128)]
    method_document_id: Annotated[str, Field(min_length=1, max_length=128)]
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    evidence_needed: Annotated[str, Field(min_length=1, max_length=2000)]
    completion_rule: Annotated[str, Field(min_length=1, max_length=2000)]


class MethodAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    method_document_id: Annotated[str, Field(min_length=1, max_length=128)]
    relevance: Literal["RELEVANT", "POSSIBLY_RELEVANT", "NOT_RELEVANT"]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    matched_signals: list[str] = Field(default_factory=list, max_length=100)


class PlannedSearch(BaseModel):
    model_config = ConfigDict(extra="ignore")

    query_id: Annotated[str, Field(min_length=1, max_length=128)]
    query: Annotated[str, Field(min_length=1, max_length=2000)]
    method_document_ids: Annotated[list[str], Field(min_length=1, max_length=100)]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]
    expected_evidence: Annotated[str, Field(min_length=1, max_length=2000)]


class PlanningRound(BaseModel):
    model_config = ConfigDict(extra="ignore")

    read_document_ids: list[str] = Field(default_factory=list, max_length=5000)
    method_assessments: list[MethodAssessment] = Field(
        default_factory=list, max_length=5000,
    )
    hypotheses: list[str] = Field(default_factory=list, max_length=100)
    checks: list[PlannedCheck] = Field(default_factory=list, max_length=500)
    search_queries: list[PlannedSearch] = Field(
        default_factory=list, max_length=MAX_QUERIES_PER_ROUND,
    )
    evidence_gaps: list[str] = Field(default_factory=list, max_length=100)
    continue_analysis: bool = True
    stop_reason: str = Field(default="MORE_EVIDENCE_NEEDED", max_length=256)


def _text_value(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if not isinstance(value, dict):
        return ""
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _string_list(value: Any, *keys: str) -> list[str]:
    values = value if isinstance(value, list) else []
    result: list[str] = []
    for item in values:
        rendered = _text_value(item, *keys)
        if rendered:
            result.append(rendered)
    return result


def _normalize_relevance(value: Any) -> str:
    rendered = str(value or "").strip().casefold().replace("-", "_")
    if rendered in {"relevant", "high", "yes", "true", "相关", "高度相关"}:
        return "RELEVANT"
    if rendered in {
        "possibly_relevant", "possible", "medium", "partial", "maybe",
        "可能相关", "部分相关",
    }:
        return "POSSIBLY_RELEVANT"
    return "NOT_RELEVANT"


def normalize_planning_round(raw: Any) -> Any:
    """Accept common GLM JSON variations while preserving ID validation."""
    if not isinstance(raw, dict):
        return raw
    wrapped = raw.get("diagnostic_planning_round")
    if len(raw) == 1 and isinstance(wrapped, dict):
        raw = wrapped
    normalized = dict(raw)
    normalized["hypotheses"] = _string_list(
        normalized.get("hypotheses"), "description", "hypothesis", "title", "name",
    )

    assessments: list[dict[str, Any]] = []
    for item in normalized.get("method_assessments") or []:
        if not isinstance(item, dict):
            continue
        document_id = _text_value(
            item, "method_document_id", "document_id", "evidence_id", "id",
        )
        rationale = _text_value(item, "rationale", "reason", "analysis", "description")
        if document_id and rationale:
            assessments.append({
                "method_document_id": document_id,
                "relevance": _normalize_relevance(
                    item.get("relevance", item.get("applicable"))
                ),
                "rationale": rationale,
                "matched_signals": _string_list(
                    item.get("matched_signals") or item.get("signals") or [],
                    "signal", "text", "name",
                ),
            })
    normalized["method_assessments"] = assessments

    checks: list[dict[str, Any]] = []
    for position, item in enumerate(normalized.get("checks") or [], start=1):
        if isinstance(item, str):
            item = {"description": item}
        if not isinstance(item, dict):
            continue
        description = _text_value(
            item, "description", "check", "action", "step", "title",
        )
        if description:
            checks.append({
                "check_id": _text_value(item, "check_id", "id", "step_id")
                or f"check-{position}",
                "method_document_id": _text_value(
                    item, "method_document_id", "document_id", "source_document_id",
                ),
                "description": description,
                "evidence_needed": _text_value(
                    item, "evidence_needed", "expected_evidence", "required_evidence",
                    "evidence", "observation",
                ) or "支持或排除该检查的日志、知识或图谱证据",
                "completion_rule": _text_value(
                    item, "completion_rule", "success_criteria", "completion_criteria",
                    "decision_rule", "expected_result",
                ) or "记录支持、反证或明确的证据缺口",
            })
    normalized["checks"] = checks

    searches: list[dict[str, Any]] = []
    for position, item in enumerate(normalized.get("search_queries") or [], start=1):
        if isinstance(item, str):
            item = {"query": item}
        if not isinstance(item, dict):
            continue
        query = _text_value(item, "query", "search_query", "text", "keywords")
        document_ids = _string_list(
            item.get("method_document_ids")
            or item.get("document_ids")
            or item.get("source_document_ids")
            or [],
            "id", "document_id",
        )
        single_document_id = _text_value(
            item, "method_document_id", "document_id", "source_document_id",
        )
        if single_document_id:
            document_ids.append(single_document_id)
        if query:
            searches.append({
                "query_id": _text_value(item, "query_id", "id") or f"query-{position}",
                "query": query,
                "method_document_ids": list(dict.fromkeys(document_ids)),
                "rationale": _text_value(item, "rationale", "reason", "purpose")
                or "验证尚未确认的诊断假设",
                "expected_evidence": _text_value(
                    item, "expected_evidence", "evidence_needed", "expected_result",
                ) or "支持或排除该查询对应假设的证据",
            })
    normalized["search_queries"] = searches
    normalized["evidence_gaps"] = _string_list(
        normalized.get("evidence_gaps") or normalized.get("missing_information") or [],
        "description", "gap", "missing", "text",
    )
    return normalized


def _case_terms(case: Case) -> set[str]:
    issue = "\n".join(
        str(value or "") for value in (
            case.title, case.description, case.reproduction_steps, case.topology,
        )
    ).casefold()
    terms = set(re.findall(r"[a-z][a-z0-9_.:/-]{1,}|[\u4e00-\u9fff]{2,4}", issue))
    for run in re.findall(r"[\u4e00-\u9fff]+", issue):
        terms.update(run[index:index + 2] for index in range(max(0, len(run) - 1)))
    return {term for term in terms if term not in _CASE_TERM_STOPWORDS}


def symptom_relevant_method_ids(
    case: Case,
    methods: list[DiagnosticMethodDocument],
) -> set[str]:
    terms = _case_terms(case)
    if not terms:
        return set()
    return {
        method.id
        for method in methods
        if method.role == "FAULT_TREE"
        and any(term in f"{method.title}\n{method.content}".casefold() for term in terms)
    }


def validate_planning_round(
    parsed: PlanningRound,
    *,
    round_number: int,
    case: Case,
    methods: list[DiagnosticMethodDocument],
) -> None:
    expected = {method.id for method in methods}
    if set(parsed.read_document_ids) != expected:
        raise ValueError("Model did not attest reading every applicable method document")
    assessed = {item.method_document_id for item in parsed.method_assessments}
    if assessed != expected:
        raise ValueError("Model did not assess every applicable method document")
    unknown_method_ids = {
        check.method_document_id for check in parsed.checks
    }.union(
        document_id
        for query in parsed.search_queries
        for document_id in query.method_document_ids
    ).difference(expected)
    if unknown_method_ids:
        raise ValueError("Model planned checks or searches for unknown method documents")
    symptom_relevant = symptom_relevant_method_ids(case, methods)
    assessed_not_relevant = {
        item.method_document_id
        for item in parsed.method_assessments
        if item.relevance == "NOT_RELEVANT"
    }
    if symptom_relevant.intersection(assessed_not_relevant):
        raise ValueError("Model rejected a fault tree that overlaps the case symptom")
    relevant = {
        item.method_document_id
        for item in parsed.method_assessments
        if item.relevance != "NOT_RELEVANT"
    }
    relevant.update(symptom_relevant)
    if relevant.difference(check.method_document_id for check in parsed.checks):
        raise ValueError("Relevant methods do not have an executable check")
    searched_methods = {
        document_id
        for query in parsed.search_queries
        for document_id in query.method_document_ids
    }
    if round_number == 1 and relevant.difference(searched_methods):
        raise ValueError("Every relevant method must drive an initial planned search query")
