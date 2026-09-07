from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from sqlalchemy import case as sql_case, select

from app.core.db import SessionLocal
from app.core.utils import json_dumps, mask_sensitive
from app.diagnostic_models import LogTriageRun
from app.models import Artifact, Case, LogEvent
from app.services.agentic.tools import ToolContext
from app.services.agentic_search import agentic_search
from app.services.demo_diagnostic_methods import (
    load_bundled_demo_methods_for_case,
    validate_bundled_demo_method_compilation,
)
from app.services.diagnostic_log_search import search_persisted_log_evidence
from app.services.diagnostic_methods import (
    DiagnosticMethodDocument,
    DiagnosticPattern,
    compile_diagnostic_patterns,
    load_applicable_diagnostic_methods,
)
from app.services.diagnostic_planning_contract import PlanningRound
from app.services.diagnostic_planning_coverage import initial_fault_tree_coverage
from app.services.diagnostic_scope import normalize_artifact_source
from app.services.diagnostic_tools import (
    DiagnosticToolEnvironment,
    GetEvidenceInput,
    ListDiagnosticDocumentsInput,
    ReadDiagnosticDocumentsInput,
    SearchKnowledgeInput,
    SearchLogInput,
    build_diagnostic_tool_registry,
    invoke_diagnostic_tool,
)
from app.services.diagnosis_contract import LLMDiagnosis
from app.services.events import active_log_event_clause
from app.services.fault_tree_coverage import FaultTreeCoverageItem, compile_fault_tree_items


HOST_DIAGNOSTIC_CONTRACT_VERSION = "1.0"
HOST_DIAGNOSTIC_PROMPT_VERSION = "host-cli-diagnostic-v1"
HOST_CONTEXT_EVENT_LIMIT = 300
_HOST_TOOL_INPUT_MODELS = {
    "list_diagnostic_documents": ListDiagnosticDocumentsInput,
    "read_diagnostic_documents": ReadDiagnosticDocumentsInput,
    "search_knowledge": SearchKnowledgeInput,
    "search_log": SearchLogInput,
    "get_evidence": GetEvidenceInput,
}


@dataclass(frozen=True)
class HostDiagnosticSnapshot:
    case: Case
    artifacts: list[dict[str, Any]]
    artifact_sources: list[dict[str, Any]]
    triage_run_ids: list[str]
    methods: list[DiagnosticMethodDocument]
    patterns: list[DiagnosticPattern]
    fault_tree_items: list[FaultTreeCoverageItem]
    initial_evidence: list[dict[str, Any]]
    case_snapshot_hash: str
    method_manifest_hash: str
    knowledge_view: tuple[str, ...] = ()

    @property
    def method_manifest(self) -> list[dict[str, Any]]:
        return [method.public_snapshot() for method in self.methods]

    @property
    def parse_generations(self) -> dict[str, str | None]:
        return {
            str(item["id"]): (
                str(item["active_parse_run_id"])
                if item.get("active_parse_run_id")
                else None
            )
            for item in self.artifacts
        }

    @property
    def parse_snapshot_hash(self) -> str:
        return _payload_hash(self.parse_generations)


def _payload_hash(value: Any) -> str:
    return hashlib.sha256(json_dumps(value).encode("utf-8")).hexdigest()


def normalize_host_diagnostic_tool_arguments(
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    normalized_name = tool_name.removeprefix("debug_")
    input_model = _HOST_TOOL_INPUT_MODELS.get(normalized_name)
    if input_model is None:
        raise ValueError(f"Unsupported host diagnostic tool: {tool_name}")
    return input_model.model_validate(arguments).model_dump(mode="json")


def _event_evidence(event: LogEvent, artifact_source: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "evidence_id": event.id,
        "source_type": "log_event",
        "artifact_id": event.artifact_id,
        "artifact_source": artifact_source or {},
        "source_file": event.source_file,
        "line_start": event.line_start,
        "line_end": event.line_end,
        "title": f"{event.event_code} - {event.source_file}:{event.line_start}",
        "content": mask_sensitive(event.message or event.raw_text or "")[:2000],
        "event_code": event.event_code,
        "component": event.component,
        "module": event.module,
        "level": event.level,
        "timestamp": event.timestamp_normalized or event.timestamp_raw,
        "score": float(event.confidence or 0.0),
    }


def _active_triage_runs(
    case_id: str,
    artifacts: list[Artifact],
    session_factory: Any,
) -> list[str]:
    active_generations = {
        (artifact.id, artifact.active_parse_run_id)
        for artifact in artifacts
        if artifact.active_parse_run_id
    }
    if not active_generations:
        return []
    with session_factory() as db:
        rows = list(db.scalars(
            select(LogTriageRun)
            .where(
                LogTriageRun.case_id == case_id,
                LogTriageRun.status == "COMPLETED",
            )
            .order_by(LogTriageRun.created_at.desc())
        ).all())
    latest_by_generation: dict[tuple[str, str | None], str] = {}
    for row in rows:
        key = (row.artifact_id, row.parse_run_id)
        if key in active_generations:
            latest_by_generation.setdefault(key, row.id)
    return [
        latest_by_generation[key]
        for key in sorted(active_generations)
        if key in latest_by_generation
    ]


def load_host_diagnostic_snapshot(
    case_id: str,
    *,
    session_factory: Any = SessionLocal,
    knowledge_view: list[str] | None = None,
) -> HostDiagnosticSnapshot:
    """Load a stable, provider-neutral snapshot for one host-owned run."""

    with session_factory() as db:
        case = db.get(Case, case_id)
        if not case:
            raise ValueError("Case not found")
        artifact_rows = list(db.scalars(
            select(Artifact)
            .where(Artifact.case_id == case_id)
            .order_by(Artifact.created_at.asc(), Artifact.id.asc())
        ).all())
        demo_methods = load_bundled_demo_methods_for_case(case, artifact_rows)
        methods = (
            demo_methods
            if demo_methods is not None
            else load_applicable_diagnostic_methods(db, case, **({"knowledge_view": knowledge_view} if knowledge_view else {}))
        )
        artifact_sources_by_id = {
            artifact.id: normalize_artifact_source(artifact, case)
            for artifact in artifact_rows
        }
        severity_rank = sql_case(
            (LogEvent.level == "CRITICAL", 0),
            (LogEvent.level == "ERROR", 1),
            (LogEvent.level == "WARN", 2),
            (LogEvent.level == "INFO", 3),
            else_=4,
        )
        event_rows = list(db.scalars(
            select(LogEvent)
            .join(Artifact, Artifact.id == LogEvent.artifact_id)
            .where(LogEvent.case_id == case_id, active_log_event_clause())
            .order_by(
                severity_rank.asc(),
                LogEvent.confidence.desc(),
                LogEvent.line_start.asc(),
            )
            .limit(HOST_CONTEXT_EVENT_LIMIT)
        ).all())

        artifacts = [{
            "id": artifact.id,
            "kind": artifact.kind,
            "original_name": artifact.original_name,
            "sha256": artifact.sha256,
            "size_bytes": artifact.size_bytes,
            "status": artifact.status,
            "source_device_type": artifact.source_device_type,
            "source_device_role": artifact.source_device_role,
            "active_parse_run_id": artifact.active_parse_run_id,
        } for artifact in artifact_rows]
        case_snapshot = {
            "id": case.id,
            "title": case.title,
            "device_type": case.device_type,
            "device_model": case.device_model,
            "firmware_version": case.firmware_version,
            "topology": case.topology,
            "description": case.description,
            "reproduction_steps": case.reproduction_steps,
            "issue_time": case.issue_time,
            "updated_at": case.updated_at,
            "artifacts": artifacts,
        }
        method_manifest = [method.public_snapshot() for method in methods]
        initial_evidence = [
            _event_evidence(event, artifact_sources_by_id.get(event.artifact_id))
            for event in event_rows
        ]

    patterns = compile_diagnostic_patterns(methods)
    fault_tree_items = compile_fault_tree_items(methods)
    if demo_methods is not None:
        validate_bundled_demo_method_compilation(methods, patterns, fault_tree_items)
    return HostDiagnosticSnapshot(
        knowledge_view=tuple(knowledge_view or []),
        case=case,
        artifacts=artifacts,
        artifact_sources=list(artifact_sources_by_id.values()),
        triage_run_ids=_active_triage_runs(case_id, artifact_rows, session_factory),
        methods=methods,
        patterns=patterns,
        fault_tree_items=fault_tree_items,
        initial_evidence=initial_evidence,
        case_snapshot_hash=_payload_hash(case_snapshot),
        method_manifest_hash=_payload_hash(method_manifest),
    )


def host_diagnostic_context(snapshot: HostDiagnosticSnapshot) -> dict[str, Any]:
    return {
        "contract_version": HOST_DIAGNOSTIC_CONTRACT_VERSION,
        "prompt_version": HOST_DIAGNOSTIC_PROMPT_VERSION,
        "inference_owner": "host_cli",
        "backend_chat_allowed": False,
        "case": {
            "id": snapshot.case.id,
            "title": snapshot.case.title,
            "device_type": snapshot.case.device_type,
            "device_model": snapshot.case.device_model,
            "firmware_version": snapshot.case.firmware_version,
            "topology": snapshot.case.topology,
            "description": snapshot.case.description,
            "reproduction_steps": snapshot.case.reproduction_steps,
            "issue_time": snapshot.case.issue_time,
        },
        "artifacts": snapshot.artifacts,
        "parse_generations": snapshot.parse_generations,
        "case_snapshot_hash": snapshot.case_snapshot_hash,
        "parse_snapshot_hash": snapshot.parse_snapshot_hash,
        "method_manifest_hash": snapshot.method_manifest_hash,
        "method_manifest": snapshot.method_manifest,
        "fault_tree_coverage": initial_fault_tree_coverage(snapshot.fault_tree_items),
        "initial_evidence": snapshot.initial_evidence,
        "triage_ready": bool(snapshot.triage_run_ids) or not snapshot.artifacts,
        "triage_run_ids": snapshot.triage_run_ids,
        "schemas": {
            "planning_round": PlanningRound.model_json_schema(),
            "diagnosis": LLMDiagnosis.model_json_schema(),
        },
        "limits": {
            "max_rounds": 20,
            "max_tools_per_round": 4,
            "max_evidence_ids_per_read": 100,
            "context_event_limit": HOST_CONTEXT_EVENT_LIMIT,
        },
    }


def _knowledge_search(
    case_id: str,
    query: str,
    top_k: int,
    session_factory: Any,
    knowledge_view: list[str] | None = None,
) -> dict[str, Any]:
    with session_factory() as db:
        result = agentic_search(
            db,
            case_id=case_id,
            query=query,
            top_k=max(1, min(top_k, 20)),
            max_hops=2,
            record_memory=False,
            execution_mode="host_cli_mcp",
            joint_diagnostic_scope=True,
            knowledge_view=knowledge_view,
        )
    return {
        "query": query,
        "run_id": result.get("run_id"),
        "plan": result.get("plan", {}),
        "summary": result.get("summary", {}),
        "results": result.get("results", []),
        "paths": result.get("paths", []),
    }


def invoke_host_diagnostic_tool(
    snapshot: HostDiagnosticSnapshot,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    prior_evidence: list[dict[str, Any]] | None = None,
    session_factory: Any = SessionLocal,
) -> dict[str, Any]:
    """Invoke a typed diagnostic tool without resolving or calling a Chat provider."""

    environment = DiagnosticToolEnvironment(
        case=snapshot.case,
        methods=snapshot.methods,
        patterns=snapshot.patterns,
        fault_tree_items=snapshot.fault_tree_items,
        evidence=[*snapshot.initial_evidence, *(prior_evidence or [])],
        knowledge_search=lambda query, top_k: _knowledge_search(
            snapshot.case.id,
            query,
            top_k,
            session_factory,
            list(snapshot.knowledge_view),
        ),
        log_search=(
            lambda payload: search_persisted_log_evidence(
                triage_run_ids=snapshot.triage_run_ids,
                artifact_sources=snapshot.artifact_sources,
                payload=payload,
                session_factory=session_factory,
            )
        ) if snapshot.triage_run_ids else None,
        spill_store=None,
    )
    invocation = invoke_diagnostic_tool(
        build_diagnostic_tool_registry(environment),
        ToolContext(role="ENGINEER", case_id=snapshot.case.id),
        tool_name=tool_name,
        arguments=arguments,
    )
    evidence = [
        dict(item)
        for item in invocation.output.get("results", [])
        if isinstance(item, dict) and item.get("evidence_id")
    ]
    return {
        "tool_name": invocation.tool_name,
        "arguments": invocation.arguments,
        "output": invocation.output,
        "duration_ms": invocation.duration_ms,
        # The lower-level invocation also returns method-document IDs for the
        # explicit read tool. The MCP registry uses those IDs as read
        # attestations while still caching only actual evidence result objects.
        "evidence_ids": invocation.evidence_ids,
        "evidence": evidence,
    }
