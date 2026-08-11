"""Forget who planned a trip, while keeping the trip in the corridor numbers.

`TripStat` rows are kept indefinitely — they are coarse by construction and
they are the corridor history, which is the one asset here that gets more
valuable the longer it runs. The persistent pseudonym attached to them is a
different matter: a random id joined to a travel pattern is personal data, and
personal data kept forever because nobody chose a number is how a defensible
design stops being one.

So this nulls `client_id` and leaves the row. Afterwards the trip still counts
toward every corridor, distance and charging-gap question, and toward no
question about a person.

**Why 15 months and not 90 days.** This is a holiday-driving app, so its cycle
is annual: the question worth answering is "did last August's users come back
this August", and a 90-day window — the one the location trails and usage
events use — deletes the answer eight months before it can be asked. Anything
under ~13 months cannot see a year at all. 15 leaves a margin for someone who
travels in early July one year and late August the next.

    cd src && uv run python -m scripts.purge_old_trip_stat_ids           # dry run
    cd src && uv run python -m scripts.purge_old_trip_stat_ids --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select, update

from app.core.database import AsyncSessionLocal
from app.models import TripStat

logger = logging.getLogger("purge_old_trip_stat_ids")

# Long enough to see one year-over-year holiday season, and no longer.
RETENTION_DAYS = 456  # ~15 months


async def purge(days: int, apply: bool) -> int:
    """Returns the number of rows whose `client_id` was (or would be) cleared."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        stale = (TripStat.created_at < cutoff, TripStat.client_id.isnot(None))
        n = (
            await db.execute(select(func.count()).select_from(TripStat).where(*stale))
        ).scalar_one()
        if apply and n:
            # An UPDATE, not a DELETE. The corridor is history worth keeping;
            # only the person attached to it expires.
            await db.execute(update(TripStat).where(*stale).values(client_id=None))
            await db.commit()
        return int(n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=RETENTION_DAYS)
    ap.add_argument(
        "--apply", action="store_true", help="actually clear (default is a dry run)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    n = asyncio.run(purge(args.days, args.apply))
    verb = "Cleared" if args.apply else "Would clear"
    logger.info(
        "%s the planner id on %d trip stat row(s) older than %d days "
        "(the rows themselves are kept).",
        verb, n, args.days,
    )
    if not args.apply and n:
        logger.info("Dry run — pass --apply to clear.")


if __name__ == "__main__":
    main()
