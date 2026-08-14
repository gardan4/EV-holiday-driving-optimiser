"""What the usernames are doing — the reporting side of `api/users.py`.

The trips drawer shipped with no way to tell whether it worked. Trips were
counted, drives were counted, corridors were counted; the one feature with a
stated purpose — "stop people replanning a journey they already planned" — had
no number attached to it at all, so the only available verdict was a hunch.

This is the one reader of `profiles` and `trips.owner_hash` for reporting, in
the same sense `services/corridors.py` is the one reader of `trip_stats`: the
terminal report, the token-gated stats endpoint and the dashboard all call it,
so they cannot end up disagreeing about what "a name" or "an active name"
counts.

**No username ever leaves this module, and there is no per-name row.** Every
number here is an aggregate over names, never a list of them. That is not
squeamishness: a "top usernames" table would be a list of people, on a console
whose whole claim is that it collects nothing and shows nobody — and the
usernames are the one handle in this app that could plausibly be somebody's
real name, which is precisely why `events.normalize_path` collapses them out of
the events table in the first place. Counting them and then naming them on
another screen would give the collapse away.

Two clocks, deliberately, and the report labels which is which:

* **Window** — claims, releases, list views, drawer opens, and the share of the
  window's trips that carried a name. These answer "is it being used lately".
* **Lifetime** — names alive now, names holding nothing, names that came back,
  and the trips-per-name spread. A name is not a windowed thing; scoped to a
  7-day window every one of these reads zero on a quiet week and looks like a
  dead feature rather than a small one.

Claims are counted twice on purpose, from two sources that answer different
questions. `profiles.created_at` is the truth about names alive *now* and
reaches back to the day the feature shipped. The `profile_claimed` event counts
claims that *happened*, including the ones later released — which is the only
way to see churn, because releasing deletes the row and takes its history with
it. Neither can be derived from the other.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppEvent, Profile, Trip
from app.services.analytics import Filters, Slice

# The three events this reads. Written by `api/users.py` (server-side, so they
# cannot be forged or blocked) and by the drawer itself.
CLAIMED_EVENT = "profile_claimed"
RELEASED_EVENT = "profile_released"
DRAWER_EVENT = "trips_opened"

# The normalised path of a public list page — `events.KNOWN_PATHS`, not a
# literal built here, because the two must stay the same string.
LIST_PATH = "/u/:username"

# "Came back" needs a gap, not a second row: somebody who plans two legs of the
# same holiday in one sitting has not returned, and counting that would flatter
# every name into looking retained. A day is the smallest gap that means the
# person left and chose to come back.
RETURN_GAP = timedelta(days=1)

# The trips-per-name spread, as bucket ceilings. Kept ASCII (see the working
# norms) and kept small: with a handful of names a ten-bucket histogram is a row
# of ones that says nothing.
_SPREAD: tuple[tuple[int, str], ...] = (
    (1, "1 trip"),
    (2, "2 trips"),
    (5, "3-5 trips"),
    (10, "6-10 trips"),
)
_SPREAD_TAIL = "11+ trips"


@dataclass(frozen=True)
class NameStats:
    """Everything the reporting surfaces know about usernames.

    Counts only. Any field that could carry a name instead carries a number.
    """

    # --- this window ---
    claimed: int          # `profile_claimed` events — includes names since released
    released: int         # `profile_released` events
    list_views: int       # page views of a public /u/<name> page
    drawer_opens: int     # somebody opened their trips panel on purpose
    trips: int            # trips planned in the window
    named_trips: int      # …of which published under a name
    active: int           # distinct names that planned something in the window

    # --- lifetime ---
    live: int             # names that exist right now
    with_trips: int       # …holding at least one trip
    dormant: int          # …holding nothing (claimed a name, never planned again)
    returning: int        # …whose trips span more than RETURN_GAP
    spread: list[Slice]   # trips per name, bucketed

    @property
    def named_share(self) -> float | None:
        """Share of this window's trips that were published under a name.

        The adoption number. None rather than zero when nothing was planned:
        "0% of no trips" reads as a feature nobody wants rather than as a quiet
        week, and the two need to look different on a dashboard.
        """
        return (self.named_trips / self.trips) if self.trips else None

    @property
    def return_rate(self) -> float | None:
        """Share of names holding trips that came back on another day.

        Quoted against `with_trips`, never against `live`: a name claimed
        yesterday has not had the chance to return, and dividing by it would
        report a feature as failing when it is merely young.
        """
        return (self.returning / self.with_trips) if self.with_trips else None


def as_dict(s: NameStats) -> dict:
    """The wire shape, written once.

    Both readers go through this: `api/admin.py` returns it to the console as
    JSON, and `services/usage.py` feeds it straight into `NameStatsOut`. Two
    hand-written serialisations of the same dataclass is how the dashboard and
    the terminal report end up quoting different denominators for the same word
    — the mistake `services/corridors.py` exists to prevent for corridors.
    """
    return {
        "claimed": s.claimed,
        "released": s.released,
        "list_views": s.list_views,
        "drawer_opens": s.drawer_opens,
        "trips": s.trips,
        "named_trips": s.named_trips,
        "active": s.active,
        "named_share": s.named_share,
        "live": s.live,
        "with_trips": s.with_trips,
        "dormant": s.dormant,
        "returning": s.returning,
        "return_rate": s.return_rate,
        # `{label, value}` like every other row the admin API emits, so the
        # console's `Bars` component takes it without a per-panel adapter.
        # `usage.usage_stats` renames it to `LabelCount`'s `count` on the way
        # into the schema, which is the shape the terminal report reads.
        "spread": [{"label": b.label, "value": b.value} for b in s.spread],
    }


async def name_stats(db: AsyncSession, f: Filters) -> NameStats:
    """The username numbers for one window.

    Takes `Filters` for its window only — the facet chips (device, campaign,
    referrer) are deliberately not applied. Half of this comes from `profiles`
    and `trips`, which carry no device and no campaign, so narrowing the other
    half would produce a panel where some numbers moved and others did not.
    That is the same rule `/api/journeys` follows, and the dashboard says so
    rather than showing quietly unfiltered data.
    """
    since = f.since

    # Window, from events. One grouped query rather than four round trips: these
    # are three names out of one indexed window scan.
    ev = (
        await db.execute(
            select(
                func.sum(case((AppEvent.name == CLAIMED_EVENT, 1), else_=0)),
                func.sum(case((AppEvent.name == RELEASED_EVENT, 1), else_=0)),
                func.sum(case((AppEvent.name == DRAWER_EVENT, 1), else_=0)),
                func.sum(
                    case(
                        (
                            (AppEvent.name == "page_view")
                            & (AppEvent.path == LIST_PATH),
                            1,
                        ),
                        else_=0,
                    )
                ),
            ).where(AppEvent.at >= since)
        )
    ).one()
    claimed, released, drawer_opens, list_views = (int(v or 0) for v in ev)

    # Window, from the trips table — so adoption is measured against what
    # actually happened rather than against what a browser managed to report.
    # `sum(case(...))` and not `cast(col.isnot(None), Float)`: MSSQL has no
    # boolean value type and rejects a cast over a comparison.
    trips_row = (
        await db.execute(
            select(
                func.count(),
                func.sum(case((Trip.owner_hash.isnot(None), 1), else_=0)),
                func.count(distinct(Trip.owner_hash)),
            ).where(Trip.created_at >= since)
        )
    ).one()
    trips, named_trips, active = (int(v or 0) for v in trips_row)

    live = int(
        (await db.execute(select(func.count()).select_from(Profile))).scalar_one() or 0
    )

    # Lifetime, per name, aggregated in SQL and bucketed in Python. One row per
    # name that has ever published a trip — bounded by the number of usernames,
    # which is the smallest table in the schema. The min/max pair is what makes
    # "came back" answerable without a date-truncation branch: subtracting two
    # datetimes is dialect-specific, subtracting two Python ones is not.
    per_name = (
        await db.execute(
            select(
                Trip.owner_hash,
                func.count(),
                func.min(Trip.created_at),
                func.max(Trip.created_at),
            )
            .where(Trip.owner_hash.isnot(None))
            .group_by(Trip.owner_hash)
        )
    ).all()

    with_trips = len(per_name)
    returning = sum(1 for _, _, first, last in per_name if last - first >= RETURN_GAP)

    counts = [0] * (len(_SPREAD) + 1)
    for _, n, _first, _last in per_name:
        for i, (ceiling, _label) in enumerate(_SPREAD):
            if n <= ceiling:
                counts[i] += 1
                break
        else:
            counts[-1] += 1
    spread = [
        Slice(label=label, value=value)
        for (_ceiling, label), value in zip(_SPREAD, counts)
    ]
    spread.append(Slice(label=_SPREAD_TAIL, value=counts[-1]))

    return NameStats(
        claimed=claimed,
        released=released,
        list_views=list_views,
        drawer_opens=drawer_opens,
        trips=trips,
        named_trips=named_trips,
        active=active,
        live=live,
        with_trips=with_trips,
        # Never negative: a stamped trip whose profile is gone would otherwise
        # push this below zero, and a dashboard printing "-1 names" is a bug
        # report about the dashboard rather than about the invariant it caught.
        dormant=max(0, live - with_trips),
        returning=returning,
        spread=spread,
    )
