"""atomically publish successful parse generations

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004"
down_revision: Union[str, Sequence[str], None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    artifact_columns = _column_names("artifacts")
    if "active_parse_run_id" not in artifact_columns:
        with op.batch_alter_table("artifacts", schema=None) as batch_op:
            batch_op.add_column(sa.Column("active_parse_run_id", sa.String(length=40), nullable=True))

    event_columns = _column_names("log_events")
    if "parse_run_id" not in event_columns:
        with op.batch_alter_table("log_events", schema=None) as batch_op:
            batch_op.add_column(sa.Column("parse_run_id", sa.String(length=40), nullable=True))

    connection = op.get_bind()
    artifact_ids = connection.execute(sa.text(
        "SELECT DISTINCT artifact_id FROM log_events WHERE artifact_id IS NOT NULL"
    )).scalars().all()
    for artifact_id in artifact_ids:
        connection.execute(
            sa.text("UPDATE log_events SET parse_run_id = :run_id WHERE artifact_id = :artifact_id"),
            {"run_id": artifact_id, "artifact_id": artifact_id},
        )
        connection.execute(
            sa.text("UPDATE artifacts SET active_parse_run_id = :run_id WHERE id = :artifact_id"),
            {"run_id": artifact_id, "artifact_id": artifact_id},
        )

    artifact_indexes = _index_names("artifacts")
    if "ix_artifacts_active_parse_run_id" not in artifact_indexes:
        op.create_index("ix_artifacts_active_parse_run_id", "artifacts", ["active_parse_run_id"], unique=False)
    event_indexes = _index_names("log_events")
    if "ix_log_events_parse_run_id" not in event_indexes:
        op.create_index("ix_log_events_parse_run_id", "log_events", ["parse_run_id"], unique=False)
    if "ix_log_events_artifact_parse_run" not in event_indexes:
        op.create_index(
            "ix_log_events_artifact_parse_run",
            "log_events",
            ["artifact_id", "parse_run_id"],
            unique=False,
        )


def downgrade() -> None:
    event_indexes = _index_names("log_events")
    for name in ("ix_log_events_artifact_parse_run", "ix_log_events_parse_run_id"):
        if name in event_indexes:
            op.drop_index(name, table_name="log_events")
    artifact_indexes = _index_names("artifacts")
    if "ix_artifacts_active_parse_run_id" in artifact_indexes:
        op.drop_index("ix_artifacts_active_parse_run_id", table_name="artifacts")
    if "parse_run_id" in _column_names("log_events"):
        with op.batch_alter_table("log_events", schema=None) as batch_op:
            batch_op.drop_column("parse_run_id")
    if "active_parse_run_id" in _column_names("artifacts"):
        with op.batch_alter_table("artifacts", schema=None) as batch_op:
            batch_op.drop_column("active_parse_run_id")
