"""Compact, role-aware product boundaries for the diagnosis workbench."""
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.core.db import get_db
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from app.core.utils import json_dumps, json_loads
from app.models import KnowledgeDocument, ModelProfile
from app.workbench_models import WorkbenchRecord
from app.services.workbench import categories, KNOWLEDGE_ROLES, knowledge_scope, make_record, record_payload
from app.services.problem_categories import categories_with_status, change_category
from app.services.bundled_knowledge import packaged_knowledge_status
from app.services.model_access import (
    ModelAccessError, model_profile_payload, require_model_identity, resolve_user_chat_profile, visible_model_clause,
)

router = APIRouter(prefix="/workbench", tags=["workbench"])
Db = Annotated[Session, Depends(get_db)]


def principal(request):
    return getattr(request.state, "principal", {})


def admin(request):
    identity = principal(request)
    if identity.get("role") not in {"ADMIN", "EXPERT"}:
        raise HTTPException(403, "仅管理员或专家可执行此操作")
    return identity


class Preferences(BaseModel):
    chat_profile_id: str | None = Field(default=None, max_length=40)


@router.get("/bootstrap")
def bootstrap(request: Request, db: Db):
    identity = principal(request)
    try:
        require_model_identity(identity)
    except ModelAccessError as error:
        raise HTTPException(error.status_code, str(error)) from error
    pref = db.get(WorkbenchRecord, "pref-" + str(identity.get("id", "local")))
    try:
        selection = {"profile_id": resolve_user_chat_profile(db, identity).id, "error": None}
    except ModelAccessError as error:
        selection = {"profile_id": None, "error": str(error)}
    return {"principal": identity, "categories": categories_with_status(db), "knowledge_roles": KNOWLEDGE_ROLES,
            "preferences": json_loads(pref.payload_json, {}) if pref else {},
            "model_selection": selection,
            "bundled_knowledge": packaged_knowledge_status(db) if identity.get("role") in {"ADMIN", "EXPERT"} else None,
            "models": [{**model_profile_payload(db, identity, row), "active": row.is_active} for row in db.scalars(
                select(ModelProfile).where(ModelProfile.task_type == "chat", ModelProfile.enabled.is_(True),
                    visible_model_clause(identity)).order_by(ModelProfile.is_active.desc(), ModelProfile.name))]}


@router.put("/preferences")
def preferences(payload: Preferences, request: Request, db: Db):
    from app.services.workbench import validate_case_options
    try:
        require_model_identity(principal(request))
        validate_case_options(db, payload.model_dump(), principal=principal(request))
    except ValueError as error:
        raise HTTPException(getattr(error, "status_code", 422), str(error)) from error
    key = "pref-" + str(principal(request).get("id", "local"))
    row = db.get(WorkbenchRecord, key)
    if row is None:
        row = WorkbenchRecord(id=key, kind="preferences", owner_id=principal(request).get("id"))
        db.add(row)
    row.payload_json = json_dumps(payload.model_dump())
    db.commit()
    return payload.model_dump()


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)


class CategoryVersion(BaseModel):
    version: int = Field(ge=1)


class CategoryUpdate(CategoryInput, CategoryVersion):
    pass


class TemplateChoice(BaseModel):
    document_id: str
    version: int


@router.put("/templates/{category_id}")
def choose_template(category_id: str, payload: TemplateChoice, request: Request, db: Db):
    from app.services.knowledge_access import knowledge_kind
    identity = admin(request)
    doc = db.get(KnowledgeDocument, payload.document_id)
    if (not doc or not doc.active or doc.review_status != "ACTIVE" or doc.version != payload.version
            or doc.confidentiality not in {"PUBLIC", "INTERNAL"}):
        raise HTTPException(409, "模板未发布或版本已变化，请刷新")
    if category_id not in {item["id"] for item in categories(db)} or category_id not in knowledge_scope(doc):
        raise HTTPException(422, "模板不属于此问题类别")
    if knowledge_kind(doc) != "SKILL" or json_loads(doc.metadata_json, {}).get("knowledge_role") != "report_template":
        raise HTTPException(422, "请选择报告格式文档")
    key = "template-" + category_id
    record = db.get(WorkbenchRecord, key)
    if record is None:
        record = WorkbenchRecord(id=key, kind="template_default", owner_id=identity.get("id"))
        db.add(record)
    record.payload_json = json_dumps({"document_id": doc.id})
    db.commit()
    return {"category_id": category_id, "document_id": doc.id, "version": doc.version}


@router.post("/categories")
def add_category(payload: CategoryInput, request: Request, db: Db):
    identity = admin(request)
    return save_category(db, identity, name=payload.name)


def save_category(db, identity, **changes):
    try:
        result = change_category(db, identity.get("id"), **changes)
        db.commit()
        return result
    except (ValueError, StaleDataError) as error:
        db.rollback()
        raise HTTPException(409, str(error)) from error


@router.patch("/categories/{category_id}")
def rename_category(category_id: str, payload: CategoryUpdate, request: Request, db: Db):
    return save_category(db, admin(request), category_id=category_id, version=payload.version, name=payload.name)


@router.delete("/categories/{category_id}")
def delete_category(category_id: str, payload: CategoryVersion, request: Request, db: Db):
    return save_category(db, admin(request), category_id=category_id, version=payload.version, deactivate=True)


@router.get("/knowledge")
def knowledge(request: Request, db: Db):
    from app.services.knowledge_access import visible_knowledge_clause, knowledge_kind
    rows = db.scalars(select(KnowledgeDocument).where(visible_knowledge_clause(principal(request)),
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE"))
    result = []
    for row in rows:
        metadata = json_loads(row.metadata_json, {})
        result.append({"id": row.id, "title": row.title, "version": row.version, "lock_version": row.lock_version,
            "status": row.review_status, "content": row.content, "categories": knowledge_scope(row),
            "role": metadata.get("knowledge_role") or ({"fault_tree": "fault_tree", "analysis_method": "diagnosis"}.get(row.source_type, "log_analysis")),
            "bundle_id": metadata.get("bundle_id"), "source_paths": metadata.get("source_paths", []),
            "content_kind": knowledge_kind(row), "metadata": metadata,
            "legacy": not bool(metadata.get("problem_categories"))})
    return result


class LibrarySubmission(BaseModel):
    title: str = Field(min_length=2, max_length=255)
    problem_category: str = "unknown"
    content: str = Field(min_length=5, max_length=500000)
    case_id: str | None = None
    analysis_id: str | None = None


@router.post("/library")
def submit_library(payload: LibrarySubmission, request: Request, db: Db):
    from app.services.workbench_library import prepare_submission, conclusion_contribution
    identity = principal(request)
    try:
        snapshot = prepare_submission(db, identity, payload.model_dump())
    except PermissionError as error:
        raise HTTPException(403, str(error)) from error
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    row = make_record(db, "library", identity.get("id"), snapshot)
    contribution = conclusion_contribution(db, identity, row.id)
    db.commit()
    return {**record_payload(row), "contribution_id": contribution.id}


@router.get("/library")
def library(request: Request, db: Db):
    identity = principal(request)
    rows = [record_payload(row) for row in db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "library"))]
    return [row for row in rows if identity.get("role") in {"ADMIN", "EXPERT"} or row.get("status") == "CONFIRMED" or row["owner_id"] == identity.get("id")]


class ReviewInput(BaseModel):
    version: int
    approve: bool


@router.post("/library/{record_id}/review")
def review_library(record_id: str, payload: ReviewInput, request: Request, db: Db):
    from app.services.workbench_library import review_submission
    identity = admin(request)
    try:
        row = review_submission(db, identity, record_id, payload.version, payload.approve)
        db.commit()
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except (ValueError, StaleDataError) as error:
        db.rollback()
        raise HTTPException(409, "提交内容已变更，请刷新") from error
    return record_payload(row)
