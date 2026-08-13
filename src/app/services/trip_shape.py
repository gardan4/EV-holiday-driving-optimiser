"""The shape of the trips people plan, not just their averages.

`services/corridors.py` answers "where do people drive". This answers "what
kind of journey is it" — how long, how much of it is spent plugged in, and
which countries get crossed on the way. Every column it reads has been on
`trip_stats` since that table shipped; only the mean distance was ever used,
and a mean is the one statistic that hides the distribution it came from.

The headline number here is **charge share**: charging minutes over total
journey minutes. That is the number the whole product exists to minimise, and
until now it was stored on every row and computed by nothing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Float, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TripStat
from app.services.analytics.queries import Slice, histogram

# Distance bands, in km. Chosen around how the trips differ rather than on
# round numbers: under ~250 km is one charge at most, 250-500 is a day out,
# 500-1000 is the holiday drive this app was built for, and beyond that is a
# two-day journey where the model's "no rest stops" assumption starts to bite.
DISTANCE_EDGES = [250.0, 500.0, 750.0, 1000.0, 1500.0]
DISTANCE_LABELS = [
    "under 250 km",
    "250-500 km",
    "500-750 km",
    "750-1000 km",
    "1000-1500 km",
    "over 1500 km",
]

# Charge share, as a percentage of total journey time.
CHARGE_SHARE_EDGES = [5.0, 10.0, 15.0, 20.0, 30.0]
CHARGE_SHARE_LABELS = [
    "under 5%",
    "5-10%",
    "10-15%",
    "15-20%",
    "20-30%",
    "over 30%",
]

MONTHS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]


@dataclass(frozen=True)
class ShapeSummary:
    trips: int
    feasible: int
    mean_km: float
    # Time actually spent charging, as a share of the whole journey, averaged
    # over trips. Weighted per trip rather than pooled minutes — otherwise one
    # 1500 km drive counts for as much as ten weekend trips.
    mean_charge_share: float | None
    median_charge_share: float | None
    distances: list[Slice]
    charge_shares: list[Slice]
    months: list[Slice]
    transit_countries: list[Slice]

    @property
    def feasible_rate(self) -> float | None:
        return (self.feasible / self.trips) if self.trips else None


def _window(q, cutoff: datetime | None):
    return q if cutoff is None else q.where(TripStat.created_at >= cutoff)


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


async def summary(db: AsyncSession, cutoff: datetime | None) -> ShapeSummary:
    rows = (
        await db.execute(
            _window(
                select(
                    TripStat.distance_km,
                    TripStat.departure_month,
                    TripStat.feasible,
                    TripStat.optimum_total_min,
                    TripStat.optimum_charge_min,
                    TripStat.countries,
                ),
                cutoff,
            )
        )
    ).all()

    distances = [float(r[0] or 0) for r in rows]
    # `feasible` is a Boolean column: compare with == rather than .is_(), which
    # compiles to `IS 1` and is a T-SQL syntax error. Here it is a Python-side
    # truth test, so it is only the SQL filters that need the care.
    feasible = sum(1 for r in rows if r[2])

    shares: list[float] = []
    for _, _, _, total_min, charge_min, _ in rows:
        if total_min and total_min > 0 and charge_min is not None:
            shares.append(100.0 * float(charge_min) / float(total_min))

    month_counts = Counter(int(r[1]) for r in rows if r[1])
    months = [
        Slice(label=MONTHS[m - 1], value=month_counts.get(m, 0))
        for m in range(1, 13)
        if 1 <= m <= 12
    ]

    # Which countries a journey passes through, counted once per trip. The
    # list is capped at 20 entries per row when it is written, so a trip that
    # crosses more than that under-reports its tail — acceptable, and worth
    # knowing before reading anything into the long tail of this chart.
    transit = Counter()
    for *_, countries in rows:
        if isinstance(countries, list):
            for cc in {c for c in countries if isinstance(c, str) and c}:
                transit[cc] += 1

    return ShapeSummary(
        trips=len(rows),
        feasible=feasible,
        mean_km=(sum(distances) / len(distances)) if distances else 0.0,
        mean_charge_share=(sum(shares) / len(shares)) if shares else None,
        median_charge_share=_median(shares),
        distances=histogram(distances, DISTANCE_EDGES, DISTANCE_LABELS),
        charge_shares=histogram(shares, CHARGE_SHARE_EDGES, CHARGE_SHARE_LABELS),
        months=months,
        transit_countries=[
            Slice(label=cc, value=n) for cc, n in transit.most_common(15)
        ],
    )


async def speed_choice(
    db: AsyncSession, cutoff: datetime | None
) -> list[tuple[float, int]]:
    """How often each optimum cruise speed comes out of the planner.

    The answer to "should you really drive 100?" in aggregate — and the one
    chart on this dashboard that is about the product's actual thesis rather
    than about its traffic.
    """
    rows = (
        await db.execute(
            _window(
                select(TripStat.optimum_speed_kph, func.count())
                .where(TripStat.optimum_speed_kph.isnot(None))
                .group_by(TripStat.optimum_speed_kph)
                .order_by(TripStat.optimum_speed_kph),
                cutoff,
            )
        )
    ).all()
    return [(float(s), int(n)) for s, n in rows if s]


async def stops_by_distance(
    db: AsyncSession, cutoff: datetime | None
) -> list[tuple[int, float]]:
    """Average optimum stop count per distance band.

    `cast(..., Float)` before the average is mandatory: integer AVG on MSSQL
    truncates while SQLite returns a float, so without it the test engine and
    production disagree about the same query (see `corridors._avg`).
    """
    rows = (
        await db.execute(
            _window(
                select(
                    TripStat.distance_km,
                    func.avg(cast(TripStat.optimum_n_stops, Float)),
                )
                .where(TripStat.optimum_n_stops.isnot(None))
                .group_by(TripStat.distance_km)
                .order_by(TripStat.distance_km),
                cutoff,
            )
        )
    ).all()
    return [(int(km or 0), float(avg or 0.0)) for km, avg in rows]
