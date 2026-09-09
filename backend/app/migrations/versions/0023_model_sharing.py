"""Private Chat profiles and administrator/expert shared model ownership."""
from alembic import op
import sqlalchemy as sa

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "model_profile_access" not in sa.inspect(bind).get_table_names():
        op.create_table(
            "model_profile_access",
            sa.Column("profile_id", sa.String(40), sa.ForeignKey("model_profiles.id", ondelete="CASCADE"),
                      primary_key=True),
            sa.Column("owner_id", sa.String(128), nullable=True),
            sa.Column("visibility", sa.String(16), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.CheckConstraint("visibility IN ('SHARED', 'PRIVATE')", name="ck_model_access_visibility"),
            sa.CheckConstraint("visibility = 'SHARED' OR owner_id IS NOT NULL", name="ck_private_model_owner"),
        )
        op.create_index("ix_model_profile_access_owner_id", "model_profile_access", ["owner_id"])
        op.create_index("ix_model_profile_access_visibility", "model_profile_access", ["visibility"])
    # Existing profiles were global. Preserve active selections and all personal
    # preferences; upgrades never change an already-recorded ownership decision.
    bind.execute(sa.text(
        "INSERT INTO model_profile_access (profile_id, owner_id, visibility, version) "
        "SELECT id, NULL, 'SHARED', 1 FROM model_profiles "
        "WHERE NOT EXISTS (SELECT 1 FROM model_profile_access WHERE profile_id = model_profiles.id)"
    ))


def downgrade():
    raise RuntimeError("Restore a matching backup; private profile ownership must not be discarded")
