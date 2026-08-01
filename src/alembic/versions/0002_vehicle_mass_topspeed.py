"""add mass_kg and top_speed_kph to vehicles

Revision ID: 0002_vehicle_mass_topspeed
Revises: 0001_initial
Create Date: 2026-08-01 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_vehicle_mass_topspeed"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vehicles",
        sa.Column("mass_kg", sa.Float(), nullable=False, server_default="1900"),
    )
    op.add_column(
        "vehicles",
        sa.Column("top_speed_kph", sa.Float(), nullable=False, server_default="180"),
    )


def downgrade() -> None:
    op.drop_column("vehicles", "top_speed_kph")
    op.drop_column("vehicles", "mass_kg")
