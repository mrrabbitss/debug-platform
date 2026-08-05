"""add leases, retry budgets and dead-letter metadata to jobs

Revision ID: 0012
Revises: 0011
"""

from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _columns() -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_columns("jobs")
    }


def _indexes() -> set[str]:
    return {
        item["name"]
        for item in sa.inspect(op.get_bind()).get_indexes("jobs")
    }


def upgrade() -> None:
    columns = _columns()
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        if "idempotency_key" not in columns:
            batch_op.add_column(sa.Column("idempotency_key", sa.String(128), nullable=True))
        if "attempt" not in columns:
            batch_op.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"))
        if "max_attempts" not in columns:
            batch_op.add_column(sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"))
        if "available_at" not in columns:
            batch_op.add_column(sa.Column(
                "available_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ))
        if "lease_owner" not in columns:
            batch_op.add_column(sa.Column("lease_owner", sa.String(160), nullable=True))
        if "lease_expires_at" not in columns:
            batch_op.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
        if "heartbeat_at" not in columns:
            batch_op.add_column(sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True))
        if "deadline_at" not in columns:
            batch_op.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
        if "timeout_seconds" not in columns:
            batch_op.add_column(sa.Column(
                "timeout_seconds",
                sa.Integer(),
                nullable=False,
                server_default="1800",
            ))
        if "resource_limits_json" not in columns:
            batch_op.add_column(sa.Column(
                "resource_limits_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
            ))
        if "dead_letter_at" not in columns:
            batch_op.add_column(sa.Column("dead_letter_at", sa.DateTime(timezone=True), nullable=True))
        if "dead_letter_reason" not in columns:
            batch_op.add_column(sa.Column("dead_letter_reason", sa.Text(), nullable=True))

    indexes = _indexes()
    if "ix_jobs_idempotency_key" not in indexes:
        op.create_index("ix_jobs_idempotency_key", "jobs", ["idempotency_key"])
    if "ix_jobs_dispatch" not in indexes:
        op.create_index("ix_jobs_dispatch", "jobs", ["status", "available_at", "created_at"])
    if "ix_jobs_lease" not in indexes:
        op.create_index("ix_jobs_lease", "jobs", ["status", "lease_expires_at"])


def downgrade() -> None:
    indexes = _indexes()
    for index_name in (
        "ix_jobs_lease",
        "ix_jobs_dispatch",
        "ix_jobs_idempotency_key",
    ):
        if index_name in indexes:
            op.drop_index(index_name, table_name="jobs")
    columns = _columns()
    with op.batch_alter_table("jobs", schema=None) as batch_op:
        for column_name in (
            "dead_letter_reason",
            "dead_letter_at",
            "resource_limits_json",
            "timeout_seconds",
            "deadline_at",
            "heartbeat_at",
            "lease_expires_at",
            "lease_owner",
            "available_at",
            "max_attempts",
            "attempt",
            "idempotency_key",
        ):
            if column_name in columns:
                batch_op.drop_column(column_name)
