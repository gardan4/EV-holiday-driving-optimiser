"""initial EV Trip Optimizer schema

Creates the vehicle catalog (vehicles), external-data caches (chargers,
ocm_tiles, route_cache), and persisted trip plans (trips).

This is the fresh baseline. After you add or change models, generate
incremental migrations with:

    cd src && uv run alembic revision --autogenerate -m "describe change"

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-31 00:00:00.000000
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
        "vehicles",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("make", sa.Unicode(length=80), nullable=False),
        sa.Column("model", sa.Unicode(length=80), nullable=False),
        sa.Column("variant", sa.Unicode(length=80), nullable=True),
        sa.Column("usable_kwh", sa.Float(), nullable=False),
        sa.Column("consumption", sa.JSON(), nullable=False),
        sa.Column("charge_curve", sa.JSON(), nullable=False),
        sa.Column("max_dc_kw", sa.Float(), nullable=False),
        sa.Column("charge_efficiency", sa.Float(), nullable=False),
        sa.Column("source_note", sa.UnicodeText(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_vehicles_slug", "vehicles", ["slug"], unique=True)

    op.create_table(
        "chargers",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("ocm_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Unicode(length=200), nullable=False),
        sa.Column("operator", sa.Unicode(length=200), nullable=True),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("max_power_kw", sa.Float(), nullable=False),
        sa.Column("n_points", sa.Integer(), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("is_operational", sa.Boolean(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chargers_ocm_id", "chargers", ["ocm_id"], unique=True)
    op.create_index("ix_chargers_lat_lon", "chargers", ["lat", "lon"])

    op.create_table(
        "ocm_tiles",
        sa.Column("tile_key", sa.String(length=12), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("tile_key"),
    )

    op.create_table(
        "route_cache",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_route_cache_cache_key", "route_cache", ["cache_key"], unique=True)

    op.create_table(
        "trips",
        sa.Column("id", GUID(), nullable=False),
        sa.Column("vehicle_id", GUID(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("result_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trips_vehicle_id", "trips", ["vehicle_id"])
    op.create_index("ix_trips_created_at", "trips", ["created_at"])


def downgrade() -> None:
    op.drop_table("trips")
    op.drop_table("route_cache")
    op.drop_table("ocm_tiles")
    op.drop_table("chargers")
    op.drop_table("vehicles")
