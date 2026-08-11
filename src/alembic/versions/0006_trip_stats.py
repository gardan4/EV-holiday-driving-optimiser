"""add trip_stats

Revision ID: 0006_trip_stats
Revises: 0005_app_events
Create Date: 2026-08-10 00:00:00.000000

Guarded like 0003/0004/0005: a fresh DB bootstrapped through `create_all()` +
`stamp` already has the table, and an unguarded DDL replay hangs on MSSQL's
schema-modification lock rather than failing fast.

Creates the table empty. Existing trips are derived by
`scripts.backfill_trip_stats`, which is a separate, resumable, restartable step
on purpose — deriving thousands of rows inside a migration means a schema lock
held for the length of a data job, and a failure halfway leaves the deploy
wedged rather than the backfill unfinished.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_table
from app.models import GUID

revision = "0006_trip_stats"
down_revision = "0005_app_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("trip_stats"):
        op.create_table(
            "trip_stats",
            sa.Column("id", GUID(), primary_key=True, nullable=False),
            sa.Column("trip_id", GUID(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("vehicle_id", GUID(), nullable=False),
            sa.Column("origin_gh", sa.String(length=4), nullable=False),
            sa.Column("dest_gh", sa.String(length=4), nullable=False),
            sa.Column("origin_cc", sa.String(length=2), nullable=True),
            sa.Column("dest_cc", sa.String(length=2), nullable=True),
            sa.Column("countries", sa.JSON(), nullable=True),
            sa.Column("distance_km", sa.Integer(), nullable=False),
            sa.Column("departure_month", sa.Integer(), nullable=True),
            sa.Column("feasible", sa.Boolean(), nullable=False),
            sa.Column("optimum_speed_kph", sa.Float(), nullable=True),
            sa.Column("optimum_n_stops", sa.Integer(), nullable=True),
            sa.Column("optimum_total_min", sa.Float(), nullable=True),
            sa.Column("optimum_charge_min", sa.Float(), nullable=True),
            sa.ForeignKeyConstraint(["trip_id"], ["trips.id"]),
            sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"]),
        )
        # Unique, so re-running the backfill cannot double-count a corridor.
        op.create_index(
            "ix_trip_stats_trip_id", "trip_stats", ["trip_id"], unique=True
        )
        op.create_index("ix_trip_stats_vehicle_id", "trip_stats", ["vehicle_id"])
        op.create_index("ix_trip_stats_created_at", "trip_stats", ["created_at"])


def downgrade() -> None:
    if has_table("trip_stats"):
        op.drop_index("ix_trip_stats_created_at", table_name="trip_stats")
        op.drop_index("ix_trip_stats_vehicle_id", table_name="trip_stats")
        op.drop_index("ix_trip_stats_trip_id", table_name="trip_stats")
        op.drop_table("trip_stats")
