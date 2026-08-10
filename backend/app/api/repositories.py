from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id
from app.models import AnalysisRun, Artifact, Case, CodeSymbol, Job, Repository
from app.schemas import JobOut, PatchRequest, RepositoryImportOut, StaticAnalysisRequest
from app.services.code_graph import code_graph_snapshot, search_code_graph
from app.services.code_index import index_repository_job
from app.services.commit_graph import commit_graph_snapshot
from app.services.import_jobs import import_repository_job
from app.services.jobs import job_runner
from app.services.llm import get_llm_provider
from app.services.static_tools import static_analysis_job
from app.services.storage import storage


router = APIRouter()
Db = Annotated[Session, Depends(get_db)]

job_runner.register(
    "import_repository",
    import_repository_job,
    ("repository_id",),
    cancellable=True,
)
job_runner.register("index_repository", index_repository_job, ("repository_id",))
job_runner.register(
    "static_analysis",
    static_analysis_job,
    ("repository_id", "tools"),
    cancellable=True,
)


@router.post(
    "/cases/{case_id}/repositories",
    response_model=RepositoryImportOut,
    status_code=202,
)
async def upload_repository(case_id: str, db: Db, file: UploadFile = File(...)) -> dict:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    artifact_id = new_id("ART")
    repository_id = new_id("REPO")
    uploaded_name = Path((file.filename or "repository.zip").replace("\\", "/")).name
    try:
        path, size, digest = await storage.save_upload(file, artifact_id, target_name=uploaded_name)
    except ValueError as exc:
        storage.remove_artifact(artifact_id)
        raise HTTPException(413, str(exc)) from exc
    import_format = (
        "git_bundle"
        if uploaded_name.lower().endswith(".bundle")
        else "archive"
    )
    destination = storage.repository_dir(repository_id)
    artifact = Artifact(
        id=artifact_id, case_id=case_id, kind="source_repository", original_name=uploaded_name[:512],
        stored_path=storage.storage_key(path), sha256=digest, size_bytes=size, status="UPLOADED",
        metadata_json=json_dumps({
            "import_format": import_format,
            "import_status": "QUEUED",
        }),
    )
    repository = Repository(
        id=repository_id, case_id=case_id, artifact_id=artifact_id,
        name=Path(uploaded_name).stem[:255] or "repository",
        root_path=storage.storage_key(destination),
        branch=None,
        commit_hash=None,
        status="IMPORT_QUEUED",
        graph_status="NOT_INDEXED",
        commit_graph_status="NOT_INDEXED",
        index_metadata_json=json_dumps({
            "import_format": import_format,
            "import_status": "QUEUED",
        }),
    )
    db.add(artifact)
    db.flush()
    db.add(repository)
    db.commit()
    job = job_runner.submit(
        db,
        "import_repository",
        import_repository_job,
        repository_id,
        input_data={
            "case_id": case_id,
            "repository_id": repository_id,
            "artifact_id": artifact_id,
        },
    )
    return {
        "repository_id": repository_id,
        "artifact_id": artifact_id,
        "job": job,
    }


@router.get("/cases/{case_id}/repositories")
def list_repositories(case_id: str, db: Db) -> list[dict]:
    rows = db.scalars(select(Repository).where(Repository.case_id == case_id).order_by(Repository.created_at.desc())).all()
    return [
        {
            "id": row.id,
            "name": row.name,
            "status": row.status,
            "graph_status": row.graph_status,
            "active_graph_generation_id": row.active_graph_generation_id,
            "commit_graph_status": row.commit_graph_status,
            "branch": row.branch,
            "commit_hash": row.commit_hash,
            "index_metadata": json_loads(row.index_metadata_json, {}),
            "indexed_at": row.indexed_at,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/repositories/{repository_id}/index", response_model=JobOut)
def index_repository(repository_id: str, db: Db) -> Job:
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(404, "Repository not found")
    if repository.status not in {"UPLOADED", "INDEXED", "INDEX_FAILED"}:
        raise HTTPException(
            409,
            f"Repository import is not ready: {repository.status}",
        )
    return job_runner.submit(db, "index_repository", index_repository_job, repository_id, input_data={"repository_id": repository_id})


@router.get("/repositories/{repository_id}/symbols")
def list_symbols(
    repository_id: str,
    db: Db,
    search: str | None = None,
    kind: str | None = None,
    limit: int = Query(default=300, ge=1, le=2000),
) -> list[dict]:
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(404, "Repository not found")
    if not repository.active_graph_generation_id:
        return []
    query = select(CodeSymbol).where(
        CodeSymbol.repository_id == repository_id,
        CodeSymbol.generation_id
        == repository.active_graph_generation_id,
    )
    if search:
        query = query.where((CodeSymbol.name.ilike(f"%{search}%")) | (CodeSymbol.file_path.ilike(f"%{search}%")))
    if kind:
        query = query.where(CodeSymbol.kind == kind)
    rows = db.scalars(query.order_by(CodeSymbol.file_path, CodeSymbol.line_start).limit(limit)).all()
    return [
        {
            "id": row.logical_id or row.id,
            "revision_id": row.id,
            "kind": row.kind, "name": row.name, "file_path": row.file_path,
            "line_start": row.line_start, "line_end": row.line_end, "signature": row.signature,
            "module": row.module, "calls": json_loads(row.calls_json, []),
            "metadata": json_loads(row.metadata_json, {}), "code": row.code,
        }
        for row in rows
    ]


@router.get("/repositories/{repository_id}/graph")
def get_repository_graph(
    repository_id: str,
    db: Db,
    query: str | None = None,
    relation_type: str | None = Query(
        default=None,
        pattern="^(CALLS|REFERENCES|INHERITS|IMPLEMENTS)$",
    ),
    limit: int = Query(default=300, ge=1, le=2000),
) -> dict:
    try:
        return code_graph_snapshot(
            db,
            repository_id,
            query=query,
            relation_type=relation_type,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/repositories/{repository_id}/graph/search")
def search_repository_graph(
    repository_id: str,
    db: Db,
    query: str = Query(min_length=2, max_length=10000),
    max_hops: int = Query(default=2, ge=0, le=3),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    try:
        return search_code_graph(
            db,
            repository_id,
            query,
            max_hops=max_hops,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.get("/repositories/{repository_id}/commit-graph")
def get_repository_commit_graph(
    repository_id: str,
    db: Db,
    query: str | None = Query(default=None, max_length=10000),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    try:
        return commit_graph_snapshot(
            db,
            repository_id,
            query=query,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/repositories/{repository_id}/static-analysis", response_model=JobOut)
def run_static_analysis(repository_id: str, payload: StaticAnalysisRequest, db: Db) -> Job:
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(404, "Repository not found")
    if repository.status not in {"UPLOADED", "INDEXED", "INDEX_FAILED"}:
        raise HTTPException(
            409,
            f"Repository import is not ready: {repository.status}",
        )
    return job_runner.submit(
        db, "static_analysis", static_analysis_job, repository_id, payload.tools,
        input_data={"repository_id": repository_id, "tools": payload.tools},
    )


@router.post("/cases/{case_id}/patch-suggestions")
async def patch_suggestion(case_id: str, payload: PatchRequest, db: Db) -> dict:
    case = db.get(Case, case_id)
    symbol = db.scalars(
        select(CodeSymbol)
        .join(Repository, CodeSymbol.repository_id == Repository.id)
        .where(
            Repository.case_id == case_id,
            CodeSymbol.generation_id
            == Repository.active_graph_generation_id,
            or_(
                CodeSymbol.logical_id == payload.symbol_id,
                CodeSymbol.id == payload.symbol_id,
            ),
        )
        .limit(1)
    ).first()
    repository = db.get(Repository, symbol.repository_id) if symbol else None
    if (
        not case
        or not symbol
        or not repository
        or repository.case_id != case_id
        or symbol.generation_id != repository.active_graph_generation_id
    ):
        raise HTTPException(404, "Case or symbol not found")
    latest = db.scalars(
        select(AnalysisRun).where(AnalysisRun.case_id == case_id, AnalysisRun.status == "COMPLETED")
        .order_by(AnalysisRun.created_at.desc()).limit(1)
    ).first()
    diagnosis = json_loads(latest.result_json, {}) if latest else {}
    if get_settings().agent_mode == "external":
        return {
            "status": "EXTERNAL_AGENT_REQUIRED",
            "message": (
                "External Agent Mode does not invoke the platform Chat LLM for code edits. "
                "Use Claude Code/OpenCode/CodeArts native workspace tools after reviewing evidence."
            ),
            "symbol": {
                "symbol_id": symbol.logical_id or symbol.id,
                "file": symbol.file_path,
                "name": symbol.name,
                "line_start": symbol.line_start,
                "line_end": symbol.line_end,
            },
            "review_checklist": [
                "Confirm log/knowledge evidence reaches this symbol through a data-flow or graph path",
                "Read the current workspace file before editing",
                "Make the smallest change consistent with the evidence",
                "Run targeted tests/static analysis and inspect git diff",
            ],
            "auto_applied": False,
        }
    provider = get_llm_provider()
    if provider.is_mock:
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
    text = await provider.generate_text(
        "你是 C/C++ 网络设备代码审查工程师，生成最小、可审查、未自动应用的候选补丁。",
        json_dumps(prompt),
        purpose="patch_suggestion",
    )
    return {"status": "SUGGESTED", "patch": text, "auto_applied": False}
