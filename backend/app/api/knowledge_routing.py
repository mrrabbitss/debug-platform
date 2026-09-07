from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import Artifact
from app.schemas import JobOut
from app.services.jobs import job_runner
from app.services.knowledge_governance import actor_id
from app.services.knowledge_routing import (
    MAX_ROUTING_DOCUMENTS,
    resolve_routing_model,
    route_markdown_knowledge_job,
)
from app.services.storage import storage


router = APIRouter(prefix="/knowledge-routing", tags=["knowledge-routing"])
Db = Annotated[Session, Depends(get_db)]

job_runner.register(
    "route_markdown_knowledge",
    route_markdown_knowledge_job,
    ("artifact_id", "document_id"),
    cancellable=True,
    max_attempts=1,
    timeout_seconds=900,
)


def _require_admin(request: Request) -> dict[str, Any]:
    principal = getattr(request.state, "principal", {}) or {}
    if principal.get("role") not in {"ADMIN", "ENGINEER"}:
        raise HTTPException(403, "Engineer or administrator role required")
    return principal


def _relative_paths(raw: str, files: list[UploadFile]) -> list[str]:
    values = json_loads(raw, [])
    if values == []:
        values = [file.filename or "knowledge.md" for file in files]
    if not isinstance(values, list) or len(values) != len(files):
        raise HTTPException(
            400,
            "relative_paths_json must contain one path for every Markdown file",
        )
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise HTTPException(400, "Every Markdown relative path is required")
        candidate = value.replace("\\", "/").strip()
        path = PurePosixPath(candidate)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise HTTPException(400, "Markdown relative paths must be safe and relative")
        normalized.append(path.as_posix())
    if len(set(item.casefold() for item in normalized)) != len(normalized):
        raise HTTPException(409, "Markdown relative paths must be unique")
    return normalized


@router.post("/import", status_code=202)
async def import_markdown_for_routing(
    request: Request,
    db: Db,
    files: list[UploadFile] = File(...),
    relative_paths_json: str = Form(default="[]"),
    reasoning_owner: Literal["platform_llm", "host_cli"] = Form(
        default="platform_llm"
    ),
    model_profile_id: str | None = Form(default=None),
    consent_model_egress: bool = Form(default=False),
    trust_level: str = Form(default="MEDIUM"),
    confidentiality: str = Form(default="INTERNAL"),
) -> dict[str, Any]:
    principal = _require_admin(request)
    if not 1 <= len(files) <= MAX_ROUTING_DOCUMENTS:
        raise HTTPException(
            400,
            f"Select between 1 and {MAX_ROUTING_DOCUMENTS} Markdown files",
        )
    if trust_level not in {"LOW", "MEDIUM", "HIGH"}:
        raise HTTPException(400, "Unsupported trust level")
    if confidentiality not in {"PUBLIC", "INTERNAL", "RESTRICTED"}:
        raise HTTPException(400, "Unsupported confidentiality level")
    relative_paths = _relative_paths(relative_paths_json, files)
    for file, relative_path in zip(files, relative_paths, strict=True):
        filename = Path(file.filename or relative_path).name
        if Path(filename).suffix.casefold() not in {".md", ".markdown"}:
            raise HTTPException(400, "Only .md and .markdown files are supported")

    selected_profile_id: str | None = None
    if reasoning_owner == "platform_llm":
        try:
            profile, _provider, _snapshot = resolve_routing_model(
                db,
                model_profile_id,
            )
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, str(exc)) from exc
        if profile.mode == "api" and not consent_model_egress:
            raise HTTPException(
                409,
                "Confirm that masked Markdown excerpts may be sent to the selected model API",
            )
        selected_profile_id = profile.id

    batch_id = new_id("KRBATCH")
    created_by = actor_id(principal)
    staged: list[dict[str, Any]] = []
    try:
        for file, relative_path in zip(files, relative_paths, strict=True):
            artifact_id = new_id("ART")
            document_id = new_id("DOC")
            filename = Path(file.filename or relative_path).name
            path, size, digest = await storage.save_upload(
                file,
                artifact_id,
                target_name=filename,
            )
            artifact = Artifact(
                id=artifact_id,
                case_id=None,
                kind="knowledge_routing_source",
                original_name=filename,
                stored_path=storage.storage_key(path),
                sha256=digest,
                size_bytes=size,
                status="UPLOADED",
                metadata_json=json_dumps({
                    "routing_batch_id": batch_id,
                    "document_id": document_id,
                    "relative_path": relative_path,
                    "reasoning_owner": reasoning_owner,
                    "model_profile_id": selected_profile_id,
                    "model_egress_consent": bool(consent_model_egress),
                    "model_egress_consent_at": (
                        utcnow().isoformat() if consent_model_egress else None
                    ),
                    "trust_level": trust_level,
                    "confidentiality": confidentiality,
                    "created_by": created_by,
                    "raw_source_local_only": True,
                }),
            )
            db.add(artifact)
            staged.append({
                "artifact": artifact,
                "artifact_id": artifact_id,
                "document_id": document_id,
                "relative_path": relative_path,
            })
        db.commit()
    except Exception:
        db.rollback()
        for item in staged:
            try:
                storage.remove_artifact(item["artifact_id"])
            except (FileNotFoundError, OSError, ValueError):
                pass
        raise

    items: list[dict[str, Any]] = []
    for item in staged:
        job = job_runner.submit(
            db,
            "route_markdown_knowledge",
            route_markdown_knowledge_job,
            item["artifact_id"],
            item["document_id"],
            input_data={
                "artifact_id": item["artifact_id"],
                "document_id": item["document_id"],
                "routing_batch_id": batch_id,
            },
        )
        items.append({
            "relative_path": item["relative_path"],
            "artifact_id": item["artifact_id"],
            "document_id": item["document_id"],
            "job": JobOut.model_validate(job).model_dump(mode="json"),
        })
    return {
        "batch_id": batch_id,
        "reasoning_owner": reasoning_owner,
        "model_profile_id": selected_profile_id,
        "file_count": len(items),
        "items": items,
        "draft_only": True,
        "human_review_required": True,
    }
