"""Content-addressed publication snapshots reused by running diagnoses."""
import hashlib
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.utils import json_dumps, json_loads
from app.models import KnowledgeChunk, KnowledgeDocument
from app.workbench_models import WorkbenchRecord


DOCUMENT_FIELDS = ("id", "title", "source_type", "device_type", "device_model", "firmware_range",
                   "module", "trust_level", "confidentiality", "content", "metadata_json",
                   "active", "review_status", "version", "lock_version")
CHUNK_FIELDS = ("id", "document_id", "document_version", "chunk_index", "heading", "content",
                "token_estimate", "metadata_json")


def library_material(db):
    """Reviewed historical cases are guidance, never facts about the current case."""
    from app.services.knowledge import chunk_document
    result = []
    for record in db.scalars(select(WorkbenchRecord).where(WorkbenchRecord.kind == "library")):
        value = json_loads(record.payload_json, {})
        if value.get("status") != "CONFIRMED":
            continue
        content = value["content"] + ("\n\n# 已确认报告\n" + value["report_markdown"] if value.get("report_markdown") else "")
        doc = KnowledgeDocument(id=record.id, title=value["title"], source_type="fault_case", device_type="GENERAL",
            content=content, active=True, review_status="ACTIVE", version=record.version, lock_version=record.version,
            trust_level="HIGH", confidentiality="INTERNAL", metadata_json=json_dumps({
                "problem_categories": [value.get("problem_category", "unknown")], "knowledge_role": "prior_knowledge",
                "library_record_id": record.id, "source_case_id": value.get("case_id"), "human_confirmed": True}))
        chunks = [SimpleNamespace(id="LIB-" + hashlib.sha256(f"{record.id}:{record.version}:{index}".encode()).hexdigest()[:32],
            document_id=record.id, document_version=record.version, chunk_index=index, heading=heading,
            content=text, token_estimate=max(1, len(text)//3), metadata_json="{}")
            for index, (heading, text) in enumerate(chunk_document(content))]
        result.append((doc, chunks))
    return result


def capture_knowledge(db):
    documents = list(db.scalars(select(KnowledgeDocument).where(
        KnowledgeDocument.active.is_(True), KnowledgeDocument.review_status == "ACTIVE",
        KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"])).order_by(KnowledgeDocument.id)))
    if len(documents) > 5000:
        raise ValueError("可用知识超过5000篇，请先整理知识范围；没有截断诊断快照")
    materials = [(doc, db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == doc.id,
        KnowledgeChunk.document_version == doc.version).order_by(KnowledgeChunk.chunk_index)).all()) for doc in documents]
    materials.extend(library_material(db))
    references, total = [], 0
    for doc, chunks in materials:
        payload = {"document": {key: getattr(doc, key) for key in DOCUMENT_FIELDS},
                   "chunks": [{key: getattr(chunk, key) for key in CHUNK_FIELDS} for chunk in chunks]}
        content = json_dumps(payload)
        total += len(content.encode())
        if total > 64 * 1024 * 1024:
            raise ValueError("知识快照超过64MiB，请先整理知识范围；没有截断")
        key = "KS-" + hashlib.sha256(content.encode()).hexdigest()
        if db.get(WorkbenchRecord, key) is None:
            try:
                with db.begin_nested():
                    db.add(WorkbenchRecord(id=key, kind="knowledge_snapshot", payload_json=content))
                    db.flush()
            except IntegrityError:
                if db.get(WorkbenchRecord, key) is None:
                    raise
        references.append(key)
    return references


def load_knowledge(db, references):
    """Return detached document/chunk values; published edits cannot alter a run."""
    documents, rows = [], []
    for key in references:
        record = db.get(WorkbenchRecord, key)
        if record is None or record.kind != "knowledge_snapshot":
            raise ValueError("诊断知识快照不可用，请恢复对应备份后继续")
        if key != "KS-" + hashlib.sha256(record.payload_json.encode()).hexdigest():
            raise ValueError("诊断知识快照校验失败")
        value = json_loads(record.payload_json, {})
        doc = KnowledgeDocument(**value["document"])
        documents.append(doc)
        rows.extend((SimpleNamespace(**chunk), doc) for chunk in value["chunks"])
    return documents, rows
