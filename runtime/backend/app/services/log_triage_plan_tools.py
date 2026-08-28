from __future__ import annotations

from typing import Any

from app.services.diagnostic_methods import DiagnosticMethodDocument


def attach_log_triage_tool_calls(
    plan: dict[str, Any],
    documents: list[DiagnosticMethodDocument],
    *,
    invoked_by: str,
    pattern_ids: list[str],
    keywords: list[str],
) -> dict[str, Any]:
    """Attach the auditable policy reads and planned local log search."""
    document_ids = [document.id for document in documents]
    tool_calls = [{
        "round": 0,
        "tool_name": "list_diagnostic_documents",
        "invoked_by": "POLICY",
        "method_document_ids": document_ids,
        "status": "COMPLETED",
    }]
    if document_ids:
        tool_calls.append({
            "round": 0,
            "tool_name": "read_diagnostic_documents",
            "invoked_by": "POLICY",
            "method_document_ids": document_ids,
            "status": "COMPLETED",
        })
    tool_calls.append({
        "round": 1,
        "tool_name": "search_log",
        "invoked_by": invoked_by,
        "method_document_ids": document_ids,
        "arguments": {"pattern_ids": pattern_ids, "keywords": keywords},
        "status": "PLANNED",
    })
    plan.update({
        "agent_mode": "typed_read_only_tools",
        "read_attestation_source": "TOOL_RUNTIME",
        "tool_calls": tool_calls,
    })
    return plan
