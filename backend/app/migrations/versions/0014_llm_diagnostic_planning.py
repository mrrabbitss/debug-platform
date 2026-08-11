"""add persistent LLM diagnostic planning and log triage state

Revision ID: 0014
Revises: 0013
"""

from alembic import op
import sqlalchemy as sa


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
        if item.get("name")
    }


def _add_runtime_columns() -> None:
    case_columns = _columns("cases")
    with op.batch_alter_table("cases", schema=None) as batch_op:
        if "model_egress_approved" not in case_columns:
            batch_op.add_column(sa.Column(
                "model_egress_approved",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ))

    analysis_columns = _columns("analysis_runs")
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        if "agent_run_id" not in analysis_columns:
            batch_op.add_column(sa.Column("agent_run_id", sa.String(40), nullable=True))
            batch_op.create_foreign_key(
                "fk_analysis_runs_agent_run_id",
                "agent_runs",
                ["agent_run_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if "ix_analysis_runs_agent_run_id" not in _indexes("analysis_runs"):
        op.create_index(
            "ix_analysis_runs_agent_run_id",
            "analysis_runs",
            ["agent_run_id"],
        )

    message_columns = _columns("conversation_messages")
    with op.batch_alter_table("conversation_messages", schema=None) as batch_op:
        if "status" not in message_columns:
            batch_op.add_column(sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default="COMPLETED",
            ))
        if "job_id" not in message_columns:
            batch_op.add_column(sa.Column("job_id", sa.String(40), nullable=True))
            batch_op.create_foreign_key(
                "fk_conversation_messages_job_id",
                "jobs",
                ["job_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "agent_run_id" not in message_columns:
            batch_op.add_column(sa.Column("agent_run_id", sa.String(40), nullable=True))
            batch_op.create_foreign_key(
                "fk_conversation_messages_agent_run_id",
                "agent_runs",
                ["agent_run_id"],
                ["id"],
                ondelete="SET NULL",
            )
        if "error_message" not in message_columns:
            batch_op.add_column(sa.Column("error_message", sa.Text(), nullable=True))
    for index_name, columns in (
        ("ix_conversation_messages_status", ["status"]),
        ("ix_conversation_messages_job_id", ["job_id"]),
        ("ix_conversation_messages_agent_run_id", ["agent_run_id"]),
    ):
        if index_name not in _indexes("conversation_messages"):
            op.create_index(index_name, "conversation_messages", columns)


def _create_triage_tables() -> None:
    if "log_triage_runs" not in _tables():
        op.create_table(
            "log_triage_runs",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("case_id", sa.String(40), nullable=False),
            sa.Column("artifact_id", sa.String(40), nullable=False),
            sa.Column("parse_run_id", sa.String(40), nullable=True),
            sa.Column("agent_run_id", sa.String(40), nullable=True),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("issue_snapshot", sa.Text(), nullable=False, server_default=""),
            sa.Column("model_profile_id", sa.String(40), nullable=True),
            sa.Column("model_name", sa.String(512), nullable=True),
            sa.Column("method_coverage_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("plan_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"], ondelete="SET NULL"),
        )
        op.create_index("ix_log_triage_runs_case_id", "log_triage_runs", ["case_id"])
        op.create_index("ix_log_triage_runs_artifact_id", "log_triage_runs", ["artifact_id"])
        op.create_index("ix_log_triage_runs_parse_run_id", "log_triage_runs", ["parse_run_id"])
        op.create_index("ix_log_triage_runs_agent_run_id", "log_triage_runs", ["agent_run_id"])
        op.create_index("ix_log_triage_runs_status", "log_triage_runs", ["status"])
        op.create_index("ix_log_triage_case_created", "log_triage_runs", ["case_id", "created_at"])
        op.create_index("ix_log_triage_artifact_created", "log_triage_runs", ["artifact_id", "created_at"])
        op.create_index("ix_log_triage_status_created", "log_triage_runs", ["status", "created_at"])

    if "log_evidence_matches" not in _tables():
        op.create_table(
            "log_evidence_matches",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("triage_run_id", sa.String(40), nullable=False),
            sa.Column("case_id", sa.String(40), nullable=False),
            sa.Column("artifact_id", sa.String(40), nullable=False),
            sa.Column("source_file", sa.Text(), nullable=False),
            sa.Column("line_start", sa.Integer(), nullable=False),
            sa.Column("line_end", sa.Integer(), nullable=False),
            sa.Column("bucket", sa.String(32), nullable=False),
            sa.Column("relevance_score", sa.Float(), nullable=False),
            sa.Column("pattern_id", sa.String(96), nullable=False),
            sa.Column("pattern_text", sa.Text(), nullable=False),
            sa.Column("match_kind", sa.String(32), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("method_document_id", sa.String(40), nullable=True),
            sa.Column("method_version", sa.Integer(), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("occurrence_count", sa.Integer(), nullable=False),
            sa.Column("first_timestamp", sa.String(128), nullable=True),
            sa.Column("last_timestamp", sa.String(128), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["triage_run_id"], ["log_triage_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["artifact_id"], ["artifacts.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(
                ["method_document_id"],
                ["knowledge_documents.id"],
                ondelete="SET NULL",
            ),
        )
        op.create_index("ix_log_evidence_matches_triage_run_id", "log_evidence_matches", ["triage_run_id"])
        op.create_index("ix_log_evidence_matches_case_id", "log_evidence_matches", ["case_id"])
        op.create_index("ix_log_evidence_matches_artifact_id", "log_evidence_matches", ["artifact_id"])
        op.create_index("ix_log_evidence_matches_bucket", "log_evidence_matches", ["bucket"])
        op.create_index("ix_log_evidence_matches_relevance_score", "log_evidence_matches", ["relevance_score"])
        op.create_index("ix_log_evidence_matches_pattern_id", "log_evidence_matches", ["pattern_id"])
        op.create_index("ix_log_evidence_matches_method_document_id", "log_evidence_matches", ["method_document_id"])
        op.create_index(
            "ix_log_evidence_triage_bucket_score",
            "log_evidence_matches",
            ["triage_run_id", "bucket", "relevance_score"],
        )
        op.create_index(
            "ix_log_evidence_source_line",
            "log_evidence_matches",
            ["artifact_id", "source_file", "line_start"],
        )

    if "log_evidence_occurrences" not in _tables():
        op.create_table(
            "log_evidence_occurrences",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("triage_run_id", sa.String(40), nullable=False),
            sa.Column("event_id", sa.String(40), nullable=False),
            sa.Column("bucket", sa.String(32), nullable=False),
            sa.Column("relevance_score", sa.Float(), nullable=False),
            sa.Column("pattern_ids_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["triage_run_id"],
                ["log_triage_runs.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["event_id"], ["log_events.id"], ondelete="CASCADE"),
            sa.UniqueConstraint(
                "triage_run_id",
                "event_id",
                name="uq_log_evidence_occurrence_event",
            ),
        )
        op.create_index(
            "ix_log_evidence_occurrences_triage_run_id",
            "log_evidence_occurrences",
            ["triage_run_id"],
        )
        op.create_index(
            "ix_log_evidence_occurrences_event_id",
            "log_evidence_occurrences",
            ["event_id"],
        )
        op.create_index(
            "ix_log_evidence_occurrences_bucket",
            "log_evidence_occurrences",
            ["bucket"],
        )
        op.create_index(
            "ix_log_evidence_occurrence_triage_bucket",
            "log_evidence_occurrences",
            ["triage_run_id", "bucket"],
        )


def upgrade() -> None:
    _add_runtime_columns()
    _create_triage_tables()


def downgrade() -> None:
    if "log_evidence_occurrences" in _tables():
        op.drop_table("log_evidence_occurrences")
    if "log_evidence_matches" in _tables():
        op.drop_table("log_evidence_matches")
    if "log_triage_runs" in _tables():
        op.drop_table("log_triage_runs")

    message_columns = _columns("conversation_messages")
    with op.batch_alter_table("conversation_messages", schema=None) as batch_op:
        for column in ("error_message", "agent_run_id", "job_id", "status"):
            if column in message_columns:
                batch_op.drop_column(column)
    if "agent_run_id" in _columns("analysis_runs"):
        with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
            batch_op.drop_column("agent_run_id")
    if "model_egress_approved" in _columns("cases"):
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.drop_column("model_egress_approved")
