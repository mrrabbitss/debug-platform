from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.utils import utcnow


class UserAccount(Base):
    __tablename__ = "user_accounts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(32), default="VIEWER", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AccessToken(Base):
    __tablename__ = "access_tokens"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="default")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_hint: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Case(Base):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
    device_type: Mapped[str] = mapped_column(String(32), default="GW")
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    firmware_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    topology: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    reproduction_steps: Mapped[str | None] = mapped_column(Text, nullable=True)
    issue_time: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="DRAFT", index=True)
    severity: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    model_egress_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    artifacts: Mapped[list["Artifact"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    events: Mapped[list["LogEvent"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    analyses: Mapped[list["AnalysisRun"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class CaseMember(Base):
    __tablename__ = "case_members"
    __table_args__ = (UniqueConstraint("case_id", "user_id", name="uq_case_member"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("user_accounts.id", ondelete="CASCADE"), index=True)
    permission: Mapped[str] = mapped_column(String(32), default="VIEWER")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Artifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), default="debug_log")
    original_name: Mapped[str] = mapped_column(String(512))
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    source_device_type: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    source_device_role: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    active_parse_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case | None] = relationship(back_populates="artifacts")


class LogEvent(Base):
    __tablename__ = "log_events"
    __table_args__ = (
        Index("ix_log_events_case_time", "case_id", "timestamp_normalized", "line_start"),
        Index("ix_log_events_case_level", "case_id", "level"),
        Index("ix_log_events_case_module", "case_id", "module"),
        Index("ix_log_events_artifact_parse_run", "artifact_id", "parse_run_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    parse_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_file: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int] = mapped_column(Integer, default=1)
    line_end: Mapped[int] = mapped_column(Integer, default=1)
    timestamp_raw: Mapped[str | None] = mapped_column(String(128), nullable=True)
    timestamp_normalized: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    level: Mapped[str] = mapped_column(String(16), default="INFO", index=True)
    module: Mapped[str] = mapped_column(String(64), default="UNKNOWN", index=True)
    component: Mapped[str] = mapped_column(String(128), default="unknown", index=True)
    event_code: Mapped[str] = mapped_column(String(128), default="GENERIC_LOG", index=True)
    message: Mapped[str] = mapped_column(Text)
    raw_text: Mapped[str] = mapped_column(Text)
    entities_json: Mapped[str] = mapped_column(Text, default="{}")
    parser_id: Mapped[str] = mapped_column(String(64), default="generic")
    parser_version: Mapped[str] = mapped_column(String(32), default="1.0")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    case: Mapped[Case] = relationship(back_populates="events")


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(512))
    source_type: Mapped[str] = mapped_column(String(64), default="document", index=True)
    device_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    firmware_range: Mapped[str | None] = mapped_column(String(255), nullable=True)
    module: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trust_level: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    confidentiality: Mapped[str] = mapped_column(String(32), default="INTERNAL")
    content: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    review_status: Mapped[str] = mapped_column(String(32), default="ACTIVE", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Governance operations increment lock_version explicitly. SQLAlchemy then
    # includes the previously loaded value in UPDATE statements, so two writers
    # cannot both publish edits that were based on the same document version.
    __mapper_args__ = {
        "version_id_col": lock_version,
        "version_id_generator": False,
    }

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="document", cascade="all, delete-orphan")


class KnowledgeRevision(Base):
    __tablename__ = "knowledge_revisions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_knowledge_revision_version"),
        Index("ix_knowledge_revisions_document_created", "document_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    snapshot_json: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    change_summary: Mapped[str] = mapped_column(String(512), default="")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeCurationSession(Base):
    __tablename__ = "knowledge_curation_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    title_hint: Mapped[str] = mapped_column(String(512), default="")
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_categories.id", ondelete="SET NULL"), nullable=True, index=True
    )
    device_type: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    device_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    firmware_range: Mapped[str | None] = mapped_column(String(255), nullable=True)
    module: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    trust_level: Mapped[str] = mapped_column(String(16), default="MEDIUM")
    confidentiality: Mapped[str] = mapped_column(String(32), default="RESTRICTED")
    model_profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    source_manifest_json: Mapped[str] = mapped_column(Text, default="{}")
    draft_title: Mapped[str] = mapped_column(String(512), default="")
    draft_markdown: Mapped[str] = mapped_column(Text, default="")
    draft_version: Mapped[int] = mapped_column(Integer, default=0)
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    open_questions_json: Mapped[str] = mapped_column(Text, default="[]")
    knowledge_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        nullable=True,
        unique=True,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeCurationSourceFile(Base):
    __tablename__ = "knowledge_curation_source_files"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "relative_path",
            name="uq_knowledge_curation_source_path",
        ),
        Index(
            "ix_knowledge_curation_sources_session_created",
            "session_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_curation_sessions.id", ondelete="CASCADE"), index=True
    )
    source_ref: Mapped[str] = mapped_column(String(32))
    relative_path: Mapped[str] = mapped_column(Text)
    stored_path: Mapped[str] = mapped_column(Text)
    extracted_text_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_text_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    extraction_truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    text_encoding: Mapped[str | None] = mapped_column(String(64), nullable=True)
    line_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_role: Mapped[str] = mapped_column(String(32), default="context", index=True)
    included: Mapped[bool] = mapped_column(Boolean, default=True)
    skip_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeCurationRevision(Base):
    __tablename__ = "knowledge_curation_revisions"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "version",
            name="uq_knowledge_curation_revision_version",
        ),
        Index(
            "ix_knowledge_curation_revisions_session_created",
            "session_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_curation_sessions.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    markdown: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    change_summary: Mapped[str] = mapped_column(String(512), default="")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    source_message_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeCurationMessage(Base):
    __tablename__ = "knowledge_curation_messages"
    __table_args__ = (
        Index(
            "ix_knowledge_curation_messages_session_created",
            "session_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_curation_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    draft_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    heading: Mapped[str | None] = mapped_column(String(512), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    token_estimate: Mapped[int] = mapped_column(Integer, default=0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")

    document: Mapped[KnowledgeDocument] = relationship(back_populates="chunks")
    embeddings: Mapped[list["KnowledgeEmbedding"]] = relationship(cascade="all, delete-orphan")


class KnowledgeCategory(Base):
    __tablename__ = "knowledge_categories"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    code: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_categories.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    system: Mapped[bool] = mapped_column(Boolean, default=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class KnowledgeDocumentCategory(Base):
    __tablename__ = "knowledge_document_categories"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_categories.id", ondelete="RESTRICT"), index=True
    )


class KnowledgeDerivation(Base):
    __tablename__ = "knowledge_derivations"
    __table_args__ = (
        UniqueConstraint(
            "source_document_id",
            "derivation_type",
            name="uq_knowledge_derivation_source_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source_document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    derived_document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    derivation_type: Mapped[str] = mapped_column(String(64), index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class KnowledgeGraphState(Base):
    __tablename__ = "knowledge_graph_states"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    active_generation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    building_generation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="NOT_BUILT", index=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class KnowledgeEntity(Base):
    __tablename__ = "knowledge_entities"
    __table_args__ = (
        UniqueConstraint(
            "generation_id",
            "entity_type",
            "normalized_name",
            name="uq_knowledge_entity_generation_name",
        ),
        Index(
            "ix_knowledge_entities_generation_type",
            "generation_id",
            "entity_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    logical_id: Mapped[str] = mapped_column(String(40), index=True)
    generation_id: Mapped[str] = mapped_column(String(40), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    canonical_name: Mapped[str] = mapped_column(String(512))
    normalized_name: Mapped[str] = mapped_column(String(512), index=True)
    aliases_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class KnowledgeEntityMention(Base):
    __tablename__ = "knowledge_entity_mentions"
    __table_args__ = (
        Index(
            "ix_knowledge_mentions_generation_document",
            "generation_id",
            "document_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    generation_id: Mapped[str] = mapped_column(String(40), index=True)
    entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), nullable=True, index=True
    )
    excerpt: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class KnowledgeRelation(Base):
    __tablename__ = "knowledge_relations"
    __table_args__ = (
        Index(
            "ix_knowledge_relations_generation_source",
            "generation_id",
            "source_entity_id",
        ),
        Index(
            "ix_knowledge_relations_generation_target",
            "generation_id",
            "target_entity_id",
        ),
        Index(
            "ix_knowledge_relations_generation_type",
            "generation_id",
            "relation_type",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    logical_id: Mapped[str] = mapped_column(String(40), index=True)
    generation_id: Mapped[str] = mapped_column(String(40), index=True)
    source_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True
    )
    target_entity_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_entities.id", ondelete="CASCADE"), index=True
    )
    relation_type: Mapped[str] = mapped_column(String(64), index=True)
    evidence_document_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True
    )
    evidence_chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ModelProfile(Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        Index(
            "uq_model_profiles_active_task",
            "task_type",
            unique=True,
            sqlite_where=text("is_active = 1"),
            postgresql_where=text("is_active = true"),
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    task_type: Mapped[str] = mapped_column(String(32), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    provider: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(512), default="")
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    proxy_url_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    proxy_url_hint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    active_embedding_generation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class KnowledgeEmbedding(Base):
    __tablename__ = "knowledge_embeddings"
    __table_args__ = (
        UniqueConstraint(
            "chunk_id",
            "profile_id",
            "generation_id",
            name="uq_knowledge_embedding_generation",
        ),
        Index(
            "ix_knowledge_embeddings_profile_generation",
            "profile_id",
            "generation_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    chunk_id: Mapped[str] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="CASCADE"), index=True
    )
    generation_id: Mapped[str] = mapped_column(String(40), default="legacy", index=True)
    dimension: Mapped[int] = mapped_column(Integer)
    vector_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Repository(Base):
    __tablename__ = "repositories"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    root_path: Mapped[str] = mapped_column(Text)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="UPLOADED")
    graph_status: Mapped[str] = mapped_column(String(32), default="NOT_INDEXED", index=True)
    active_graph_generation_id: Mapped[str | None] = mapped_column(
        String(40), nullable=True, index=True
    )
    commit_graph_status: Mapped[str] = mapped_column(String(32), default="NOT_INDEXED", index=True)
    index_metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CodeSymbol(Base):
    __tablename__ = "code_symbols"
    __table_args__ = (
        Index("ix_code_symbols_repo_generation", "repository_id", "generation_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    repository_id: Mapped[str] = mapped_column(ForeignKey("repositories.id", ondelete="CASCADE"), index=True)
    generation_id: Mapped[str] = mapped_column(String(40), default="legacy", index=True)
    logical_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    file_path: Mapped[str] = mapped_column(Text, index=True)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str] = mapped_column(Text)
    module: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    calls_json: Mapped[str] = mapped_column(Text, default="[]")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class CodeRelation(Base):
    __tablename__ = "code_relations"
    __table_args__ = (
        Index("ix_code_relations_repo_type", "repository_id", "relation_type"),
        Index("ix_code_relations_repo_generation", "repository_id", "generation_id"),
        Index("ix_code_relations_source_target", "source_symbol_id", "target_symbol_id"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    generation_id: Mapped[str] = mapped_column(String(40), default="legacy", index=True)
    logical_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    source_symbol_id: Mapped[str] = mapped_column(
        ForeignKey("code_symbols.id", ondelete="CASCADE"), index=True
    )
    target_symbol_id: Mapped[str | None] = mapped_column(
        ForeignKey("code_symbols.id", ondelete="CASCADE"), nullable=True, index=True
    )
    target_name: Mapped[str] = mapped_column(String(512))
    relation_type: Mapped[str] = mapped_column(String(32), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    evidence_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommitRecord(Base):
    __tablename__ = "commit_records"
    __table_args__ = (
        UniqueConstraint("repository_id", "commit_hash", name="uq_commit_repository_hash"),
        Index("ix_commit_records_repo_time", "repository_id", "authored_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    commit_hash: Mapped[str] = mapped_column(String(64), index=True)
    parent_hashes_json: Mapped[str] = mapped_column(Text, default="[]")
    author_name: Mapped[str] = mapped_column(String(255), default="")
    authored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CommitFileChange(Base):
    __tablename__ = "commit_file_changes"
    __table_args__ = (
        Index("ix_commit_file_changes_repo_path", "repository_id", "file_path"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    commit_id: Mapped[str] = mapped_column(
        ForeignKey("commit_records.id", ondelete="CASCADE"), index=True
    )
    change_type: Mapped[str] = mapped_column(String(16), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    old_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class AgentMemory(Base):
    __tablename__ = "agent_memories"
    __table_args__ = (
        Index("ix_agent_memories_kind_outcome", "memory_type", "outcome"),
        Index("ix_agent_memories_case_kind", "case_id", "memory_type"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    memory_type: Mapped[str] = mapped_column(String(32), index=True)
    source_kind: Mapped[str] = mapped_column(String(64), index=True)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(512))
    content: Mapped[str] = mapped_column(Text)
    context_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    outcome: Mapped[str] = mapped_column(String(32), default="UNKNOWN", index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    reuse_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    provider: Mapped[str] = mapped_column(String(64), default="mock")
    model: Mapped[str] = mapped_column(String(512), default="rule-engine")
    model_profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_config_json: Mapped[str] = mapped_column(Text, default="{}")
    prompt_version: Mapped[str] = mapped_column(String(32), default="v2-evidence-validated")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    case: Mapped[Case] = relationship(back_populates="analyses")


class DiagnosisFeedback(Base):
    __tablename__ = "diagnosis_feedback"
    __table_args__ = (
        Index("ix_diagnosis_feedback_case_created", "case_id", "created_at"),
        Index("ix_diagnosis_feedback_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    analysis_run_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    verdict: Mapped[str] = mapped_column(String(32), index=True)
    root_cause_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evidence_correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    comment: Mapped[str] = mapped_column(Text, default="")
    corrections_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="SUBMITTED", index=True)
    submitted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    incorporated_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_case_created", "case_id", "created_at"),
        Index("ix_agent_runs_resource_created", "resource_type", "resource_id", "created_at"),
        Index("ix_agent_runs_operation_status", "operation", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str | None] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=True, index=True
    )
    resource_type: Mapped[str] = mapped_column(String(64), default="case", index=True)
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(64), index=True)
    execution_mode: Mapped[str] = mapped_column(String(32), default="deterministic")
    status: Mapped[str] = mapped_column(String(32), default="RUNNING", index=True)
    model_profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    model_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    model_config_json: Mapped[str] = mapped_column(Text, default="{}")
    prompt_version: Mapped[str] = mapped_column(String(128), default="")
    input_summary_hash: Mapped[str] = mapped_column(String(64))
    output_summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    stop_reason: Mapped[str] = mapped_column(String(128), default="UNKNOWN", index=True)
    approval_status: Mapped[str] = mapped_column(String(64), default="NOT_REQUIRED", index=True)
    replay_of_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    replay_payload_json: Mapped[str] = mapped_column(Text, default="{}")
    score_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTraceEvent(Base):
    __tablename__ = "agent_trace_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_agent_trace_event_sequence"),
        Index("ix_agent_trace_events_run_created", "run_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(128), index=True)
    tool_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", index=True)
    input_summary_hash: Mapped[str] = mapped_column(String(64))
    output_summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    stop_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_idempotency_key", "idempotency_key"),
        Index("ix_jobs_dispatch", "status", "available_at", "created_at"),
        Index("ix_jobs_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    resource_limits_json: Mapped[str] = mapped_column(Text, default="{}")
    dead_letter_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dead_letter_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RetrievalEvaluationDataset(Base):
    __tablename__ = "retrieval_evaluation_datasets"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RetrievalEvaluationCase(Base):
    __tablename__ = "retrieval_evaluation_cases"
    __table_args__ = (
        Index(
            "ix_retrieval_evaluation_cases_dataset_created",
            "dataset_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evaluation_datasets.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    query: Mapped[str] = mapped_column(Text)
    expected_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    expected_root_causes_json: Mapped[str] = mapped_column(Text, default="[]")
    modules_json: Mapped[str] = mapped_column(Text, default="[]")
    top_k: Mapped[int] = mapped_column(Integer, default=10)
    max_hops: Mapped[int] = mapped_column(Integer, default=2)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class RetrievalEvaluationRun(Base):
    __tablename__ = "retrieval_evaluation_runs"
    __table_args__ = (
        Index(
            "ix_retrieval_evaluation_runs_dataset_created",
            "dataset_id",
            "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    dataset_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evaluation_datasets.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    results_json: Mapped[str] = mapped_column(Text, default="[]")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    citations_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", index=True)
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        UniqueConstraint(
            "case_id",
            "analysis_run_id",
            "format",
            "version",
            name="uq_report_case_analysis_format_version",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    analysis_run_id: Mapped[str] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True)
    format: Mapped[str] = mapped_column(String(16))
    version: Mapped[int] = mapped_column(Integer, default=1)
    stored_path: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_action_created", "action", "created_at"),
        Index("ix_audit_events_case_created", "case_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    actor_type: Mapped[str] = mapped_column(String(32), default="system")
    action: Mapped[str] = mapped_column(String(128), index=True)
    resource_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    case_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(32), default="SUCCESS", index=True)
    ip_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


from app import diagnostic_models as _diagnostic_models  # noqa: E402, F401
