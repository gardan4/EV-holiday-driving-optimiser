"""add app_events

Revision ID: 0005_app_events
Revises: 0004_feedback
Create Date: 2026-08-08 00:00:00.000000

Guarded like 0003/0004: a fresh DB bootstrapped through `create_all()` +
`stamp` already has the table, and an unguarded DDL replay hangs on MSSQL's
schema-modification lock rather than failing fast.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_table
from app.models import GUID

revision = "0005_app_events"
down_revision = "0004_feedback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("app_events"):
        op.create_table(
            "app_events",
            sa.Column("id", GUID(), primary_key=True, nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("name", sa.String(length=32), nullable=False),
            sa.Column("path", sa.String(length=120), nullable=True),
            sa.Column("visitor", sa.String(length=32), nullable=False),
            sa.Column("referrer", sa.String(length=120), nullable=True),
        )
        op.create_index("ix_app_events_at_name", "app_events", ["at", "name"])


def downgrade() -> None:
    if has_table("app_events"):
        op.drop_index("ix_app_events_at_name", table_name="app_events")
        op.drop_table("app_events")
