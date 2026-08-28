"""add knowledge derivations, code/commit graphs and agent memory

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-28
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, Sequence[str], None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade() -> None:
    tables = _tables()
    if "knowledge_derivations" not in tables:
        op.create_table(
            "knowledge_derivations",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("source_document_id", sa.String(length=40), nullable=False),
            sa.Column("derived_document_id", sa.String(length=40), nullable=False),
            sa.Column("derivation_type", sa.String(length=64), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["source_document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["derived_document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "source_document_id",
                "derivation_type",
                name="uq_knowledge_derivation_source_type",
            ),
        )
        op.create_index(
            "ix_knowledge_derivations_source_document_id",
            "knowledge_derivations",
            ["source_document_id"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_derivations_derived_document_id",
            "knowledge_derivations",
            ["derived_document_id"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_derivations_derivation_type",
            "knowledge_derivations",
            ["derivation_type"],
            unique=False,
        )

    repository_columns = _columns("repositories")
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        if "graph_status" not in repository_columns:
            batch_op.add_column(sa.Column(
                "graph_status",
                sa.String(length=32),
                nullable=False,
                server_default="NOT_INDEXED",
            ))
        if "commit_graph_status" not in repository_columns:
            batch_op.add_column(sa.Column(
                "commit_graph_status",
                sa.String(length=32),
                nullable=False,
                server_default="NOT_INDEXED",
            ))
        if "index_metadata_json" not in repository_columns:
            batch_op.add_column(sa.Column(
                "index_metadata_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            ))
        if "indexed_at" not in repository_columns:
            batch_op.add_column(sa.Column(
                "indexed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ))
    repository_indexes = _indexes("repositories")
    if "ix_repositories_graph_status" not in repository_indexes:
        op.create_index(
            "ix_repositories_graph_status",
            "repositories",
            ["graph_status"],
            unique=False,
        )
    if "ix_repositories_commit_graph_status" not in repository_indexes:
        op.create_index(
            "ix_repositories_commit_graph_status",
            "repositories",
            ["commit_graph_status"],
            unique=False,
        )

    tables = _tables()
    if "code_relations" not in tables:
        op.create_table(
            "code_relations",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("repository_id", sa.String(length=40), nullable=False),
            sa.Column("source_symbol_id", sa.String(length=40), nullable=False),
            sa.Column("target_symbol_id", sa.String(length=40), nullable=True),
            sa.Column("target_name", sa.String(length=512), nullable=False),
            sa.Column("relation_type", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["repository_id"], ["repositories.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["source_symbol_id"], ["code_symbols.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["target_symbol_id"], ["code_symbols.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_code_relations_repository_id",
            "code_relations",
            ["repository_id"],
            unique=False,
        )
        op.create_index(
            "ix_code_relations_source_symbol_id",
            "code_relations",
            ["source_symbol_id"],
            unique=False,
        )
        op.create_index(
            "ix_code_relations_target_symbol_id",
            "code_relations",
            ["target_symbol_id"],
            unique=False,
        )
        op.create_index(
            "ix_code_relations_relation_type",
            "code_relations",
            ["relation_type"],
            unique=False,
        )
        op.create_index(
            "ix_code_relations_repo_type",
            "code_relations",
            ["repository_id", "relation_type"],
            unique=False,
        )
        op.create_index(
            "ix_code_relations_source_target",
            "code_relations",
            ["source_symbol_id", "target_symbol_id"],
            unique=False,
        )

    tables = _tables()
    if "commit_records" not in tables:
        op.create_table(
            "commit_records",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("repository_id", sa.String(length=40), nullable=False),
            sa.Column("commit_hash", sa.String(length=64), nullable=False),
            sa.Column("parent_hashes_json", sa.Text(), nullable=False),
            sa.Column("author_name", sa.String(length=255), nullable=False),
            sa.Column("authored_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("subject", sa.Text(), nullable=False),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["repository_id"], ["repositories.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "repository_id",
                "commit_hash",
                name="uq_commit_repository_hash",
            ),
        )
        op.create_index(
            "ix_commit_records_repository_id",
            "commit_records",
            ["repository_id"],
            unique=False,
        )
        op.create_index(
            "ix_commit_records_commit_hash",
            "commit_records",
            ["commit_hash"],
            unique=False,
        )
        op.create_index(
            "ix_commit_records_authored_at",
            "commit_records",
            ["authored_at"],
            unique=False,
        )
        op.create_index(
            "ix_commit_records_repo_time",
            "commit_records",
            ["repository_id", "authored_at"],
            unique=False,
        )

    tables = _tables()
    if "commit_file_changes" not in tables:
        op.create_table(
            "commit_file_changes",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("repository_id", sa.String(length=40), nullable=False),
            sa.Column("commit_id", sa.String(length=40), nullable=False),
            sa.Column("change_type", sa.String(length=16), nullable=False),
            sa.Column("file_path", sa.Text(), nullable=False),
            sa.Column("old_path", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["repository_id"], ["repositories.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["commit_id"], ["commit_records.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_commit_file_changes_repository_id",
            "commit_file_changes",
            ["repository_id"],
            unique=False,
        )
        op.create_index(
            "ix_commit_file_changes_commit_id",
            "commit_file_changes",
            ["commit_id"],
            unique=False,
        )
        op.create_index(
            "ix_commit_file_changes_change_type",
            "commit_file_changes",
            ["change_type"],
            unique=False,
        )
        op.create_index(
            "ix_commit_file_changes_repo_path",
            "commit_file_changes",
            ["repository_id", "file_path"],
            unique=False,
        )

    tables = _tables()
    if "agent_memories" not in tables:
        op.create_table(
            "agent_memories",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("case_id", sa.String(length=40), nullable=True),
            sa.Column("memory_type", sa.String(length=32), nullable=False),
            sa.Column("source_kind", sa.String(length=64), nullable=False),
            sa.Column("source_id", sa.String(length=64), nullable=True),
            sa.Column("title", sa.String(length=512), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("context_json", sa.Text(), nullable=False),
            sa.Column("evidence_json", sa.Text(), nullable=False),
            sa.Column("outcome", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("fingerprint", sa.String(length=64), nullable=False),
            sa.Column("occurrence_count", sa.Integer(), nullable=False),
            sa.Column("reuse_count", sa.Integer(), nullable=False),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_agent_memories_case_id",
            "agent_memories",
            ["case_id"],
            unique=False,
        )
        op.create_index(
            "ix_agent_memories_memory_type",
            "agent_memories",
            ["memory_type"],
            unique=False,
        )
        op.create_index(
            "ix_agent_memories_source_kind",
            "agent_memories",
            ["source_kind"],
            unique=False,
        )
        op.create_index(
            "ix_agent_memories_source_id",
            "agent_memories",
            ["source_id"],
            unique=False,
        )
        op.create_index(
            "ix_agent_memories_outcome",
            "agent_memories",
            ["outcome"],
            unique=False,
        )
        op.create_index(
            "ix_agent_memories_fingerprint",
            "agent_memories",
            ["fingerprint"],
            unique=False,
        )
        op.create_index(
            "ix_agent_memories_kind_outcome",
            "agent_memories",
            ["memory_type", "outcome"],
            unique=False,
        )
        op.create_index(
            "ix_agent_memories_case_kind",
            "agent_memories",
            ["case_id", "memory_type"],
            unique=False,
        )


def downgrade() -> None:
    tables = _tables()
    for table_name in (
        "agent_memories",
        "commit_file_changes",
        "commit_records",
        "code_relations",
        "knowledge_derivations",
    ):
        if table_name in tables:
            op.drop_table(table_name)
    repository_columns = _columns("repositories")
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        for column_name in (
            "indexed_at",
            "index_metadata_json",
            "commit_graph_status",
            "graph_status",
        ):
            if column_name in repository_columns:
                batch_op.drop_column(column_name)
