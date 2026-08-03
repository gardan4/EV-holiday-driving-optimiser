"""add feedback

Revision ID: 0004_feedback
Revises: 0003_trip_runs
Create Date: 2026-08-03 00:00:00.000000

Guarded like 0003: a fresh DB bootstrapped through `create_all()` + `stamp`
already has the table, and an unguarded DDL replay hangs on MSSQL's
schema-modification lock rather than failing fast.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_table
from app.models import GUID

revision = "0004_feedback"
down_revision = "0003_trip_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("feedback"):
        op.create_table(
            "feedback",
            sa.Column("id", GUID(), primary_key=True, nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("message", sa.String(length=2000), nullable=False),
            sa.Column("contact", sa.String(length=200), nullable=True),
            sa.Column("path", sa.String(length=300), nullable=True),
        )


def downgrade() -> None:
    if has_table("feedback"):
        op.drop_table("feedback")
