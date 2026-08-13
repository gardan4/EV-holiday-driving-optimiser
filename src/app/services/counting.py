"""Counting the things a delete would otherwise erase.

`api/events.py` is the public write path: a browser posts the name of what it
did, and everything about that request is treated as hostile. A few events
cannot come from there at all, and this is where those are written instead.

Two reasons an event has to be server-side, and the second is why this module
exists rather than only the wrapper in `api/trips.py`:

* **The browser does not know the answer.** `plan_failed` carries the reason
  planning actually failed; the client only ever sees a sentence written for a
  human. And an event fired from a page can be blocked, so a client-side count
  is measured against a different population than the table it is compared with.
* **The row it describes is about to stop existing.** Releasing a username
  deletes the profile; deleting a trip deletes the trip, its coarsened stat and
  its drives. Every other number in the reporting is a count of rows that are
  still there — so without an event these two are not merely uncounted, they are
  retroactively invisible, and the corridor totals shrink with nothing saying
  why.

**These rows carry the ordinary daily pseudonym, and that is safe precisely
because of what they record.** An event marking a *claim* would be a different
matter: `profiles.created_at` would sit within a second of it, so two timestamps
would join a name somebody chose for themselves to a day of their browsing —
the correlation `events._USERNAME_PATH_RE` already exists to prevent. Nothing
similar is possible here, because the profile and the trip are gone by the time
the row lands and there is nothing left to line the timestamp up against. Claims
are counted from `profiles` instead (`services/profiles.py`), where the answer
is exact and no new correlation is created.

Nothing else about the caller is stored: no path (these are API calls, not
pages, and `KNOWN_PATHS` is an allowlist of pages), no referrer, and none of the
`client_context` buckets — the same shape `plan_failed` has always had.

Never raises, for the same reason `quota.record` does not: this is telemetry on
the path of the thing it measures, and a counter must never be able to turn a
successful delete into a 500.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from starlette.requests import Request
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.visitor import request_client_id, request_visitor
from app.models import AppEvent

logger = logging.getLogger(__name__)

# The two events this module exists for. Both are refused from the public
# endpoint (`events.SERVER_ONLY_EVENTS`) — anyone could otherwise post
# deletions that never happened, and the count would stop being a cross-check
# on tables that shrank.
TRIP_DELETED = "trip_deleted"
USERNAME_RELEASED = "username_released"


async def record(
    db: AsyncSession,
    request: Request,
    name: str,
    *,
    reason: str | None = None,
    rollback: bool = False,
) -> None:
    """Write one server-witnessed event. Best effort, never fatal.

    `rollback` is for the failure path: when the request is already failing,
    whatever partial work the session holds is being discarded anyway, and a
    session left in a failed state cannot write this row at all.

    The commit is deliberate — the caller has usually just committed the thing
    being counted (or is about to answer 204), so there is no later commit to
    ride along on.
    """
    try:
        if rollback:
            await db.rollback()
        db.add(
            AppEvent(
                name=name,
                path=None,
                visitor=request_visitor(request),
                client_id=request_client_id(request),
                reason=reason,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — a counter must not break what it counts
        logger.warning("could not record %s event", name, exc_info=True)


@dataclass(frozen=True)
class Erasures:
    """What was taken away during a window, in the window's own terms."""

    trips_deleted: int = 0
    usernames_released: int = 0


async def erasures(db: AsyncSession, cutoff: datetime | None) -> Erasures:
    """The read side of what `record` writes, in one query.

    Here rather than in `services/usage.py` or `services/profiles.py` because
    both need it and neither owns it: a deletion is not a usage number and a
    released username is not a profile, since by then there is no profile. Same
    rule as `services/corridors.py` — one query, so the terminal report and the
    dashboard cannot disagree about how much history went missing.

    Reads `app_events`, so these start the day counting shipped and expire at
    90 days, and they miss anyone who opted out. A floor on how much the
    trip-derived numbers have quietly lost, not a measurement.
    """
    q = (
        select(
            func.sum(_when(TRIP_DELETED)),
            func.sum(_when(USERNAME_RELEASED)),
        )
        .select_from(AppEvent)
        .where(AppEvent.name.in_((TRIP_DELETED, USERNAME_RELEASED)))
    )
    if cutoff:
        q = q.where(AppEvent.at >= cutoff)
    trips, names = (await db.execute(q)).one()
    return Erasures(trips_deleted=int(trips or 0), usernames_released=int(names or 0))


def _when(name: str):
    # `case`, not `cast(col == name, Integer)`: MSSQL has no boolean value type,
    # so casting a comparison is a syntax error there and silently fine on the
    # SQLite test engine.
    return case((AppEvent.name == name, 1), else_=0)
