"""ORS and OpenChargeMap over time, rather than only today.

`services/quota.py` answers "how much is left today", which is the right
question at 22:00 on launch night and the wrong one every other time. Two
things it cannot show:

- **The cache hit rate as a trend.** The hit rate is what decides whether a
  traffic spike costs one upstream call or a thousand, so its *direction* over
  a week predicts whether a launch survives its own front page. A single
  today-figure cannot tell you it has been sliding.
- **Latency as a distribution.** `avg_ms` is a mean over a bimodal population —
  cache-warm operations taking milliseconds and cold corridor searches taking
  seconds — so it lands in the gap between them and describes neither. The p50
  and p95 do.

`ok` keeps the meaning it has in `quota.record`: it marks the provider failing
us, not the answer being negative. ORS reporting no road between two points is
a successful call with a disappointing result, and counting it as an outage
makes the health signal cry wolf every time somebody picks an island.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import DATE, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UpstreamCall
from app.services.analytics.filters import dialect_of

PROVIDERS = ("ors", "ocm")


@dataclass(frozen=True)
class DayHealth:
    day: str
    calls: int
    cache_hits: int
    failures: int
    operations: int

    @property
    def hit_rate(self) -> float | None:
        total = self.calls + self.cache_hits
        return (self.cache_hits / total) if total else None


@dataclass(frozen=True)
class Latency:
    provider: str
    kind: str
    operations: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: int | None


def _day_bucket(dialect: str):
    """As `analytics.filters.day_bucket`, over `upstream_calls.at`.

    Same MSSQL/SQLite split and the same reason for it — see that function for
    why `Date` cannot be used here and `DATE` can.
    """
    if dialect == "sqlite":
        return func.strftime("%Y-%m-%d", UpstreamCall.at)
    return cast(UpstreamCall.at, DATE)


def _iso(value) -> str:
    if isinstance(value, str):
        return value[:10]
    return value.isoformat()[:10] if value is not None else ""


def _percentile(values: list[int], pct: float) -> float | None:
    """Nearest-rank percentile over an already-fetched list.

    Computed in Python rather than with `PERCENTILE_CONT`: SQLite has no such
    function, so a SQL version would only ever run in production and would be
    untested everywhere it could be checked. The row count is bounded by one
    row per app-level operation (not per HTTP request — see `quota.record`),
    which is small enough to pull.
    """
    if not values:
        return None
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(pct / 100.0 * len(s) + 0.5)) - 1))
    return float(s[idx])


def daily_query(provider: str, since: datetime, dialect: str):
    """The per-day aggregate, as a statement.

    Separated from `daily` so a test can compile the REAL query against the
    MSSQL dialect. That matters more than usual here: the tests run on SQLite,
    and the two engines disagree about this exact statement in two ways that
    SQLite accepts silently (see below).
    """
    b = _day_bucket(dialect)
    return (
        select(
            b,
            func.coalesce(func.sum(UpstreamCall.calls), 0),
            func.coalesce(func.sum(UpstreamCall.cache_hits), 0),
            # A CASE, not a cast of the comparison. `CAST(ok = 0 AS FLOAT)` is
            # valid SQLite (a comparison there yields 0/1) and a T-SQL syntax
            # error — SQL Server has no boolean *value* type, so `ok = 0` is a
            # predicate and cannot be cast. This shipped once and only showed
            # up against the real database. `== False` rather than
            # `.is_(False)` for the usual reason: the latter compiles to
            # `IS 0`, also a syntax error.
            func.sum(case((UpstreamCall.ok == False, 1), else_=0)),  # noqa: E712
            func.count(),
        )
        .where(UpstreamCall.at >= since, UpstreamCall.provider == provider)
        .group_by(b)
        .order_by(b)
    )


async def daily(
    db: AsyncSession, provider: str, days: int = 14
) -> list[DayHealth]:
    """Calls, cache hits and failures per day, zero-filled across the window."""
    since = datetime.utcnow() - timedelta(days=max(1, days))
    rows = (
        await db.execute(daily_query(provider, since, dialect_of(db)))
    ).all()
    seen = {
        _iso(day): DayHealth(
            day=_iso(day),
            calls=int(calls or 0),
            cache_hits=int(hits or 0),
            failures=int(fails or 0),
            operations=int(ops or 0),
        )
        for day, calls, hits, fails, ops in rows
    }

    # Zero-fill: a quiet day is a real answer, and a chart that skips it draws
    # a straight line over the gap.
    end = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    spine = [(end - timedelta(days=i)).date().isoformat() for i in range(days - 1, -1, -1)]
    return [
        seen.get(day, DayHealth(day=day, calls=0, cache_hits=0, failures=0, operations=0))
        for day in spine
    ]


async def latency(db: AsyncSession, days: int = 7) -> list[Latency]:
    """p50/p95 duration per provider and kind over the window.

    Split by `kind` because the two are different operations with different
    expected costs — an ORS `directions` call and an OCM `chargers` corridor
    sweep pooled together produce a percentile that describes neither.
    """
    since = datetime.utcnow() - timedelta(days=max(1, days))
    rows = (
        await db.execute(
            select(UpstreamCall.provider, UpstreamCall.kind, UpstreamCall.duration_ms)
            .where(UpstreamCall.at >= since, UpstreamCall.duration_ms > 0)
            .order_by(UpstreamCall.provider, UpstreamCall.kind)
        )
    ).all()

    grouped: dict[tuple[str, str], list[int]] = {}
    for provider, kind, ms in rows:
        grouped.setdefault((str(provider), str(kind)), []).append(int(ms or 0))

    return [
        Latency(
            provider=provider,
            kind=kind,
            operations=len(values),
            p50_ms=_percentile(values, 50),
            p95_ms=_percentile(values, 95),
            max_ms=max(values) if values else None,
        )
        for (provider, kind), values in sorted(grouped.items())
    ]
