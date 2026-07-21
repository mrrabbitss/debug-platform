import re
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.utils import json_dumps, new_id
from app.models import KnowledgeChunk, KnowledgeDocument
from app.services.vector_store import get_vector_store


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
    store = get_vector_store()
    if store.enabled:
        store.delete_document(document.id)
        store.upsert({"id": chunk.id, "text": chunk.content, "payload": {"document_id": document.id, "title": document.title, "module": document.module}} for chunk in persisted)
    return len(chunks)


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
