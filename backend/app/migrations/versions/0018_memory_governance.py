"""Quarantine unreviewed memories and separate real case outcomes from model output.

Revision ID: 0018
Revises: 0017
"""
from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def _add_missing(table: str, columns: list[sa.Column]) -> None:
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}
    for column in columns:
        if column.name not in existing:
            op.add_column(table, column)


def upgrade() -> None:
    _add_missing("agent_memories", [
        sa.Column("review_status", sa.String(32), nullable=False, server_default="CANDIDATE"),
        sa.Column("scope", sa.String(16), nullable=False, server_default="CASE"),
        sa.Column("review_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    ])
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("agent_memories")}
    if "ix_agent_memories_review_status" not in indexes:
        op.create_index("ix_agent_memories_review_status", "agent_memories", ["review_status"])
    _add_missing("diagnosis_feedback", [
        sa.Column("resolution_status", sa.String(32), nullable=False, server_default="UNKNOWN"),
        sa.Column("resolution_notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("resolution_observed_at", sa.DateTime(timezone=True), nullable=True),
    ])
    # The old SUCCESS field meant "a model produced hypotheses/citations", not
    # "the device fault was actually fixed". Do not carry that assertion forward.
    op.execute(sa.text("UPDATE agent_memories SET outcome='UNKNOWN' WHERE source_kind IN ('analysis_run','case_chat') AND outcome IN ('SUCCESS','PARTIAL')"))
    op.execute(sa.text("UPDATE agent_memories SET case_id=(SELECT case_id FROM analysis_runs WHERE analysis_runs.id=agent_memories.source_id) WHERE case_id IS NULL AND source_kind='analysis_run'"))


def downgrade() -> None:
    # Downgrade cannot reconstruct the previous unverified SUCCESS assertions.
    raise RuntimeError("Restore the pre-migration backup with its matching application version; memory governance downgrade is not lossless")
