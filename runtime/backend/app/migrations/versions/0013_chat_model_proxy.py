"""add encrypted per-profile Chat proxy configuration

Revision ID: 0013
Revises: 0012
"""

from alembic import op
import sqlalchemy as sa


revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("model_profiles")
    }


def upgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("model_profiles", schema=None) as batch_op:
        if "proxy_url_ciphertext" not in columns:
            batch_op.add_column(sa.Column("proxy_url_ciphertext", sa.Text(), nullable=True))
        if "proxy_url_hint" not in columns:
            batch_op.add_column(sa.Column("proxy_url_hint", sa.String(512), nullable=True))


def downgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("model_profiles", schema=None) as batch_op:
        if "proxy_url_hint" in columns:
            batch_op.drop_column("proxy_url_hint")
        if "proxy_url_ciphertext" in columns:
            batch_op.drop_column("proxy_url_ciphertext")
