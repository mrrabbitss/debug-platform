from __future__ import annotations

import hashlib
import re
from typing import Any

from app.models import Case
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    DiagnosticPattern,
)
from app.services.fault_tree_coverage import FaultTreeCoverageItem


_TERMINAL_WITH_EVIDENCE = frozenset({"SUPPORTED", "EXCLUDED"})
_GENERIC_HINTS = frozenset({
    "ap", "gw", "udm", "日志", "判断", "检查", "问题", "故障", "异常",
    "正常", "确认", "查看", "步骤", "场景", "设备", "当前", "需要",
})


def _unique_strings(values: Any, *, limit: int = 5000) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        rendered = str(value or "").strip()
        if rendered and rendered not in result:
            result.append(rendered)
        if len(result) >= limit:
            break
    return result


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _case_keywords(case: Case) -> list[str]:
    text = "\n".join(str(value or "") for value in (
        case.title, case.description, case.reproduction_steps, case.topology,
    ))
    candidates = re.findall(
        r"[A-Za-z_][A-Za-z0-9_.:/!-]{2,}|[\u4e00-\u9fff]{2,12}", text,
    )
    return list(dict.fromkeys(candidates))[:10]


def _coverage_keywords(
    items: list[FaultTreeCoverageItem],
    case: Case,
) -> list[str]:
    result: list[str] = []
    for item in items:
        for hint in item.evidence_hints:
            cleaned = str(hint or "").strip().strip("`[]")
            if (
                len(cleaned) >= 3
                and cleaned.casefold() not in _GENERIC_HINTS
                and cleaned not in result
            ):
                result.append(cleaned)
            if len(result) >= 30:
                return result
    for keyword in _case_keywords(case):
        if keyword not in result:
            result.append(keyword)
    return result[:30]


def _pattern_score(
    pattern: DiagnosticPattern,
    items: list[FaultTreeCoverageItem],
) -> tuple[int, int, str]:
    item_text = "\n".join(
        f"{item.label}\n{item.description}\n{' '.join(item.evidence_hints)}"
        for item in items
    ).casefold()
    pattern_text = pattern.text.casefold()
    score = 0
    if pattern_text and pattern_text in item_text:
        score += 20
    for token in re.findall(r"[a-z_][a-z0-9_.:/!-]{2,}", pattern_text):
        if token not in _GENERIC_HINTS and token in item_text:
            score += 2
    if pattern.heading and pattern.heading.casefold() in item_text:
        score += 3
    return (-score, pattern.line_start, pattern.id)


def _coverage_pattern_ids(
    patterns: list[DiagnosticPattern],
    method_ids: set[str],
    items: list[FaultTreeCoverageItem],
) -> list[str]:
    candidates = [
        pattern for pattern in patterns if pattern.document_id in method_ids
    ]
    candidates.sort(key=lambda pattern: _pattern_score(pattern, items))
    return [pattern.id for pattern in candidates[:60]]


def _repair_method_assessments(
    payload: dict[str, Any],
    methods: list[DiagnosticMethodDocument],
    symptom_relevant_ids: set[str],
    repairs: list[dict[str, Any]],
) -> set[str]:
    known = {method.id for method in methods}
    assessments: dict[str, dict[str, Any]] = {}
    for raw in payload.get("method_assessments") or []:
        if not isinstance(raw, dict):
            continue
        method_id = str(raw.get("method_document_id") or "").strip()
        if method_id in known:
            assessments[method_id] = dict(raw)
    missing = known.difference(assessments)
    for method_id in sorted(missing):
        assessments[method_id] = {
            "method_document_id": method_id,
            "relevance": "POSSIBLY_RELEVANT",
            "rationale": "模型遗漏逐文档判断；后端按保守策略保留并执行只读核验。",
            "matched_signals": [],
        }
    upgraded = 0
    for method_id in symptom_relevant_ids:
        assessment = assessments.get(method_id)
        if assessment and assessment.get("relevance") == "NOT_RELEVANT":
            assessment["relevance"] = "RELEVANT"
            assessment["rationale"] = (
                "该故障树与案例现象直接重叠；后端保守提升为相关并要求证据核验。"
            )
            upgraded += 1
    payload["method_assessments"] = [
        assessments[method.id] for method in methods if method.id in assessments
    ]
    if missing:
        repairs.append({"code": "MISSING_METHOD_ASSESSMENTS_ADDED", "count": len(missing)})
    if upgraded:
        repairs.append({"code": "SYMPTOM_METHOD_RELEVANCE_UPGRADED", "count": upgraded})
    return {
        method_id for method_id, assessment in assessments.items()
        if assessment.get("relevance") != "NOT_RELEVANT"
    }.union(symptom_relevant_ids)


def _repair_fault_tree_assessments(
    payload: dict[str, Any],
    items: list[FaultTreeCoverageItem],
    valid_evidence_ids: set[str],
    repairs: list[dict[str, Any]],
) -> None:
    item_by_id = {item.id: item for item in items}
    repaired: list[dict[str, Any]] = []
    downgraded = 0
    removed_unknown = 0
    for raw in payload.get("fault_tree_assessments") or []:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("item_id") or "").strip()
        item = item_by_id.get(item_id)
        if item is None:
            removed_unknown += 1
            continue
        current = dict(raw)
        current["method_document_id"] = item.method_document_id
        evidence_ids = [
            evidence_id
            for evidence_id in _unique_strings(current.get("evidence_ids"), limit=100)
            if evidence_id in valid_evidence_ids
        ]
        current["evidence_ids"] = evidence_ids
        status = str(current.get("status") or "PENDING").upper()
        if status in _TERMINAL_WITH_EVIDENCE and not evidence_ids:
            current["status"] = "INSUFFICIENT_EVIDENCE"
            current["rationale"] = (
                f"{str(current.get('rationale') or '').strip()} "
                "模型结论未绑定当前案例的有效证据，已安全降级为证据不足。"
            ).strip()
            current["next_action"] = str(current.get("next_action") or "").strip() or (
                "按该故障树节点的日志 Pattern 继续采集并核验 GW/AP 双侧证据。"
            )
            downgraded += 1
        repaired.append(current)
    payload["fault_tree_assessments"] = repaired
    if downgraded:
        repairs.append({"code": "UNSUPPORTED_CONCLUSIONS_DOWNGRADED", "count": downgraded})
    if removed_unknown:
        repairs.append({"code": "UNKNOWN_FAULT_TREE_ITEMS_REMOVED", "count": removed_unknown})


def _ensure_checks(
    payload: dict[str, Any],
    methods: list[DiagnosticMethodDocument],
    relevant_method_ids: set[str],
    unattempted_ids: set[str],
    items: list[FaultTreeCoverageItem],
    repairs: list[dict[str, Any]],
) -> None:
    known = {method.id for method in methods}
    checks = [
        dict(item) for item in payload.get("checks") or []
        if isinstance(item, dict) and item.get("method_document_id") in known
    ]
    covered_by_method: dict[str, set[str]] = {}
    for check in checks:
        method_id = str(check.get("method_document_id") or "")
        covered_by_method.setdefault(method_id, set()).update(
            _unique_strings(check.get("fault_tree_item_ids"), limit=100)
        )
    added = 0
    for method_id in sorted(relevant_method_ids):
        method_items = [
            item.id for item in items
            if item.method_document_id == method_id and item.id in unattempted_ids
        ]
        missing_ids = [
            item_id for item_id in method_items
            if item_id not in covered_by_method.get(method_id, set())
        ]
        needs_method_check = method_id not in covered_by_method
        chunks = _chunks(missing_ids, 100) or ([[]] if needs_method_check else [])
        for position, item_ids in enumerate(chunks, start=1):
            digest = hashlib.sha256(
                f"{method_id}:{position}:{','.join(item_ids)}".encode("utf-8")
            ).hexdigest()[:12]
            checks.append({
                "check_id": f"policy-check-{digest}",
                "method_document_id": method_id,
                "description": "按该方法的判断步骤核验当前案例 GW/AP 双侧日志证据。",
                "evidence_needed": "与方法 Pattern 对应且带文件名和行号的实际日志证据。",
                "completion_rule": "记录证据支持、明确反证，或证据不足及下一步采集动作。",
                "fault_tree_item_ids": item_ids,
            })
            added += 1
    payload["checks"] = checks[:500]
    if added:
        repairs.append({"code": "EXECUTABLE_CHECKS_ADDED", "count": added})


def _sanitize_existing_tool_calls(
    payload: dict[str, Any],
    methods: list[DiagnosticMethodDocument],
    patterns: list[DiagnosticPattern],
    valid_evidence_ids: set[str],
) -> list[dict[str, Any]]:
    known_methods = {method.id for method in methods}
    known_patterns = {pattern.id for pattern in patterns}
    result: list[dict[str, Any]] = []
    for raw in payload.get("tool_calls") or []:
        if not isinstance(raw, dict):
            continue
        call = dict(raw)
        name = str(call.get("tool_name") or "")
        arguments = dict(call.get("arguments") or {})
        method_ids = [
            item for item in _unique_strings(call.get("method_document_ids"))
            if item in known_methods
        ]
        if name in {"search_knowledge", "search_log"}:
            argument_methods = [
                item for item in _unique_strings(arguments.get("method_document_ids"))
                if item in known_methods
            ]
            method_ids = list(dict.fromkeys([*method_ids, *argument_methods]))
            arguments["method_document_ids"] = method_ids
        if name == "search_log":
            arguments["pattern_ids"] = [
                item for item in _unique_strings(arguments.get("pattern_ids"), limit=60)
                if item in known_patterns
            ]
            arguments["keywords"] = _unique_strings(
                arguments.get("keywords"), limit=30,
            )
            if not arguments["pattern_ids"] and not arguments["keywords"]:
                continue
        elif name == "get_evidence":
            arguments["evidence_ids"] = [
                item for item in _unique_strings(arguments.get("evidence_ids"), limit=100)
                if item in valid_evidence_ids
            ]
            if not arguments["evidence_ids"]:
                continue
        elif name == "read_diagnostic_documents":
            arguments["document_ids"] = [
                item for item in _unique_strings(arguments.get("document_ids"))
                if item in known_methods
            ]
            if not arguments["document_ids"]:
                continue
        call["arguments"] = arguments
        call["method_document_ids"] = method_ids
        result.append(call)
    return result


def _coverage_tool_calls(
    *,
    case: Case,
    methods: list[DiagnosticMethodDocument],
    patterns: list[DiagnosticPattern],
    items: list[FaultTreeCoverageItem],
    relevant_method_ids: set[str],
    unattempted_ids: set[str],
    round_number: int,
) -> list[dict[str, Any]]:
    target_items = [item for item in items if item.id in unattempted_ids]
    item_chunks = _chunks([item.id for item in target_items], 100)
    if not item_chunks:
        item_chunks = [[]]
    calls: list[dict[str, Any]] = []
    for position, item_ids in enumerate(item_chunks[:4], start=1):
        chunk_items = [item for item in target_items if item.id in set(item_ids)]
        method_ids = {
            item.method_document_id for item in chunk_items
        } or set(relevant_method_ids)
        if round_number == 1 and position == 1:
            method_ids.update(relevant_method_ids)
        pattern_ids = _coverage_pattern_ids(patterns, method_ids, chunk_items)
        keywords = _coverage_keywords(chunk_items, case)
        if not pattern_ids and not keywords:
            keywords = ["AP offline"]
        digest = hashlib.sha256(
            f"{round_number}:{position}:{','.join(item_ids)}".encode("utf-8")
        ).hexdigest()[:12]
        calls.append({
            "call_id": f"policy-log-scan-{digest}",
            "tool_name": "search_log",
            "arguments": {
                "keywords": keywords,
                "pattern_ids": pattern_ids,
                "method_document_ids": sorted(method_ids),
                "top_k": 100,
            },
            "method_document_ids": sorted(method_ids),
            "rationale": "后端补齐故障树节点与实际日志证据之间的只读检索绑定。",
            "fault_tree_item_ids": item_ids,
        })
    return calls


def repair_planning_payload(
    payload: dict[str, Any],
    *,
    round_number: int,
    case: Case,
    methods: list[DiagnosticMethodDocument],
    diagnostic_patterns: list[DiagnosticPattern],
    fault_tree_items: list[FaultTreeCoverageItem],
    symptom_relevant_method_ids: set[str],
    unattempted_fault_tree_item_ids: set[str],
    valid_evidence_ids: set[str],
    valid_evidence_locator_ids: set[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Repair only safe, deterministic shape defects in an untrusted model plan.

    Repairs never manufacture evidence or upgrade certainty. Unsupported terminal
    conclusions are downgraded, while missing checks/search bindings are filled by
    auditable read-only policy calls derived from the compiled method documents.
    """

    repaired = dict(payload)
    repairs: list[dict[str, Any]] = []
    relevant = _repair_method_assessments(
        repaired, methods, symptom_relevant_method_ids, repairs,
    )
    _repair_fault_tree_assessments(
        repaired, fault_tree_items, valid_evidence_ids, repairs,
    )
    _ensure_checks(
        repaired,
        methods,
        relevant,
        unattempted_fault_tree_item_ids,
        fault_tree_items,
        repairs,
    )

    existing_calls = _sanitize_existing_tool_calls(
        repaired,
        methods,
        diagnostic_patterns,
        valid_evidence_locator_ids or valid_evidence_ids,
    )
    searched_methods = {
        method_id
        for call in existing_calls
        if call.get("tool_name") in {"search_knowledge", "search_log"}
        for method_id in call.get("method_document_ids", [])
    }
    targeted_items = {
        item_id
        for call in existing_calls
        if call.get("tool_name") in {"search_knowledge", "search_log", "get_evidence"}
        for item_id in call.get("fault_tree_item_ids", [])
    }
    needs_method_search = round_number == 1 and bool(relevant.difference(searched_methods))
    needs_item_search = bool(
        unattempted_fault_tree_item_ids.difference(targeted_items)
    )
    if needs_method_search or needs_item_search:
        policy_calls = _coverage_tool_calls(
            case=case,
            methods=methods,
            patterns=diagnostic_patterns,
            items=fault_tree_items,
            relevant_method_ids=relevant,
            unattempted_ids=unattempted_fault_tree_item_ids,
            round_number=round_number,
        )
        existing_calls = [
            *policy_calls,
            *[
                call for call in existing_calls
                if call.get("tool_name") not in {
                    "list_diagnostic_documents", "read_diagnostic_documents",
                }
            ],
        ][:4]
        repairs.append({
            "code": "READ_ONLY_EVIDENCE_BINDINGS_ADDED",
            "count": len(policy_calls),
        })
    repaired["tool_calls"] = existing_calls[:4]
    repaired["read_document_ids"] = sorted(method.id for method in methods)
    return repaired, repairs
