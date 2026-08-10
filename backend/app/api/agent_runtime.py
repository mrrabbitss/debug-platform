from __future__ import annotations

import json

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, new_id, sha256_file
from app.models import Artifact, Case, Repository
from app.services.code_graph import search_code_graph
from app.services.code_index import index_repository_job
from app.services.external_agent import build_evidence_bundle
from app.services.git_repository import GitRepositoryError, repository_head
from app.services.jobs import job_runner
from app.services.local_model_discovery import (
    LocalModelDiscoveryError,
    activate_local_model,
    load_local_model_registry,
    scan_local_models,
    validate_local_model,
)
from app.services.storage import storage


router = APIRouter(tags=["agent-runtime"])
Db = Annotated[Session, Depends(get_db)]


class EvidenceBundleRequest(BaseModel):
    query: str | None = Field(default=None, max_length=20_000)
    top_k: int = Field(default=12, ge=1, le=20)
    max_hops: int = Field(default=2, ge=0, le=3)
    modules: list[str] | None = Field(default=None, max_length=8)


class LocalModelValidateRequest(BaseModel):
    device: str = Field(default="cpu", max_length=64)


class LocalModelActivateRequest(BaseModel):
    device: str = Field(default="cpu", max_length=64)
    force: bool = False


class WorkspaceAttachRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=255)


class CodeContextRequest(BaseModel):
    query: str = Field(min_length=2, max_length=20_000)
    max_hops: int = Field(default=2, ge=0, le=3)
    limit: int = Field(default=20, ge=1, le=100)


def _under_root(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_workspace_path(raw_path: str) -> Path:
    settings = get_settings()
    if not settings.workspace_attach_enabled:
        raise ValueError("Local workspace attachment is disabled")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError("Workspace path must be absolute")
    if path.is_symlink():
        raise ValueError("Workspace root must not be a symbolic link")
    resolved = path.resolve(strict=True)
    if not resolved.is_dir():
        raise ValueError("Workspace path must be an existing directory")
    allowed = settings.workspace_root_paths
    if allowed and not any(_under_root(resolved, root) for root in allowed):
        raise ValueError("Workspace path is outside WORKSPACE_ROOTS")
    if settings.auth_mode != "local" and not allowed:
        raise ValueError(
            "WORKSPACE_ROOTS must be configured before local path attachment "
            "when authentication is enabled"
        )
    return resolved


def _workspace_payload(repository: Repository) -> dict:
    return {
        "id": repository.id,
        "case_id": repository.case_id,
        "name": repository.name,
        "root_path": repository.root_path,
        "branch": repository.branch,
        "commit_hash": repository.commit_hash,
        "status": repository.status,
        "graph_status": repository.graph_status,
        "commit_graph_status": repository.commit_graph_status,
        "index_metadata": json.loads(repository.index_metadata_json or "{}"),
        "created_at": repository.created_at,
    }


@router.get("/system/agent-runtime")
def agent_runtime_status() -> dict:
    settings = get_settings()
    return {
        "agent_mode": settings.agent_mode,
        "external_agent": settings.agent_mode == "external",
        "runtime": {
            "host": settings.agent_runtime_host,
            "port": settings.agent_runtime_port,
            "api_base": f"http://{settings.agent_runtime_host}:{settings.agent_runtime_port}{settings.api_prefix}",
            "web_ui": f"http://{settings.agent_runtime_host}:{settings.agent_runtime_port}/ui/",
        },
        "serve_frontend": settings.serve_frontend,
        "frontend_available": (settings.frontend_dist / "index.html").is_file(),
        "model_roots": [str(path) for path in settings.model_root_paths],
        "workspace_roots": [str(path) for path in settings.workspace_root_paths],
        "workspace_attach_enabled": settings.workspace_attach_enabled,
        "reasoning_contract": (
            "Platform returns bounded evidence; Claude Code/OpenCode performs final root-cause reasoning."
            if settings.agent_mode == "external"
            else "Platform may use its active Chat model for final synthesis."
        ),
    }


@router.get("/system/local-models")
def list_local_models() -> list[dict]:
    return load_local_model_registry()


@router.post("/system/local-models/scan")
async def scan_models(db: Db, use_llm: bool = Query(default=True)) -> dict:
    try:
        models = await scan_local_models(db, use_llm=use_llm)
    except (LocalModelDiscoveryError, ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"models": models, "count": len(models), "llm_review_enabled": use_llm}


@router.post("/system/local-models/{candidate_id}/validate")
async def validate_model(candidate_id: str, payload: LocalModelValidateRequest) -> dict:
    try:
        return await validate_local_model(candidate_id, device=payload.device)
    except (LocalModelDiscoveryError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/system/local-models/{candidate_id}/activate")
def activate_model(candidate_id: str, payload: LocalModelActivateRequest, db: Db) -> dict:
    try:
        return activate_local_model(
            db,
            candidate_id,
            device=payload.device,
            force=payload.force,
        )
    except (LocalModelDiscoveryError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/cases/{case_id}/evidence-bundle")
def evidence_bundle(case_id: str, payload: EvidenceBundleRequest, db: Db) -> dict:
    try:
        return build_evidence_bundle(
            db,
            case_id=case_id,
            query=payload.query,
            top_k=payload.top_k,
            max_hops=payload.max_hops,
            modules=payload.modules,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/cases/{case_id}/workspaces/attach", status_code=201)
def attach_workspace(case_id: str, payload: WorkspaceAttachRequest, db: Db) -> dict:
    if not db.get(Case, case_id):
        raise HTTPException(404, "Case not found")
    try:
        workspace = _validate_workspace_path(payload.path)
    except (ValueError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc

    repository_id = new_id("REPO")
    artifact_id = new_id("ART")
    managed_artifact_dir = storage.artifact_dir(artifact_id)
    manifest_path = managed_artifact_dir / "workspace.json"
    branch = None
    commit_hash = None
    try:
        branch, commit_hash = repository_head(workspace)
    except (GitRepositoryError, OSError, ValueError):
        pass
    manifest = {
        "workspace_path": str(workspace),
        "read_only": True,
        "attached_in_place": True,
        "branch": branch,
        "commit_hash": commit_hash,
    }
    manifest_path.write_text(json_dumps(manifest), encoding="utf-8")
    destination = storage.repository_dir(repository_id)
    artifact = Artifact(
        id=artifact_id,
        case_id=case_id,
        kind="local_workspace",
        original_name="workspace.json",
        stored_path=storage.storage_key(manifest_path),
        sha256=sha256_file(manifest_path),
        size_bytes=manifest_path.stat().st_size,
        status="UPLOADED",
        metadata_json=json_dumps(manifest),
    )
    repository = Repository(
        id=repository_id,
        case_id=case_id,
        artifact_id=artifact_id,
        name=(payload.name or workspace.name or "workspace")[:255],
        root_path=str(workspace),
        branch=branch,
        commit_hash=commit_hash,
        status="UPLOADED",
        graph_status="NOT_INDEXED",
        commit_graph_status="NOT_INDEXED",
        index_metadata_json=json_dumps({
            "import_format": "local_workspace",
            "local_workspace": True,
            "read_only": True,
            "workspace_path": str(workspace),
            "managed_placeholder": storage.storage_key(destination),
        }),
    )
    db.add(artifact)
    db.add(repository)
    db.commit()
    db.refresh(repository)
    return _workspace_payload(repository)


@router.get("/cases/{case_id}/workspaces")
def list_workspaces(case_id: str, db: Db) -> list[dict]:
    rows = list(db.scalars(
        select(Repository)
        .where(Repository.case_id == case_id)
        .order_by(Repository.created_at.desc())
    ).all())
    return [
        _workspace_payload(row)
        for row in rows
        if __import__("json").loads(row.index_metadata_json or "{}").get("local_workspace")
    ]


@router.post("/workspaces/{repository_id}/index")
def index_workspace(repository_id: str, db: Db) -> dict:
    repository = db.get(Repository, repository_id)
    if not repository:
        raise HTTPException(404, "Repository not found")
    metadata = json.loads(repository.index_metadata_json or "{}")
    if not metadata.get("local_workspace"):
        raise HTTPException(409, "Repository is not an attached local workspace")
    job = job_runner.submit(
        db,
        "index_repository",
        index_repository_job,
        repository_id,
        input_data={"repository_id": repository_id},
    )
    return {
        "job_id": job.id,
        "status": job.status,
        "repository_id": repository_id,
    }


@router.post("/workspaces/{repository_id}/code-context")
def workspace_code_context(repository_id: str, payload: CodeContextRequest, db: Db) -> dict:
    try:
        return search_code_graph(
            db,
            repository_id,
            payload.query,
            max_hops=payload.max_hops,
            limit=payload.limit,
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
