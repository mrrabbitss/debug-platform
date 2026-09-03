"""add persistent provider-neutral host agent sessions

Revision ID: 0017
Revises: 0016
"""

from alembic import op
import sqlalchemy as sa


revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    # ``Base.metadata.create_all`` remains a supported legacy adoption path.
    # In that case the ORM table may already exist when Alembic reaches 0017.
    if "host_agent_sessions" in _tables():
        return

    op.create_table(
        "host_agent_sessions",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column(
            "case_id",
            sa.String(length=40),
            sa.ForeignKey("cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_run_id",
            sa.String(length=40),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("executor", sa.String(length=64), nullable=False),
        sa.Column(
            "client_model_claim",
            sa.String(length=512),
            nullable=False,
            server_default="unknown",
        ),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("skill_version", sa.String(length=128), nullable=False),
        sa.Column("case_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("parse_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("method_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("coverage_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("planning_rounds_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column(
            "allowed_evidence_ids_json",
            sa.Text(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("tool_receipts_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("evidence_cache_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default="CREATED",
        ),
        sa.Column("status_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("lease_owner", sa.String(length=160), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "agent_run_id", name="uq_host_agent_sessions_agent_run_id"
        ),
    )

    for name, columns in (
        ("ix_host_agent_sessions_case_id", ["case_id"]),
        ("ix_host_agent_sessions_agent_run_id", ["agent_run_id"]),
        ("ix_host_agent_sessions_executor", ["executor"]),
        ("ix_host_agent_sessions_case_snapshot_hash", ["case_snapshot_hash"]),
        ("ix_host_agent_sessions_parse_snapshot_hash", ["parse_snapshot_hash"]),
        ("ix_host_agent_sessions_method_snapshot_hash", ["method_snapshot_hash"]),
        ("ix_host_agent_sessions_status", ["status"]),
        ("ix_host_agent_sessions_lease_expires_at", ["lease_expires_at"]),
        ("ix_host_agent_sessions_expires_at", ["expires_at"]),
        ("ix_host_agent_sessions_case_created", ["case_id", "created_at"]),
        ("ix_host_agent_sessions_status_expires", ["status", "expires_at"]),
        ("ix_host_agent_sessions_lease", ["lease_owner", "lease_expires_at"]),
    ):
        op.create_index(name, "host_agent_sessions", columns)


def downgrade() -> None:
    if "host_agent_sessions" in _tables():
        op.drop_table("host_agent_sessions")
