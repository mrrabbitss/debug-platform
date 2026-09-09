from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.utils import mask_sensitive
from app.models import Case
from app.services.agent_runtime import EvidenceSpillStore
from app.services.agentic.tools import (
    ToolContext,
    ToolPermission,
    ToolRegistry,
    ToolSpec,
)
from app.services.diagnostic_methods import DiagnosticMethodDocument, DiagnosticPattern
from app.services.fault_tree_coverage import FaultTreeCoverageItem


class ListDiagnosticDocumentsInput(BaseModel):
    roles: list[str] = Field(default_factory=list, max_length=20)


class ListDiagnosticDocumentsOutput(BaseModel):
    documents: list[dict[str, Any]]


class ReadDiagnosticDocumentsInput(BaseModel):
    document_ids: list[str] = Field(min_length=1, max_length=5000)


class ReadDiagnosticDocumentsOutput(BaseModel):
    documents: list[dict[str, Any]]
    patterns: list[dict[str, Any]] = Field(default_factory=list)
    fault_tree_items: list[dict[str, Any]] = Field(default_factory=list)


class SearchKnowledgeInput(BaseModel):
    query: str = Field(min_length=2, max_length=4000)
    method_document_ids: list[str] = Field(default_factory=list, max_length=5000)
    top_k: int = Field(default=10, ge=1, le=20)


class SearchLogInput(BaseModel):
    keywords: list[str] = Field(default_factory=list, max_length=30)
    pattern_ids: list[str] = Field(default_factory=list, max_length=60)
    artifact_ids: list[str] = Field(default_factory=list, max_length=100)
    method_document_ids: list[str] = Field(default_factory=list, max_length=5000)
    top_k: int = Field(default=20, ge=1, le=100)


class GetEvidenceInput(BaseModel):
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class EvidenceToolOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")

    results: list[dict[str, Any]] = Field(default_factory=list)
    returned: int = 0
    total_candidates: int = 0


KnowledgeSearch = Callable[[str, int], dict[str, Any]]
LogSearch = Callable[[SearchLogInput], dict[str, Any]]


@dataclass
class DiagnosticToolEnvironment:
    case: Case
    methods: list[DiagnosticMethodDocument]
    patterns: list[DiagnosticPattern]
    fault_tree_items: list[FaultTreeCoverageItem] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    knowledge_search: KnowledgeSearch | None = None
    log_search: LogSearch | None = None
    spill_store: EvidenceSpillStore | None = None

    def __post_init__(self) -> None:
        self.method_by_id = {method.id: method for method in self.methods}
        self.pattern_by_id = {pattern.id: pattern for pattern in self.patterns}
        self.evidence_by_id = {
            str(item.get("evidence_id")): dict(item)
            for item in self.evidence
            if item.get("evidence_id")
        }

    def remember_evidence(self, items: list[dict[str, Any]]) -> None:
        for item in items:
            evidence_id = str(item.get("evidence_id") or "")
            if evidence_id:
                self.evidence_by_id[evidence_id] = dict(item)


@dataclass(frozen=True)
class DiagnosticToolInvocation:
    tool_name: str
    arguments: dict[str, Any]
    output: dict[str, Any]
    duration_ms: int
    evidence_ids: list[str]


def _catalog_handler(
    environment: DiagnosticToolEnvironment,
    _context: ToolContext,
    payload: ListDiagnosticDocumentsInput,
) -> ListDiagnosticDocumentsOutput:
    roles = {item.strip().upper() for item in payload.roles if item.strip()}
    documents = [
        method.public_snapshot()
        for method in environment.methods
        if not roles or method.role.upper() in roles
    ]
    return ListDiagnosticDocumentsOutput(documents=documents)


def _read_handler(
    environment: DiagnosticToolEnvironment,
    _context: ToolContext,
    payload: ReadDiagnosticDocumentsInput,
) -> ReadDiagnosticDocumentsOutput:
    requested = list(dict.fromkeys(payload.document_ids))
    unknown = set(requested).difference(environment.method_by_id)
    if unknown:
        raise ValueError("Tool requested unknown diagnostic method documents")
    for document_id in requested:
        for dependency in environment.method_by_id[document_id].dependency_ids:
            if dependency not in environment.method_by_id:
                raise ValueError("Skill dependency is absent from the pinned method snapshot")
            if dependency not in requested:
                requested.append(dependency)
    documents = [
        {
            **environment.method_by_id[document_id].public_snapshot(),
            "content": environment.method_by_id[document_id].content,
            "evidence_id": document_id,
        }
        for document_id in requested
    ]
    patterns = [
        pattern.public_snapshot()
        for pattern in environment.patterns
        if pattern.document_id in requested
    ]
    fault_tree_items = [
        item.public_snapshot()
        for item in environment.fault_tree_items
        if item.method_document_id in requested
    ]
    return ReadDiagnosticDocumentsOutput(
        documents=documents,
        patterns=patterns,
        fault_tree_items=fault_tree_items,
    )


def _knowledge_handler(
    environment: DiagnosticToolEnvironment,
    _context: ToolContext,
    payload: SearchKnowledgeInput,
) -> EvidenceToolOutput:
    unknown = set(payload.method_document_ids).difference(environment.method_by_id)
    if unknown:
        raise ValueError("Tool search referenced unknown method documents")
    if environment.knowledge_search is None:
        return EvidenceToolOutput(results=[], returned=0, total_candidates=0)
    raw = environment.knowledge_search(payload.query, payload.top_k)
    raw_results = raw.get("results", []) if isinstance(raw, dict) else []
    results: list[dict[str, Any]] = []
    for item in raw_results[: payload.top_k]:
        if not isinstance(item, dict) or not item.get("evidence_id"):
            continue
        results.append({
            "evidence_id": str(item["evidence_id"]),
            "source_type": str(item.get("source_type") or "knowledge"),
            "title": str(item.get("title") or item["evidence_id"])[:500],
            "content": str(item.get("content") or "")[:6000],
            "score": float(
                item.get("reranker_score")
                or item.get("combined_score")
                or item.get("source_score")
                or 0.0
            ),
            "method_document_ids": payload.method_document_ids,
            "metadata": item.get("metadata", {}),
        })
    environment.remember_evidence(results)
    return EvidenceToolOutput(
        results=results,
        returned=len(results),
        total_candidates=len(raw_results),
    )


def _log_handler(
    environment: DiagnosticToolEnvironment,
    _context: ToolContext,
    payload: SearchLogInput,
) -> EvidenceToolOutput:
    unknown_methods = set(payload.method_document_ids).difference(environment.method_by_id)
    if unknown_methods:
        raise ValueError("Tool log search referenced unknown method documents")
    unknown_patterns = set(payload.pattern_ids).difference(environment.pattern_by_id)
    if unknown_patterns:
        raise ValueError("Tool log search referenced unknown diagnostic patterns")
    if environment.log_search is not None:
        raw = environment.log_search(payload)
        raw_results = raw.get("results", []) if isinstance(raw, dict) else []
        results = [dict(item) for item in raw_results[: payload.top_k] if isinstance(item, dict)]
        environment.remember_evidence(results)
        return EvidenceToolOutput(
            results=results,
            returned=len(results),
            total_candidates=int(raw.get("total_candidates") or len(raw_results)),
        )
    terms = [item.strip().casefold() for item in payload.keywords if len(item.strip()) >= 2]
    pattern_ids = set(payload.pattern_ids)
    artifact_ids = set(payload.artifact_ids)
    candidates: list[tuple[float, dict[str, Any]]] = []
    for item in environment.evidence_by_id.values():
        source_type = str(item.get("source_type") or "")
        if source_type not in {"log_event", "log_triage_match"}:
            continue
        artifact_id = str(item.get("artifact_id") or "")
        if artifact_ids and artifact_id not in artifact_ids:
            continue
        rendered = "\n".join(str(item.get(key) or "") for key in (
            "content", "pattern_text", "event_code", "title",
        )).casefold()
        pattern_match = bool(pattern_ids and str(item.get("pattern_id") or "") in pattern_ids)
        term_matches = sum(1 for term in terms if term in rendered)
        if (pattern_ids or terms) and not pattern_match and not term_matches:
            continue
        score = float(item.get("score") or item.get("source_score") or 0.0)
        score += 0.2 if pattern_match else 0.0
        score += min(0.2, term_matches * 0.04)
        candidates.append((score, item))
    candidates.sort(key=lambda entry: (-entry[0], str(entry[1].get("evidence_id"))))
    results = [{
        "evidence_id": str(item.get("evidence_id")),
        "source_type": str(item.get("source_type") or "log_event"),
        "artifact_id": item.get("artifact_id"),
        "artifact_source": item.get("artifact_source") or item.get("metadata", {}).get("artifact_source"),
        "source_file": item.get("source_file"),
        "line_start": item.get("line_start"),
        "line_end": item.get("line_end"),
        "pattern_id": item.get("pattern_id"),
        "content": mask_sensitive(str(item.get("content") or ""))[:2000],
        "score": round(score, 6),
        "method_document_ids": payload.method_document_ids,
    } for score, item in candidates[: payload.top_k]]
    return EvidenceToolOutput(
        results=results,
        returned=len(results),
        total_candidates=len(candidates),
    )


def _evidence_handler(
    environment: DiagnosticToolEnvironment,
    _context: ToolContext,
    payload: GetEvidenceInput,
) -> EvidenceToolOutput:
    results: list[dict[str, Any]] = []
    for evidence_id in dict.fromkeys(payload.evidence_ids):
        if evidence_id in environment.evidence_by_id:
            results.append(environment.evidence_by_id[evidence_id])
            continue
        if environment.spill_store is not None:
            resolved = environment.spill_store.resolve(evidence_id)
            if resolved is not None:
                results.append(resolved)
    return EvidenceToolOutput(
        results=results,
        returned=len(results),
        total_candidates=len(payload.evidence_ids),
    )


def build_diagnostic_tool_registry(environment: DiagnosticToolEnvironment) -> ToolRegistry:
    registry = ToolRegistry()
    common = {
        "permission": ToolPermission.READ,
        "allowed_roles": frozenset({"ENGINEER"}),
        "max_retries": 0,
    }
    registry.register(ToolSpec(
        name="list_diagnostic_documents",
        description="列出当前 GW/AP 联合诊断域内全部已发布方法文档及版本。",
        input_schema=ListDiagnosticDocumentsInput,
        output_schema=ListDiagnosticDocumentsOutput,
        handler=lambda context, payload: _catalog_handler(environment, context, payload),
        **common,
    ))
    registry.register(ToolSpec(
        name="read_diagnostic_documents",
        description="读取指定方法文档全文及其已编译日志 Pattern；只能读取目录中的文档 ID。",
        input_schema=ReadDiagnosticDocumentsInput,
        output_schema=ReadDiagnosticDocumentsOutput,
        handler=lambda context, payload: _read_handler(environment, context, payload),
        **common,
    ))
    registry.register(ToolSpec(
        name="search_knowledge",
        description="使用现有 BM25、Dense Embedding 与 Reranker 联合检索已发布知识。",
        input_schema=SearchKnowledgeInput,
        output_schema=EvidenceToolOutput,
        handler=lambda context, payload: _knowledge_handler(environment, context, payload),
        **common,
    ))
    registry.register(ToolSpec(
        name="search_log",
        description="按关键词或 Pattern ID 检索当前案例已脱敏且带证据 ID 的日志证据。",
        input_schema=SearchLogInput,
        output_schema=EvidenceToolOutput,
        handler=lambda context, payload: _log_handler(environment, context, payload),
        **common,
    ))
    registry.register(ToolSpec(
        name="get_evidence",
        description=(
            "按已有 evidence_id 读取证据详情，或按 content_handle 分段读取被"
            "上下文治理器压缩的正文；句柄本身不能作为事实证据引用。"
        ),
        input_schema=GetEvidenceInput,
        output_schema=EvidenceToolOutput,
        handler=lambda context, payload: _evidence_handler(environment, context, payload),
        **common,
    ))
    return registry


def invoke_diagnostic_tool(
    registry: ToolRegistry,
    context: ToolContext,
    *,
    tool_name: str,
    arguments: dict[str, Any],
) -> DiagnosticToolInvocation:
    started = perf_counter()
    spec = registry.get(tool_name, role=context.role)
    if spec.permission != ToolPermission.READ:
        raise ValueError("Diagnostic agent only permits read-only tools")
    validated_input = spec.input_schema.model_validate(arguments)
    validated_output = spec.output_schema.model_validate(spec.handler(context, validated_input))
    output = validated_output.model_dump(mode="json")
    evidence_ids = [
        str(item.get("evidence_id"))
        for item in output.get("results", [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    if tool_name in {"list_diagnostic_documents", "read_diagnostic_documents"}:
        evidence_ids.extend(
            str(item.get("id") or item.get("evidence_id"))
            for item in output.get("documents", [])
            if isinstance(item, dict) and (item.get("id") or item.get("evidence_id"))
        )
    return DiagnosticToolInvocation(
        tool_name=tool_name,
        arguments=validated_input.model_dump(mode="json"),
        output=output,
        duration_ms=int((perf_counter() - started) * 1000),
        evidence_ids=list(dict.fromkeys(evidence_ids)),
    )


DiagnosticToolName = Literal[
    "list_diagnostic_documents",
    "read_diagnostic_documents",
    "search_knowledge",
    "search_log",
    "get_evidence",
]


def summarize_method_usage(
    methods: list[DiagnosticMethodDocument],
    rounds: list[dict[str, Any]],
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    assessments: dict[str, dict[str, Any]] = {}
    check_counts: dict[str, int] = {}
    for planning_round in rounds:
        for assessment in planning_round.get("method_assessments", []):
            document_id = str(assessment.get("method_document_id") or "")
            if document_id:
                assessments[document_id] = assessment
        for check in planning_round.get("checks", []):
            document_id = str(check.get("method_document_id") or "")
            if document_id:
                check_counts[document_id] = check_counts.get(document_id, 0) + 1
    call_counts: dict[str, int] = {}
    hit_counts: dict[str, int] = {}
    for call in tool_calls:
        for document_id in call.get("method_document_ids", []):
            rendered = str(document_id)
            call_counts[rendered] = call_counts.get(rendered, 0) + 1
            if call.get("tool_name") in {
                "search_knowledge", "search_log", "get_evidence",
                "deterministic_fault_tree_scan",
            }:
                hit_counts[rendered] = hit_counts.get(rendered, 0) + int(
                    call.get("returned") or 0
                )
    return [{
        **method.public_snapshot(),
        "read_status": "READ_BY_POLICY_TOOL",
        "relevance": assessments.get(method.id, {}).get("relevance", "NOT_ASSESSED"),
        "relevance_rationale": assessments.get(method.id, {}).get("rationale", ""),
        "check_count": check_counts.get(method.id, 0),
        "tool_call_count": call_counts.get(method.id, 0),
        "evidence_hit_count": hit_counts.get(method.id, 0),
    } for method in methods]


def summarize_log_method_usage(
    methods: list[DiagnosticMethodDocument],
    patterns: list[DiagnosticPattern],
    plan: dict[str, Any],
    scan_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    selected = set(plan.get("selected_pattern_ids", []))
    occurrences = scan_summary.get("pattern_occurrences", {})
    by_document: dict[str, list[DiagnosticPattern]] = {}
    for pattern in patterns:
        by_document.setdefault(pattern.document_id, []).append(pattern)
    return [{
        **method.public_snapshot(),
        "read_status": "READ_BY_POLICY_TOOL",
        "compiled_pattern_count": len(by_document.get(method.id, [])),
        "selected_pattern_count": sum(
            1 for pattern in by_document.get(method.id, []) if pattern.id in selected
        ),
        "matched_pattern_count": sum(
            1 for pattern in by_document.get(method.id, []) if occurrences.get(pattern.id)
        ),
        "occurrence_count": sum(
            int(occurrences.get(pattern.id) or 0)
            for pattern in by_document.get(method.id, [])
        ),
    } for method in methods]
