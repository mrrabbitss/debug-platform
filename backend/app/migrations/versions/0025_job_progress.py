"""Persist observable stages separately from task results and model content."""
from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("jobs", sa.Column("progress_json", sa.Text(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("jobs", "progress_json")
