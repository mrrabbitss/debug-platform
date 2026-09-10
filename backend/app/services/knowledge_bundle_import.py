"""Explicit additive import of the packaged Skill; never retire an existing corpus."""
from pathlib import PurePosixPath

from sqlalchemy import select

from app.core.utils import json_dumps, json_loads
from app.models import KnowledgeChunk
from app.workbench_models import WorkbenchRecord
from app.services import knowledge_reset as reset


def _matches(document, item):
    snapshot = json_loads(getattr(document, "snapshot_json", "{}"), {})
    metadata = snapshot.get("metadata", json_loads(getattr(document, "metadata_json", "{}"), {}))
    if not isinstance(metadata, dict):
        metadata = {}
    paths = metadata.get("source_paths", [])
    if not isinstance(paths, list):
        paths = []
    names = [snapshot.get("title", getattr(document, "title", "")), *[p for p in paths if isinstance(p, str)]]
    basename = PurePosixPath(item["path"]).name.casefold()
    # Different child files can legitimately share identical bytes. A content
    # hash alone is not a file identity and must not create false conflicts.
    return any(PurePosixPath(name.replace("\\", "/")).name.casefold() == basename for name in names if isinstance(name, str))


def extend_preview(plan, bundle, snapshot):
    inspection = []
    for item in bundle["files"]:
        matches = [d for d in snapshot["documents"] if _matches(d, item)]
        drafts = [d for d in snapshot["drafts"] if _matches(d, item)]
        identical = len(matches) == 1 and not drafts and matches[0].active and matches[0].review_status == "ACTIVE"
        if identical:
            metadata = json_loads(matches[0].metadata_json, {})
            identical = (reset.digest(matches[0].content) == item["sha256"]
                         and metadata.get("content_kind") == "SKILL"
                         and "network" in metadata.get("problem_categories", [])
                         and metadata.get("knowledge_role") == item["role"])
        inspection.append({"path": item["path"], "disposition": "EXISTS" if identical else
                           "CONFLICT" if matches or drafts else "ADD",
                           "conflicts": [d.id for d in [*matches, *drafts]]})
    can_confirm = all(item["disposition"] == "ADD" for item in inspection)
    message = ("将新增完整的六文件组网包，保留现有知识、Skill、案例、报告和已设置的报告模板。" if can_confirm else
               "六份 Skill 已存在，无需重复导入。" if all(i["disposition"] == "EXISTS" for i in inspection) else
               "发现已存在、已删除或已修改的同名文件，请先核对冲突；此入口不会覆盖或重复创建它们。")
    plan.update(preserve_existing=True, inspection=inspection, can_confirm=can_confirm, message=message,
                preserved=[*plan["preserved"], "existing_knowledge", "existing_skills", "drafts", "global_memories", "existing_templates"])
    plan["counts"].update(added_files=sum(i["disposition"] == "ADD" for i in inspection), retired_documents=0)
    return plan


def public_preview(plan):
    checks = {item["path"]: item for item in plan["inspection"]}
    return {**plan, "manifest": [{**item, **checks[item["path"]]} for item in plan["manifest"]]}


def track_operation(db, operation_id, source_sha256):
    from app.services.bundled_knowledge import RECORD_ID
    marker = db.get(WorkbenchRecord, RECORD_ID)
    if marker is None:
        marker = WorkbenchRecord(id=RECORD_ID, kind="bundled_knowledge")
        db.add(marker)
    marker.payload_json = json_dumps({"status": "APPROVED", "operation_id": operation_id,
                                     "source_sha256": source_sha256})


def retain_index_inputs(db, snapshot, candidates, by_document):
    """The replacement generation must still contain all existing public live chunks."""
    documents = [d for d in snapshot["documents"] if d.active and d.review_status == "ACTIVE"
                 and d.confidentiality in {"PUBLIC", "INTERNAL"}]
    for document in documents:
        by_document[document.id] = list(db.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document.id, KnowledgeChunk.document_version == document.version)
            .order_by(KnowledgeChunk.chunk_index)))
    return [*documents, *candidates]
