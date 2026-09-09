"""Problem categories and confirmed, versioned knowledge workbench records."""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("cases", sa.Column("problem_category", sa.String(80), nullable=False, server_default="unknown"))
    op.add_column("cases", sa.Column("chat_profile_id", sa.String(40), nullable=True))
    op.create_table("workbench_records",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_workbench_records_kind", "workbench_records", ["kind"])
    op.create_index("ix_workbench_records_owner_id", "workbench_records", ["owner_id"])


def downgrade():
    raise RuntimeError("Restore a matching backup; workspace history must be retained")
