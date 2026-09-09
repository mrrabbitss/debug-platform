"""Private contributions, immutable review revisions and durable publication approvals."""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class KnowledgeContribution(Base):
    __tablename__ = "knowledge_contributions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    operation: Mapped[str] = mapped_column(String(16))
    content_kind: Mapped[str] = mapped_column(String(16), default="KNOWLEDGE", index=True)
    target_document_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=True)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_lock_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_curation_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_curation_sessions.id"), nullable=True, unique=True)
    source_library_id: Mapped[str | None] = mapped_column(
        ForeignKey("workbench_records.id"), nullable=True, unique=True)
    candidate_json: Mapped[str] = mapped_column(Text)
    original_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(24), default="DRAFT", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str] = mapped_column(String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    publication_job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"), nullable=True)
    published_document_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=True)
    building_generation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    worker_token: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class KnowledgeContributionRevision(Base):
    __tablename__ = "knowledge_contribution_revisions"
    __table_args__ = (UniqueConstraint("contribution_id", "version", name="uq_contribution_revision_version"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    contribution_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_contributions.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(32))
    candidate_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    diff: Mapped[str] = mapped_column(Text, default="")
    actor_id: Mapped[str] = mapped_column(String(128))
    comment: Mapped[str] = mapped_column(Text, default="")
    messages_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
