"""store exact log triage hits and method-derived meanings

Revision ID: 0016
Revises: 0015
"""

from alembic import op
import sqlalchemy as sa


revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    if table_name not in _tables():
        return set()
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def upgrade() -> None:
    if "meaning" not in _columns("log_evidence_matches"):
        with op.batch_alter_table("log_evidence_matches", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "meaning", sa.Text(), nullable=False, server_default="",
            ))

    if "log_evidence_hits" not in _tables():
        op.create_table(
            "log_evidence_hits",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("triage_run_id", sa.String(40), nullable=False),
            sa.Column("match_id", sa.String(40), nullable=False),
            sa.Column("artifact_id", sa.String(40), nullable=False),
            sa.Column("source_file", sa.Text(), nullable=False),
            sa.Column("line_start", sa.Integer(), nullable=False),
            sa.Column("line_end", sa.Integer(), nullable=False),
            sa.Column("timestamp", sa.String(128), nullable=True),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["triage_run_id"], ["log_triage_runs.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["match_id"], ["log_evidence_matches.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["artifact_id"], ["artifacts.id"], ondelete="CASCADE"
            ),
        )
        for name, columns in (
            ("ix_log_evidence_hits_triage_run_id", ["triage_run_id"]),
            ("ix_log_evidence_hits_match_id", ["match_id"]),
            ("ix_log_evidence_hits_artifact_id", ["artifact_id"]),
            ("ix_log_evidence_hit_match_line", ["match_id", "line_start"]),
            (
                "ix_log_evidence_hit_triage_source_line",
                ["triage_run_id", "source_file", "line_start"],
            ),
        ):
            op.create_index(name, "log_evidence_hits", columns)


def downgrade() -> None:
    if "log_evidence_hits" in _tables():
        op.drop_table("log_evidence_hits")
    if "meaning" in _columns("log_evidence_matches"):
        with op.batch_alter_table("log_evidence_matches", schema=None) as batch_op:
            batch_op.drop_column("meaning")
