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


def extend_preview(plan, bundle, snapshot, *, replace_bundle=False):
    if replace_bundle:
        return replacement_preview(plan, bundle, snapshot)
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


def track_operation(db, operation_id, source_sha256, *, status="APPROVED"):
    from app.services.bundled_knowledge import RECORD_ID
    marker = db.get(WorkbenchRecord, RECORD_ID)
    if marker is None:
        marker = WorkbenchRecord(id=RECORD_ID, kind="bundled_knowledge")
        db.add(marker)
    marker.payload_json = json_dumps({"status": status, "operation_id": operation_id,
                                     "source_sha256": source_sha256})


def retain_index_inputs(db, snapshot, candidates, by_document, *, retired_ids=()):
    """The replacement generation must still contain all existing public live chunks."""
    documents = [d for d in snapshot["documents"] if d.id not in retired_ids and d.active and d.review_status == "ACTIVE"
                 and d.confidentiality in {"PUBLIC", "INTERNAL"}]
    for document in documents:
        by_document[document.id] = list(db.scalars(select(KnowledgeChunk).where(
            KnowledgeChunk.document_id == document.id, KnowledgeChunk.document_version == document.version)
            .order_by(KnowledgeChunk.chunk_index)))
    return [*documents, *candidates]


def replacement_preview(plan, bundle, snapshot):
    """Offline operator scope: exact bundle paths AND network Skill classification.

    Same basenames in another folder/category, drafts and custom templates are
    never replacement targets. Retired bodies remain immutable and readable.
    """
    inspection, retired = [], set()
    for item in bundle["files"]:
        matches = []
        for document in snapshot["documents"]:
            metadata = json_loads(document.metadata_json, {})
            paths = metadata.get("source_paths", [])
            paths = [document.title, *(paths if isinstance(paths, list) else [])]
            exact = any(isinstance(path, str) and path.replace("\\", "/") == item["path"] for path in paths)
            if (exact and metadata.get("content_kind") == "SKILL"
                    and "network" in metadata.get("problem_categories", []) and document.active):
                matches.append(document)
        identical = (len(matches) == 1 and matches[0].review_status == "ACTIVE"
                     and reset.digest(matches[0].content) == item["sha256"]
                     and json_loads(matches[0].metadata_json, {}).get("knowledge_role") == item["role"]
                     and json_loads(matches[0].metadata_json, {}).get("embedding_status") == "INDEXED"
                     and len(json_loads(matches[0].metadata_json, {}).get("bundle_manifest", [])) == 6)
        retired.update(d.id for d in matches)
        inspection.append({"path": item["path"], "disposition": "EXISTS" if identical else
                           "REPLACE" if matches else "ADD", "conflicts": [d.id for d in matches]})
    unchanged = all(i["disposition"] == "EXISTS" for i in inspection)
    plan.update(preserve_existing=True, replace_bundle=True, inspection=inspection,
                retired_document_ids=sorted(retired), can_confirm=not unchanged,
                message="六份组网 Skill 已完整生效，无需更新。" if unchanged else
                "更新指定组网包的六份文件；保留其他知识、草稿、案例、报告和自定义模板。",
                preserved=[*plan["preserved"], "unrelated_knowledge", "drafts", "global_memories", "custom_templates"])
    plan["counts"].update(added_files=sum(i["disposition"] == "ADD" for i in inspection),
                          retired_documents=0 if unchanged else len(retired))
    return plan


def retire_replaced_bundle(db, snapshot, value):
    ids = set(value["approved_plan"].get("retired_document_ids", []))
    reset._retire(db, {**snapshot, "documents": [d for d in snapshot["documents"] if d.id in ids],
                      "drafts": [], "memories": [], "templates": []}, value)
