"""Delete usernames that are old and no longer hold anything.

A `Profile` is a name and a hashed secret — the smallest row in the schema, and
the one with the weakest claim to living forever. Two conditions have to hold
before it goes, and the second is what makes this safe:

* it was claimed more than the retention window ago, and
* **no trip carries its stamp any more.**

The second is the real rule. Trips purge at two years, so a name whose owner
stopped using the app drains to zero trips on its own and is collected here a
while later; a name still holding trips is never touched no matter how old the
claim is, because deleting it would make somebody's live public page vanish.
That is also why this cannot simply mirror the trip window: a profile claimed
three years ago and used yesterday is active, and its `created_at` says nothing
about that.

Releasing a username is the deliberate version of this and is instant
(`api.users.release_username`). This only sweeps up the ones nobody released
and nobody uses.

    cd src && uv run python -m scripts.purge_old_profiles           # dry run
    cd src && uv run python -m scripts.purge_old_profiles --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select

from app.core.database import AsyncSessionLocal
from app.models import Profile, Trip

logger = logging.getLogger("purge_old_profiles")

# Matches what the privacy page promises. Change both together. Longer than the
# trip window on purpose: a profile is only collectable once its trips are gone,
# so this clock effectively starts after theirs has run out.
DEFAULT_RETENTION_DAYS = 730

BATCH = 200


async def purge(days: int, apply: bool) -> int:
    """Delete dormant profiles. Returns how many were (or would be) removed."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    done = 0

    async with AsyncSessionLocal() as db:
        while True:
            # A plain correlated NOT EXISTS: no boolean casts, nothing that
            # compiles differently on MSSQL and SQLite.
            still_used = (
                select(Trip.id).where(Trip.owner_hash == Profile.owner_hash).exists()
            )
            ids = (
                (
                    await db.execute(
                        select(Profile.id)
                        .where(Profile.created_at < cutoff)
                        .where(~still_used)
                        .limit(BATCH)
                    )
                )
                .scalars()
                .all()
            )
            if not ids:
                break

            done += len(ids)
            if not apply:
                # Nothing was deleted, so the same rows would match forever.
                break

            await db.execute(delete(Profile).where(Profile.id.in_(ids)))
            await db.commit()

    return done


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
    logger.info(
        "%s %d dormant username(s) claimed over %d days ago.", verb, n, args.days
    )


if __name__ == "__main__":
    main()
