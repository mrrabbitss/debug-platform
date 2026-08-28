"""add per-user tokens, roles and case membership

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, Sequence[str], None] = "0005"
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
    if "user_accounts" not in tables:
        op.create_table(
            "user_accounts",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("username", sa.String(length=128), nullable=False),
            sa.Column("display_name", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_user_accounts_username", "user_accounts", ["username"], unique=True)
        op.create_index("ix_user_accounts_role", "user_accounts", ["role"], unique=False)
        op.create_index("ix_user_accounts_active", "user_accounts", ["active"], unique=False)

    if "access_tokens" not in tables:
        op.create_table(
            "access_tokens",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("user_id", sa.String(length=40), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("token_hash", sa.String(length=64), nullable=False),
            sa.Column("token_hint", sa.String(length=32), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_access_tokens_user_id", "access_tokens", ["user_id"], unique=False)
        op.create_index("ix_access_tokens_token_hash", "access_tokens", ["token_hash"], unique=True)
        op.create_index("ix_access_tokens_revoked_at", "access_tokens", ["revoked_at"], unique=False)

    if "owner_id" not in _columns("cases"):
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.add_column(sa.Column("owner_id", sa.String(length=40), nullable=True))
            batch_op.create_foreign_key(
                "fk_cases_owner_id_user_accounts",
                "user_accounts",
                ["owner_id"],
                ["id"],
                ondelete="SET NULL",
            )
    if "ix_cases_owner_id" not in _indexes("cases"):
        op.create_index("ix_cases_owner_id", "cases", ["owner_id"], unique=False)

    tables = _tables()
    if "case_members" not in tables:
        op.create_table(
            "case_members",
            sa.Column("id", sa.String(length=40), nullable=False),
            sa.Column("case_id", sa.String(length=40), nullable=False),
            sa.Column("user_id", sa.String(length=40), nullable=False),
            sa.Column("permission", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["case_id"], ["cases.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user_accounts.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("case_id", "user_id", name="uq_case_member"),
        )
        op.create_index("ix_case_members_case_id", "case_members", ["case_id"], unique=False)
        op.create_index("ix_case_members_user_id", "case_members", ["user_id"], unique=False)


def downgrade() -> None:
    tables = _tables()
    if "case_members" in tables:
        op.drop_table("case_members")
    if "ix_cases_owner_id" in _indexes("cases"):
        op.drop_index("ix_cases_owner_id", table_name="cases")
    if "owner_id" in _columns("cases"):
        with op.batch_alter_table("cases", schema=None) as batch_op:
            batch_op.drop_column("owner_id")
    tables = _tables()
    if "access_tokens" in tables:
        op.drop_table("access_tokens")
    if "user_accounts" in tables:
        op.drop_table("user_accounts")
