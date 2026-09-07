from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.config import get_settings
from app.core.db import get_db
from app.core.utils import json_dumps, json_loads, new_id, utcnow
from app.models import (
    Artifact,
    Job,
    KnowledgeCategory,
    KnowledgeChunk,
    KnowledgeDerivation,
    KnowledgeDocument,
    KnowledgeDocumentCategory,
    KnowledgeEmbedding,
)
from app.schemas import (
    JobOut,
    KnowledgeCategoryCreate,
    KnowledgeCategoryOut,
    KnowledgeCategoryUpdate,
    KnowledgeCreate,
    KnowledgeDetailOut,
    KnowledgeImportOut,
    KnowledgeOut,
    KnowledgeUpdate,
)
from app.services.import_jobs import import_knowledge_job
from app.services.jobs import job_runner
from app.services.knowledge import index_document, reindex_knowledge_job
from app.services.knowledge_drafts import attach_pending_drafts, save_draft
from app.services.knowledge_visibility import current_chunk_clause
from app.services.knowledge_access import bind_owner, visible_knowledge_clause
from app.services.knowledge_governance import (
    actor_id,
    advance_document_version,
    create_document_revision,
    mark_domain_graph_stale,
    require_lock_version,
)
from app.services.knowledge_methods import (
    FAULT_CASE_TEMPLATE,
    STRUCTURED_SOURCE_TYPES,
    analysis_method_is_unmodified,
    derive_analysis_method,
)
from app.services.knowledge_taxonomy import (
    category_document_counts,
    descendant_category_ids,
    get_default_category_id,
    new_category_code,
    new_category_id,
    set_document_category,
    validate_category_parent,
)
from app.services.model_profiles import get_active_model_profile
from app.services.storage import storage


router = APIRouter()
Db = Annotated[Session, Depends(get_db)]

job_runner.register("reindex_knowledge", reindex_knowledge_job, ("profile_id",), cancellable=True)
job_runner.register(
    "import_knowledge",
    import_knowledge_job,
    ("document_id", "artifact_id"),
    cancellable=True,
)


def _knowledge_to_dict(
    document: KnowledgeDocument,
    category_map: dict[str, tuple[str, str]],
    chunk_counts: dict[str, int],
    *,
    include_content: bool = False,
) -> dict:
    category = category_map.get(document.id)
    result = {
        "id": document.id,
        "title": document.title,
        "source_type": document.source_type,
        "device_type": document.device_type,
        "device_model": document.device_model,
        "firmware_range": document.firmware_range,
        "module": document.module,
        "trust_level": document.trust_level,
        "confidentiality": document.confidentiality,
        "active": document.active,
        "review_status": document.review_status,
        "version": document.version,
        "lock_version": document.lock_version,
        "reviewed_by": document.reviewed_by,
        "reviewed_at": document.reviewed_at,
        "review_comment": document.review_comment,
        "published_at": document.published_at,
        "category_id": category[0] if category else None,
        "category_name": category[1] if category else None,
        "chunk_count": chunk_counts.get(document.id, 0),
        "metadata": json_loads(document.metadata_json, {}),
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }
    if include_content:
        result["content"] = document.content
    return result


def _knowledge_response_maps(
    db: Session,
    document_ids: list[str],
) -> tuple[dict[str, tuple[str, str]], dict[str, int]]:
    if not document_ids:
        return {}, {}
    category_rows = db.execute(
        select(KnowledgeDocumentCategory.document_id, KnowledgeCategory.id, KnowledgeCategory.name)
        .join(KnowledgeCategory, KnowledgeDocumentCategory.category_id == KnowledgeCategory.id)
        .where(KnowledgeDocumentCategory.document_id.in_(document_ids))
    ).all()
    chunk_rows = db.execute(
        select(KnowledgeChunk.document_id, func.count(KnowledgeChunk.id))
        .join(KnowledgeDocument)
        .where(KnowledgeChunk.document_id.in_(document_ids), current_chunk_clause())
        .group_by(KnowledgeChunk.document_id)
    ).all()
    return (
        {document_id: (category_id, name) for document_id, category_id, name in category_rows},
        {document_id: int(count) for document_id, count in chunk_rows},
    )


def _single_knowledge_response(db: Session, document: KnowledgeDocument, *, detail: bool = False) -> dict:
    categories, chunks = _knowledge_response_maps(db, [document.id])
    return attach_pending_drafts(db, [_knowledge_to_dict(document, categories, chunks, include_content=detail)], detail=detail)[0]


def _delete_knowledge_rows(db: Session, document_id: str) -> None:
    chunk_ids = select(KnowledgeChunk.id).where(
        KnowledgeChunk.document_id == document_id
    )
    db.execute(delete(KnowledgeEmbedding).where(
        KnowledgeEmbedding.chunk_id.in_(chunk_ids)
    ))
    db.execute(delete(KnowledgeChunk).where(
        KnowledgeChunk.document_id == document_id
    ))
    link = db.get(KnowledgeDocumentCategory, document_id)
    if link:
        db.delete(link)
    document = db.get(KnowledgeDocument, document_id)
    if document:
        db.delete(document)


@router.post("/knowledge", response_model=KnowledgeDetailOut)
def create_knowledge(payload: KnowledgeCreate, request: Request, db: Db) -> dict:
    document = KnowledgeDocument(
        id=new_id("DOC"), title=payload.title, source_type=payload.source_type,
        device_type=payload.device_type, device_model=payload.device_model,
        firmware_range=payload.firmware_range, module=payload.module,
        trust_level=payload.trust_level, confidentiality=payload.confidentiality,
        content=payload.content, metadata_json=json_dumps(payload.metadata),
        active=False, review_status="DRAFT",
    )
    db.add(document)
    db.flush()
    bind_owner(db, document.id, actor_id(getattr(request.state, "principal", {})))
    category_id = payload.category_id or get_default_category_id(db, payload.source_type)
    set_document_category(db, document.id, category_id)
    index_document(db, document)
    create_document_revision(
        db,
        document,
        created_by=actor_id(getattr(request.state, "principal", {})),
        change_summary="Initial version",
    )
    db.commit()
    db.refresh(document)
    return _single_knowledge_response(db, document, detail=True)


@router.post(
    "/knowledge/upload",
    response_model=KnowledgeImportOut,
    status_code=202,
)
async def upload_knowledge(
    db: Db,
    file: UploadFile = File(...),
    source_type: str = Form(default="document"),
    device_type: str | None = Form(default=None),
    module: str | None = Form(default=None),
    trust_level: str = Form(default="MEDIUM"),
    category_id: str | None = Form(default=None),
) -> dict:
    artifact_id = new_id("ART")
    document_id = new_id("DOC")
    uploaded_name = Path(
        (file.filename or "knowledge.md").replace("\\", "/")
    ).name
    try:
        path, size, digest = await storage.save_upload(
            file,
            artifact_id,
            target_name=uploaded_name,
        )
    except ValueError as exc:
        storage.remove_artifact(artifact_id)
        raise HTTPException(413, str(exc)) from exc
    if size > get_settings().max_single_file_bytes:
        storage.remove_artifact(artifact_id)
        raise HTTPException(413, "Knowledge file is too large")
    artifact = Artifact(
        id=artifact_id,
        case_id=None,
        kind="knowledge_source",
        original_name=uploaded_name,
        stored_path=storage.storage_key(path),
        sha256=digest,
        size_bytes=size,
        status="UPLOADED",
        metadata_json=json_dumps({"document_id": document_id}),
    )
    document = KnowledgeDocument(
        id=document_id,
        title=uploaded_name or "Knowledge document",
        source_type=source_type,
        device_type=device_type, module=module, trust_level=trust_level,
        content="",
        active=False,
        review_status="DRAFT",
        metadata_json=json_dumps({
            "original_name": uploaded_name,
            "artifact_id": artifact_id,
            "import_status": "QUEUED",
            "source_bytes": size,
        }),
    )
    db.add(artifact)
    db.flush()
    db.add(document)
    db.flush()
    set_document_category(db, document.id, category_id or get_default_category_id(db, source_type))
    db.commit()
    job = job_runner.submit(
        db,
        "import_knowledge",
        import_knowledge_job,
        document_id,
        artifact_id,
        input_data={
            "document_id": document_id,
            "artifact_id": artifact_id,
        },
    )
    return {
        "document_id": document_id,
        "artifact_id": artifact_id,
        "job": job,
    }

@router.get("/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(
    db: Db,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    category_id: str | None = None,
    include_descendants: bool = True,
    source_type: str | None = None,
    search: str | None = None,
) -> list[dict]:
    principal = getattr(request.state, "principal", {})
    query = select(KnowledgeDocument).where(visible_knowledge_clause(principal))
    if category_id:
        category_ids = (
            descendant_category_ids(db, category_id) if include_descendants else {category_id}
        )
        query = query.join(
            KnowledgeDocumentCategory,
            KnowledgeDocumentCategory.document_id == KnowledgeDocument.id,
        ).where(KnowledgeDocumentCategory.category_id.in_(category_ids))
    if source_type:
        query = query.where(KnowledgeDocument.source_type == source_type)
    if search:
        query = query.where(
            KnowledgeDocument.title.ilike(f"%{search}%")
            | KnowledgeDocument.content.ilike(f"%{search}%")
        )
    documents = list(db.scalars(
        query.order_by(KnowledgeDocument.updated_at.desc()).limit(limit)
    ).all())
    categories, chunks = _knowledge_response_maps(db, [document.id for document in documents])
    return attach_pending_drafts(db, [_knowledge_to_dict(document, categories, chunks) for document in documents], principal=principal)


@router.get("/knowledge/categories", response_model=list[KnowledgeCategoryOut])
def list_knowledge_categories(db: Db) -> list[dict]:
    counts = category_document_counts(db)
    categories = list(db.scalars(
        select(KnowledgeCategory).order_by(KnowledgeCategory.sort_order, KnowledgeCategory.name)
    ).all())
    return [
        {
            "id": category.id,
            "name": category.name,
            "code": category.code,
            "parent_id": category.parent_id,
            "description": category.description,
            "sort_order": category.sort_order,
            "system": category.system,
            "active": category.active,
            "document_count": counts.get(category.id, 0),
            "created_at": category.created_at,
            "updated_at": category.updated_at,
        }
        for category in categories
    ]


@router.post("/knowledge/categories", response_model=KnowledgeCategoryOut)
def create_knowledge_category(payload: KnowledgeCategoryCreate, db: Db) -> dict:
    category_id = new_category_id()
    category = KnowledgeCategory(
        id=category_id,
        name=payload.name,
        code=new_category_code(category_id),
        parent_id=payload.parent_id,
        description=payload.description,
        sort_order=payload.sort_order,
        system=False,
    )
    db.add(category)
    db.flush()
    try:
        validate_category_parent(db, category, payload.parent_id)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    db.refresh(category)
    return {**KnowledgeCategoryOut.model_validate(category).model_dump(), "document_count": 0}


@router.patch("/knowledge/categories/{category_id}", response_model=KnowledgeCategoryOut)
def update_knowledge_category(
    category_id: str,
    payload: KnowledgeCategoryUpdate,
    db: Db,
) -> dict:
    category = db.get(KnowledgeCategory, category_id)
    if not category:
        raise HTTPException(404, "Knowledge category not found")
    values = payload.model_dump(exclude_unset=True)
    if "parent_id" in values:
        try:
            validate_category_parent(db, category, values["parent_id"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    for key, value in values.items():
        setattr(category, key, value)
    db.commit()
    db.refresh(category)
    count = category_document_counts(db).get(category.id, 0)
    return {**KnowledgeCategoryOut.model_validate(category).model_dump(), "document_count": count}


@router.delete("/knowledge/categories/{category_id}")
def delete_knowledge_category(category_id: str, db: Db) -> dict:
    category = db.get(KnowledgeCategory, category_id)
    if not category:
        raise HTTPException(404, "Knowledge category not found")
    if category.system:
        raise HTTPException(409, "Built-in categories cannot be deleted")
    has_children = db.scalar(
        select(KnowledgeCategory.id).where(KnowledgeCategory.parent_id == category_id).limit(1)
    )
    has_documents = db.scalar(
        select(KnowledgeDocumentCategory.document_id)
        .where(KnowledgeDocumentCategory.category_id == category_id)
        .limit(1)
    )
    if has_children or has_documents:
        raise HTTPException(409, "Move child categories and documents before deleting this category")
    db.delete(category)
    db.commit()
    return {"deleted": category_id}


@router.post("/knowledge/reindex", response_model=JobOut)
def reindex_knowledge(db: Db) -> Job:
    profile = get_active_model_profile("embedding", db)
    if not profile:
        raise HTTPException(409, "No active embedding model")
    return job_runner.submit(
        db,
        "reindex_knowledge",
        reindex_knowledge_job,
        profile.id,
        input_data={"profile_id": profile.id},
    )


@router.get("/knowledge/templates/fault-case")
def get_fault_case_template() -> dict:
    return {
        "name": "structured_fault_case_markdown_v1",
        "source_type": "fault_case",
        "required_sections": ["错误形式", "日志分析", "错误定位", "解决方案"],
        "optional_sections": ["验证结果", "适用范围与限制"],
        "content": FAULT_CASE_TEMPLATE,
    }


@router.get("/knowledge/{document_id}", response_model=KnowledgeDetailOut)
def get_knowledge(document_id: str, request: Request, db: Db) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    principal = getattr(request.state, "principal", {})
    from app.services.knowledge_access import require_knowledge_access
    require_knowledge_access(db, document_id, principal)
    return attach_pending_drafts(db, [_single_knowledge_response(db, document, detail=True)], detail=True, principal=principal)[0]


@router.post("/knowledge/{document_id}/extract-method")
def extract_knowledge_method(document_id: str, db: Db) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    if document.source_type not in STRUCTURED_SOURCE_TYPES:
        raise HTTPException(
            409,
            "Only fault cases, fault trees, historical cases or analysis skills can be extracted",
        )
    try:
        derived, created = derive_analysis_method(db, document)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "created": created,
        "source_document_id": document.id,
        "derived_document": _single_knowledge_response(db, derived, detail=True),
    }


@router.patch("/knowledge/{document_id}", response_model=KnowledgeDetailOut)
def update_knowledge(
    document_id: str,
    payload: KnowledgeUpdate,
    request: Request,
    db: Db,
) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    values = payload.model_dump(exclude_unset=True)
    expected_lock_version = values.pop("expected_lock_version", None)
    expected_draft_version = values.pop("expected_draft_version", None)
    try:
        require_lock_version(document, expected_lock_version)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if document.active and document.review_status == "ACTIVE":
        if "active" in values:
            raise HTTPException(409, "Use review endpoints to archive a publication")
        try:
            save_draft(db, document, values, expected_lock_version=expected_lock_version,
                       expected_draft_version=expected_draft_version,
                       author=actor_id(getattr(request.state, "principal", {})))
            db.commit()
        except (ValueError, StaleDataError) as error:
            db.rollback()
            raise HTTPException(409, "Draft edit conflict; refresh and verify your changes") from error
        return attach_pending_drafts(db, [_single_knowledge_response(db, document, detail=True)],
                                     detail=True, principal=getattr(request.state, "principal", {}))[0]
    category_was_set = "category_id" in values
    category_id = values.pop("category_id", None)
    metadata = values.pop("metadata", None)
    if "active" in values:
        raise HTTPException(
            409,
            "Use the knowledge review endpoints to publish or archive a document",
        )
    for key, value in values.items():
        setattr(document, key, value)
    if metadata is not None:
        document.metadata_json = json_dumps(metadata)
    if category_was_set:
        try:
            set_document_category(db, document.id, category_id)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    try:
        advance_document_version(
            db,
            document,
            created_by=actor_id(getattr(request.state, "principal", {})),
            change_summary="Knowledge document updated",
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    # The DRAFT/active=False transition is flushed before index_document can
    # commit chunks, preventing edited content from being briefly searchable
    # under the previously published lifecycle state.
    index_document(db, document)
    db.commit()
    existing_derivation = db.scalar(select(KnowledgeDerivation).where(
        KnowledgeDerivation.source_document_id == document.id,
        KnowledgeDerivation.derivation_type == "analysis_method",
    ))
    if existing_derivation and document.source_type in STRUCTURED_SOURCE_TYPES:
        derived = db.get(KnowledgeDocument, existing_derivation.derived_document_id)
        if derived and analysis_method_is_unmodified(derived):
            derive_analysis_method(db, document)
        elif derived:
            metadata = json_loads(derived.metadata_json, {})
            metadata["derivation_status"] = "SOURCE_UPDATED_DERIVED_MANUALLY_EDITED"
            metadata["source_updated_at"] = document.updated_at
            derived.metadata_json = json_dumps(metadata)
            derivation_metadata = json_loads(existing_derivation.metadata_json, {})
            derivation_metadata["status"] = "MANUAL_REVIEW_REQUIRED"
            existing_derivation.metadata_json = json_dumps(derivation_metadata)
            existing_derivation.updated_at = utcnow()
            db.commit()
    elif existing_derivation:
        derived = db.get(KnowledgeDocument, existing_derivation.derived_document_id)
        if derived:
            derived.active = False
            metadata = json_loads(derived.metadata_json, {})
            metadata["derivation_status"] = "SOURCE_TYPE_INCOMPATIBLE"
            derived.metadata_json = json_dumps(metadata)
        metadata = json_loads(existing_derivation.metadata_json, {})
        metadata["status"] = "SOURCE_TYPE_INCOMPATIBLE"
        existing_derivation.metadata_json = json_dumps(metadata)
        existing_derivation.updated_at = utcnow()
        db.commit()
    db.refresh(document)
    return _single_knowledge_response(db, document, detail=True)


@router.delete("/knowledge/{document_id}")
def delete_knowledge(document_id: str, db: Db) -> dict:
    document = db.get(KnowledgeDocument, document_id)
    if not document:
        raise HTTPException(404, "Knowledge document not found")
    if document.active:
        mark_domain_graph_stale(
            db,
            f"knowledge document {document.id} deleted",
        )
    source_derivations = list(db.scalars(select(KnowledgeDerivation).where(
        KnowledgeDerivation.source_document_id == document_id
    )).all())
    incoming_derivations = list(db.scalars(select(KnowledgeDerivation).where(
        KnowledgeDerivation.derived_document_id == document_id
    )).all())
    derived_ids = {
        derivation.derived_document_id
        for derivation in source_derivations
        if derivation.derived_document_id != document_id
    }
    for derivation in [*source_derivations, *incoming_derivations]:
        db.delete(derivation)
    db.flush()
    for derivation in incoming_derivations:
        source = db.get(KnowledgeDocument, derivation.source_document_id)
        if source:
            metadata = json_loads(source.metadata_json, {})
            if metadata.get("derived_analysis_method_id") == document_id:
                metadata.pop("derived_analysis_method_id", None)
                source.metadata_json = json_dumps(metadata)
    for derived_id in derived_ids:
        still_referenced = db.scalar(select(KnowledgeDerivation.id).where(
            KnowledgeDerivation.derived_document_id == derived_id
        ).limit(1))
        if not still_referenced:
            _delete_knowledge_rows(db, derived_id)
    _delete_knowledge_rows(db, document_id)
    db.commit()
    return {
        "deleted": document_id,
        "deleted_derived_documents": sorted(derived_ids),
    }
