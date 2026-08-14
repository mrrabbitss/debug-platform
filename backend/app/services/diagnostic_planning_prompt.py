from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.diagnostic_methods import DiagnosticPattern
from app.services.fault_tree_coverage import FaultTreeCoverageItem


def _recommended_patterns(
    item: FaultTreeCoverageItem,
    patterns: list[DiagnosticPattern],
) -> list[DiagnosticPattern]:
    item_text = f"{item.label}\n{item.description}\n{item.section}".casefold()
    hints = [hint.casefold() for hint in item.evidence_hints if len(hint) >= 3]
    candidates: list[tuple[int, DiagnosticPattern]] = []
    for pattern in patterns:
        pattern_text = pattern.text.casefold()
        same_document = pattern.document_id == item.method_document_id
        score = 0
        if pattern_text in item_text:
            score += 20
        score += 6 * sum(
            hint in pattern_text or pattern_text in hint
            for hint in hints
        )
        if same_document and item.section and pattern.heading == item.section:
            score += 4
        if same_document:
            distance = abs(pattern.line_start - item.line_start)
            if distance <= 3:
                score += 5
            elif distance <= 12:
                score += 2
        if score:
            candidates.append((score, pattern))
    candidates.sort(key=lambda entry: (-entry[0], entry[1].line_start, entry[1].id))
    return list(dict.fromkeys(pattern for _, pattern in candidates))[:12]


def fault_tree_items_for_prompt(
    items: list[FaultTreeCoverageItem],
    patterns: list[DiagnosticPattern],
) -> list[dict[str, Any]]:
    rendered: list[dict[str, Any]] = []
    for item in items:
        recommended = _recommended_patterns(item, patterns)
        search_terms = list(dict.fromkeys([
            *item.evidence_hints,
            item.label,
        ]))[:20]
        recommended_tools = ["search_knowledge"]
        if recommended:
            recommended_tools.insert(0, "search_log")
        rendered.append({
            **item.public_snapshot(),
            "recommended_pattern_ids": [pattern.id for pattern in recommended],
            "recommended_search_terms": search_terms,
            "recommended_tools": recommended_tools,
            "recommended_patterns": [
                {
                    "id": pattern.id,
                    "text": pattern.text,
                    "document_id": pattern.document_id,
                    "document_title": pattern.document_title,
                    "heading": pattern.heading,
                    "line_start": pattern.line_start,
                }
                for pattern in recommended
            ],
        })
    return rendered


def compact_ranked_log_evidence(
    evidence: list[dict[str, Any]],
    *,
    limit: int = 250,
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for original in evidence[:limit]:
        item = {
            key: deepcopy(original.get(key))
            for key in (
                "evidence_id",
                "source_type",
                "artifact_id",
                "artifact_source",
                "bucket",
                "source_file",
                "line_start",
                "line_end",
                "pattern_id",
                "pattern_text",
                "reason",
                "occurrence_count",
                "score",
                "method_document_id",
            )
            if original.get(key) is not None
        }
        content = str(original.get("content") or "")
        item["content"] = content[:1200] + ("…[truncated]" if len(content) > 1200 else "")
        compact.append(item)
    return compact


def compact_prior_rounds(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for original in rounds[-6:]:
        compact.append({
            "round": original.get("round"),
            "method_assessments": original.get("method_assessments", []),
            "hypotheses": original.get("hypotheses", [])[-20:],
            "checks": original.get("checks", []),
            "fault_tree_assessments": original.get("fault_tree_assessments", []),
            "evidence_gaps": original.get("evidence_gaps", []),
            "continue_analysis": original.get("continue_analysis"),
            "stop_reason": original.get("stop_reason"),
            "executed_tool_calls": [{
                key: call.get(key)
                for key in (
                    "call_id", "tool_name", "method_document_ids",
                    "fault_tree_item_ids", "status", "returned",
                    "total_candidates", "evidence_ids",
                )
            } for call in original.get("executed_tool_calls", [])],
        })
    return compact


def compact_search_observations(
    observations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for original in observations[-24:]:
        results = []
        for result in original.get("results", [])[:8]:
            if not isinstance(result, dict):
                continue
            item = {
                key: deepcopy(result.get(key))
                for key in (
                    "evidence_id", "source_type", "title", "artifact_id",
                    "artifact_source", "source_file", "line_start", "line_end",
                    "pattern_id", "score", "method_document_id",
                )
                if result.get(key) is not None
            }
            content = str(result.get("content") or "")
            item["content"] = content[:1000] + (
                "…[truncated]" if len(content) > 1000 else ""
            )
            results.append(item)
        compact.append({
            "round": original.get("round"),
            "query": original.get("query"),
            "tool_name": original.get("tool_name"),
            "arguments": original.get("arguments", {}),
            "summary": original.get("summary", {}),
            "results": results,
        })
    return compact
