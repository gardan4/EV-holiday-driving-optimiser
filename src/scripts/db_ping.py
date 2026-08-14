"""Can this process reach its database, and which one is it?

A `SELECT 1` and a label. Exists for `./scripts/dashboard.sh --prod`, which
hands the console the deployed connection string: without a preflight, the one
failure that mode actually has — no SQL firewall rule for the machine you are
sitting at — looks like a console that started perfectly and then answered every
panel with a 500. That reads as a bug in the dashboard rather than as a missing
firewall rule, and it is the wrong thing to go debugging.

Read-only by construction: one `SELECT 1`, no schema access, no migration, no
seed. Safe to point at anything, which is the point — it is the first thing that
touches a database this repo has not connected to before.

    cd src && uv run python -m scripts.db_ping
    cd src && DATABASE_URL=… uv run python -m scripts.db_ping
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.database import AsyncSessionLocal, describe_url


async def ping() -> str:
    """The database's own name for itself, proving the login worked."""
    async with AsyncSessionLocal() as db:
        return str((await db.execute(text("SELECT DB_NAME()"))).scalar_one())


def main() -> None:
    # Host and name only — `describe_url` never returns credentials, which
    # matters here because this line ends up in a terminal, a task pane and
    # occasionally a screenshot.
    where = describe_url()
    label = f"{where['host']}/{where['name']}"
    try:
        name = asyncio.run(ping())
    except Exception as exc:  # noqa: BLE001 — the message is the whole output
        print(f"✗ {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    kind = "local" if where["local"] else "REMOTE"
    print(f"✓ {label} reachable ({kind}, connected as {name})")


if __name__ == "__main__":
    main()
