"""add LLM-assisted knowledge curation sessions

Revision ID: 0010
Revises: 0009
Create Date: 2026-08-03
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "knowledge_curation_sessions" not in tables:
        op.create_table(
            "knowledge_curation_sessions",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("title_hint", sa.String(length=512), nullable=False),
            sa.Column("category_id", sa.String(length=40), nullable=True),
            sa.Column("device_type", sa.String(length=32), nullable=True),
            sa.Column("device_model", sa.String(length=128), nullable=True),
            sa.Column("firmware_range", sa.String(length=255), nullable=True),
            sa.Column("module", sa.String(length=64), nullable=True),
            sa.Column("trust_level", sa.String(length=16), nullable=False),
            sa.Column("confidentiality", sa.String(length=32), nullable=False),
            sa.Column("model_profile_id", sa.String(length=40), nullable=True),
            sa.Column("job_id", sa.String(length=40), nullable=True),
            sa.Column("model_snapshot_json", sa.Text(), nullable=False),
            sa.Column("source_manifest_json", sa.Text(), nullable=False),
            sa.Column("draft_title", sa.String(length=512), nullable=False),
            sa.Column("draft_markdown", sa.Text(), nullable=False),
            sa.Column("draft_version", sa.Integer(), nullable=False),
            sa.Column("validation_json", sa.Text(), nullable=False),
            sa.Column("open_questions_json", sa.Text(), nullable=False),
            sa.Column("knowledge_document_id", sa.String(length=40), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["category_id"], ["knowledge_categories.id"], ondelete="SET NULL"
            ),
            sa.ForeignKeyConstraint(
                ["knowledge_document_id"],
                ["knowledge_documents.id"],
                ondelete="SET NULL",
            ),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("knowledge_document_id"),
        )
        for name, columns in (
            ("ix_knowledge_curation_sessions_status", ["status"]),
            ("ix_knowledge_curation_sessions_category_id", ["category_id"]),
            ("ix_knowledge_curation_sessions_device_type", ["device_type"]),
            ("ix_knowledge_curation_sessions_module", ["module"]),
            ("ix_knowledge_curation_sessions_model_profile_id", ["model_profile_id"]),
            ("ix_knowledge_curation_sessions_job_id", ["job_id"]),
            (
                "ix_knowledge_curation_sessions_knowledge_document_id",
                ["knowledge_document_id"],
            ),
        ):
            op.create_index(name, "knowledge_curation_sessions", columns, unique=False)

    tables = _tables()
    if "knowledge_curation_source_files" not in tables:
        op.create_table(
            "knowledge_curation_source_files",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("session_id", sa.String(length=40), nullable=False),
            sa.Column("source_ref", sa.String(length=32), nullable=False),
            sa.Column("relative_path", sa.Text(), nullable=False),
            sa.Column("stored_path", sa.Text(), nullable=False),
            sa.Column("extracted_text_path", sa.Text(), nullable=True),
            sa.Column("extracted_text_sha256", sa.String(length=64), nullable=True),
            sa.Column("extraction_method", sa.String(length=64), nullable=True),
            sa.Column(
                "extraction_truncated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("page_count", sa.Integer(), nullable=True),
            sa.Column("sha256", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.BigInteger(), nullable=False),
            sa.Column("media_type", sa.String(length=255), nullable=True),
            sa.Column("text_encoding", sa.String(length=64), nullable=True),
            sa.Column("line_count", sa.Integer(), nullable=True),
            sa.Column("source_role", sa.String(length=32), nullable=False),
            sa.Column("included", sa.Boolean(), nullable=False),
            sa.Column("skip_reason", sa.String(length=512), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"], ["knowledge_curation_sessions.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "session_id",
                "relative_path",
                name="uq_knowledge_curation_source_path",
            ),
        )
        op.create_index(
            "ix_knowledge_curation_source_files_session_id",
            "knowledge_curation_source_files",
            ["session_id"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_curation_source_files_sha256",
            "knowledge_curation_source_files",
            ["sha256"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_curation_source_files_source_role",
            "knowledge_curation_source_files",
            ["source_role"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_curation_sources_session_created",
            "knowledge_curation_source_files",
            ["session_id", "created_at"],
            unique=False,
        )

    tables = _tables()
    if "knowledge_curation_revisions" not in tables:
        op.create_table(
            "knowledge_curation_revisions",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("session_id", sa.String(length=40), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("markdown", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("change_summary", sa.String(length=512), nullable=False),
            sa.Column("validation_json", sa.Text(), nullable=False),
            sa.Column("source_message_id", sa.String(length=40), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"], ["knowledge_curation_sessions.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "session_id",
                "version",
                name="uq_knowledge_curation_revision_version",
            ),
        )
        op.create_index(
            "ix_knowledge_curation_revisions_session_id",
            "knowledge_curation_revisions",
            ["session_id"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_curation_revisions_content_hash",
            "knowledge_curation_revisions",
            ["content_hash"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_curation_revisions_session_created",
            "knowledge_curation_revisions",
            ["session_id", "created_at"],
            unique=False,
        )

    tables = _tables()
    if "knowledge_curation_messages" not in tables:
        op.create_table(
            "knowledge_curation_messages",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("session_id", sa.String(length=40), nullable=False),
            sa.Column("role", sa.String(length=16), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("citations_json", sa.Text(), nullable=False),
            sa.Column("draft_version", sa.Integer(), nullable=True),
            sa.Column("model_profile_id", sa.String(length=40), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"], ["knowledge_curation_sessions.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_knowledge_curation_messages_session_id",
            "knowledge_curation_messages",
            ["session_id"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_curation_messages_session_created",
            "knowledge_curation_messages",
            ["session_id", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    for table_name in (
        "knowledge_curation_messages",
        "knowledge_curation_revisions",
        "knowledge_curation_source_files",
        "knowledge_curation_sessions",
    ):
        if table_name in _tables():
            op.drop_table(table_name)
