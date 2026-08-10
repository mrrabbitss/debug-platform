from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.agent_runs import router as agent_runs_router
from app.api.agent_runtime import router as agent_runtime_router
from app.api.jobs import router as jobs_router
from app.api.knowledge import router as knowledge_router
from app.api.knowledge_governance import router as knowledge_governance_router
from app.api.knowledge_graph import router as knowledge_graph_router
from app.api.knowledge_curation import router as knowledge_curation_router
from app.api.retrieval_evaluation import router as retrieval_evaluation_router
from app.api.repositories import (
    patch_suggestion as patch_suggestion,
    router as repositories_router,
    upload_repository as upload_repository,
)
from app.api.system import router as system_router
from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id
from app.models import (
    AgentMemory, AnalysisRun, Artifact, Case, CaseMember,
    Job, LogEvent, Repository, Report, UserAccount,
)
from app.schemas import (
    AgenticSearchRequest, AnalysisOut, ArtifactOut, CaseCreate,
    CaseMemberUpdate, CaseOut, CaseUpdate, ChatRequest, ChatResponse, JobOut,
)
from app.services.access_control import accessible_case_clause, case_permission
from app.services.agentic_search import agentic_search
from app.services.diagnosis import analyze_case_job, chat_about_case
from app.services.events import active_log_event_clause
from app.services.jobs import job_runner
from app.services.memory import (
    memory_to_dict,
    search_memories,
)
from app.services.parse_service import parse_artifact_job
from app.services.report import generate_docx, generate_html_file, generate_pdf, render_html
from app.services.storage import normalize_debug_log_filename, storage
from app.services.text_files import read_text_range, search_text_lines

router = APIRouter()
router.include_router(agent_runs_router)
router.include_router(agent_runtime_router)
router.include_router(jobs_router)
router.include_router(knowledge_router)
router.include_router(knowledge_governance_router)
router.include_router(knowledge_graph_router)
router.include_router(knowledge_curation_router)
router.include_router(retrieval_evaluation_router)
router.include_router(repositories_router)
router.include_router(system_router)
Db = Annotated[Session, Depends(get_db)]

job_runner.register("parse_artifact", parse_artifact_job, ("case_id", "artifact_id"), cancellable=True)
job_runner.register("analyze_case", analyze_case_job, ("case_id",), cancellable=True)


@router.post("/cases", response_model=CaseOut)
def create_case(payload: CaseCreate, request: Request, db: Db) -> Case:
    principal = getattr(request.state, "principal", {})
    owner_id = principal.get("id") if principal.get("type") == "user_token" else None
    case = Case(id=new_id("CASE"), owner_id=owner_id, **payload.model_dump())
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


def _require_case_owner(request: Request, db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    principal = getattr(request.state, "principal", {})
    if principal.get("role") != "ADMIN" and case_permission(db, case_id, principal) != "OWNER":
        raise HTTPException(403, "Only an administrator or case owner may manage members")
    return case


@router.get("/cases/{case_id}/members")
def list_case_members(case_id: str, request: Request, db: Db) -> list[dict]:
    _require_case_owner(request, db, case_id)
    rows = db.execute(
        select(CaseMember, UserAccount)
        .join(UserAccount, UserAccount.id == CaseMember.user_id)
        .where(CaseMember.case_id == case_id)
        .order_by(UserAccount.username)
    ).all()
    return [
        {
            "id": membership.id,
            "case_id": membership.case_id,
            "user_id": user.id,
            "username": user.username,
            "display_name": user.display_name,
            "permission": membership.permission,
        }
        for membership, user in rows
    ]


@router.put("/cases/{case_id}/members/{user_id}")
def set_case_member(
    case_id: str,
    user_id: str,
    payload: CaseMemberUpdate,
    request: Request,
    db: Db,
) -> dict:
    case = _require_case_owner(request, db, case_id)
    user = db.get(UserAccount, user_id)
    if not user or not user.active:
        raise HTTPException(404, "Active user not found")
    if case.owner_id == user_id:
        raise HTTPException(409, "The case owner already has owner permission")
    membership = db.scalar(select(CaseMember).where(
        CaseMember.case_id == case_id,
        CaseMember.user_id == user_id,
    ))
    if membership:
        membership.permission = payload.permission
    else:
        membership = CaseMember(
            id=new_id("MEM"),
            case_id=case_id,
            user_id=user_id,
            permission=payload.permission,
        )
        db.add(membership)
    db.commit()
    return {
        "id": membership.id,
        "case_id": case_id,
        "user_id": user_id,
        "permission": membership.permission,
    }


@router.delete("/cases/{case_id}/members/{user_id}")
def remove_case_member(case_id: str, user_id: str, request: Request, db: Db) -> dict:
    _require_case_owner(request, db, case_id)
    membership = db.scalar(select(CaseMember).where(
        CaseMember.case_id == case_id,
        CaseMember.user_id == user_id,
    ))
    if not membership:
        raise HTTPException(404, "Case member not found")
    db.delete(membership)
    db.commit()
    return {"deleted": membership.id}


@router.get("/cases", response_model=list[CaseOut])
def list_cases(request: Request, db: Db, limit: int = Query(default=100, ge=1, le=500)) -> list[Case]:
    query = select(Case)
    principal = getattr(request.state, "principal", {})
    if principal.get("type") == "user_token" and principal.get("role") != "ADMIN":
        query = query.where(accessible_case_clause(str(principal["id"])))
    return list(db.scalars(query.order_by(Case.created_at.desc()).limit(limit)).all())


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str, db: Db) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


@router.get("/cases/{case_id}/access")
def get_case_access(case_id: str, request: Request, db: Db) -> dict:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    principal = getattr(request.state, "principal", {})
    return {
        "case_id": case_id,
        "role": principal.get("role", "VIEWER"),
        "permission": case_permission(db, case_id, principal),
    }


@router.patch("/cases/{case_id}", response_model=CaseOut)
def update_case(case_id: str, payload: CaseUpdate, db: Db) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(case, key, value)
    db.commit()
    db.refresh(case)
    return case


@router.delete("/cases/{case_id}")
def delete_case(case_id: str, db: Db) -> dict:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    artifact_ids = list(db.scalars(select(Artifact.id).where(Artifact.case_id == case_id)).all())
    repository_ids = list(db.scalars(select(Repository.id).where(Repository.case_id == case_id)).all())
    db.delete(case)
    db.commit()
    cleanup_errors = storage.cleanup_case(artifact_ids, repository_ids, case_id)
    return {"deleted": case_id, "storage_cleanup_errors": cleanup_errors}


@router.post("/cases/{case_id}/artifacts", response_model=ArtifactOut)
async def upload_artifact(
    case_id: str,
    db: Db,
    file: UploadFile = File(...),
    kind: str = Form(default="debug_log"),
) -> Artifact:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    artifact_id = new_id("ART")
    raw_uploaded_name = file.filename or "collectDebuginfo"
    if kind == "debug_log":
        uploaded_name, stored_name = normalize_debug_log_filename(raw_uploaded_name)
    else:
        uploaded_name = Path(raw_uploaded_name.replace("\\", "/")).name
        stored_name = uploaded_name
    try:
        path, size, digest = await storage.save_upload(file, artifact_id, target_name=stored_name)
    except ValueError as exc:
        storage.remove_artifact(artifact_id)
        raise HTTPException(413, str(exc)) from exc
    artifact = Artifact(
        id=artifact_id, case_id=case_id, kind=kind, original_name=stored_name,
        stored_path=storage.storage_key(path), sha256=digest, size_bytes=size, status="UPLOADED",
        metadata_json=json_dumps({
            "uploaded_original_name": uploaded_name,
            "filename_normalized": uploaded_name != stored_name,
        }),
    )
    db.add(artifact)
    case.status = "UPLOADED"
    db.commit()
    db.refresh(artifact)
    return artifact


@router.get("/cases/{case_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(case_id: str, db: Db) -> list[Artifact]:
    return list(db.scalars(select(Artifact).where(Artifact.case_id == case_id).order_by(Artifact.created_at.desc())).all())


@router.delete("/cases/{case_id}/artifacts/{artifact_id}")
def delete_artifact(case_id: str, artifact_id: str, db: Db) -> dict:
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.case_id != case_id:
        raise HTTPException(404, "Artifact not found")
    repository_ids = list(db.scalars(
        select(Repository.id).where(Repository.artifact_id == artifact_id)
    ).all())
    db.delete(artifact)
    db.commit()
    cleanup_errors: list[str] = []
    try:
        storage.remove_artifact(artifact_id)
    except (OSError, ValueError) as exc:
        cleanup_errors.append(str(exc))
    for repository_id in repository_ids:
        try:
            storage.remove_repository(repository_id)
        except (OSError, ValueError) as exc:
            cleanup_errors.append(str(exc))
    return {"deleted": artifact_id, "storage_cleanup_errors": cleanup_errors}


@router.post("/cases/{case_id}/artifacts/{artifact_id}/parse", response_model=JobOut)
def parse_artifact(case_id: str, artifact_id: str, db: Db) -> Job:
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.case_id != case_id:
        raise HTTPException(404, "Artifact not found")
    return job_runner.submit(db, "parse_artifact", parse_artifact_job, case_id, artifact_id, input_data={"case_id": case_id, "artifact_id": artifact_id})


@router.get("/cases/{case_id}/events", response_model=list[dict])
def list_events(
    case_id: str,
    db: Db,
    level: str | None = None,
    module: str | None = None,
    component: str | None = None,
    search: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> list[dict]:
    query = _filtered_event_query(case_id, level, module, component, search)
    rows = db.scalars(query.order_by(LogEvent.timestamp_normalized.asc().nullslast(), LogEvent.line_start.asc()).offset(offset).limit(limit)).all()
    return [
        {
            "id": row.id, "artifact_id": row.artifact_id,
            "source_file": row.source_file, "line_start": row.line_start, "line_end": row.line_end,
            "timestamp_raw": row.timestamp_raw, "timestamp_normalized": row.timestamp_normalized,
            "level": row.level, "module": row.module, "component": row.component,
            "event_code": row.event_code, "message": row.message, "raw_text": row.raw_text,
            "entities": json_loads(row.entities_json, {}), "confidence": row.confidence,
        }
        for row in rows
    ]


def _filtered_event_query(
    case_id: str,
    level: str | None = None,
    module: str | None = None,
    component: str | None = None,
    search: str | None = None,
):
    query = (
        select(LogEvent)
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
    )
    if level:
        query = query.where(LogEvent.level == level.upper())
    if module:
        query = query.where(LogEvent.module == module.upper())
    if component:
        query = query.where(LogEvent.component.ilike(f"%{component}%"))
    if search:
        query = query.where((LogEvent.message.ilike(f"%{search}%")) | (LogEvent.event_code.ilike(f"%{search}%")))
    return query


@router.get("/cases/{case_id}/events/stats")
def event_stats(
    case_id: str,
    db: Db,
    level: str | None = None,
    module: str | None = None,
    component: str | None = None,
    search: str | None = None,
) -> dict:
    filtered = _filtered_event_query(case_id, level, module, component, search).subquery()
    filtered_total = int(db.scalar(select(func.count()).select_from(filtered)) or 0)
    total = int(db.scalar(
        select(func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
    ) or 0)
    level_counts = dict(db.execute(
        select(LogEvent.level, func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .group_by(LogEvent.level)
    ).all())
    module_counts = dict(db.execute(
        select(LogEvent.module, func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .group_by(LogEvent.module)
    ).all())
    return {
        "total": total,
        "filtered_total": filtered_total,
        "level_counts": level_counts,
        "module_counts": module_counts,
    }


@router.get("/cases/{case_id}/timeline")
def timeline(case_id: str, db: Db, limit: int = Query(default=1000, ge=1, le=5000)) -> dict:
    rows = db.scalars(
        select(LogEvent)
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .order_by(LogEvent.timestamp_normalized.asc().nullslast(), LogEvent.source_file.asc(), LogEvent.line_start.asc())
        .limit(limit)
    ).all()
    module_counts = dict(db.execute(
        select(LogEvent.module, func.count(LogEvent.id))
        .join(Artifact, Artifact.id == LogEvent.artifact_id)
        .where(LogEvent.case_id == case_id, active_log_event_clause())
        .group_by(LogEvent.module)
    ).all())
    return {
        "items": [
            {
                "id": row.id, "artifact_id": row.artifact_id,
                "time": row.timestamp_normalized or row.timestamp_raw,
                "module": row.module, "component": row.component, "level": row.level,
                "event_code": row.event_code, "message": row.message,
                "source_file": row.source_file, "line_start": row.line_start,
            }
            for row in rows
        ],
        "module_counts": module_counts,
    }


@router.get("/artifacts/{artifact_id}/files")
def artifact_files(artifact_id: str, db: Db) -> dict:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    return json_loads(artifact.metadata_json, {})


@router.get("/artifacts/{artifact_id}/content", response_class=PlainTextResponse)
def artifact_content(
    artifact_id: str,
    db: Db,
    path: str = Query(...),
    start_line: int = Query(default=1, ge=1),
    line_count: int = Query(default=500, ge=1, le=5000),
) -> PlainTextResponse:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    meta = json_loads(artifact.metadata_json, {})
    root_text = meta.get("extract_root")
    if not root_text:
        raise HTTPException(409, "Artifact has not been parsed")
    try:
        root = storage.resolve_path(root_text)
    except ValueError as exc:
        raise HTTPException(400, "Unsafe stored path") from exc
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "Unsafe path")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    manifest_item = next(
        (item for item in meta.get("manifest", []) if item.get("path", "").replace("\\", "/") == path.replace("\\", "/")),
        {},
    )
    selected = read_text_range(
        target,
        start_line,
        line_count,
        line_index=manifest_item.get("line_index"),
        encoding_hint=manifest_item.get("encoding"),
    )
    if selected is None:
        raise HTTPException(415, "File is binary or exceeds the text parsing limit")
    total_lines = int(manifest_item.get("line_count") or 0)
    has_more = selected.has_more or bool(total_lines and start_line - 1 + selected.returned_lines < total_lines)
    return PlainTextResponse(
        selected.text,
        headers={
            "X-Start-Line": str(start_line),
            "X-Returned-Lines": str(selected.returned_lines),
            "X-Total-Lines": str(total_lines),
            "X-Has-More": "true" if has_more else "false",
            "X-Text-Encoding": selected.encoding,
        },
    )


@router.get("/artifacts/{artifact_id}/search")
def search_artifact_content(
    artifact_id: str,
    db: Db,
    path: str = Query(...),
    query: str = Query(..., min_length=1, max_length=256),
    start_line: int = Query(default=1, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    meta = json_loads(artifact.metadata_json, {})
    root_text = meta.get("extract_root")
    if not root_text:
        raise HTTPException(409, "Artifact has not been parsed")
    try:
        root = storage.resolve_path(root_text)
    except ValueError as exc:
        raise HTTPException(400, "Unsafe stored path") from exc
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "Unsafe path")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    manifest_item = next(
        (item for item in meta.get("manifest", []) if item.get("path", "").replace("\\", "/") == path.replace("\\", "/")),
        {},
    )
    result = search_text_lines(
        target,
        query,
        start_line=start_line,
        max_matches=limit,
        max_scan_lines=get_settings().text_search_max_scan_lines,
        line_index=manifest_item.get("line_index"),
        encoding_hint=manifest_item.get("encoding"),
    )
    if result is None:
        raise HTTPException(415, "File is binary or exceeds the text parsing limit")
    return {
        "query": query,
        "path": path,
        "encoding": result.encoding,
        "scanned_from_line": result.scanned_from_line,
        "scanned_to_line": result.scanned_to_line,
        "has_more": result.has_more,
        "next_start_line": result.scanned_to_line + 1 if result.has_more else None,
        "matches": [
            {"line_number": match.line_number, "text": match.text}
            for match in result.matches
        ],
    }


@router.post("/cases/{case_id}/analyses", response_model=JobOut)
def analyze_case(case_id: str, db: Db) -> Job:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    return job_runner.submit(db, "analyze_case", analyze_case_job, case_id, input_data={"case_id": case_id})


@router.get("/cases/{case_id}/analyses", response_model=list[AnalysisOut])
def list_analyses(case_id: str, db: Db) -> list[AnalysisRun]:
    return list(db.scalars(select(AnalysisRun).where(AnalysisRun.case_id == case_id).order_by(AnalysisRun.created_at.desc())).all())


@router.get("/analyses/{analysis_id}", response_model=AnalysisOut)
def get_analysis(analysis_id: str, db: Db) -> AnalysisRun:
    run = db.get(AnalysisRun, analysis_id)
    if not run:
        raise HTTPException(404, "Analysis not found")
    return run


@router.post("/cases/{case_id}/chat", response_model=ChatResponse)
async def case_chat(case_id: str, payload: ChatRequest, db: Db) -> ChatResponse:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    answer, citations = await chat_about_case(case_id, payload.question)
    return ChatResponse(answer=answer, citations=citations)


@router.post("/cases/{case_id}/agentic-search")
def run_agentic_search(
    case_id: str,
    payload: AgenticSearchRequest,
    db: Db,
) -> dict:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    try:
        return agentic_search(
            db,
            case_id=case_id,
            query=payload.query,
            top_k=payload.top_k,
            max_hops=payload.max_hops,
            requested_modules=payload.modules,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/cases/{case_id}/memories")
def list_case_memories(
    case_id: str,
    db: Db,
    memory_type: str | None = Query(
        default=None,
        pattern="^(EPISODIC|PROCEDURAL|FAILURE)$",
    ),
    search: str | None = Query(default=None, max_length=10000),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict]:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    if search:
        results = search_memories(
            db,
            search,
            case_id=case_id,
            memory_types={memory_type} if memory_type else None,
            limit=limit,
        )
        return [memory_to_dict(memory, score) for memory, score in results]
    query = select(AgentMemory).where(or_(
        AgentMemory.case_id == case_id,
        AgentMemory.case_id.is_(None),
    ))
    if memory_type:
        query = query.where(AgentMemory.memory_type == memory_type)
    memories = list(db.scalars(
        query.order_by(AgentMemory.updated_at.desc()).limit(limit)
    ).all())
    return [memory_to_dict(memory) for memory in memories]


@router.get("/cases/{case_id}/analyses/{analysis_id}/report/preview", response_class=HTMLResponse)
def preview_report(case_id: str, analysis_id: str) -> str:
    try:
        return render_html(case_id, analysis_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/cases/{case_id}/analyses/{analysis_id}/reports/{fmt}")
def generate_report(case_id: str, analysis_id: str, fmt: str, db: Db) -> dict:
    run = db.get(AnalysisRun, analysis_id)
    if not run or run.case_id != case_id:
        raise HTTPException(404, "Analysis not found")
    generators = {"html": generate_html_file, "pdf": generate_pdf, "docx": generate_docx}
    if fmt not in generators:
        raise HTTPException(400, "Supported formats: html, pdf, docx")
    report = generators[fmt](case_id, analysis_id)
    return {"report_id": report.id, "format": report.format, "version": report.version, "sha256": report.sha256}


@router.get("/reports/{report_id}/download")
def download_report(report_id: str, db: Db) -> FileResponse:
    report = db.get(Report, report_id)
    report_path = storage.resolve_path(report.stored_path) if report else None
    if not report or not report_path or not report_path.is_file():
        raise HTTPException(404, "Report not found")
    media = {"html": "text/html", "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    return FileResponse(report_path, media_type=media.get(report.format, "application/octet-stream"), filename=report_path.name)
