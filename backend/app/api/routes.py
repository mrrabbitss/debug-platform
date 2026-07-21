from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id
from app.models import (
    AnalysisRun, Artifact, Case, CodeSymbol, Job, KnowledgeDocument, LogEvent,
    Repository, Report,
)
from app.schemas import (
    AnalysisOut, ArtifactOut, CaseCreate, CaseOut, CaseUpdate, ChatRequest, ChatResponse,
    JobOut, KnowledgeCreate, KnowledgeOut, PatchRequest, StaticAnalysisRequest,
)
from app.services.archive import UnsafeArchiveError, extract_archive
from app.services.code_index import index_repository_job
from app.services.diagnosis import analyze_case_job, chat_about_case
from app.services.jobs import job_runner
from app.services.knowledge import index_document
from app.services.llm import get_llm_provider
from app.services.parse_service import parse_artifact_job
from app.services.report import generate_docx, generate_html_file, generate_pdf, render_html
from app.services.static_tools import static_analysis_job
from app.services.storage import storage
from app.services.text_files import read_text_file

router = APIRouter()
Db = Annotated[Session, Depends(get_db)]


@router.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "environment": settings.app_env,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model or "rule-engine",
    }


@router.post("/cases", response_model=CaseOut)
def create_case(payload: CaseCreate, db: Db) -> Case:
    case = Case(id=new_id("CASE"), **payload.model_dump())
    db.add(case)
    db.commit()
    db.refresh(case)
    return case


@router.get("/cases", response_model=list[CaseOut])
def list_cases(db: Db, limit: int = Query(default=100, ge=1, le=500)) -> list[Case]:
    return list(db.scalars(select(Case).order_by(Case.created_at.desc()).limit(limit)).all())


@router.get("/cases/{case_id}", response_model=CaseOut)
def get_case(case_id: str, db: Db) -> Case:
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case not found")
    return case


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
    db.delete(case)
    db.commit()
    return {"deleted": case_id}


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
    try:
        path, size, digest = await storage.save_upload(file, artifact_id)
    except ValueError as exc:
        raise HTTPException(413, str(exc)) from exc
    artifact = Artifact(
        id=artifact_id, case_id=case_id, kind=kind, original_name=file.filename or path.name,
        stored_path=str(path), sha256=digest, size_bytes=size, status="UPLOADED",
    )
    db.add(artifact)
    case.status = "UPLOADED"
    db.commit()
    db.refresh(artifact)
    return artifact


@router.get("/cases/{case_id}/artifacts", response_model=list[ArtifactOut])
def list_artifacts(case_id: str, db: Db) -> list[Artifact]:
    return list(db.scalars(select(Artifact).where(Artifact.case_id == case_id).order_by(Artifact.created_at.desc())).all())


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
    query = select(LogEvent).where(LogEvent.case_id == case_id)
    if level:
        query = query.where(LogEvent.level == level.upper())
    if module:
        query = query.where(LogEvent.module == module.upper())
    if component:
        query = query.where(LogEvent.component.ilike(f"%{component}%"))
    if search:
        query = query.where((LogEvent.message.ilike(f"%{search}%")) | (LogEvent.event_code.ilike(f"%{search}%")))
    rows = db.scalars(query.order_by(LogEvent.timestamp_normalized.asc().nullslast(), LogEvent.line_start.asc()).offset(offset).limit(limit)).all()
    return [
        {
            "id": row.id, "source_file": row.source_file, "line_start": row.line_start, "line_end": row.line_end,
            "timestamp_raw": row.timestamp_raw, "timestamp_normalized": row.timestamp_normalized,
            "level": row.level, "module": row.module, "component": row.component,
            "event_code": row.event_code, "message": row.message, "raw_text": row.raw_text,
            "entities": json_loads(row.entities_json, {}), "confidence": row.confidence,
        }
        for row in rows
    ]


@router.get("/cases/{case_id}/timeline")
def timeline(case_id: str, db: Db, limit: int = Query(default=1000, ge=1, le=5000)) -> dict:
    rows = db.scalars(
        select(LogEvent).where(LogEvent.case_id == case_id)
        .order_by(LogEvent.timestamp_normalized.asc().nullslast(), LogEvent.source_file.asc(), LogEvent.line_start.asc())
        .limit(limit)
    ).all()
    module_counts = dict(db.execute(
        select(LogEvent.module, func.count(LogEvent.id)).where(LogEvent.case_id == case_id).group_by(LogEvent.module)
    ).all())
    return {
        "items": [
            {
                "id": row.id, "time": row.timestamp_normalized or row.timestamp_raw,
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
) -> str:
    artifact = db.get(Artifact, artifact_id)
    if not artifact:
        raise HTTPException(404, "Artifact not found")
    meta = json_loads(artifact.metadata_json, {})
    root_text = meta.get("extract_root")
    if not root_text:
        raise HTTPException(409, "Artifact has not been parsed")
    root = Path(root_text).resolve()
    target = (root / path).resolve()
    if root not in target.parents and target != root:
        raise HTTPException(400, "Unsafe path")
    if not target.is_file():
        raise HTTPException(404, "File not found")
    text = read_text_file(target)
    if text is None:
        raise HTTPException(415, "File is binary or exceeds the text parsing limit")
    lines = text.splitlines()
    return "\n".join(lines[start_line - 1:start_line - 1 + line_count])


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
    if not report or not Path(report.stored_path).is_file():
        raise HTTPException(404, "Report not found")
    media = {"html": "text/html", "pdf": "application/pdf", "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    return FileResponse(report.stored_path, media_type=media.get(report.format, "application/octet-stream"), filename=Path(report.stored_path).name)


@router.post("/knowledge", response_model=KnowledgeOut)
def create_knowledge(payload: KnowledgeCreate, db: Db) -> KnowledgeDocument:
    document = KnowledgeDocument(
        id=new_id("DOC"), title=payload.title, source_type=payload.source_type,
        device_type=payload.device_type, device_model=payload.device_model,
        firmware_range=payload.firmware_range, module=payload.module,
        trust_level=payload.trust_level, confidentiality=payload.confidentiality,
        content=payload.content, metadata_json=json_dumps(payload.metadata),
    )
    db.add(document)
    db.flush()
    index_document(db, document)
    db.refresh(document)
    return document


@router.post("/knowledge/upload", response_model=KnowledgeOut)
async def upload_knowledge(
    db: Db,
    file: UploadFile = File(...),
    source_type: str = Form(default="document"),
    device_type: str | None = Form(default=None),
    module: str | None = Form(default=None),
    trust_level: str = Form(default="MEDIUM"),
) -> KnowledgeDocument:
    raw = await file.read(get_settings().max_single_file_bytes + 1)
    if len(raw) > get_settings().max_single_file_bytes:
        raise HTTPException(413, "Knowledge file is too large")
    content = raw.decode("utf-8", errors="replace")
    document = KnowledgeDocument(
        id=new_id("DOC"), title=file.filename or "Knowledge document", source_type=source_type,
        device_type=device_type, module=module, trust_level=trust_level,
        content=content, metadata_json=json_dumps({"original_name": file.filename}),
    )
    db.add(document)
    db.flush()
    index_document(db, document)
    db.refresh(document)
    return document


@router.get("/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(db: Db, limit: int = Query(default=200, ge=1, le=1000)) -> list[KnowledgeDocument]:
    return list(db.scalars(select(KnowledgeDocument).order_by(KnowledgeDocument.created_at.desc()).limit(limit)).all())


@router.delete("/knowledge/{document_id}")
def delete_knowledge(document_id: str, db: Db) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    db.delete(document)
    db.commit()
    return {"deleted": document_id}


@router.post("/cases/{case_id}/repositories", response_model=dict)
async def upload_repository(case_id: str, db: Db, file: UploadFile = File(...)) -> dict:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    artifact_id = new_id("ART")
    repository_id = new_id("REPO")
    try:
        path, size, digest = await storage.save_upload(file, artifact_id)
        destination = storage.repository_dir(repository_id)
        manifest = extract_archive(path, destination)
    except (ValueError, UnsafeArchiveError) as exc:
        raise HTTPException(400, str(exc)) from exc
    artifact = Artifact(
        id=artifact_id, case_id=case_id, kind="source_repository", original_name=file.filename or path.name,
        stored_path=str(path), sha256=digest, size_bytes=size, status="EXTRACTED",
        metadata_json=json_dumps({"manifest_file_count": len(manifest.files), "extracted_bytes": manifest.total_bytes}),
    )
    repository = Repository(
        id=repository_id, case_id=case_id, artifact_id=artifact_id,
        name=Path(file.filename or "repository").stem, root_path=str(destination), status="UPLOADED",
    )
    db.add_all([artifact, repository])
    db.commit()
    return {"repository_id": repository_id, "artifact_id": artifact_id, "files": len(manifest.files)}


@router.get("/cases/{case_id}/repositories")
def list_repositories(case_id: str, db: Db) -> list[dict]:
    rows = db.scalars(select(Repository).where(Repository.case_id == case_id).order_by(Repository.created_at.desc())).all()
    return [{"id": row.id, "name": row.name, "status": row.status, "branch": row.branch, "commit_hash": row.commit_hash, "created_at": row.created_at} for row in rows]


@router.post("/repositories/{repository_id}/index", response_model=JobOut)
def index_repository(repository_id: str, db: Db) -> Job:
    if not db.get(Repository, repository_id):
        raise HTTPException(404, "Repository not found")
    return job_runner.submit(db, "index_repository", index_repository_job, repository_id, input_data={"repository_id": repository_id})


@router.get("/repositories/{repository_id}/symbols")
def list_symbols(
    repository_id: str,
    db: Db,
    search: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=300, ge=1, le=2000),
) -> list[dict]:
    query = select(CodeSymbol).where(CodeSymbol.repository_id == repository_id)
    if search:
        query = query.where((CodeSymbol.name.ilike(f"%{search}%")) | (CodeSymbol.file_path.ilike(f"%{search}%")))
    if kind:
        query = query.where(CodeSymbol.kind == kind)
    rows = db.scalars(query.order_by(CodeSymbol.file_path, CodeSymbol.line_start).limit(limit)).all()
    return [
        {
            "id": row.id, "kind": row.kind, "name": row.name, "file_path": row.file_path,
            "line_start": row.line_start, "line_end": row.line_end, "signature": row.signature,
            "module": row.module, "calls": json_loads(row.calls_json, []), "code": row.code,
        }
        for row in rows
    ]


@router.post("/repositories/{repository_id}/static-analysis", response_model=JobOut)
def run_static_analysis(repository_id: str, payload: StaticAnalysisRequest, db: Db) -> Job:
    if not db.get(Repository, repository_id):
        raise HTTPException(404, "Repository not found")
    return job_runner.submit(
        db, "static_analysis", static_analysis_job, repository_id, payload.tools,
        input_data={"repository_id": repository_id, "tools": payload.tools},
    )


@router.post("/cases/{case_id}/patch-suggestions")
async def patch_suggestion(case_id: str, payload: PatchRequest, db: Db) -> dict:
    case = db.get(Case, case_id)
    symbol = db.get(CodeSymbol, payload.symbol_id)
    if not case or not symbol:
        raise HTTPException(404, "Case or symbol not found")
    latest = db.scalars(
        select(AnalysisRun).where(AnalysisRun.case_id == case_id, AnalysisRun.status == "COMPLETED")
        .order_by(AnalysisRun.created_at.desc()).limit(1)
    ).first()
    diagnosis = json_loads(latest.result_json, {}) if latest else {}
    provider = get_llm_provider()
    if get_settings().llm_provider == "mock":
        return {
            "status": "NEED_LLM_CONFIGURATION",
            "message": "配置 Qwen/GLM API 后可生成候选 unified diff。当前仅返回人工审查模板。",
            "symbol": {"file": symbol.file_path, "name": symbol.name, "line_start": symbol.line_start},
            "review_checklist": ["确认日志证据与该函数存在数据流或调用关系", "采用最小修改", "重新编译并运行相关测试", "不得直接覆盖原文件"],
        }
    prompt = {
        "instruction": payload.instruction,
        "case": {"title": case.title, "description": case.description, "device": case.device_type},
        "diagnosis": diagnosis,
        "symbol": {"file_path": symbol.file_path, "line_start": symbol.line_start, "line_end": symbol.line_end, "code": symbol.code},
        "output": "只输出 unified diff；不得修改无关文件；不得调用不存在的 API；无法安全修复时说明 NEED_HUMAN_REVIEW。",
    }
    text = await provider.generate_text("你是 C/C++ 网络设备代码审查工程师，生成最小、可审查、未自动应用的候选补丁。", json_dumps(prompt))
    return {"status": "SUGGESTED", "patch": text, "auto_applied": False}


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Db) -> Job:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@router.get("/system/model")
def model_config() -> dict:
    settings = get_settings()
    return {
        "provider": settings.llm_provider,
        "base_url_configured": bool(settings.llm_base_url),
        "api_key_configured": bool(settings.llm_api_key),
        "model": settings.llm_model or "rule-engine",
        "compatible_providers": ["Qwen Model Studio OpenAI-compatible API", "GLM OpenAI-compatible API", "internal OpenAI-compatible gateway"],
    }


@router.post("/system/model/test")
async def test_model() -> dict:
    provider = get_llm_provider()
    text = await provider.generate_text("你是连接测试助手。", "仅回复 MODEL_CONNECTION_OK")
    return {"ok": "MODEL_CONNECTION_OK" in text or get_settings().llm_provider == "mock", "response": text[:500]}
