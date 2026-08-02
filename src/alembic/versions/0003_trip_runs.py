"""add trip_runs and trip_events for live journey tracking

Revision ID: 0003_trip_runs
Revises: 0002_vehicle_mass_topspeed
Create Date: 2026-08-02 00:00:00.000000

Guarded with `migration_utils`, unlike 0002: a fresh DB bootstrapped through
`Base.metadata.create_all()` + `stamp("head")` already has these tables, and on
MSSQL an unguarded DDL replay hangs on the schema-modification lock rather than
failing fast.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_index, has_table
from app.models import GUID  # every id/FK column — CHAR(36) on MSSQL

revision = "0003_trip_runs"
down_revision = "0002_vehicle_mass_topspeed"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("trip_runs"):
        op.create_table(
            "trip_runs",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("trip_id", GUID(), sa.ForeignKey("trips.id"), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="active"),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=False),
            sa.Column("planned_speed_kph", sa.Float(), nullable=False),
            sa.Column("route_snapshot", sa.JSON(), nullable=False),
            sa.Column("state", sa.JSON(), nullable=False),
            sa.Column("plan", sa.JSON(), nullable=True),
            sa.Column("plan_version", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("n_pings", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("n_replans", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("n_soc_readings", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
    if has_table("trip_runs"):
        if not has_index("trip_runs", "ix_trip_runs_trip_status"):
            op.create_index(
                "ix_trip_runs_trip_status", "trip_runs", ["trip_id", "status"]
            )
        if not has_index("trip_runs", "ix_trip_runs_last_seen_at"):
            op.create_index("ix_trip_runs_last_seen_at", "trip_runs", ["last_seen_at"])

    if not has_table("trip_events"):
        op.create_table(
            "trip_events",
            sa.Column("id", GUID(), primary_key=True),
            sa.Column("run_id", GUID(), sa.ForeignKey("trip_runs.id"), nullable=False),
            sa.Column("at", sa.DateTime(), nullable=False),
            sa.Column("kind", sa.String(24), nullable=False),
            sa.Column("offset_m", sa.Float(), nullable=False, server_default="0"),
            sa.Column("lat", sa.Float(), nullable=True),
            sa.Column("lon", sa.Float(), nullable=True),
            sa.Column("soc", sa.Float(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=True),
        )
    if has_table("trip_events") and not has_index("trip_events", "ix_trip_events_run_at"):
        op.create_index("ix_trip_events_run_at", "trip_events", ["run_id", "at"])


def downgrade() -> None:
    # Events reference runs, and there is no cascade — drop the child first.
    if has_table("trip_events"):
        op.drop_table("trip_events")
    if has_table("trip_runs"):
        op.drop_table("trip_runs")
