"""Delete usage events once they are older than the retention window.

`AppEvent` rows are already close to anonymous — a daily-rotating pseudonym, a
route pattern, a referring host — but "close to anonymous and kept forever" is
how an analytics table quietly turns into a behavioural record. The window is
what keeps the privacy page's claim about this true, so it is enforced by code
that actually runs (`main._purge_loop`) rather than by intent.

    cd src && uv run python -m scripts.purge_old_events           # dry run
    cd src && uv run python -m scripts.purge_old_events --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.database import AsyncSessionLocal
from app.models import AppEvent

logger = logging.getLogger("purge_old_events")

# Same 90 days as the location trails, so the privacy page states one number
# rather than a table of them. Change both together.
DEFAULT_RETENTION_DAYS = 90


async def purge(days: int, apply: bool) -> int:
    """Returns the number of event rows affected."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        n = (
            await db.execute(
                select(func.count()).select_from(AppEvent).where(AppEvent.at < cutoff)
            )
        ).scalar_one()
        if apply and n:
            await db.execute(delete(AppEvent).where(AppEvent.at < cutoff))
            await db.commit()
        return int(n)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS)
    ap.add_argument(
        "--apply", action="store_true", help="actually delete (default is a dry run)"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    n = asyncio.run(purge(args.days, args.apply))
    verb = "Deleted" if args.apply else "Would delete"
    logger.info("%s %d usage event(s) older than %d days.", verb, n, args.days)
    if not args.apply and n:
        logger.info("Dry run — pass --apply to delete.")


if __name__ == "__main__":
    main()
