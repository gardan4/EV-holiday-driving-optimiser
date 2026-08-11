"""add the failure reason to app_events

Revision ID: 0011_plan_failure_reason
Revises: 0010_quota_and_campaign
Create Date: 2026-08-11 00:00:00.000000

Nullable, no backfill, no index. Null on every event that is not a
`plan_failed`, and null on the `plan_failed` rows written before this shipped —
those failures happened, and there is nothing left anywhere that says why, so
inventing a reason for them would be worse than the gap.

No index: the funnel query already groups over a window the `(at, name)` index
narrows, and the column holds a dozen distinct values.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_column, has_table

revision = "0011_plan_failure_reason"
down_revision = "0010_quota_and_campaign"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if has_table("app_events") and not has_column("app_events", "reason"):
        op.add_column(
            "app_events", sa.Column("reason", sa.String(length=24), nullable=True)
        )


def downgrade() -> None:
    if has_table("app_events") and has_column("app_events", "reason"):
        op.drop_column("app_events", "reason")
