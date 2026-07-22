import re
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.core.utils import json_dumps, json_loads, new_id
from app.models import KnowledgeChunk, KnowledgeDocument, KnowledgeEmbedding, ModelProfile
from app.services.jobs import JobContext
from app.services.retrieval_models import RetrievalModelError, index_active_embeddings, reindex_all_embeddings


def chunk_document(content: str, max_chars: int = 1800, overlap_chars: int = 180) -> list[tuple[str | None, str]]:
    lines = content.replace("\r\n", "\n").split("\n")
    sections: list[tuple[str | None, str]] = []
    heading: str | None = None
    buffer: list[str] = []
    for line in lines:
        if re.match(r"^#{1,6}\s+", line):
            if buffer:
                sections.append((heading, "\n".join(buffer).strip()))
                buffer = []
            heading = re.sub(r"^#{1,6}\s+", "", line).strip()
        else:
            buffer.append(line)
    if buffer:
        sections.append((heading, "\n".join(buffer).strip()))

    chunks: list[tuple[str | None, str]] = []
    for current_heading, section in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section) if p.strip()]
        current = ""
        for paragraph in paragraphs:
            candidate = f"{current}\n\n{paragraph}".strip()
            if current and len(candidate) > max_chars:
                chunks.append((current_heading, current))
                current = (current[-overlap_chars:] + "\n\n" + paragraph).strip()
            else:
                current = candidate
        if current:
            chunks.append((current_heading, current))
    if not chunks and content.strip():
        chunks.append((None, content[:max_chars]))
    return chunks


def index_document(db: Session, document: KnowledgeDocument) -> int:
    old_chunk_ids = select(KnowledgeChunk.id).where(KnowledgeChunk.document_id == document.id)
    db.execute(delete(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id.in_(old_chunk_ids)))
    db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id))
    chunks = chunk_document(document.content)
    for index, (heading, content) in enumerate(chunks):
        db.add(KnowledgeChunk(
            id=new_id("CHK"), document_id=document.id, chunk_index=index, heading=heading,
            content=content, token_estimate=max(1, len(content) // 3),
            metadata_json=json_dumps({"title": document.title, "source_type": document.source_type}),
        ))
    db.commit()
    persisted = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)).all()
    try:
        indexed_vectors = index_active_embeddings(db, persisted)
        metadata = json_loads(document.metadata_json, {})
        metadata["embedding_status"] = "INDEXED"
        metadata["embedding_vector_count"] = indexed_vectors
        metadata.pop("embedding_error", None)
        document.metadata_json = json_dumps(metadata)
        db.commit()
    except RetrievalModelError as exc:
        db.rollback()
        document = db.get(KnowledgeDocument, document.id)
        if document:
            metadata = json_loads(document.metadata_json, {})
            metadata["embedding_status"] = "FAILED"
            metadata["embedding_error"] = str(exc)
            document.metadata_json = json_dumps(metadata)
            db.commit()
    return len(chunks)


def reindex_knowledge_job(ctx: JobContext, profile_id: str) -> dict:
    with SessionLocal() as db:
        profile = db.get(ModelProfile, profile_id)
        if not profile or profile.task_type != "embedding":
            raise ValueError("Embedding model profile not found")

        def update_progress(completed: int, total: int) -> None:
            progress = 10 + int(80 * completed / max(total, 1))
            ctx.update(progress, f"Embedding knowledge chunks: {completed}/{total}")

        ctx.update(5, f"Loading embedding model: {profile.name}")
        count = reindex_all_embeddings(db, profile, update_progress)
        documents = list(db.scalars(select(KnowledgeDocument)).all())
        for document in documents:
            metadata = json_loads(document.metadata_json, {})
            metadata["embedding_status"] = "INDEXED"
            metadata["embedding_profile_id"] = profile.id
            metadata.pop("embedding_error", None)
            document.metadata_json = json_dumps(metadata)
        db.commit()
    ctx.update(95, "Knowledge embedding index rebuilt")
    return {"profile_id": profile_id, "vectors": count}


def seed_builtin_knowledge(db: Session, seed_dir: Path) -> int:
    existing = db.scalar(select(KnowledgeDocument.id).limit(1))
    if existing:
        return 0
    count = 0
    for path in sorted(seed_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        title = content.splitlines()[0].lstrip("# ").strip() if content.splitlines() else path.stem
        document = KnowledgeDocument(
            id=new_id("DOC"), title=title, source_type="builtin_rule", trust_level="HIGH",
            confidentiality="INTERNAL", content=content,
            metadata_json=json_dumps({"seed_file": path.name}),
        )
        lower = path.stem.lower()
        if "wifi" in lower or "ap" in lower:
            document.device_type = "AP"
            document.module = "WLAN"
        elif "gw" in lower or "wan" in lower:
            document.device_type = "GW"
            document.module = "WAN"
        db.add(document)
        db.flush()
        index_document(db, document)
        count += 1
    return count
