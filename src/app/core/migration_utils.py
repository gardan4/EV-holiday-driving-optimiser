"""Idempotency helpers shared by Alembic migrations.

Lets each migration no-op cleanly when the column/table/index/FK it
adds is already present. The motivating case: a fresh DB bootstrapped
via ``Base.metadata.create_all()`` + ``stamp(v2_clean_rebuild)`` already
has every column from every later migration, and on MSSQL the
unguarded ``ALTER TABLE ... ADD COLUMN`` for an existing column hangs
on the schema-modification lock indefinitely instead of erroring fast.

Use the helpers below at the top of each ``upgrade()`` to skip
already-applied schema operations.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


def has_column(table: str, column: str) -> bool:
    """Return True if ``table.column`` exists on the current DB."""
    bind = op.get_bind()
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def has_table(table: str) -> bool:
    """Return True if ``table`` exists on the current DB."""
    return sa.inspect(op.get_bind()).has_table(table)


def has_index(table: str, index_name: str) -> bool:
    """Return True if an index with this name exists on ``table``."""
    bind = op.get_bind()
    return any(
        i.get("name") == index_name for i in sa.inspect(bind).get_indexes(table)
    )


def has_foreign_key(table: str, fk_name: str) -> bool:
    """Return True if an FK with this name exists on ``table``."""
    bind = op.get_bind()
    return any(
        fk.get("name") == fk_name for fk in sa.inspect(bind).get_foreign_keys(table)
    )
