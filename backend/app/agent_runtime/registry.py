from __future__ import annotations

import json
from typing import Any

from app.agent_runtime.client import RuntimeClient
from app.agent_runtime.contracts import (
    AttachWorkspaceInput,
    CodeContextInput,
    CreateCaseInput,
    DiagnoseInput,
    EvidenceBundleInput,
    GenerateReportInput,
    IngestInput,
    InspectInput,
    OpenUiInput,
    SearchInput,
    StatusInput,
    ToolResult,
    WaitJobInput,
)
from app.services.agentic.tools import (
    ToolContext,
    ToolNotAllowedError,
    ToolPermission,
    ToolRegistry,
    ToolSpec,
)


WRITE_ROLES = frozenset({"ADMIN", "ENGINEER"})
READ_ROLES = frozenset({"ADMIN", "ENGINEER", "VIEWER"})


def _client(context: ToolContext) -> RuntimeClient:
    return RuntimeClient(
        base_url=context.metadata.get("runtime_url"),
        api_key=context.metadata.get("api_key"),
    )


def build_agent_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()

    registry.register(ToolSpec(
        name="debug_status",
        description="Read local GW/AP Debug Runtime health, agent mode and integration status.",
        input_schema=StatusInput,
        output_schema=ToolResult,
        handler=lambda ctx, _p: {"data": _client(ctx).status()},
    ))
    registry.register(ToolSpec(
        name="debug_create_case",
        description="Create a GW/AP debug case. This is a write and requires explicit confirmation.",
        input_schema=CreateCaseInput,
        output_schema=ToolResult,
        permission=ToolPermission.WRITE,
        allowed_roles=WRITE_ROLES,
        handler=lambda ctx, p: {"data": _client(ctx).create_case(p.model_dump(exclude={"confirm_write"}))},
    ))

    def ingest(ctx: ToolContext, p: IngestInput) -> dict[str, Any]:
        client = _client(ctx)
        result = client.ingest(p.case_id, p.local_file)
        if p.wait:
            result["parse_result"] = client.wait_job(result["job"]["id"], p.timeout_seconds)
        return {"data": result}

    registry.register(ToolSpec(
        name="debug_ingest",
        description="Upload and parse a local collectDebuginfo/log file into a case. Requires write confirmation.",
        input_schema=IngestInput,
        output_schema=ToolResult,
        permission=ToolPermission.WRITE,
        allowed_roles=WRITE_ROLES,
        handler=ingest,
        timeout_seconds=1800,
        idempotent=False,
    ))
    registry.register(ToolSpec(
        name="debug_wait_job",
        description="Wait for a bounded background parse/index/analysis job and return its terminal state.",
        input_schema=WaitJobInput,
        output_schema=ToolResult,
        handler=lambda ctx, p: {"data": _client(ctx).wait_job(p.job_id, p.timeout_seconds)},
        timeout_seconds=1800,
    ))
    registry.register(ToolSpec(
        name="debug_inspect",
        description="Inspect a bounded set of structured log events; never dumps an entire large log.",
        input_schema=InspectInput,
        output_schema=ToolResult,
        handler=lambda ctx, p: {"data": _client(ctx).inspect(
            p.case_id, level=p.level, module=p.module, search=p.search, limit=p.limit
        )},
    ))
    registry.register(ToolSpec(
        name="debug_search",
        description="Run bounded Agentic Search across knowledge, graphs, memory, code and commits.",
        input_schema=SearchInput,
        output_schema=ToolResult,
        handler=lambda ctx, p: {"data": _client(ctx).search(p.case_id, p.model_dump(exclude={"case_id"}))},
        timeout_seconds=120,
    ))
    registry.register(ToolSpec(
        name="debug_evidence_bundle",
        description="Primary External Agent Mode tool: return a compact evidence bundle for Claude/OpenCode final reasoning.",
        input_schema=EvidenceBundleInput,
        output_schema=ToolResult,
        handler=lambda ctx, p: {"data": _client(ctx).evidence_bundle(
            p.case_id, {**p.model_dump(exclude={"case_id"}), "query": p.query or None}
        )},
        timeout_seconds=120,
    ))

    def attach(ctx: ToolContext, p: AttachWorkspaceInput) -> dict[str, Any]:
        client = _client(ctx)
        workspace = client.attach_workspace(p.case_id, p.path, p.name)
        result: dict[str, Any] = {"workspace": workspace}
        if p.index:
            job = client.index_workspace(workspace["id"])
            result["index_job"] = job
            if p.wait:
                result["index_result"] = client.wait_job(job["job_id"], p.timeout_seconds)
        return {"data": result}

    registry.register(ToolSpec(
        name="debug_attach_workspace",
        description="Attach a same-machine source workspace read-only and optionally index Code/Commit Graph. Requires write confirmation.",
        input_schema=AttachWorkspaceInput,
        output_schema=ToolResult,
        permission=ToolPermission.WRITE,
        allowed_roles=WRITE_ROLES,
        handler=attach,
        timeout_seconds=1800,
        idempotent=False,
    ))
    registry.register(ToolSpec(
        name="debug_code_context",
        description="Search an indexed workspace Code Graph with bounded multi-hop expansion.",
        input_schema=CodeContextInput,
        output_schema=ToolResult,
        handler=lambda ctx, p: {"data": _client(ctx).code_context(
            p.repository_id, p.model_dump(exclude={"repository_id"})
        )},
    ))

    def diagnose(ctx: ToolContext, p: DiagnoseInput) -> dict[str, Any]:
        client = _client(ctx)
        job = client.diagnose(p.case_id)
        result: dict[str, Any] = {"job": job}
        if p.wait:
            result["job_result"] = client.wait_job(job["id"], p.timeout_seconds)
            analysis = client.latest_analysis(p.case_id)
            if analysis:
                try:
                    payload = json.loads(analysis.get("result_json") or "{}")
                except (TypeError, json.JSONDecodeError):
                    payload = {}
                result["analysis"] = {
                    "id": analysis.get("id"),
                    "status": analysis.get("status"),
                    "provider": analysis.get("provider"),
                    "model": analysis.get("model"),
                    "prompt_version": analysis.get("prompt_version"),
                    "analysis_engine": payload.get("analysis_engine"),
                    "summary": payload.get("summary"),
                    "hypotheses": list(payload.get("hypotheses") or [])[:5],
                    "warnings": list(payload.get("warnings") or [])[:10],
                }
        return {"data": result}

    registry.register(ToolSpec(
        name="debug_diagnose",
        description=(
            "Run platform diagnosis. In External Agent Mode this remains deterministic/evidence-only "
            "and does not invoke a second Chat LLM. Requires write confirmation because it persists an analysis run."
        ),
        input_schema=DiagnoseInput,
        output_schema=ToolResult,
        permission=ToolPermission.WRITE,
        allowed_roles=WRITE_ROLES,
        handler=diagnose,
        timeout_seconds=1800,
        idempotent=False,
    ))
    registry.register(ToolSpec(
        name="debug_generate_report",
        description=(
            "Generate and persist an HTML/PDF/DOCX report from the latest analysis. "
            "Requires write confirmation."
        ),
        input_schema=GenerateReportInput,
        output_schema=ToolResult,
        permission=ToolPermission.WRITE,
        allowed_roles=WRITE_ROLES,
        handler=lambda ctx, p: {"data": _client(ctx).generate_report(p.case_id, p.format)},
        timeout_seconds=120,
        idempotent=False,
    ))

    def open_ui(ctx: ToolContext, p: OpenUiInput) -> dict[str, Any]:
        client = _client(ctx)
        if p.open_browser:
            return {"data": client.open_ui(p.case_id)}
        return {"data": {"url": client.ui_url(p.case_id), "opened": False}}

    registry.register(ToolSpec(
        name="debug_open_ui",
        description="Open or return the optional localhost Web UI URL for visual logs, graphs, trace, settings and reports.",
        input_schema=OpenUiInput,
        output_schema=ToolResult,
        handler=open_ui,
    ))
    return registry


def invoke_agent_tool(
    registry: ToolRegistry,
    name: str,
    arguments: dict[str, Any],
    *,
    role: str = "ENGINEER",
    runtime_url: str | None = None,
    api_key: str | None = None,
) -> ToolResult:
    spec = registry.get(name, role=role)
    payload = spec.input_schema.model_validate(arguments)
    confirm_write = bool(getattr(payload, "confirm_write", False))
    approved = frozenset({name}) if confirm_write else frozenset()
    if spec.permission == ToolPermission.WRITE and name not in approved:
        raise ToolNotAllowedError(
            f"{name} is a write-capable tool and requires confirm_write=true"
        )
    context = ToolContext(
        role=role,
        case_id=getattr(payload, "case_id", None),
        approved_tools=approved,
        metadata={"runtime_url": runtime_url, "api_key": api_key},
    )
    result = spec.handler(context, payload)
    return result if isinstance(result, ToolResult) else ToolResult.model_validate(result)
