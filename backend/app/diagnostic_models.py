from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class LogTriageRun(Base):
    __tablename__ = "log_triage_runs"
    __table_args__ = (
        Index("ix_log_triage_case_created", "case_id", "created_at"),
        Index("ix_log_triage_artifact_created", "artifact_id", "created_at"),
        Index("ix_log_triage_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    parse_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    issue_snapshot: Mapped[str] = mapped_column(Text, default="")
    model_profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    method_coverage_json: Mapped[str] = mapped_column(Text, default="{}")
    plan_json: Mapped[str] = mapped_column(Text, default="{}")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class LogEvidenceMatch(Base):
    __tablename__ = "log_evidence_matches"
    __table_args__ = (
        Index(
            "ix_log_evidence_triage_bucket_score",
            "triage_run_id",
            "bucket",
            "relevance_score",
        ),
        Index(
            "ix_log_evidence_source_line",
            "artifact_id",
            "source_file",
            "line_start",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    triage_run_id: Mapped[str] = mapped_column(
        ForeignKey("log_triage_runs.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    source_file: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int] = mapped_column(Integer, default=1)
    line_end: Mapped[int] = mapped_column(Integer, default=1)
    bucket: Mapped[str] = mapped_column(String(32), index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    pattern_id: Mapped[str] = mapped_column(String(96), index=True)
    pattern_text: Mapped[str] = mapped_column(Text)
    match_kind: Mapped[str] = mapped_column(String(32), default="literal")
    reason: Mapped[str] = mapped_column(Text, default="")
    meaning: Mapped[str] = mapped_column(Text, default="")
    method_document_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    method_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1)
    first_timestamp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_timestamp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LogEvidenceHit(Base):
    """One exact source location for a grouped log triage match."""

    __tablename__ = "log_evidence_hits"
    __table_args__ = (
        Index("ix_log_evidence_hit_match_line", "match_id", "line_start"),
        Index(
            "ix_log_evidence_hit_triage_source_line",
            "triage_run_id",
            "source_file",
            "line_start",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    triage_run_id: Mapped[str] = mapped_column(
        ForeignKey("log_triage_runs.id", ondelete="CASCADE"), index=True
    )
    match_id: Mapped[str] = mapped_column(
        ForeignKey("log_evidence_matches.id", ondelete="CASCADE"), index=True
    )
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    source_file: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int] = mapped_column(Integer)
    line_end: Mapped[int] = mapped_column(Integer)
    timestamp: Mapped[str | None] = mapped_column(String(128), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LogEvidenceOccurrence(Base):
    __tablename__ = "log_evidence_occurrences"
    __table_args__ = (
        UniqueConstraint(
            "triage_run_id",
            "event_id",
            name="uq_log_evidence_occurrence_event",
        ),
        Index(
            "ix_log_evidence_occurrence_triage_bucket",
            "triage_run_id",
            "bucket",
        ),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    triage_run_id: Mapped[str] = mapped_column(
        ForeignKey("log_triage_runs.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(
        ForeignKey("log_events.id", ondelete="CASCADE"), index=True
    )
    bucket: Mapped[str] = mapped_column(String(32), index=True)
    relevance_score: Mapped[float] = mapped_column(Float, default=0.0)
    pattern_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisRevision(Base):
    __tablename__ = "analysis_revisions"
    __table_args__ = (
        Index("ix_analysis_revisions_case_created", "case_id", "created_at"),
        Index("ix_analysis_revisions_source_status", "source_analysis_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    source_analysis_id: Mapped[str] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="CASCADE"), index=True
    )
    applied_analysis_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source_message_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversation_messages.id", ondelete="SET NULL"), nullable=True, index=True
    )
    job_id: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="QUEUED", index=True)
    instruction: Mapped[str] = mapped_column(Text)
    proposed_result_json: Mapped[str] = mapped_column(Text, default="{}")
    proposed_evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    change_summary: Mapped[str] = mapped_column(Text, default="")
    validation_json: Mapped[str] = mapped_column(Text, default="{}")
    model_profile_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
