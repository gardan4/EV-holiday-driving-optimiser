"""add coarse client context to app_events

Revision ID: 0008_event_context
Revises: 0007_client_id
Create Date: 2026-08-10 00:00:00.000000

Country, device, browser, OS, viewport band and the referring path. All
nullable with no backfill: none of it can be reconstructed for a row that was
written before the deploy that started collecting it, and a default would
invent a device mix that never existed.

No indexes. Every query over these is a GROUP BY inside a window that the
existing `(at, name)` index already narrows, and each column holds a handful of
distinct values — an index on a column with eight values earns nothing and
costs a write on the busiest table in the app.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.migration_utils import has_column, has_table

revision = "0008_event_context"
down_revision = "0007_client_id"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("referrer_path", sa.String(length=200)),
    ("country", sa.String(length=2)),
    ("device", sa.String(length=8)),
    ("browser", sa.String(length=16)),
    ("os", sa.String(length=16)),
    ("viewport", sa.String(length=12)),
)


def upgrade() -> None:
    if not has_table("app_events"):
        return
    for name, type_ in _COLUMNS:
        if not has_column("app_events", name):
            op.add_column("app_events", sa.Column(name, type_, nullable=True))


def downgrade() -> None:
    if not has_table("app_events"):
        return
    for name, _ in reversed(_COLUMNS):
        if has_column("app_events", name):
            op.drop_column("app_events", name)
