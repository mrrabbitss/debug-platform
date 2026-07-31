"""add knowledge governance, domain graph and retrieval evaluation

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-30
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, Sequence[str], None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name: str) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _create_knowledge_revisions() -> None:
    if "knowledge_revisions" in _tables():
        return
    op.create_table(
        "knowledge_revisions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("document_id", sa.String(length=40), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("change_summary", sa.String(length=512), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "version",
            name="uq_knowledge_revision_version",
        ),
    )
    op.create_index(
        "ix_knowledge_revisions_document_id",
        "knowledge_revisions",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_revisions_content_hash",
        "knowledge_revisions",
        ["content_hash"],
        unique=False,
    )
    op.create_index(
        "ix_knowledge_revisions_document_created",
        "knowledge_revisions",
        ["document_id", "created_at"],
        unique=False,
    )


def _create_domain_graph_tables() -> None:
    tables = _tables()
    if "knowledge_graph_states" not in tables:
        op.create_table(
            "knowledge_graph_states",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column(
                "active_generation_id",
                sa.String(length=40),
                nullable=True,
            ),
            sa.Column(
                "building_generation_id",
                sa.String(length=40),
                nullable=True,
            ),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_knowledge_graph_states_active_generation_id",
            "knowledge_graph_states",
            ["active_generation_id"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_graph_states_building_generation_id",
            "knowledge_graph_states",
            ["building_generation_id"],
            unique=False,
        )
        op.create_index(
            "ix_knowledge_graph_states_status",
            "knowledge_graph_states",
            ["status"],
            unique=False,
        )

    tables = _tables()
    if "knowledge_entities" not in tables:
        op.create_table(
            "knowledge_entities",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("logical_id", sa.String(length=40), nullable=False),
            sa.Column("generation_id", sa.String(length=40), nullable=False),
            sa.Column("entity_type", sa.String(length=32), nullable=False),
            sa.Column("canonical_name", sa.String(length=512), nullable=False),
            sa.Column("normalized_name", sa.String(length=512), nullable=False),
            sa.Column("aliases_json", sa.Text(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "generation_id",
                "entity_type",
                "normalized_name",
                name="uq_knowledge_entity_generation_name",
            ),
        )
        for index_name, columns in (
            ("ix_knowledge_entities_logical_id", ["logical_id"]),
            ("ix_knowledge_entities_generation_id", ["generation_id"]),
            ("ix_knowledge_entities_entity_type", ["entity_type"]),
            ("ix_knowledge_entities_normalized_name", ["normalized_name"]),
            (
                "ix_knowledge_entities_generation_type",
                ["generation_id", "entity_type"],
            ),
        ):
            op.create_index(
                index_name,
                "knowledge_entities",
                columns,
                unique=False,
            )

    tables = _tables()
    if "knowledge_entity_mentions" not in tables:
        op.create_table(
            "knowledge_entity_mentions",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("generation_id", sa.String(length=40), nullable=False),
            sa.Column("entity_id", sa.String(length=40), nullable=False),
            sa.Column("document_id", sa.String(length=40), nullable=False),
            sa.Column("chunk_id", sa.String(length=40), nullable=True),
            sa.Column("excerpt", sa.Text(), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["entity_id"], ["knowledge_entities.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["document_id"], ["knowledge_documents.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["chunk_id"], ["knowledge_chunks.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for index_name, columns in (
            ("ix_knowledge_entity_mentions_generation_id", ["generation_id"]),
            ("ix_knowledge_entity_mentions_entity_id", ["entity_id"]),
            ("ix_knowledge_entity_mentions_document_id", ["document_id"]),
            ("ix_knowledge_entity_mentions_chunk_id", ["chunk_id"]),
            (
                "ix_knowledge_mentions_generation_document",
                ["generation_id", "document_id"],
            ),
        ):
            op.create_index(
                index_name,
                "knowledge_entity_mentions",
                columns,
                unique=False,
            )

    tables = _tables()
    if "knowledge_relations" not in tables:
        op.create_table(
            "knowledge_relations",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("logical_id", sa.String(length=40), nullable=False),
            sa.Column("generation_id", sa.String(length=40), nullable=False),
            sa.Column("source_entity_id", sa.String(length=40), nullable=False),
            sa.Column("target_entity_id", sa.String(length=40), nullable=False),
            sa.Column("relation_type", sa.String(length=64), nullable=False),
            sa.Column(
                "evidence_document_id",
                sa.String(length=40),
                nullable=False,
            ),
            sa.Column(
                "evidence_chunk_id",
                sa.String(length=40),
                nullable=True,
            ),
            sa.Column("confidence", sa.Float(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["source_entity_id"],
                ["knowledge_entities.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["target_entity_id"],
                ["knowledge_entities.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["evidence_document_id"],
                ["knowledge_documents.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["evidence_chunk_id"],
                ["knowledge_chunks.id"],
                ondelete="SET NULL",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        for index_name, columns in (
            ("ix_knowledge_relations_logical_id", ["logical_id"]),
            ("ix_knowledge_relations_generation_id", ["generation_id"]),
            ("ix_knowledge_relations_source_entity_id", ["source_entity_id"]),
            ("ix_knowledge_relations_target_entity_id", ["target_entity_id"]),
            ("ix_knowledge_relations_relation_type", ["relation_type"]),
            (
                "ix_knowledge_relations_evidence_document_id",
                ["evidence_document_id"],
            ),
            (
                "ix_knowledge_relations_evidence_chunk_id",
                ["evidence_chunk_id"],
            ),
            (
                "ix_knowledge_relations_generation_source",
                ["generation_id", "source_entity_id"],
            ),
            (
                "ix_knowledge_relations_generation_target",
                ["generation_id", "target_entity_id"],
            ),
            (
                "ix_knowledge_relations_generation_type",
                ["generation_id", "relation_type"],
            ),
        ):
            op.create_index(
                index_name,
                "knowledge_relations",
                columns,
                unique=False,
            )


def _create_feedback_table() -> None:
    if "diagnosis_feedback" in _tables():
        return
    op.create_table(
        "diagnosis_feedback",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("case_id", sa.String(length=40), nullable=False),
        sa.Column("analysis_run_id", sa.String(length=40), nullable=False),
        sa.Column("verdict", sa.String(length=32), nullable=False),
        sa.Column("root_cause_correct", sa.Boolean(), nullable=True),
        sa.Column("evidence_correct", sa.Boolean(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("corrections_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("submitted_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_by", sa.String(length=128), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_comment", sa.Text(), nullable=True),
        sa.Column(
            "incorporated_document_id",
            sa.String(length=40),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["analysis_runs.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["incorporated_document_id"],
            ["knowledge_documents.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for index_name, columns in (
        ("ix_diagnosis_feedback_case_id", ["case_id"]),
        ("ix_diagnosis_feedback_analysis_run_id", ["analysis_run_id"]),
        ("ix_diagnosis_feedback_verdict", ["verdict"]),
        ("ix_diagnosis_feedback_status", ["status"]),
        (
            "ix_diagnosis_feedback_incorporated_document_id",
            ["incorporated_document_id"],
        ),
        ("ix_diagnosis_feedback_case_created", ["case_id", "created_at"]),
        (
            "ix_diagnosis_feedback_status_created",
            ["status", "created_at"],
        ),
    ):
        op.create_index(index_name, "diagnosis_feedback", columns, unique=False)


def _create_evaluation_tables() -> None:
    tables = _tables()
    if "retrieval_evaluation_datasets" not in tables:
        op.create_table(
            "retrieval_evaluation_datasets",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name"),
        )
        op.create_index(
            "ix_retrieval_evaluation_datasets_active",
            "retrieval_evaluation_datasets",
            ["active"],
            unique=False,
        )

    tables = _tables()
    if "retrieval_evaluation_cases" not in tables:
        op.create_table(
            "retrieval_evaluation_cases",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("dataset_id", sa.String(length=40), nullable=False),
            sa.Column("case_id", sa.String(length=40), nullable=False),
            sa.Column("query", sa.Text(), nullable=False),
            sa.Column("expected_evidence_json", sa.Text(), nullable=False),
            sa.Column("expected_root_causes_json", sa.Text(), nullable=False),
            sa.Column("modules_json", sa.Text(), nullable=False),
            sa.Column("top_k", sa.Integer(), nullable=False),
            sa.Column("max_hops", sa.Integer(), nullable=False),
            sa.Column("metadata_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["dataset_id"],
                ["retrieval_evaluation_datasets.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["case_id"],
                ["cases.id"],
                ondelete="CASCADE",
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_retrieval_evaluation_cases_dataset_id",
            "retrieval_evaluation_cases",
            ["dataset_id"],
            unique=False,
        )
        op.create_index(
            "ix_retrieval_evaluation_cases_case_id",
            "retrieval_evaluation_cases",
            ["case_id"],
            unique=False,
        )
        op.create_index(
            "ix_retrieval_evaluation_cases_dataset_created",
            "retrieval_evaluation_cases",
            ["dataset_id", "created_at"],
            unique=False,
        )

    tables = _tables()
    if "retrieval_evaluation_runs" not in tables:
        op.create_table(
            "retrieval_evaluation_runs",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("dataset_id", sa.String(length=40), nullable=False),
            sa.Column("job_id", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("config_json", sa.Text(), nullable=False),
            sa.Column("metrics_json", sa.Text(), nullable=False),
            sa.Column("results_json", sa.Text(), nullable=False),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(
                ["dataset_id"],
                ["retrieval_evaluation_datasets.id"],
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_retrieval_evaluation_runs_dataset_id",
            "retrieval_evaluation_runs",
            ["dataset_id"],
            unique=False,
        )
        op.create_index(
            "ix_retrieval_evaluation_runs_job_id",
            "retrieval_evaluation_runs",
            ["job_id"],
            unique=False,
        )
        op.create_index(
            "ix_retrieval_evaluation_runs_status",
            "retrieval_evaluation_runs",
            ["status"],
            unique=False,
        )
        op.create_index(
            "ix_retrieval_evaluation_runs_dataset_created",
            "retrieval_evaluation_runs",
            ["dataset_id", "created_at"],
            unique=False,
        )


def _backfill_initial_revisions() -> None:
    connection = op.get_bind()
    rows = list(connection.execute(sa.text(
        "SELECT id, title, source_type, device_type, device_model, "
        "firmware_range, module, trust_level, confidentiality, content, "
        "metadata_json, active, review_status, version, lock_version, "
        "created_at, updated_at "
        "FROM knowledge_documents "
        "WHERE NOT EXISTS ("
        "SELECT 1 FROM knowledge_revisions "
        "WHERE knowledge_revisions.document_id = knowledge_documents.id"
        ")"
    )).mappings())
    for row in rows:
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {
                "_legacy_raw_metadata": str(row["metadata_json"] or "")[:4000]
            }
        snapshot = {
            "title": row["title"],
            "source_type": row["source_type"],
            "device_type": row["device_type"],
            "device_model": row["device_model"],
            "firmware_range": row["firmware_range"],
            "module": row["module"],
            "trust_level": row["trust_level"],
            "confidentiality": row["confidentiality"],
            "content": row["content"],
            "metadata": metadata,
            "active": bool(row["active"]),
            "review_status": row["review_status"],
        }
        content = str(row["content"] or "")
        connection.execute(
            sa.text(
                "INSERT INTO knowledge_revisions "
                "(id, document_id, version, snapshot_json, content_hash, "
                "change_summary, created_by, created_at) "
                "VALUES (:id, :document_id, :version, :snapshot_json, "
                ":content_hash, :change_summary, NULL, :created_at)"
            ),
            {
                "id": _new_id("KREV"),
                "document_id": row["id"],
                "version": int(row["version"] or 1),
                "snapshot_json": json.dumps(
                    snapshot,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "content_hash": hashlib.sha256(
                    content.encode("utf-8")
                ).hexdigest(),
                "change_summary": "Migration baseline",
                "created_at": row["updated_at"] or row["created_at"],
            },
        )


def upgrade() -> None:
    document_columns = _columns("knowledge_documents")
    review_status_added = "review_status" not in document_columns
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        if "review_status" not in document_columns:
            batch_op.add_column(sa.Column(
                "review_status",
                sa.String(length=32),
                nullable=False,
                server_default="ACTIVE",
            ))
        if "version" not in document_columns:
            batch_op.add_column(sa.Column(
                "version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ))
        if "lock_version" not in document_columns:
            batch_op.add_column(sa.Column(
                "lock_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ))
        if "reviewed_by" not in document_columns:
            batch_op.add_column(sa.Column(
                "reviewed_by",
                sa.String(length=128),
                nullable=True,
            ))
        if "reviewed_at" not in document_columns:
            batch_op.add_column(sa.Column(
                "reviewed_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ))
        if "review_comment" not in document_columns:
            batch_op.add_column(sa.Column(
                "review_comment",
                sa.Text(),
                nullable=True,
            ))
        if "published_at" not in document_columns:
            batch_op.add_column(sa.Column(
                "published_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ))

    document_indexes = _indexes("knowledge_documents")
    if "ix_knowledge_documents_review_status" not in document_indexes:
        op.create_index(
            "ix_knowledge_documents_review_status",
            "knowledge_documents",
            ["review_status"],
            unique=False,
        )

    connection = op.get_bind()
    if review_status_added:
        connection.execute(sa.text(
            "UPDATE knowledge_documents "
            "SET review_status = CASE "
            "WHEN active = true THEN 'ACTIVE' ELSE 'DRAFT' END"
        ))
    else:
        connection.execute(sa.text(
            "UPDATE knowledge_documents "
            "SET review_status = CASE "
            "WHEN active = true THEN 'ACTIVE' ELSE 'DRAFT' END "
            "WHERE review_status IS NULL OR review_status = ''"
        ))
    connection.execute(sa.text(
        "UPDATE knowledge_documents SET published_at = updated_at "
        "WHERE active = true AND published_at IS NULL"
    ))

    _create_knowledge_revisions()
    _create_domain_graph_tables()
    _create_feedback_table()
    _create_evaluation_tables()
    _backfill_initial_revisions()


def downgrade() -> None:
    for table_name in (
        "retrieval_evaluation_runs",
        "retrieval_evaluation_cases",
        "retrieval_evaluation_datasets",
        "diagnosis_feedback",
        "knowledge_relations",
        "knowledge_entity_mentions",
        "knowledge_entities",
        "knowledge_graph_states",
        "knowledge_revisions",
    ):
        if table_name in _tables():
            op.drop_table(table_name)

    if (
        "ix_knowledge_documents_review_status"
        in _indexes("knowledge_documents")
    ):
        op.drop_index(
            "ix_knowledge_documents_review_status",
            table_name="knowledge_documents",
        )
    document_columns = _columns("knowledge_documents")
    with op.batch_alter_table("knowledge_documents", schema=None) as batch_op:
        for column_name in (
            "published_at",
            "review_comment",
            "reviewed_at",
            "reviewed_by",
            "lock_version",
            "version",
            "review_status",
        ):
            if column_name in document_columns:
                batch_op.drop_column(column_name)
