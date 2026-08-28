from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.fault_tree_coverage import FaultTreeCoverageItem


FaultTreeStatus = Literal[
    "PENDING",
    "SUPPORTED",
    "EXCLUDED",
    "INSUFFICIENT_EVIDENCE",
]
TERMINAL_FAULT_TREE_STATUSES = frozenset({
    "SUPPORTED",
    "EXCLUDED",
    "INSUFFICIENT_EVIDENCE",
})


class FaultTreeAssessment(BaseModel):
    model_config = ConfigDict(extra="ignore")

    item_id: Annotated[str, Field(min_length=1, max_length=128)]
    method_document_id: Annotated[str, Field(min_length=1, max_length=128)]
    status: FaultTreeStatus = "PENDING"
    rationale: Annotated[str, Field(min_length=1, max_length=4000)]
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    next_action: str = Field(default="", max_length=4000)


def _text(value: Any, *keys: str) -> str:
    if isinstance(value, str):
        return value.strip()
    if not isinstance(value, dict):
        return ""
    for key in keys:
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return ""


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        rendered = _text(item, "id", "evidence_id", "item_id", "value")
        if rendered:
            result.append(rendered)
    return list(dict.fromkeys(result))


def normalize_item_ids(item: dict[str, Any], arguments: dict[str, Any] | None = None) -> list[str]:
    arguments = arguments or {}
    ids = _strings(
        item.get("fault_tree_item_ids")
        or item.get("coverage_item_ids")
        or item.get("fault_tree_items")
        or arguments.get("fault_tree_item_ids")
        or []
    )
    single = _text(item, "fault_tree_item_id", "coverage_item_id", "item_id")
    if single:
        ids.append(single)
    return list(dict.fromkeys(ids))


def normalize_fault_tree_assessments(value: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        item_id = _text(item, "item_id", "fault_tree_item_id", "coverage_item_id", "id")
        method_id = _text(
            item, "method_document_id", "document_id", "source_document_id",
        )
        rationale = _text(item, "rationale", "reason", "conclusion", "analysis")
        raw_status = _text(item, "status", "result", "conclusion_status").casefold()
        status = {
            "supported": "SUPPORTED",
            "support": "SUPPORTED",
            "confirmed": "SUPPORTED",
            "支持": "SUPPORTED",
            "确认": "SUPPORTED",
            "excluded": "EXCLUDED",
            "exclude": "EXCLUDED",
            "ruled_out": "EXCLUDED",
            "排除": "EXCLUDED",
            "insufficient_evidence": "INSUFFICIENT_EVIDENCE",
            "insufficient": "INSUFFICIENT_EVIDENCE",
            "unknown": "INSUFFICIENT_EVIDENCE",
            "证据不足": "INSUFFICIENT_EVIDENCE",
            "pending": "PENDING",
            "待确认": "PENDING",
        }.get(raw_status, str(item.get("status") or "PENDING").strip().upper())
        if item_id and method_id and rationale:
            normalized.append({
                "item_id": item_id,
                "method_document_id": method_id,
                "status": status,
                "rationale": rationale,
                "evidence_ids": _strings(
                    item.get("evidence_ids") or item.get("supporting_evidence") or []
                ),
                "next_action": _text(
                    item, "next_action", "recommended_action", "evidence_needed",
                ),
            })
    return normalized


def normalize_payload_fault_tree_assessments(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    return normalize_fault_tree_assessments(
        payload.get("fault_tree_assessments")
        or payload.get("coverage_assessments")
        or []
    )


def validate_fault_tree_bindings(
    planning_round: Any,
    *,
    items: list[FaultTreeCoverageItem],
    unattempted_item_ids: set[str] | None = None,
    valid_evidence_ids: set[str] | None = None,
) -> None:
    if not items:
        return
    item_by_id = {item.id: item for item in items}
    known_ids = set(item_by_id)
    assessments = list(planning_round.fault_tree_assessments)
    assessed_ids = [assessment.item_id for assessment in assessments]
    if len(assessed_ids) != len(set(assessed_ids)):
        raise ValueError("Fault-tree coverage contains duplicate item assessments")
    referenced_ids = set(assessed_ids)
    for check in planning_round.checks:
        referenced_ids.update(check.fault_tree_item_ids)
    for query in planning_round.search_queries:
        referenced_ids.update(query.fault_tree_item_ids)
    for tool_call in planning_round.tool_calls:
        referenced_ids.update(tool_call.fault_tree_item_ids)
    if referenced_ids.difference(known_ids):
        raise ValueError("Model referenced unknown fault-tree coverage items")
    for assessment in assessments:
        expected_method_id = item_by_id[assessment.item_id].method_document_id
        if assessment.method_document_id != expected_method_id:
            raise ValueError("Fault-tree item assessment is bound to the wrong method document")
        if (
            assessment.status in {"SUPPORTED", "EXCLUDED"}
            and not assessment.evidence_ids
        ):
            raise ValueError("Supported or excluded fault-tree conclusions require evidence IDs")
        if valid_evidence_ids is not None and set(assessment.evidence_ids).difference(valid_evidence_ids):
            raise ValueError("Fault-tree conclusion cited unknown evidence IDs")
    unattempted = set(unattempted_item_ids or ())
    if not unattempted:
        return
    evidence_targeted: set[str] = set()
    checked: set[str] = set()
    for check in planning_round.checks:
        checked.update(check.fault_tree_item_ids)
    for query in planning_round.search_queries:
        evidence_targeted.update(query.fault_tree_item_ids)
    for call in planning_round.tool_calls:
        if call.tool_name in {"search_knowledge", "search_log", "get_evidence"}:
            evidence_targeted.update(call.fault_tree_item_ids)
        if (
            call.tool_name == "search_log"
            and call.fault_tree_item_ids
            and not call.arguments.get("keywords")
            and not call.arguments.get("pattern_ids")
        ):
            raise ValueError(
                "Fault-tree log searches require at least one keyword or compiled pattern ID"
            )
    newly_targeted = unattempted & evidence_targeted
    if not newly_targeted:
        raise ValueError(
            "Planning round did not bind an unattempted fault-tree item to both a check and a read-only evidence tool"
        )
    if not newly_targeted.issubset(checked):
        raise ValueError(
            "Every newly targeted fault-tree item must also have an explicit executable check"
        )
    prematurely_concluded = {
        assessment.item_id
        for assessment in assessments
        if assessment.item_id in unattempted
        and assessment.status in TERMINAL_FAULT_TREE_STATUSES
    }.difference(newly_targeted)
    if prematurely_concluded:
        raise ValueError(
            "Unattempted fault-tree items cannot receive terminal conclusions before an evidence tool is bound"
        )


def initial_fault_tree_coverage(
    items: list[FaultTreeCoverageItem],
) -> dict[str, dict[str, Any]]:
    return {
        item.id: {
            **item.public_snapshot(),
            "status": "PENDING",
            "rationale": "尚未完成该故障树节点的证据核验。",
            "evidence_ids": [],
            "next_action": "由规划器绑定检查并调用只读证据工具。",
            "attempted": False,
            "last_round": 0,
        }
        for item in items
    }


def coverage_snapshot(
    coverage: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    items = list(coverage.values())
    attempted = sum(bool(item.get("attempted")) for item in items)
    concluded = sum(
        str(item.get("status")) in TERMINAL_FAULT_TREE_STATUSES for item in items
    )
    complete = bool(items) and attempted == len(items) and concluded == len(items)
    return {
        "total": len(items),
        "attempted": attempted,
        "concluded": concluded,
        "complete": complete,
        "status_counts": {
            status: sum(str(item.get("status")) == status for item in items)
            for status in ("PENDING", "SUPPORTED", "EXCLUDED", "INSUFFICIENT_EVIDENCE")
        },
        "items": items,
    }
