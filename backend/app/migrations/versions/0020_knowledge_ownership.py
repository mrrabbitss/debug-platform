"""Own unpublished and restricted knowledge; legacy unowned drafts remain administrator-only."""
from alembic import op
import sqlalchemy as sa

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade():
    if "knowledge_access" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table("knowledge_access",
                        sa.Column("document_id", sa.String(40), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), primary_key=True),
                        sa.Column("owner_id", sa.String(128), nullable=True))
        op.create_index("ix_knowledge_access_owner_id", "knowledge_access", ["owner_id"])
    op.execute(sa.text("INSERT INTO knowledge_access(document_id,owner_id) SELECT id,NULL FROM knowledge_documents WHERE id NOT IN (SELECT document_id FROM knowledge_access)"))
    # Legacy graph generations may have mixed restricted and shared sources.
    op.execute(sa.text("UPDATE knowledge_graph_states SET active_generation_id=NULL, building_generation_id=NULL, status='NOT_BUILT' WHERE EXISTS (SELECT 1 FROM knowledge_documents WHERE confidentiality='RESTRICTED')"))


def downgrade():
    raise RuntimeError("Restore the pre-ownership backup with the matching application; dropping access controls is unsafe")
