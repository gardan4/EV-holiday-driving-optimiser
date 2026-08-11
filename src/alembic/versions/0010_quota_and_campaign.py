"""add upstream_calls, and the campaign tag on app_events

Revision ID: 0010_quota_and_campaign
Revises: 0009_vehicle_nameplate
Create Date: 2026-08-10 00:00:00.000000

Two unrelated things in one revision because they ship together for one
purpose: knowing, during a launch, whether the free-tier quota is about to run
out and which link is bringing the people spending it.

`upstream_calls` is written on the hot path of the thing it measures, so it
carries exactly one index — the (at, provider) range every query uses. Nothing
in it identifies anybody, so there is nothing here to index by person and
nothing to correlate with.

`app_events.campaign` is nullable with no backfill: a visit that happened
before the tag existed has no answer, and inventing one would attribute
history to a link that was never posted.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_column, has_table
from app.models import GUID

revision = "0010_quota_and_campaign"
down_revision = "0009_vehicle_nameplate"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("upstream_calls"):
        op.create_table(
            "upstream_calls",
            sa.Column("id", GUID(), primary_key=True, nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("provider", sa.String(length=8), nullable=False),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("calls", sa.Integer(), nullable=False),
            sa.Column("cache_hits", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("ok", sa.Boolean(), nullable=False),
        )
        op.create_index(
            "ix_upstream_calls_at_provider", "upstream_calls", ["at", "provider"]
        )

    if has_table("app_events") and not has_column("app_events", "campaign"):
        op.add_column(
            "app_events", sa.Column("campaign", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    if has_table("app_events") and has_column("app_events", "campaign"):
        op.drop_column("app_events", "campaign")
    if has_table("upstream_calls"):
        op.drop_index("ix_upstream_calls_at_provider", table_name="upstream_calls")
        op.drop_table("upstream_calls")
