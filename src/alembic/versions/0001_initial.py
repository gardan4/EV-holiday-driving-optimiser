"""initial skeleton schema

Creates the tenancy spine (users, businesses, business_memberships), the example
child resource (items), and the audit log (user_audit_logs).

This is the fresh baseline for the template. After you add or change models,
generate incremental migrations with:

    cd src && uv run alembic revision --autogenerate -m "describe change"

Revision ID: 0001_initial
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# Custom UUID type used by every id / FK column (CHAR(36) on MSSQL).
from app.models import GUID

# revision identifiers, used by Alembic.
revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.Unicode(length=255), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=255), nullable=True),
        sa.Column("email_verified", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("onboarded", sa.Boolean(), server_default="0", nullable=False),
        sa.Column("tos_accepted_at", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    # Filtered unique index: a plain UNIQUE on a nullable column only permits one
    # NULL row on SQL Server, so scope uniqueness to non-NULL clerk_user_id. The
    # mssql_where is ignored on other dialects (e.g. SQLite in component tests).
    op.create_index(
        "uq_users_clerk_user_id",
        "users",
        ["clerk_user_id"],
        unique=True,
        mssql_where=sa.text("clerk_user_id IS NOT NULL"),
    )

    op.create_table(
        "businesses",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("owner_user_id", GUID(), nullable=False),
        sa.Column("name", sa.Unicode(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_businesses_owner_user_id", "businesses", ["owner_user_id"])
    op.create_index("ix_businesses_archived_at", "businesses", ["archived_at"])

    op.create_table(
        "business_memberships",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("business_id", GUID(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "user_id", name="uq_business_membership"),
    )
    op.create_index("ix_business_memberships_business_id", "business_memberships", ["business_id"])
    op.create_index("ix_business_memberships_user_id", "business_memberships", ["user_id"])

    op.create_table(
        "items",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("business_id", GUID(), nullable=False),
        sa.Column("name", sa.Unicode(length=200), nullable=False),
        sa.Column("description", sa.UnicodeText(), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("data", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("archived_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_items_business_id", "items", ["business_id"])
    op.create_index("ix_items_business", "items", ["business_id", "archived_at"])

    op.create_table(
        "user_audit_logs",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("business_id", GUID(), nullable=False),
        sa.Column("user_id", GUID(), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("target_type", sa.String(length=40), nullable=True),
        sa.Column("target_id", GUID(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_audit_business_time", "user_audit_logs", ["business_id", "created_at"])
    op.create_index("ix_user_audit_user_time", "user_audit_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("user_audit_logs")
    op.drop_table("items")
    op.drop_table("business_memberships")
    op.drop_table("businesses")
    op.drop_index("uq_users_clerk_user_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
