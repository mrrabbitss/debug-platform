from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from starlette.concurrency import run_in_threadpool

from app.core.db import SessionLocal
from app.core.config import get_settings
from app.core.utils import json_loads, new_id
from app.mcp.contracts import MCPToolCallContext, MCPToolError
from app.mcp.debugplatform_contracts import (
    DebugApplyKnowledgeRoutingInput,
    DebugBeginHostDiagnosisInput,
    DebugCancelHostRunInput,
    DebugCaseInput,
    DebugFinalizeDiagnosisInput,
    DebugGenerateReportInput,
    DebugGetEvidenceInput,
    DebugHostRunInput,
    DebugKnowledgeRoutingContextInput,
    DebugKnowledgeSectionsInput,
    DebugListCasesInput,
    DebugListDiagnosticDocumentsInput,
    DebugOpenUIInput,
    DebugPlanningRoundInput,
    DebugReadDiagnosticDocumentsInput,
    DebugSearchKnowledgeInput,
    DebugSearchLogInput,
    DebugStatusInput,
)
from app.mcp.registry import MCPToolRegistry
from app.models import AnalysisRun, Case
from app.services.access_control import (
    VALID_ROLES,
    accessible_case_clause,
    authorize_case_action,
)
from app.services.host_agent_session_contracts import (
    HostAgentSessionCancel,
    HostAgentSessionStatusTransition,
    HostAgentToolReceiptInput,
)
from app.services.host_agent_sessions import (
    HostAgentSessionError,
    cancel_host_agent_session,
    hash_host_agent_tool_arguments,
    record_host_agent_tool_receipt,
    require_host_agent_session,
    transition_host_agent_session,
)
from app.services.host_diagnosis import (
    HOST_ANALYSIS_ENGINE,
    HostDiagnosisError,
    HostDiagnosisInput,
    begin_host_diagnosis,
    finalize_host_diagnosis,
    require_unchanged_host_snapshot,
    submit_host_planning_round,
)
from app.services.host_diagnostic_runtime import (
    HOST_DIAGNOSTIC_CONTRACT_VERSION,
    host_diagnostic_context,
    invoke_host_diagnostic_tool,
    load_host_diagnostic_snapshot,
)
from app.services.knowledge_routing import (
    apply_knowledge_routing,
    knowledge_routing_context,
)
from app.services.report import generate_docx, generate_html_file, generate_pdf


DEBUGPLATFORM_MCP_SERVER_VERSION = "1.1.0"
DEBUGPLATFORM_MCP_TOOL_NAMES = (
    "debug_status",
    "debug_list_cases",
    "debug_get_case_context",
    "debug_begin_host_diagnosis",
    "debug_get_host_run",
    "debug_list_diagnostic_documents",
    "debug_read_diagnostic_documents",
    "debug_search_knowledge",
    "debug_search_log",
    "debug_get_evidence",
    "debug_submit_planning_round",
    "debug_finalize_diagnosis",
    "debug_cancel_host_run",
    "debug_generate_report",
    "debug_open_ui",
    "debug_get_knowledge_routing_context",
    "debug_read_knowledge_sections",
    "debug_apply_knowledge_routing",
)

_READ_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
_WRITE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
_ACTIVE_TOOL_STATES = frozenset({"CREATED", "METHODS_READ", "SEARCHING", "REJECTED"})
_EVIDENCE_TOOL_NAMES = frozenset({"search_knowledge", "search_log", "get_evidence"})


def _principal(context: MCPToolCallContext) -> dict[str, str]:
    role = str(context.principal.claims.get("role") or "VIEWER").upper()
    if role not in VALID_ROLES:
        role = "VIEWER"
    return {
        "id": context.principal.subject,
        "username": str(
            context.principal.claims.get("username") or context.principal.subject
        ),
        "display_name": str(
            context.principal.claims.get("display_name")
            or context.principal.claims.get("username")
            or context.principal.subject
        ),
        "role": role,
        "type": str(context.principal.claims.get("type") or "mcp_bearer"),
    }


def _safe_error(exc: Exception) -> MCPToolError:
    if isinstance(exc, HTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else "Request denied"
        return MCPToolError(detail[:2_000])
    if isinstance(exc, (HostAgentSessionError, HostDiagnosisError, ValueError)):
        return MCPToolError((str(exc) or "Request validation failed")[:2_000])
    return MCPToolError("Debug Platform operation failed")


def _safe_handler(
    handler: Callable[[BaseModel, MCPToolCallContext], Any],
) -> Callable[[BaseModel, MCPToolCallContext], Any]:
    async def invoke(payload: BaseModel, context: MCPToolCallContext) -> Any:
        try:
            return await run_in_threadpool(handler, payload, context)
        except MCPToolError:
            raise
        except Exception as exc:
            safe = _safe_error(exc)
            if str(safe) == "Debug Platform operation failed":
                raise safe from None
            raise safe from exc

    return invoke


def _run_payload(
    view: Any,
    *,
    include_evidence: bool = False,
    include_planning_payloads: bool = False,
) -> dict[str, Any]:
    payload = view.model_dump(mode="json")
    if not include_evidence:
        payload.pop("evidence_cache", None)
    if not include_planning_payloads:
        payload["planning_rounds"] = _compact_planning_rounds(
            payload.get("planning_rounds", [])
        )
    payload["run_id"] = payload["id"]
    payload["state_version"] = payload["version"]
    return payload


class _DebugPlatformTools:
    def __init__(
        self,
        *,
        session_factory: Any,
        public_base_url: str,
        report_generators: Mapping[str, Callable[[str, str], Any]],
    ) -> None:
        self.session_factory = session_factory
        self.public_base_url = public_base_url.rstrip("/")
        self.report_generators = dict(report_generators)

    def status(
        self,
        _payload: DebugStatusInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        with self.session_factory() as db:
            db.scalar(select(1))
        return {
            "ready": True,
            "database": "ready",
            "server": "gw-ap-debug-platform",
            "server_instance_id": get_settings().server_instance_id or "standalone",
            "client_contract_version": "1.0",
            "server_version": DEBUGPLATFORM_MCP_SERVER_VERSION,
            "contract_version": HOST_DIAGNOSTIC_CONTRACT_VERSION,
            "transport": "streamable_http",
            "inference_owner": "host_cli",
            "analysis_engine": HOST_ANALYSIS_ENGINE,
            "backend_chat_allowed": False,
            "backend_chat_calls": 0,
            "client_model_claim_verified": False,
            "authenticated_role": _principal(context)["role"],
            "tool_count": len(DEBUGPLATFORM_MCP_TOOL_NAMES),
            "knowledge_routing": {
                "inference_owner": "host_cli",
                "backend_chat_allowed": False,
                "draft_only": True,
                "max_documents": 20,
            },
        }

    def list_cases(
        self,
        payload: DebugListCasesInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        principal = _principal(context)
        statement = select(Case)
        if principal["role"] != "ADMIN":
            statement = statement.where(accessible_case_clause(principal["id"]))
        query = payload.query.strip()
        if query:
            needle = f"%{query}%"
            statement = statement.where(or_(
                Case.id.ilike(needle),
                Case.title.ilike(needle),
                Case.device_model.ilike(needle),
            ))
        with self.session_factory() as db:
            rows = list(db.scalars(
                statement.order_by(Case.updated_at.desc(), Case.id).limit(payload.limit)
            ).all())
        return {
            "cases": [{
                "id": case.id,
                "title": case.title,
                "device_type": case.device_type,
                "device_model": case.device_model,
                "firmware_version": case.firmware_version,
                "status": case.status,
                "severity": case.severity,
                "updated_at": case.updated_at,
            } for case in rows],
            "returned": len(rows),
            "limit": payload.limit,
        }

    def get_case_context(
        self,
        payload: DebugCaseInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        with self.session_factory() as db:
            authorize_case_action(db, payload.case_id, _principal(context))
            analyses = list(db.scalars(
                select(AnalysisRun)
                .where(AnalysisRun.case_id == payload.case_id)
                .order_by(AnalysisRun.created_at.desc())
                .limit(20)
            ).all())
        snapshot = load_host_diagnostic_snapshot(
            payload.case_id,
            session_factory=self.session_factory,
        )
        result = host_diagnostic_context(snapshot)
        result.pop("initial_evidence", None)
        result["initial_evidence_available"] = len(snapshot.initial_evidence)
        result["analysis_history"] = [{
            "id": analysis.id,
            "status": analysis.status,
            "provider": analysis.provider,
            "model": analysis.model,
            "analysis_engine": json_loads(analysis.result_json, {}).get(
                "analysis_engine"
            ),
            "created_at": analysis.created_at,
            "completed_at": analysis.completed_at,
        } for analysis in analyses]
        return result

    def begin_host_diagnosis(
        self,
        payload: DebugBeginHostDiagnosisInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        principal = _principal(context)
        with self.session_factory() as db:
            authorize_case_action(db, payload.case_id, principal, write=True)
        result = begin_host_diagnosis(
            payload.case_id,
            executor=payload.executor,
            client_model_claim=payload.client_model_claim,
            skill_version=payload.skill_version,
            created_by=principal["id"],
            ttl_seconds=payload.ttl_seconds,
            session_factory=self.session_factory,
        )
        result["run"] = _normalize_dumped_run(result["run"])
        result["run"].pop("evidence_cache", None)
        return result

    def get_host_run(
        self,
        payload: DebugHostRunInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        with self.session_factory() as db:
            view = require_host_agent_session(db, payload.session_id)
            authorize_case_action(db, view.case_id, _principal(context))
        return {"run": _run_payload(
            view,
            include_evidence=payload.include_evidence,
            include_planning_payloads=payload.include_planning_payloads,
        )}

    def invoke_diagnostic_tool(
        self,
        payload: BaseModel,
        context: MCPToolCallContext,
        *,
        tool_name: str,
    ) -> dict[str, Any]:
        data = payload.model_dump(mode="json")
        session_id = str(data.pop("session_id"))
        expected_version = int(data.pop("expected_version"))
        with self.session_factory() as db:
            view = require_host_agent_session(db, session_id)
            authorize_case_action(db, view.case_id, _principal(context), write=True)
            if view.version != expected_version:
                raise HostAgentSessionError(
                    "Host-agent session changed; refresh and retry"
                )
            if view.status not in _ACTIVE_TOOL_STATES:
                raise HostDiagnosisError(
                    f"Diagnostic tools are not available while session is {view.status}"
                )
            snapshot = require_unchanged_host_snapshot(
                view,
                session_factory=self.session_factory,
            )
            if (
                tool_name in _EVIDENCE_TOOL_NAMES
                and snapshot.methods
                and view.status == "CREATED"
            ):
                raise HostDiagnosisError(
                    "Read every pinned diagnostic method before searching evidence"
                )
            invocation = invoke_host_diagnostic_tool(
                snapshot,
                tool_name=tool_name,
                arguments=data,
                prior_evidence=list(view.evidence_cache.values()),
                session_factory=self.session_factory,
            )
            # Listing is discovery, not proof that the document content was read.
            # Only the read tool may attest method IDs into the run allowlist.
            accepted_evidence_ids = (
                []
                if tool_name == "list_diagnostic_documents"
                else invocation["evidence_ids"]
            )
            call_id = new_id("MCALL")
            updated = record_host_agent_tool_receipt(
                db,
                view.id,
                HostAgentToolReceiptInput(
                    expected_version=view.version,
                    call_id=call_id,
                    tool_name=f"debug_{tool_name}",
                    arguments_hash=hash_host_agent_tool_arguments(
                        invocation["arguments"]
                    ),
                    evidence_ids=accepted_evidence_ids,
                    evidence={
                        str(item["evidence_id"]): item
                        for item in invocation["evidence"]
                        if item.get("evidence_id")
                    },
                ),
            )
            if tool_name == "read_diagnostic_documents" and updated.status == "CREATED":
                method_ids = {method.id for method in snapshot.methods}
                if method_ids.issubset(set(updated.allowed_evidence_ids)):
                    updated = transition_host_agent_session(
                        db,
                        updated.id,
                        HostAgentSessionStatusTransition(
                            expected_version=updated.version,
                            status="METHODS_READ",
                            reason="Every pinned diagnostic method was read by the host CLI",
                        ),
                    )
            if (
                tool_name in _EVIDENCE_TOOL_NAMES
                and updated.status in {"CREATED", "METHODS_READ", "REJECTED"}
            ):
                updated = transition_host_agent_session(
                    db,
                    updated.id,
                    HostAgentSessionStatusTransition(
                        expected_version=updated.version,
                        status="SEARCHING",
                        reason="Host CLI is collecting bounded diagnostic evidence",
                    ),
                )
        return {
            "call_id": call_id,
            "tool_name": f"debug_{tool_name}",
            "accepted_arguments": invocation["arguments"],
            "accepted_arguments_hash": hash_host_agent_tool_arguments(
                invocation["arguments"]
            ),
            "output": invocation["output"],
            "evidence_ids": accepted_evidence_ids,
            "duration_ms": invocation["duration_ms"],
            "run": _run_payload(updated),
        }

    def submit_planning_round(
        self,
        payload: DebugPlanningRoundInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        self._authorize_session(payload.session_id, context, write=True)
        result = submit_host_planning_round(
            payload.session_id,
            expected_version=payload.expected_version,
            round_number=payload.round_number,
            payload=payload.planning.model_dump(mode="json"),
            session_factory=self.session_factory,
        )
        result["run"] = _normalize_dumped_run(result["run"])
        result["run"].pop("evidence_cache", None)
        return result

    def finalize_diagnosis(
        self,
        payload: DebugFinalizeDiagnosisInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        self._authorize_session(payload.session_id, context, write=True)
        result = finalize_host_diagnosis(
            HostDiagnosisInput(
                session_id=payload.session_id,
                expected_version=payload.expected_version,
                diagnosis=payload.diagnosis.model_dump(mode="json"),
            ),
            session_factory=self.session_factory,
        )
        result["run"] = _normalize_dumped_run(result["run"])
        result["run"].pop("evidence_cache", None)
        return result

    def cancel_host_run(
        self,
        payload: DebugCancelHostRunInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        with self.session_factory() as db:
            view = require_host_agent_session(db, payload.session_id)
            authorize_case_action(db, view.case_id, _principal(context), write=True)
            cancelled = cancel_host_agent_session(
                db,
                view.id,
                HostAgentSessionCancel(
                    expected_version=payload.expected_version,
                    reason=payload.reason,
                ),
            )
        return {"run": _run_payload(cancelled)}

    def generate_report(
        self,
        payload: DebugGenerateReportInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        with self.session_factory() as db:
            authorize_case_action(db, payload.case_id, _principal(context), write=True)
            analysis = db.get(AnalysisRun, payload.analysis_id) if payload.analysis_id else None
            if analysis is None and payload.analysis_id is None:
                analysis = db.scalar(
                    select(AnalysisRun)
                    .where(
                        AnalysisRun.case_id == payload.case_id,
                        AnalysisRun.status == "COMPLETED",
                    )
                    .order_by(AnalysisRun.completed_at.desc(), AnalysisRun.created_at.desc())
                )
            if analysis is None or analysis.case_id != payload.case_id:
                raise ValueError("Completed analysis not found for this case")
            analysis_id = analysis.id
        report = self.report_generators[payload.format](payload.case_id, analysis_id)
        return {
            "report_id": report.id,
            "case_id": payload.case_id,
            "analysis_id": analysis_id,
            "format": report.format,
            "version": report.version,
            "sha256": report.sha256,
            "download_path": f"/api/v1/reports/{quote(report.id)}/download",
        }

    def open_ui(
        self,
        payload: DebugOpenUIInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        case_id = payload.case_id
        with self.session_factory() as db:
            if payload.analysis_id:
                analysis = db.get(AnalysisRun, payload.analysis_id)
                if analysis is None:
                    raise ValueError("Analysis not found")
                if case_id is not None and analysis.case_id != case_id:
                    raise ValueError("Analysis does not belong to the requested case")
                case_id = analysis.case_id
            if case_id is not None:
                authorize_case_action(db, case_id, _principal(context))
        path = f"/cases/{quote(case_id)}" if case_id else "/cases"
        return {
            "path": path,
            "url": f"{self.public_base_url}{path}" if self.public_base_url else path,
            "opened": False,
        }

    def get_knowledge_routing_context(
        self,
        payload: DebugKnowledgeRoutingContextInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        principal = _principal(context)
        if principal["role"] not in {"ADMIN", "ENGINEER"}:
            raise MCPToolError("Engineer or administrator role required")
        with self.session_factory() as db:
            from app.services.knowledge_access import require_knowledge_access
            for document_id in payload.document_ids:
                require_knowledge_access(db, document_id, principal, write=True)
            result = knowledge_routing_context(
                db,
                payload.document_ids,
                expected_reasoning_owner="host_cli",
            )
        return {
            **result,
            "inference_owner": "host_cli",
            "backend_chat_allowed": False,
            "backend_chat_calls": 0,
            "consent_recorded": payload.consent_host_model_data,
        }

    def read_knowledge_sections(self, payload: DebugKnowledgeSectionsInput, context: MCPToolCallContext) -> dict:
        from app.services.knowledge_compiler import read_sections
        from app.models import KnowledgeDocument
        principal = _principal(context)
        with self.session_factory() as db:
            from app.services.knowledge_access import require_knowledge_access
            require_knowledge_access(db, payload.document_id, principal)
            document = db.get(KnowledgeDocument, payload.document_id)
            if not document:
                raise MCPToolError("Knowledge document not found")
            return {**read_sections(document.content, content_sha256=payload.content_sha256,
                                    offset=payload.offset, limit=payload.limit),
                    "document_id": document.id, "backend_chat_calls": 0}

    def apply_knowledge_routing(
        self,
        payload: DebugApplyKnowledgeRoutingInput,
        context: MCPToolCallContext,
    ) -> dict[str, Any]:
        principal = _principal(context)
        if principal["role"] not in {"ADMIN", "ENGINEER"}:
            raise MCPToolError("Engineer or administrator role required")
        with self.session_factory() as db:
            from app.services.knowledge_access import require_knowledge_access
            for decision in payload.decisions:
                require_knowledge_access(db, decision.document_id, principal, write=True)
            results = apply_knowledge_routing(
                db,
                [item.model_dump(mode="json") for item in payload.decisions],
                actor=principal["id"],
                reasoning_owner="host_cli",
                model_snapshot={
                    "client_model_claim": payload.client_model_claim,
                    "backend_model_profile_id": None,
                },
            )
        return {
            "documents": results,
            "updated": len(results),
            "inference_owner": "host_cli",
            "backend_chat_calls": 0,
            "draft_only": True,
            "human_review_required": True,
        }

    def _authorize_session(
        self,
        session_id: str,
        context: MCPToolCallContext,
        *,
        write: bool,
    ) -> None:
        with self.session_factory() as db:
            view = require_host_agent_session(db, session_id)
            authorize_case_action(
                db,
                view.case_id,
                _principal(context),
                write=write,
            )


def _normalize_dumped_run(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized["planning_rounds"] = _compact_planning_rounds(
        normalized.get("planning_rounds", [])
    )
    normalized["run_id"] = normalized["id"]
    normalized["state_version"] = normalized["version"]
    return normalized


def _compact_planning_rounds(rounds: Any) -> list[dict[str, Any]]:
    if not isinstance(rounds, list):
        return []
    return [{
        "round_number": item.get("round_number"),
        "payload_hash": item.get("payload_hash"),
        "summary": item.get("summary", {}),
        "recorded_at": item.get("recorded_at"),
    } for item in rounds if isinstance(item, dict)]


def create_debugplatform_mcp_registry(
    *,
    session_factory: Any = SessionLocal,
    public_base_url: str = "",
    report_generators: Mapping[str, Callable[[str, str], Any]] | None = None,
) -> MCPToolRegistry:
    tools = _DebugPlatformTools(
        session_factory=session_factory,
        public_base_url=public_base_url,
        report_generators=report_generators or {
            "html": generate_html_file,
            "pdf": generate_pdf,
            "docx": generate_docx,
        },
    )
    registry = MCPToolRegistry()

    def register(
        name: str,
        description: str,
        input_model: type[BaseModel],
        handler: Callable[[BaseModel, MCPToolCallContext], Any],
        *,
        read_only: bool,
    ) -> None:
        registry.register(
            name=name,
            description=description,
            input_model=input_model,
            handler=_safe_handler(handler),
            annotations=_READ_ANNOTATIONS if read_only else _WRITE_ANNOTATIONS,
        )

    register("debug_status", "Verify MCP readiness and the host-CLI inference boundary.", DebugStatusInput, tools.status, read_only=True)
    register("debug_list_cases", "List case summaries visible to the authenticated user.", DebugListCasesInput, tools.list_cases, read_only=True)
    register("debug_get_case_context", "Read bounded case, artifact, method, parse and analysis metadata.", DebugCaseInput, tools.get_case_context, read_only=True)
    register("debug_begin_host_diagnosis", "Pin a provider-neutral case snapshot and create a durable CLI-reasoned run.", DebugBeginHostDiagnosisInput, tools.begin_host_diagnosis, read_only=False)
    register("debug_get_host_run", "Read reconnect-safe host-run state, evidence allowlist and coverage.", DebugHostRunInput, tools.get_host_run, read_only=True)
    register("debug_list_diagnostic_documents", "List diagnostic method documents pinned to a host run.", DebugListDiagnosticDocumentsInput, lambda payload, context: tools.invoke_diagnostic_tool(payload, context, tool_name="list_diagnostic_documents"), read_only=False)
    register("debug_read_diagnostic_documents", "Read selected pinned diagnostic methods and compiled checks.", DebugReadDiagnosticDocumentsInput, lambda payload, context: tools.invoke_diagnostic_tool(payload, context, tool_name="read_diagnostic_documents"), read_only=False)
    register("debug_search_knowledge", "Search approved knowledge without invoking a backend chat model.", DebugSearchKnowledgeInput, lambda payload, context: tools.invoke_diagnostic_tool(payload, context, tool_name="search_knowledge"), read_only=False)
    register("debug_search_log", "Search bounded, masked evidence from the current case and active parse generation.", DebugSearchLogInput, lambda payload, context: tools.invoke_diagnostic_tool(payload, context, tool_name="search_log"), read_only=False)
    register("debug_get_evidence", "Resolve exact current-run evidence by server-issued evidence ID.", DebugGetEvidenceInput, lambda payload, context: tools.invoke_diagnostic_tool(payload, context, tool_name="get_evidence"), read_only=False)
    register("debug_submit_planning_round", "Validate a CLI-model planning round against server receipts and fault-tree coverage.", DebugPlanningRoundInput, tools.submit_planning_round, read_only=False)
    register("debug_finalize_diagnosis", "Validate and persist a structured, evidence-grounded CLI-model diagnosis.", DebugFinalizeDiagnosisInput, tools.finalize_diagnosis, read_only=False)
    register("debug_cancel_host_run", "Cancel an unfinished host diagnosis with a recorded reason.", DebugCancelHostRunInput, tools.cancel_host_run, read_only=False)
    register("debug_generate_report", "Render an existing completed analysis without another inference pass.", DebugGenerateReportInput, tools.generate_report, read_only=False)
    register("debug_open_ui", "Return the existing Web UI URL for a case; never launches a browser.", DebugOpenUIInput, tools.open_ui, read_only=True)
    register("debug_get_knowledge_routing_context", "Read active knowledge taxonomy and bounded masked Markdown excerpts for host-model classification.", DebugKnowledgeRoutingContextInput, tools.get_knowledge_routing_context, read_only=True)
    register("debug_read_knowledge_sections", "Read complete masked Markdown sections page by page; preserve returned section IDs for full-source classification.", DebugKnowledgeSectionsInput, tools.read_knowledge_sections, read_only=True)
    register("debug_apply_knowledge_routing", "Atomically apply host-model category decisions to DRAFT knowledge without publishing it.", DebugApplyKnowledgeRoutingInput, tools.apply_knowledge_routing, read_only=False)
    return registry
