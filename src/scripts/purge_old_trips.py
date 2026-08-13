"""Delete planned trips once they are older than the retention window.

Trips used to be kept forever, on the reasoning that a share link should not
stop working. That reasoning is fine and the conclusion was not: `Trip.request`
holds the exact coordinates of a start and a destination, and one of those is
usually somebody's house. "We keep precise geolocation indefinitely because
deleting it would be inconvenient" is not a retention period, and storage
limitation asks for one.

Two years is the compromise. This is a holiday app, so the cycle is annual and
anything under about thirteen months would kill a link between one summer and
the next. Two years clears that with room and still ends.

Deletes in the same order `api.trips.delete_trip` uses, and for the same reason:
the foreign keys have no cascade, on purpose, so nothing can remove a drive's
history as a side effect of touching something else. A trip's drives, its
location trail and its coarsened `TripStat` all go with it. A derived table
nobody deletes from is how "deleting a trip removes the whole record" quietly
becomes untrue.

Batched rather than one statement. The first run against a table that has never
been purged can match a lot of rows, and a single delete big enough to hold them
all is a transaction long enough to time out against Azure SQL.

    cd src && uv run python -m scripts.purge_old_trips           # dry run
    cd src && uv run python -m scripts.purge_old_trips --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Trip, TripEvent, TripRun, TripStat

logger = logging.getLogger("purge_old_trips")

# Matches what the privacy page promises. Change both together.
DEFAULT_RETENTION_DAYS = 730

# How many trips to delete per transaction. Small enough to stay well inside
# any statement timeout, large enough that a backlog clears in one run.
BATCH = 200


async def purge(days: int, apply: bool) -> tuple[int, int]:
    """Returns (trips, drives) affected."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    trips_done = 0
    runs_done = 0

    async with AsyncSessionLocal() as db:
        while True:
            trip_ids = (
                (
                    await db.execute(
                        select(Trip.id).where(Trip.created_at < cutoff).limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not trip_ids:
                break

            run_ids = (
                (
                    await db.execute(
                        select(TripRun.id).where(TripRun.trip_id.in_(trip_ids))
                    )
                )
                .scalars()
                .all()
            )

            trips_done += len(trip_ids)
            runs_done += len(run_ids)

            if not apply:
                # Nothing has been deleted, so the same rows would be selected
                # forever. Report what one pass would have done and stop.
                break

            if run_ids:
                await db.execute(delete(TripEvent).where(TripEvent.run_id.in_(run_ids)))
                await db.execute(delete(TripRun).where(TripRun.id.in_(run_ids)))
            await db.execute(delete(TripStat).where(TripStat.trip_id.in_(trip_ids)))
            await db.execute(delete(Trip).where(Trip.id.in_(trip_ids)))
            await db.commit()

    return (trips_done, runs_done)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS)
    ap.add_argument(
        "--apply", action="store_true", help="actually delete (default is a dry run)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    trips, runs = asyncio.run(purge(args.days, args.apply))
    verb = "Deleted" if args.apply else "Would delete"
    logger.info(
        "%s %d trip(s) and %d drive(s) older than %d days.", verb, trips, runs, args.days
    )
    if not args.apply and trips:
        logger.info(
            "Dry run of one batch (max %d) - pass --apply to delete.", BATCH
        )


if __name__ == "__main__":
    main()
