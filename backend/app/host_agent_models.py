from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.utils import utcnow


class HostAgentSession(Base):
    __tablename__ = "host_agent_sessions"
    __table_args__ = (
        UniqueConstraint("agent_run_id", name="uq_host_agent_sessions_agent_run_id"),
        Index("ix_host_agent_sessions_case_created", "case_id", "created_at"),
        Index("ix_host_agent_sessions_status_expires", "status", "expires_at"),
        Index("ix_host_agent_sessions_lease", "lease_owner", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )
    agent_run_id: Mapped[str] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True
    )
    executor: Mapped[str] = mapped_column(String(64), index=True)
    client_model_claim: Mapped[str] = mapped_column(String(512), default="unknown")
    prompt_version: Mapped[str] = mapped_column(String(128))
    skill_version: Mapped[str] = mapped_column(String(128))
    case_snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    parse_snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    method_snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    coverage_json: Mapped[str] = mapped_column(Text, default="{}")
    planning_rounds_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    tool_receipts_json: Mapped[str] = mapped_column(Text, default="[]")
    evidence_cache_json: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(32), default="CREATED", index=True)
    status_reason: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    lease_owner: Mapped[str | None] = mapped_column(String(160), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Mutations use an explicit compare-and-swap UPDATE in the service. This
    # mapper guard also prevents an accidentally stale ORM writer from
    # overwriting a newer MCP/HTTP request.
    __mapper_args__ = {
        "version_id_col": version,
        "version_id_generator": False,
    }
