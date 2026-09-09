from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id
from app.models import Artifact, Case, CodeSymbol, Job, Repository
from app.schemas import JobOut, PatchRequest, RepositoryImportOut, StaticAnalysisRequest
from app.services.code_graph import code_graph_snapshot, search_code_graph
from app.services.code_index import index_repository_job
from app.services.commit_graph import commit_graph_snapshot
from app.services.import_jobs import import_repository_job
from app.services.jobs import job_runner
from app.services.patch_suggestions import (
    create_patch_suggestion_input,
    generate_patch_suggestion,
    patch_suggestion_job,
    resolve_patch_suggestion_context,
)
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
job_runner.register(
    "patch_suggestion",
    patch_suggestion_job,
    ("case_id", "actor", "snapshot", "symbol_id", "generation_id", "instruction"),
    cancellable=True,
    max_attempts=1,
    timeout_seconds=7200,
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


def _patch_principal(request: Request | None, case: Case | None) -> dict[str, str]:
    identity = getattr(getattr(request, "state", None), "principal", {}) if request else {}
    if identity:
        return identity
    if request is not None:
        raise HTTPException(401, "An authenticated identity is required")
    # Retain direct local callers of the old synchronous function. HTTP calls
    # always supply the authenticated request principal.
    return {"id": case.owner_id if case and case.owner_id else "local-development",
            "role": "ADMIN", "type": "local"}


@router.post("/cases/{case_id}/patch-suggestions")
async def patch_suggestion(
    case_id: str,
    payload: PatchRequest,
    db: Db,
    request: Request = None,
) -> dict:
    # Resolve the target first so legacy callers retain the old 404 behavior.
    case = db.get(Case, case_id)
    if not case:
        raise HTTPException(404, "Case or symbol not found")
    identity = _patch_principal(request, case)
    data = create_patch_suggestion_input(
        db, case_id=case_id, symbol_id=payload.symbol_id,
        instruction=payload.instruction, principal=identity,
    )
    context = resolve_patch_suggestion_context(
        db, case_id=case_id, actor_id=data["actor"], snapshot=data["snapshot"],
        symbol_id=data["symbol_id"], generation_id=data["generation_id"],
    )
    return await generate_patch_suggestion(db, context, data["instruction"])


@router.post("/cases/{case_id}/patch-suggestion-jobs", response_model=JobOut)
def create_patch_suggestion_job(case_id: str, payload: PatchRequest, request: Request, db: Db) -> Job:
    data = create_patch_suggestion_input(
        db, case_id=case_id, symbol_id=payload.symbol_id,
        instruction=payload.instruction,
        principal=getattr(request.state, "principal", {}) or {},
    )
    try:
        return job_runner.submit(
            db, "patch_suggestion", patch_suggestion_job, input_data=data,
            max_attempts=1, timeout_seconds=7200,
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
