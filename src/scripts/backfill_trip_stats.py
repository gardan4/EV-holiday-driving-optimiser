"""Derive `trip_stats` rows for trips planned before the table existed.

Every number this writes has been sitting in `Trip.request` / `Trip.result`
since the first deploy — so unlike the event counters, which only start the day
they ship, the corridor data starts with full history the moment this runs.
That is the whole reason 4a was worth doing before a launch rather than after.

Safe to re-run and safe to interrupt. `trip_stats.trip_id` is unique and this
only visits trips that have no row yet, so a second run picks up where a killed
one stopped and cannot double-count. It reads trips in batches so a table that
has grown past memory is not loaded at once.

Read-mostly: it inserts derived rows and touches nothing else, so unlike
`dev_seed_trip` it has no reason to refuse a production database.

    cd src && uv run python -m scripts.backfill_trip_stats           # dry run
    cd src && uv run python -m scripts.backfill_trip_stats --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import func, select

from app.core.database import AsyncSessionLocal
from app.models import Trip, TripStat
from app.services.trip_stats import build_trip_stat

logger = logging.getLogger("backfill_trip_stats")

# Big enough that the round trips don't dominate, small enough that one batch
# of trip JSON (a long route's result is ~0.9 MB) stays a sane amount of memory.
BATCH = 200


async def backfill(apply: bool, batch: int = BATCH) -> tuple[int, int]:
    """Returns (rows written, trips skipped as underivable)."""
    written = 0
    skipped = 0

    async with AsyncSessionLocal() as db:
        # A NOT EXISTS rather than a NOT IN: the latter builds the whole id set
        # in memory on the client, which is the thing this is trying to avoid.
        pending = (
            select(func.count())
            .select_from(Trip)
            .where(~select(TripStat.id).where(TripStat.trip_id == Trip.id).exists())
        )
        total = (await db.execute(pending)).scalar_one()
        logger.info("%d trip(s) without a stat row.", total)
        if not total:
            return 0, 0

        while True:
            # Applying shrinks the pending set as it goes, so the window stays
            # at the front. A dry run writes nothing, so the same rows would
            # come back forever — it has to page past what it has already seen.
            offset = 0 if apply else written + skipped
            rows = (
                (
                    await db.execute(
                        select(Trip)
                        .where(
                            ~select(TripStat.id)
                            .where(TripStat.trip_id == Trip.id)
                            .exists()
                        )
                        .order_by(Trip.created_at)
                        .offset(offset)
                        .limit(batch)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                break

            for trip in rows:
                stat = build_trip_stat(
                    trip_id=trip.id,
                    vehicle_id=trip.vehicle_id,
                    request=trip.request or {},
                    result=trip.result or {},
                    created_at=trip.created_at,
                )
                if stat is None:
                    skipped += 1
                    continue
                if apply:
                    db.add(stat)
                written += 1

            if apply:
                await db.commit()
            else:
                # Keep the identity map from growing across the whole table.
                db.expunge_all()
            logger.info("… %d/%d", written + skipped, total)

    return written, skipped


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--apply", action="store_true", help="actually write (default is a dry run)"
    )
    ap.add_argument("--batch", type=int, default=BATCH)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    written, skipped = asyncio.run(backfill(args.apply, args.batch))
    verb = "Wrote" if args.apply else "Would write"
    logger.info("%s %d trip stat row(s).", verb, written)
    if skipped:
        logger.info(
            "Skipped %d trip(s) whose request/result could not be reduced to a "
            "corridor — see the warnings above.",
            skipped,
        )
    if not args.apply and written:
        logger.info("Dry run — pass --apply to write.")


if __name__ == "__main__":
    main()
