"""Keep draft proposals and immutable chunk versions beside published knowledge.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("knowledge_chunks")}
    if "document_version" not in columns:
        op.add_column("knowledge_chunks", sa.Column("document_version", sa.Integer(), nullable=False, server_default="1"))
        op.execute(sa.text("UPDATE knowledge_chunks SET document_version=COALESCE((SELECT version FROM knowledge_documents WHERE knowledge_documents.id=knowledge_chunks.document_id),1)"))
    indexes = {index["name"] for index in inspector.get_indexes("knowledge_chunks")}
    if "ix_knowledge_chunks_document_version" not in indexes:
        op.create_index("ix_knowledge_chunks_document_version", "knowledge_chunks", ["document_version"])
    tables = set(inspector.get_table_names())
    if "knowledge_drafts" not in tables:
        op.create_table("knowledge_drafts",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("document_id", sa.String(40), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("base_version", sa.Integer(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("snapshot_json", sa.Text(), nullable=False),
            sa.Column("status", sa.String(32), nullable=False),
            sa.Column("created_by", sa.String(128)), sa.Column("reviewed_by", sa.String(128)),
            sa.Column("review_comment", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_knowledge_drafts_document_id", "knowledge_drafts", ["document_id"], unique=True)
        op.create_index("ix_knowledge_drafts_status", "knowledge_drafts", ["status"])
    if "knowledge_publications" not in tables:
        op.create_table("knowledge_publications",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("document_id", sa.String(40), sa.ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False),
            sa.Column("document_version", sa.Integer(), nullable=False),
            sa.Column("manifest_json", sa.Text(), nullable=False),
            sa.Column("published_by", sa.String(128)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        op.create_index("ix_knowledge_publications_document_id", "knowledge_publications", ["document_id"])


def downgrade() -> None:
    raise RuntimeError("Restore the matching pre-publication backup; dropping immutable history is not a safe downgrade")
