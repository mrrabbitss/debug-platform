"""add atomic code graph and embedding generations

Revision ID: 0008
Revises: 0007
Create Date: 2026-07-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, Sequence[str], None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_columns(table_name)}


def _indexes(table_name: str) -> set[str]:
    return {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _unique_constraints(table_name: str) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_unique_constraints(table_name)
        if item.get("name")
    }


def upgrade() -> None:
    model_columns = _columns("model_profiles")
    if "active_embedding_generation_id" not in model_columns:
        with op.batch_alter_table("model_profiles", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "active_embedding_generation_id",
                sa.String(length=40),
                nullable=True,
            ))
    if "ix_model_profiles_active_embedding_generation_id" not in _indexes("model_profiles"):
        op.create_index(
            "ix_model_profiles_active_embedding_generation_id",
            "model_profiles",
            ["active_embedding_generation_id"],
            unique=False,
        )

    repository_columns = _columns("repositories")
    if "active_graph_generation_id" not in repository_columns:
        with op.batch_alter_table("repositories", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "active_graph_generation_id",
                sa.String(length=40),
                nullable=True,
            ))
    if "ix_repositories_active_graph_generation_id" not in _indexes("repositories"):
        op.create_index(
            "ix_repositories_active_graph_generation_id",
            "repositories",
            ["active_graph_generation_id"],
            unique=False,
        )

    symbol_columns = _columns("code_symbols")
    if "generation_id" not in symbol_columns:
        with op.batch_alter_table("code_symbols", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "generation_id",
                sa.String(length=40),
                nullable=False,
                server_default="legacy",
            ))
    if "logical_id" not in symbol_columns:
        with op.batch_alter_table("code_symbols", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "logical_id",
                sa.String(length=40),
                nullable=True,
            ))
    symbol_indexes = _indexes("code_symbols")
    if "ix_code_symbols_generation_id" not in symbol_indexes:
        op.create_index(
            "ix_code_symbols_generation_id",
            "code_symbols",
            ["generation_id"],
            unique=False,
        )
    if "ix_code_symbols_repo_generation" not in symbol_indexes:
        op.create_index(
            "ix_code_symbols_repo_generation",
            "code_symbols",
            ["repository_id", "generation_id"],
            unique=False,
        )
    if "ix_code_symbols_logical_id" not in symbol_indexes:
        op.create_index(
            "ix_code_symbols_logical_id",
            "code_symbols",
            ["logical_id"],
            unique=False,
        )

    relation_columns = _columns("code_relations")
    if "generation_id" not in relation_columns:
        with op.batch_alter_table("code_relations", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "generation_id",
                sa.String(length=40),
                nullable=False,
                server_default="legacy",
            ))
    if "logical_id" not in relation_columns:
        with op.batch_alter_table("code_relations", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "logical_id",
                sa.String(length=40),
                nullable=True,
            ))
    relation_indexes = _indexes("code_relations")
    if "ix_code_relations_generation_id" not in relation_indexes:
        op.create_index(
            "ix_code_relations_generation_id",
            "code_relations",
            ["generation_id"],
            unique=False,
        )
    if "ix_code_relations_repo_generation" not in relation_indexes:
        op.create_index(
            "ix_code_relations_repo_generation",
            "code_relations",
            ["repository_id", "generation_id"],
            unique=False,
        )
    if "ix_code_relations_logical_id" not in relation_indexes:
        op.create_index(
            "ix_code_relations_logical_id",
            "code_relations",
            ["logical_id"],
            unique=False,
        )

    embedding_columns = _columns("knowledge_embeddings")
    if "generation_id" not in embedding_columns:
        with op.batch_alter_table("knowledge_embeddings", schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "generation_id",
                sa.String(length=40),
                nullable=False,
                server_default="legacy",
            ))
    embedding_indexes = _indexes("knowledge_embeddings")
    if "ix_knowledge_embeddings_generation_id" not in embedding_indexes:
        op.create_index(
            "ix_knowledge_embeddings_generation_id",
            "knowledge_embeddings",
            ["generation_id"],
            unique=False,
        )
    if "ix_knowledge_embeddings_profile_generation" not in embedding_indexes:
        op.create_index(
            "ix_knowledge_embeddings_profile_generation",
            "knowledge_embeddings",
            ["profile_id", "generation_id"],
            unique=False,
        )

    unique_constraints = _unique_constraints("knowledge_embeddings")
    with op.batch_alter_table("knowledge_embeddings", schema=None) as batch_op:
        if "uq_knowledge_embedding_profile" in unique_constraints:
            batch_op.drop_constraint("uq_knowledge_embedding_profile", type_="unique")
        if "uq_knowledge_embedding_generation" not in unique_constraints:
            batch_op.create_unique_constraint(
                "uq_knowledge_embedding_generation",
                ["chunk_id", "profile_id", "generation_id"],
            )

    connection = op.get_bind()
    connection.execute(sa.text(
        "UPDATE code_symbols SET logical_id = id WHERE logical_id IS NULL"
    ))
    connection.execute(sa.text(
        "UPDATE code_relations SET logical_id = id WHERE logical_id IS NULL"
    ))
    connection.execute(sa.text(
        "UPDATE repositories SET active_graph_generation_id = 'legacy' "
        "WHERE active_graph_generation_id IS NULL "
        "AND EXISTS (SELECT 1 FROM code_symbols "
        "WHERE code_symbols.repository_id = repositories.id)"
    ))
    connection.execute(sa.text(
        "UPDATE model_profiles SET active_embedding_generation_id = 'legacy' "
        "WHERE active_embedding_generation_id IS NULL "
        "AND EXISTS (SELECT 1 FROM knowledge_embeddings "
        "WHERE knowledge_embeddings.profile_id = model_profiles.id)"
    ))

    report_unique_constraints = _unique_constraints("reports")
    if "uq_report_case_analysis_format_version" not in report_unique_constraints:
        rows = list(connection.execute(sa.text(
            "SELECT id, case_id, analysis_run_id, format, version "
            "FROM reports "
            "ORDER BY case_id, analysis_run_id, format, version, created_at, id"
        )).mappings())
        maximum_versions: dict[tuple[str, str, str], int] = {}
        for row in rows:
            key = (
                str(row["case_id"]),
                str(row["analysis_run_id"]),
                str(row["format"]),
            )
            maximum_versions[key] = max(
                maximum_versions.get(key, 0),
                int(row["version"]),
            )
        used_versions: dict[tuple[str, str, str], set[int]] = {}
        for row in rows:
            key = (
                str(row["case_id"]),
                str(row["analysis_run_id"]),
                str(row["format"]),
            )
            version = int(row["version"])
            used = used_versions.setdefault(key, set())
            if version not in used:
                used.add(version)
                continue
            maximum_versions[key] += 1
            repaired_version = maximum_versions[key]
            used.add(repaired_version)
            connection.execute(
                sa.text("UPDATE reports SET version = :version WHERE id = :id"),
                {"version": repaired_version, "id": row["id"]},
            )
        with op.batch_alter_table("reports", schema=None) as batch_op:
            batch_op.create_unique_constraint(
                "uq_report_case_analysis_format_version",
                ["case_id", "analysis_run_id", "format", "version"],
            )


def downgrade() -> None:
    if (
        "uq_report_case_analysis_format_version"
        in _unique_constraints("reports")
    ):
        with op.batch_alter_table("reports", schema=None) as batch_op:
            batch_op.drop_constraint(
                "uq_report_case_analysis_format_version",
                type_="unique",
            )

    unique_constraints = _unique_constraints("knowledge_embeddings")
    with op.batch_alter_table("knowledge_embeddings", schema=None) as batch_op:
        if "uq_knowledge_embedding_generation" in unique_constraints:
            batch_op.drop_constraint("uq_knowledge_embedding_generation", type_="unique")
        if "uq_knowledge_embedding_profile" not in unique_constraints:
            batch_op.create_unique_constraint(
                "uq_knowledge_embedding_profile",
                ["chunk_id", "profile_id"],
            )

    for table_name, indexes in (
        (
            "knowledge_embeddings",
            (
                "ix_knowledge_embeddings_profile_generation",
                "ix_knowledge_embeddings_generation_id",
            ),
        ),
        (
            "code_relations",
            (
                "ix_code_relations_logical_id",
                "ix_code_relations_repo_generation",
                "ix_code_relations_generation_id",
            ),
        ),
        (
            "code_symbols",
            (
                "ix_code_symbols_logical_id",
                "ix_code_symbols_repo_generation",
                "ix_code_symbols_generation_id",
            ),
        ),
    ):
        existing = _indexes(table_name)
        for index_name in indexes:
            if index_name in existing:
                op.drop_index(index_name, table_name=table_name)

    with op.batch_alter_table("knowledge_embeddings", schema=None) as batch_op:
        if "generation_id" in _columns("knowledge_embeddings"):
            batch_op.drop_column("generation_id")
    with op.batch_alter_table("code_relations", schema=None) as batch_op:
        if "logical_id" in _columns("code_relations"):
            batch_op.drop_column("logical_id")
        if "generation_id" in _columns("code_relations"):
            batch_op.drop_column("generation_id")
    with op.batch_alter_table("code_symbols", schema=None) as batch_op:
        if "logical_id" in _columns("code_symbols"):
            batch_op.drop_column("logical_id")
        if "generation_id" in _columns("code_symbols"):
            batch_op.drop_column("generation_id")

    if "ix_repositories_active_graph_generation_id" in _indexes("repositories"):
        op.drop_index(
            "ix_repositories_active_graph_generation_id",
            table_name="repositories",
        )
    with op.batch_alter_table("repositories", schema=None) as batch_op:
        if "active_graph_generation_id" in _columns("repositories"):
            batch_op.drop_column("active_graph_generation_id")

    if "ix_model_profiles_active_embedding_generation_id" in _indexes("model_profiles"):
        op.drop_index(
            "ix_model_profiles_active_embedding_generation_id",
            table_name="model_profiles",
        )
    with op.batch_alter_table("model_profiles", schema=None) as batch_op:
        if "active_embedding_generation_id" in _columns("model_profiles"):
            batch_op.drop_column("active_embedding_generation_id")
