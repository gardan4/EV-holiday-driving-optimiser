"""Delete feedback once it is older than the retention window.

`Feedback.contact` holds an email address somebody typed in to get a reply. It
is the only field in this app where a person hands over something that names
them directly, and it had no expiry at all: a note left the week after launch
would still have been sitting there years later, along with the address.

Two years, matching planned trips, so the privacy page states one number for
both rather than a table.

Nothing derives from this table, so unlike `purge_old_trips` there is no cascade
to get right.

    cd src && uv run python -m scripts.purge_old_feedback           # dry run
    cd src && uv run python -m scripts.purge_old_feedback --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, func, select

from app.core.database import AsyncSessionLocal
from app.models import Feedback

logger = logging.getLogger("purge_old_feedback")

# Matches what the privacy page promises. Change both together.
DEFAULT_RETENTION_DAYS = 730


async def purge(days: int, apply: bool) -> int:
    """Returns the number of feedback rows affected."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    async with AsyncSessionLocal() as db:
        n = (
            await db.execute(
                select(func.count()).select_from(Feedback).where(Feedback.created_at < cutoff)
            )
        ).scalar_one()
        if apply and n:
            await db.execute(delete(Feedback).where(Feedback.created_at < cutoff))
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
    logger.info("%s %d feedback item(s) older than %d days.", verb, n, args.days)
    if not args.apply and n:
        logger.info("Dry run - pass --apply to delete.")


if __name__ == "__main__":
    main()
