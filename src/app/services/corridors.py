"""Reading `trip_stats` back out.

Lives here rather than in the script for the same reason `services/usage.py`
does: two callers need it. `scripts.corridor_report` prints it to a terminal and
`services.usage` folds a subset into the dashboard payload. One implementation
means the corridor on the dashboard and the corridor in the report cannot
disagree about how many stops it takes.

Every query is bounded by a limit, and by a window when one is given — this runs
against the live Azure SQL instance and an unbounded scan of the trip table is
not something a dashboard refresh should be able to trigger.

Two MSSQL rules are load-bearing throughout:

* `func.avg` over an Integer column does INTEGER division on MSSQL and returns
  a float on SQLite, so the test engine and production would disagree. Every
  average here goes through `_avg`, which casts first.
* Boolean filters must be written `== True` / `== False`; `.is_(True)` compiles
  to `IS 1`, which is a T-SQL syntax error.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TripStat, Vehicle
from app.services.analytics.queries import histogram
from app.services.geo import geohash_bbox
from app.services.simulator import _default_country_caps

# "How many trips does one planner plan", in buckets. Shared with
# `services/profiles.py`, which asks the same question of named planners — the
# two lists sit side by side on the dashboard, and a reader comparing "6+" in
# one against "5+" in the other would be comparing nothing. One definition, so
# they cannot drift.
TRIP_COUNT_EDGES = [2.0, 3.0, 6.0]
TRIP_COUNT_LABELS = ["1 trip", "2 trips", "3–5 trips", "6+ trips"]


@dataclass(frozen=True)
class Corridor:
    """One origin→destination pair, already coarsened."""

    label: str
    trips: int
    distance_km: int
    avg_stops: float | None

    @property
    def stops_per_100km(self) -> float | None:
        if self.avg_stops is None or not self.distance_km:
            return None
        return (self.avg_stops / self.distance_km) * 100.0


def _centre(gh: str) -> tuple[float, float]:
    # Interleaved — lat/lon low, then lat/lon high. Unpacking it as two lats
    # then two lons yields plausible-looking coordinates that are wrong.
    lat_lo, lon_lo, lat_hi, lon_hi = geohash_bbox(gh)
    return (lat_lo + lat_hi) / 2, (lon_lo + lon_hi) / 2


def place(gh: str, cc: str | None) -> str:
    """A geohash rendered so a human can find it on a map.

    No reverse geocoding: that would be an ORS call per row, against the same
    free quota the planner depends on, to make a label prettier. A country code
    and a centre coordinate is enough to recognise a corridor.
    """
    lat, lon = _centre(gh)
    return f"{cc or '??'} {lat:.1f},{lon:.1f}"


def _avg(col):
    return func.avg(cast(col, Float))


def _corridor_select():
    return select(
        TripStat.origin_gh,
        TripStat.dest_gh,
        # MSSQL requires every non-aggregated column in the GROUP BY. The
        # country is functionally dependent on the geohash, so grouping by it
        # too changes no row counts.
        TripStat.origin_cc,
        TripStat.dest_cc,
        func.count().label("n"),
        _avg(TripStat.distance_km).label("km"),
        _avg(TripStat.optimum_n_stops).label("stops"),
    ).group_by(
        TripStat.origin_gh, TripStat.dest_gh, TripStat.origin_cc, TripStat.dest_cc
    )


def _to_corridor(r) -> Corridor:
    return Corridor(
        label=place(r.origin_gh, r.origin_cc) + " → " + place(r.dest_gh, r.dest_cc),
        trips=int(r.n),
        distance_km=int(r.km or 0),
        avg_stops=float(r.stops) if r.stops is not None else None,
    )


def _window(q, cutoff: datetime | None):
    return q.where(TripStat.created_at >= cutoff) if cutoff else q


def trip_count_buckets(counts) -> list[tuple[str, int]]:
    """Bucket a per-planner trip count, empty buckets dropped.

    Empties go because a bar chart reading "2 trips: 0" next to "1 trip: 3"
    spends half its height saying nothing.
    """
    return [
        (s.label, s.value)
        for s in histogram(list(counts), TRIP_COUNT_EDGES, TRIP_COUNT_LABELS)
        if s.value
    ]


async def headline(db: AsyncSession, cutoff: datetime | None) -> tuple[int, int, float]:
    """(trips, trips with a feasible plan, mean km)."""
    row = (
        await db.execute(
            _window(
                select(
                    func.count(),
                    func.sum(cast(TripStat.feasible, Float)),
                    _avg(TripStat.distance_km),
                ),
                cutoff,
            )
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0), float(row[2] or 0.0)


async def top_corridors(
    db: AsyncSession, cutoff: datetime | None, limit: int
) -> list[Corridor]:
    rows = (
        await db.execute(
            _window(_corridor_select(), cutoff).order_by(func.count().desc()).limit(limit)
        )
    ).all()
    return [_to_corridor(r) for r in rows]


async def charging_gaps(
    db: AsyncSession, cutoff: datetime | None, limit: int
) -> tuple[list[Corridor], list[Corridor]]:
    """(hardest to charge, no feasible plan at all).

    Ranked by stops per 100 km rather than raw stops — otherwise this is a list
    of the longest routes, which is a fact about geography rather than about
    charging. Sorted in Python: it is a ratio of two aggregates, and pushing
    that into ORDER BY behaves differently on MSSQL and SQLite.
    """
    feasible = (
        await db.execute(
            _window(_corridor_select(), cutoff).where(TripStat.feasible == True)  # noqa: E712
        )
    ).all()
    worst = sorted(
        (c for c in (_to_corridor(r) for r in feasible) if c.stops_per_100km is not None),
        key=lambda c: c.stops_per_100km,
        reverse=True,
    )[:limit]

    infeasible = (
        await db.execute(
            _window(_corridor_select(), cutoff)
            .where(TripStat.feasible == False)  # noqa: E712
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return worst, [_to_corridor(r) for r in infeasible]


async def coverage_gap(
    db: AsyncSession, cutoff: datetime | None
) -> tuple[list[tuple[str, int]], int]:
    """(countries crossed that have no real cap, trips we can't place at all).

    `countries` is a JSON list, which neither MSSQL nor SQLite can unnest
    portably, so this counts in Python over the window.

    The second number is usually the bigger finding and is easy to misread:
    `geo.country_at` only knows western-European boxes, so a US or Japanese
    route cannot even be named and contributes nothing to the first list. An
    empty list next to a large count means "we model none of this", not "no
    gap".
    """
    rows = (
        await db.execute(
            _window(
                select(TripStat.countries, TripStat.origin_cc, TripStat.dest_cc), cutoff
            )
        )
    ).all()
    modelled = set(_default_country_caps())

    unmodelled: dict[str, int] = {}
    unplaceable = 0
    for countries, o_cc, d_cc in rows:
        for cc in countries or []:
            if cc not in modelled:
                unmodelled[cc] = unmodelled.get(cc, 0) + 1
        if o_cc is None or d_cc is None:
            unplaceable += 1
    return sorted(unmodelled.items(), key=lambda kv: kv[1], reverse=True), unplaceable


async def by_vehicle(
    db: AsyncSession, cutoff: datetime | None, limit: int = 100
) -> list[tuple[str, int, float | None]]:
    rows = (
        await db.execute(
            _window(
                select(
                    Vehicle.make,
                    Vehicle.model,
                    Vehicle.variant,
                    func.count().label("n"),
                    _avg(TripStat.optimum_speed_kph).label("speed"),
                ).join(Vehicle, Vehicle.id == TripStat.vehicle_id),
                cutoff,
            )
            .group_by(Vehicle.make, Vehicle.model, Vehicle.variant)
            .order_by(func.count().desc())
            .limit(limit)
        )
    ).all()
    return [
        (
            " ".join(x for x in (r.make, r.model, r.variant) if x),
            int(r.n),
            float(r.speed) if r.speed is not None else None,
        )
        for r in rows
    ]


async def by_month(db: AsyncSession, cutoff: datetime | None) -> list[tuple[int, int]]:
    rows = (
        await db.execute(
            _window(
                select(TripStat.departure_month, func.count()).where(
                    TripStat.departure_month.isnot(None)
                ),
                cutoff,
            )
            .group_by(TripStat.departure_month)
            .order_by(TripStat.departure_month)
        )
    ).all()
    return [(int(m), int(n)) for m, n in rows]


async def repeat_planners(
    db: AsyncSession, cutoff: datetime | None
) -> list[tuple[str, int]]:
    """How many trips one person plans — the question the persistent id is for.

    Counts only rows carrying a planner id: backfilled history predates it, and
    anyone who opted out or cleared their storage never had one. So the "1 trip"
    bucket is inflated by every second visit that went unrecognised, and repeat
    use is a floor rather than a measurement.
    """
    rows = (
        await db.execute(
            _window(
                select(func.count().label("trips"))
                .where(TripStat.client_id.isnot(None))
                .group_by(TripStat.client_id),
                cutoff,
            )
        )
    ).all()
    return trip_count_buckets(float(r.trips) for r in rows)
