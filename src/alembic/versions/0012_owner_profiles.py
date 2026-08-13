"""public usernames bound to a hashed browser secret

Revision ID: 0012_owner_profiles
Revises: 0011_plan_failure_reason
Create Date: 2026-08-13 00:00:00.000000

Two unique indexes carry the semantics rather than any application check:
`username` is first-come-first-served, and `owner_hash` is one name per secret.
The claim endpoint pre-checks both for a friendly 409, but the constraints are
what actually decide a race between two simultaneous claims.

`trips.owner_hash` is nullable with no backfill and no foreign key. No backfill
because claiming a username publishes what you plan next, not what you planned
before — a backfill here would silently make old trips public. No FK because
releasing a name is `UPDATE trips SET owner_hash = NULL` plus a delete, and an
FK would entangle the trip-delete path for nothing.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_column, has_index, has_table

revision = "0012_owner_profiles"
down_revision = "0011_plan_failure_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if not has_table("profiles"):
        op.create_table(
            "profiles",
            sa.Column("id", sa.CHAR(length=36), primary_key=True),
            sa.Column("username", sa.String(length=24), nullable=False),
            sa.Column("owner_hash", sa.String(length=32), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
        )
        op.create_index("ix_profiles_username", "profiles", ["username"], unique=True)
        op.create_index(
            "ix_profiles_owner_hash", "profiles", ["owner_hash"], unique=True
        )
        op.create_index("ix_profiles_created_at", "profiles", ["created_at"])

    if has_table("trips") and not has_column("trips", "owner_hash"):
        op.add_column(
            "trips", sa.Column("owner_hash", sa.String(length=32), nullable=True)
        )
    if has_table("trips") and not has_index("trips", "ix_trips_owner_hash"):
        op.create_index("ix_trips_owner_hash", "trips", ["owner_hash"])


def downgrade() -> None:
    if has_table("trips") and has_index("trips", "ix_trips_owner_hash"):
        op.drop_index("ix_trips_owner_hash", table_name="trips")
    if has_table("trips") and has_column("trips", "owner_hash"):
        op.drop_column("trips", "owner_hash")
    if has_table("profiles"):
        op.drop_table("profiles")
