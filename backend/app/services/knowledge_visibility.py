"""All live lexical, dense and graph retrieval uses the same chunk-version boundary."""
from app.models import KnowledgeChunk, KnowledgeDocument


def current_chunk_clause():
    return (KnowledgeChunk.document_version == KnowledgeDocument.version) & KnowledgeDocument.confidentiality.in_(["PUBLIC", "INTERNAL"])
