"""store the model profile snapshot used by each diagnosis

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names() -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns("analysis_runs")}


def upgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=128),
            type_=sa.String(length=512),
            existing_nullable=False,
        )
        if "model_profile_id" not in columns:
            batch_op.add_column(sa.Column("model_profile_id", sa.String(length=40), nullable=True))
        if "model_config_json" not in columns:
            batch_op.add_column(
                sa.Column("model_config_json", sa.Text(), nullable=False, server_default="{}")
            )


def downgrade() -> None:
    columns = _column_names()
    with op.batch_alter_table("analysis_runs", schema=None) as batch_op:
        if "model_config_json" in columns:
            batch_op.drop_column("model_config_json")
        if "model_profile_id" in columns:
            batch_op.drop_column("model_profile_id")
        batch_op.alter_column(
            "model",
            existing_type=sa.String(length=512),
            type_=sa.String(length=128),
            existing_nullable=False,
        )
