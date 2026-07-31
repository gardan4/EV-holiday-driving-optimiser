"""Shared database-level utilities."""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a naive datetime.

    Replacement for the deprecated datetime.utcnow(). Returns a naive datetime
    (no tzinfo) for compatibility with the MSSQL DateTime columns and SQLAlchemy
    model defaults used across the app.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
