"""Delete location traces from drives that finished a while ago.

A `TripRun` and its `TripEvent` breadcrumbs are a record of a journey somebody
actually made, between two places one of which is usually home. That is the
most sensitive thing this app stores, and nothing else here expires, so it
needs deleting on a schedule rather than kept because deleting it was never
implemented.

Planned trips are NOT touched: they carry no location trail, and permalinks are
supposed to keep working.

Run it from a cron/Container App job, or by hand:

    cd src && uv run python -m scripts.purge_old_runs           # dry run
    cd src && uv run python -m scripts.purge_old_runs --apply
    cd src && uv run python -m scripts.purge_old_runs --apply --days 30
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.database import AsyncSessionLocal
from app.models import TripEvent, TripRun

logger = logging.getLogger("purge_old_runs")

# Matches what the privacy page promises. Change both together.
DEFAULT_RETENTION_DAYS = 90


async def purge(days: int, apply: bool) -> tuple[int, int]:
    """Returns (runs, events) affected. Cuts on `started_at` so a drive that was
    never finished — a phone that died mid-journey — expires too."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        run_ids = (
            (await db.execute(select(TripRun.id).where(TripRun.started_at < cutoff)))
            .scalars()
            .all()
        )
        if not run_ids:
            return (0, 0)

        n_events = (
            await db.execute(
                select(func.count())
                .select_from(TripEvent)
                .where(TripEvent.run_id.in_(run_ids))
            )
        ).scalar_one()

        if apply:
            # Events first: the FK has no cascade, by design — nothing should
            # be able to delete a run's history as a side effect.
            await db.execute(delete(TripEvent).where(TripEvent.run_id.in_(run_ids)))
            await db.execute(delete(TripRun).where(TripRun.id.in_(run_ids)))
            await db.commit()
        return (len(run_ids), int(n_events))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS)
    ap.add_argument(
        "--apply", action="store_true", help="actually delete (default is a dry run)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    runs, events = asyncio.run(purge(args.days, args.apply))
    verb = "Deleted" if args.apply else "Would delete"
    logger.info(
        "%s %d drive(s) and %d location point(s) older than %d days.",
        verb, runs, events, args.days,
    )
    if not args.apply and runs:
        logger.info("Dry run — pass --apply to delete.")


if __name__ == "__main__":
    main()
