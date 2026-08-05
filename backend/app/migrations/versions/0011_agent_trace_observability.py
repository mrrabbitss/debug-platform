"""add redacted agent run and trace observability

Revision ID: 0011
Revises: 0010
"""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ``Base.metadata.create_all`` was the legacy installation path.  Such a
    # database can already contain both tables before Alembic adopts it at the
    # baseline revision, so keep the forward migration idempotent for that
    # supported upgrade path.
    existing_tables = set(sa.inspect(op.get_bind()).get_table_names())
    if {"agent_runs", "agent_trace_events"}.issubset(existing_tables):
        return
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("case_id", sa.String(length=40), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=True),
        sa.Column("resource_type", sa.String(length=64), nullable=False, server_default="case"),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=64), nullable=False),
        sa.Column("execution_mode", sa.String(length=32), nullable=False, server_default="deterministic"),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RUNNING"),
        sa.Column("model_profile_id", sa.String(length=40), nullable=True),
        sa.Column("model_name", sa.String(length=512), nullable=True),
        sa.Column("model_config_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("prompt_version", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("input_summary_hash", sa.String(length=64), nullable=False),
        sa.Column("output_summary_hash", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("estimated_cost", sa.Float(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("stop_reason", sa.String(length=128), nullable=False, server_default="UNKNOWN"),
        sa.Column("approval_status", sa.String(length=64), nullable=False, server_default="NOT_REQUIRED"),
        sa.Column("replay_of_run_id", sa.String(length=40), nullable=True),
        sa.Column("replay_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("score_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_agent_runs_case_id", "agent_runs", ["case_id"])
    op.create_index("ix_agent_runs_resource_id", "agent_runs", ["resource_id"])
    op.create_index("ix_agent_runs_operation", "agent_runs", ["operation"])
    op.create_index("ix_agent_runs_status", "agent_runs", ["status"])
    op.create_index("ix_agent_runs_model_profile_id", "agent_runs", ["model_profile_id"])
    op.create_index("ix_agent_runs_stop_reason", "agent_runs", ["stop_reason"])
    op.create_index("ix_agent_runs_approval_status", "agent_runs", ["approval_status"])
    op.create_index("ix_agent_runs_replay_of_run_id", "agent_runs", ["replay_of_run_id"])
    op.create_index("ix_agent_runs_case_created", "agent_runs", ["case_id", "created_at"])
    op.create_index(
        "ix_agent_runs_resource_created",
        "agent_runs",
        ["resource_type", "resource_id", "created_at"],
    )
    op.create_index("ix_agent_runs_operation_status", "agent_runs", ["operation", "status"])

    op.create_table(
        "agent_trace_events",
        sa.Column("id", sa.String(length=40), primary_key=True),
        sa.Column("run_id", sa.String(length=40), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=128), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="COMPLETED"),
        sa.Column("input_summary_hash", sa.String(length=64), nullable=False),
        sa.Column("output_summary_hash", sa.String(length=64), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_ids_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("stop_reason", sa.String(length=128), nullable=True),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("run_id", "sequence", name="uq_agent_trace_event_sequence"),
    )
    op.create_index("ix_agent_trace_events_run_id", "agent_trace_events", ["run_id"])
    op.create_index("ix_agent_trace_events_stage", "agent_trace_events", ["stage"])
    op.create_index("ix_agent_trace_events_tool_name", "agent_trace_events", ["tool_name"])
    op.create_index("ix_agent_trace_events_status", "agent_trace_events", ["status"])
    op.create_index(
        "ix_agent_trace_events_run_created",
        "agent_trace_events",
        ["run_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("agent_trace_events")
    op.drop_table("agent_runs")
