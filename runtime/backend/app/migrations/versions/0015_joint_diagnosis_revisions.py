"""add joint GW/AP artifact provenance and diagnosis revision drafts

Revision ID: 0015
Revises: 0014
"""

from alembic import op
import sqlalchemy as sa


revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    artifact_columns = _columns("artifacts")
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        if "source_device_type" not in artifact_columns:
            batch_op.add_column(sa.Column(
                "source_device_type", sa.String(32), nullable=False,
                server_default="UNKNOWN",
            ))
        if "source_device_role" not in artifact_columns:
            batch_op.add_column(sa.Column(
                "source_device_role", sa.String(32), nullable=False,
                server_default="UNKNOWN",
            ))

    if "analysis_revisions" not in _tables():
        op.create_table(
            "analysis_revisions",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("case_id", sa.String(40), nullable=False),
            sa.Column("source_analysis_id", sa.String(40), nullable=False),
            sa.Column("applied_analysis_id", sa.String(40), nullable=True),
            sa.Column("source_message_id", sa.String(40), nullable=True),
            sa.Column("job_id", sa.String(40), nullable=True),
            sa.Column("agent_run_id", sa.String(40), nullable=True),
            sa.Column("status", sa.String(32), nullable=False, server_default="QUEUED"),
            sa.Column("instruction", sa.Text(), nullable=False),
            sa.Column("proposed_result_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("proposed_evidence_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("validation_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("model_profile_id", sa.String(40), nullable=True),
            sa.Column("model_name", sa.String(512), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(128), nullable=True),
            sa.Column("reviewed_by", sa.String(128), nullable=True),
            sa.Column("review_comment", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["source_analysis_id"], ["analysis_runs.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["applied_analysis_id"], ["analysis_runs.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["source_message_id"], ["conversation_messages.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(
                ["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"
            ),
        )
        for name, columns in (
            ("ix_analysis_revisions_case_id", ["case_id"]),
            ("ix_analysis_revisions_source_analysis_id", ["source_analysis_id"]),
            ("ix_analysis_revisions_applied_analysis_id", ["applied_analysis_id"]),
            ("ix_analysis_revisions_source_message_id", ["source_message_id"]),
            ("ix_analysis_revisions_job_id", ["job_id"]),
            ("ix_analysis_revisions_agent_run_id", ["agent_run_id"]),
            ("ix_analysis_revisions_status", ["status"]),
            ("ix_analysis_revisions_case_created", ["case_id", "created_at"]),
            ("ix_analysis_revisions_source_status", ["source_analysis_id", "status"]),
        ):
            op.create_index(name, "analysis_revisions", columns)


def downgrade() -> None:
    if "analysis_revisions" in _tables():
        op.drop_table("analysis_revisions")
    artifact_columns = _columns("artifacts")
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        if "source_device_role" in artifact_columns:
            batch_op.drop_column("source_device_role")
        if "source_device_type" in artifact_columns:
            batch_op.drop_column("source_device_type")
