"""Copy unchanged text to a new metadata revision without modifying old evidence."""
from sqlalchemy import select

from app.core.utils import json_dumps, json_loads, new_id
from app.models import KnowledgeChunk, KnowledgeEmbedding


def copy_revision_chunks(db, document, previous_version: int) -> None:
    originals = list(db.scalars(select(KnowledgeChunk).where(
        KnowledgeChunk.document_id == document.id, KnowledgeChunk.document_version == previous_version)))
    for original in originals:
        clone = KnowledgeChunk(id=new_id("CHK"), document_id=document.id, document_version=document.version,
                               chunk_index=original.chunk_index, heading=original.heading, content=original.content,
                               token_estimate=original.token_estimate,
                               metadata_json=json_dumps({**json_loads(original.metadata_json, {}), "source_type": document.source_type}))
        db.add(clone)
        db.flush()
        for vector in db.scalars(select(KnowledgeEmbedding).where(KnowledgeEmbedding.chunk_id == original.id)):
            db.add(KnowledgeEmbedding(id=new_id("VEC"), chunk_id=clone.id, profile_id=vector.profile_id,
                                     generation_id=vector.generation_id, dimension=vector.dimension, vector_json=vector.vector_json))
