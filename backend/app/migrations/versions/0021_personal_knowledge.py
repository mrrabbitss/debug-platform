"""Independent personal proposals and a stable knowledge publisher.

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c["name"] for c in inspector.get_columns("knowledge_drafts")}
    for name in ("publication_job_id", "building_generation_id"):
        if name not in columns:
            op.add_column("knowledge_drafts", sa.Column(name, sa.String(40), nullable=True))
    if "owner_key" not in columns:
        op.add_column("knowledge_drafts", sa.Column("owner_key", sa.String(128), nullable=False, server_default=""))
        op.execute(sa.text("UPDATE knowledge_drafts SET owner_key=COALESCE(created_by,'')"))
    indexes = {i["name"]: i for i in inspector.get_indexes("knowledge_drafts")}
    if indexes.get("ix_knowledge_drafts_document_id", {}).get("unique"):
        op.drop_index("ix_knowledge_drafts_document_id", table_name="knowledge_drafts")
        op.create_index("ix_knowledge_drafts_document_id", "knowledge_drafts", ["document_id"])
    if "uq_knowledge_draft_owner" not in indexes:
        op.create_index("uq_knowledge_draft_owner", "knowledge_drafts", ["document_id", "owner_key"], unique=True)
    if "publisher_id" not in {c["name"] for c in inspector.get_columns("knowledge_access")}:
        op.add_column("knowledge_access", sa.Column("publisher_id", sa.String(128), nullable=True))
        op.execute(sa.text("UPDATE knowledge_access SET publisher_id=(SELECT reviewed_by FROM knowledge_documents WHERE id=knowledge_access.document_id)"))
    if "knowledge_working_revisions" not in inspector.get_table_names():
        op.create_table("knowledge_working_revisions",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("draft_id", sa.String(40), sa.ForeignKey("knowledge_drafts.id"), nullable=False),
            sa.Column("document_id", sa.String(40), sa.ForeignKey("knowledge_documents.id"), nullable=False),
            sa.Column("owner_key", sa.String(128), nullable=False),
            sa.Column("draft_version", sa.Integer(), nullable=False),
            sa.Column("base_version", sa.Integer(), nullable=False),
            sa.Column("snapshot_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
        for name in ("draft_id", "document_id", "owner_key"):
            op.create_index(f"ix_knowledge_working_revisions_{name}", "knowledge_working_revisions", [name])
        op.create_index("uq_working_revision", "knowledge_working_revisions", ["draft_id", "draft_version"], unique=True)


def downgrade():
    raise RuntimeError("Restore the matching backup; personal proposals must not be discarded")
