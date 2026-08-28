"""runtime integrity and large-log indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_names(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.alter_column(
            "size_bytes",
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

    event_indexes = _index_names("log_events")
    for name, columns in {
        "ix_log_events_case_time": ["case_id", "timestamp_normalized", "line_start"],
        "ix_log_events_case_level": ["case_id", "level"],
        "ix_log_events_case_module": ["case_id", "module"],
    }.items():
        if name not in event_indexes:
            op.create_index(name, "log_events", columns, unique=False)

    connection = op.get_bind()
    duplicates = connection.execute(sa.text(
        "SELECT task_type FROM model_profiles WHERE is_active = :active "
        "GROUP BY task_type HAVING COUNT(*) > 1"
    ), {"active": True}).scalars().all()
    for task_type in duplicates:
        rows = connection.execute(sa.text(
            "SELECT id FROM model_profiles WHERE task_type = :task_type AND is_active = :active "
            "ORDER BY updated_at DESC, id DESC"
        ), {"task_type": task_type, "active": True}).scalars().all()
        for profile_id in rows[1:]:
            connection.execute(sa.text(
                "UPDATE model_profiles SET is_active = :inactive WHERE id = :profile_id"
            ), {"inactive": False, "profile_id": profile_id})

    model_indexes = _index_names("model_profiles")
    if "uq_model_profiles_active_task" not in model_indexes:
        dialect = connection.dialect.name
        where = sa.text("is_active = 1") if dialect == "sqlite" else sa.text("is_active = true")
        op.create_index(
            "uq_model_profiles_active_task",
            "model_profiles",
            ["task_type"],
            unique=True,
            sqlite_where=where if dialect == "sqlite" else None,
            postgresql_where=where if dialect == "postgresql" else None,
        )


def downgrade() -> None:
    model_indexes = _index_names("model_profiles")
    if "uq_model_profiles_active_task" in model_indexes:
        op.drop_index("uq_model_profiles_active_task", table_name="model_profiles")
    event_indexes = _index_names("log_events")
    for name in ("ix_log_events_case_module", "ix_log_events_case_level", "ix_log_events_case_time"):
        if name in event_indexes:
            op.drop_index(name, table_name="log_events")
    with op.batch_alter_table("artifacts", schema=None) as batch_op:
        batch_op.alter_column(
            "size_bytes",
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )
