from __future__ import annotations

from typing import Any

from app.services.diagnostic_methods import DiagnosticPattern
from app.services.diagnostic_tools import (
    GetEvidenceInput,
    ListDiagnosticDocumentsInput,
    ReadDiagnosticDocumentsInput,
    SearchKnowledgeInput,
    SearchLogInput,
)


def validate_planned_diagnostic_tool_calls(
    tool_calls: list[Any],
    *,
    known_method_ids: set[str],
    diagnostic_patterns: list[DiagnosticPattern],
    valid_evidence_ids: set[str] | None,
) -> None:
    """Validate all model tool arguments and IDs before any handler executes."""

    input_schemas = {
        "list_diagnostic_documents": ListDiagnosticDocumentsInput,
        "read_diagnostic_documents": ReadDiagnosticDocumentsInput,
        "search_knowledge": SearchKnowledgeInput,
        "search_log": SearchLogInput,
        "get_evidence": GetEvidenceInput,
    }
    known_patterns = {pattern.id for pattern in diagnostic_patterns}
    for tool_call in tool_calls:
        payload = input_schemas[tool_call.tool_name].model_validate(
            tool_call.arguments
        )
        if isinstance(payload, ReadDiagnosticDocumentsInput):
            if set(payload.document_ids).difference(known_method_ids):
                raise ValueError("Tool requested unknown diagnostic method documents")
        elif isinstance(payload, (SearchKnowledgeInput, SearchLogInput)):
            if set(payload.method_document_ids).difference(known_method_ids):
                raise ValueError("Tool search referenced unknown method documents")
        if isinstance(payload, SearchLogInput):
            if set(payload.pattern_ids).difference(known_patterns):
                raise ValueError("Model selected unknown diagnostic pattern IDs")
        elif isinstance(payload, GetEvidenceInput) and valid_evidence_ids is not None:
            if set(payload.evidence_ids).difference(valid_evidence_ids):
                raise ValueError("Model returned unknown evidence IDs")
