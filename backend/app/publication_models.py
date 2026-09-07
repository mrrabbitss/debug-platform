"""Editable proposals are separate from the currently published knowledge projection."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class KnowledgeDraft(Base):
    __tablename__ = "knowledge_drafts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    base_version: Mapped[int] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_key: Mapped[str] = mapped_column(String(128), default="", server_default="")
    publication_job_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    building_generation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __mapper_args__ = {"version_id_col": version}
    __table_args__ = (Index("uq_knowledge_draft_owner", "document_id", "owner_key", unique=True),)


class KnowledgePublication(Base):
    __tablename__ = "knowledge_publications"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    document_version: Mapped[int] = mapped_column(Integer)
    manifest_json: Mapped[str] = mapped_column(Text)
    published_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeWorkingRevision(Base):
    """Immutable personal source: runs pin IDs instead of mutable draft contents."""
    __tablename__ = "knowledge_working_revisions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    draft_id: Mapped[str] = mapped_column(ForeignKey("knowledge_drafts.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    owner_key: Mapped[str] = mapped_column(String(128), index=True)
    draft_version: Mapped[int] = mapped_column(Integer)
    base_version: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (Index("uq_working_revision", "draft_id", "draft_version", unique=True),)
