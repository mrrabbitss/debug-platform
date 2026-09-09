"""Owner-isolated ordinary knowledge and exact-version expert review."""
from alembic import op
import sqlalchemy as sa

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("knowledge_contributions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("content_kind", sa.String(16), nullable=False),
        sa.Column("target_document_id", sa.String(40), sa.ForeignKey("knowledge_documents.id"), nullable=True),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("base_lock_version", sa.Integer(), nullable=True),
        sa.Column("source_curation_id", sa.String(40), sa.ForeignKey("knowledge_curation_sessions.id"), unique=True, nullable=True),
        sa.Column("source_library_id", sa.String(80), sa.ForeignKey("workbench_records.id"), unique=True, nullable=True),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.Column("original_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_version", sa.Integer(), nullable=True),
        sa.Column("approved_hash", sa.String(64), nullable=True),
        sa.Column("reviewed_by", sa.String(128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column("publication_job_id", sa.String(40), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("published_document_id", sa.String(40), sa.ForeignKey("knowledge_documents.id"), nullable=True),
        sa.Column("building_generation_id", sa.String(40), nullable=True),
        sa.Column("worker_token", sa.String(160), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("owner_id", "content_kind", "status"):
        op.create_index(f"ix_knowledge_contributions_{column}", "knowledge_contributions", [column])
    op.create_table("knowledge_contribution_revisions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("contribution_id", sa.String(40), sa.ForeignKey("knowledge_contributions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("candidate_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("diff", sa.Text(), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("messages_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("contribution_id", "version", name="uq_contribution_revision_version"))
    op.create_index("ix_knowledge_contribution_revisions_contribution_id", "knowledge_contribution_revisions", ["contribution_id"])


def downgrade():
    raise RuntimeError("Restore a matching backup; review history must be retained")
