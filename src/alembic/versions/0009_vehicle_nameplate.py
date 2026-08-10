"""group vehicle variants under a nameplate

Revision ID: 0009_vehicle_nameplate
Revises: 0008_event_context
Create Date: 2026-08-10 00:00:00.000000

Nullable, with no backfill here on purpose. The seeder rewrites every column of
every row on each boot, so the values arrive from `scripts/seed_vehicles.py`
moments after this runs — and putting them in a migration too would mean two
places to change when a car moves between nameplates, one of which nobody would
remember.

Nullable is also the graceful degradation. A car with no nameplate falls back
to its own slug and gets a single-variant page, which is exactly the behaviour
every car had before this column existed.

Indexed because it is the lookup for `/ev/<nameplate>`, which is a public page
resolved per request.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_column, has_table

revision = "0009_vehicle_nameplate"
down_revision = "0008_event_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if has_table("vehicles") and not has_column("vehicles", "nameplate_slug"):
        op.add_column(
            "vehicles", sa.Column("nameplate_slug", sa.String(length=60), nullable=True)
        )
        op.create_index("ix_vehicles_nameplate_slug", "vehicles", ["nameplate_slug"])


def downgrade() -> None:
    if has_table("vehicles") and has_column("vehicles", "nameplate_slug"):
        op.drop_index("ix_vehicles_nameplate_slug", table_name="vehicles")
        op.drop_column("vehicles", "nameplate_slug")
