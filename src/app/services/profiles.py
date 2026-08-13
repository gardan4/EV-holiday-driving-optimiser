"""Reading the username feature back out.

The one reader of `profiles` and of `trips.owner_hash`, shared by
`scripts.usage_report` and the admin dashboard — the same rule
`services/corridors.py` lives by, for the same reason: two renderings of "how
many people picked a name" that disagree are worse than one that is missing.

Nothing here collects anything. Every number is a count over rows that already
exist, which is why `frontend/app/privacy/page.tsx` needs no edit for any of it
— the two *events* this composes with are a different matter and are documented
in `services/counting.py`.

Three questions, in the order they are worth asking:

* **Stock** — how many names exist, and how many of them publish anything. A
  name holding no trips is the feature's real drop-off: somebody opted in and
  then never planned again, and no page-view count shows that.
* **Flow** — what share of the trips planned in the window carry a name. This
  is the number that says whether the feature is used at all, and it is a share
  rather than a count because trip volume moves on its own.
* **Reach** — how often a public list is actually opened. Handing somebody a
  name and having it work is the whole feature; if nobody ever opens one, the
  lists are private history with extra steps.

Two things are deliberately NOT here. There is no per-username anything: this
module counts names, never who holds them, and nothing it returns can be joined
back to a person. And there is no claims-per-day series — `Profile.created_at`
would give one, but at this volume it is a row of ones, and the window count
answers the same question without a chart that flatters it.

MSSQL rules, as everywhere: no `.is_(True)`, and no `CAST` over a comparison —
the conditional count below is `func.sum(case(...))` for that reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppEvent, Profile, Trip
from app.services.corridors import trip_count_buckets

# The route pattern a public list lands on. Mirrors `events.KNOWN_PATHS` — a
# constant rather than an import, because `api.events` imports `services.usage`
# which imports this, and the allowlist is the API layer's business anyway.
# `tests/test_profile_reporting.py` pins the two together.
USER_LIST_PATH = "/u/:username"


@dataclass(frozen=True)
class Summary:
    """What the username feature is doing, in one struct.

    The window applies to `claimed`, `published`, `trips`, `list_views` and
    `per_name`; `live`, `with_trips` and `empty` are stock and are always
    all-time. Mixing the two in one panel is fine as long as the labels say so,
    and confusing them is how "names claimed" ends up looking like it fell off
    a cliff when the window narrowed.
    """

    live: int = 0
    claimed: int = 0
    with_trips: int = 0
    empty: int = 0
    published: int = 0
    trips: int = 0
    list_views: int = 0
    # (label, names) — the same buckets `corridors.repeat_planners` reports for
    # anonymous planners, from the same function, so the two lists on the
    # dashboard can be read against each other.
    per_name: list[tuple[str, int]] = field(default_factory=list)

    @property
    def publish_rate(self) -> float | None:
        """Share of the window's trips planned under a name.

        None rather than 0.0 when nothing was planned: an empty window has no
        rate, and drawing it as 0% reads as "everybody stopped using it".
        """
        return (self.published / self.trips) if self.trips else None

    @property
    def active_rate(self) -> float | None:
        """Share of names that have ever published a trip."""
        return (self.with_trips / self.live) if self.live else None


def flow_query(cutoff: datetime | None):
    """Trips in the window, and how many of them carry a name — one statement.

    One query, two numbers: a second SELECT could see a different set of rows
    and print a share above 100%.

    Separated from `summary` so a test can compile the REAL statement against
    the MSSQL dialect, which is the guard this repo already keeps on
    `upstream_health.daily_query`. The conditional count is a CASE rather than a
    cast of the comparison: `CAST(owner_hash IS NOT NULL AS INT)` is accepted by
    SQLite — where a comparison is a 0/1 value — and is a T-SQL syntax error,
    because SQL Server has no boolean value type. The tests run on SQLite, so
    nothing else would catch it.
    """
    q = select(
        func.count(),
        func.sum(case((Trip.owner_hash.isnot(None), 1), else_=0)),
    ).select_from(Trip)
    return q.where(Trip.created_at >= cutoff) if cutoff else q


async def summary(db: AsyncSession, cutoff: datetime | None) -> Summary:
    """Every number above, in six queries.

    Two of them are deliberately unwindowed, which is the one thing here that
    could get expensive: `profiles` is the smallest table in the schema, and the
    distinct count runs on `trips.owner_hash`, which is indexed and NULL for
    almost every row. Everything else takes the window.
    """
    live = int(
        (await db.execute(select(func.count()).select_from(Profile))).scalar_one() or 0
    )

    claimed_q = select(func.count()).select_from(Profile)
    if cutoff:
        claimed_q = claimed_q.where(Profile.created_at >= cutoff)
    claimed = int((await db.execute(claimed_q)).scalar_one() or 0)

    # Names holding at least one trip. Counted from the stamp rather than by
    # joining, because the stamp is the thing that makes a public page
    # non-empty: a released name nulls every stamp it had (`api.users`), and the
    # purge only ever collects a profile no stamp points at, so this set is a
    # subset of `profiles` by construction.
    with_trips = int(
        (
            await db.execute(
                select(func.count(func.distinct(Trip.owner_hash))).where(
                    Trip.owner_hash.isnot(None)
                )
            )
        ).scalar_one()
        or 0
    )

    trips, published = (await db.execute(flow_query(cutoff))).one()

    # Reach. From `app_events`, so unlike everything above it starts the day
    # counting shipped and expires at 90 days — and it is zero for anyone who
    # opted out. A floor, not a measurement.
    views_q = select(func.count()).select_from(AppEvent).where(
        AppEvent.name == "page_view", AppEvent.path == USER_LIST_PATH
    )
    if cutoff:
        views_q = views_q.where(AppEvent.at >= cutoff)
    list_views = int((await db.execute(views_q)).scalar_one() or 0)

    per_name_q = (
        select(func.count().label("n"))
        .select_from(Trip)
        .where(Trip.owner_hash.isnot(None))
        .group_by(Trip.owner_hash)
    )
    if cutoff:
        per_name_q = per_name_q.where(Trip.created_at >= cutoff)
    per_name = trip_count_buckets(
        float(r.n) for r in (await db.execute(per_name_q)).all()
    )

    return Summary(
        live=live,
        claimed=claimed,
        with_trips=with_trips,
        empty=max(0, live - with_trips),
        published=int(published or 0),
        trips=int(trips or 0),
        list_views=list_views,
        per_name=per_name,
    )
