"""The five shapes every dashboard panel is made of.

`services/usage.py` is fifteen bespoke queries that each answer one question.
These are five general ones that answer any question inside the allowlist, and
`usage_stats` is rebuilt on top of them — so the terminal report and the
dashboard cannot drift apart, which is the same rule `services/corridors.py`
already lives by.

Every function takes `Filters`, so a facet chosen once in the UI narrows every
panel at once without any panel knowing the filter bar exists.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppEvent
from app.services.analytics.filters import (
    Filters,
    bucket,
    clamp_limit,
    dialect_of,
    dimension,
    measure,
    normalize_bucket,
)

# The funnel in the order somebody moves through it. Alphabetical (what a
# GROUP BY returns) puts "drive_started" first and makes the drop-off between
# stages unreadable. `usage_dashboard.py` kept its own copy of this for the
# same reason; this is now the one that matters.
FUNNEL_STAGES: list[tuple[str, str]] = [
    ("page_view", "Opened a page"),
    ("plan_submitted", "Asked for a plan"),
    ("trip_planned", "Got one back"),
    ("drive_started", "Drove it"),
]

# Counted, but not a stage. Left inside the funnel it reads as a step people
# pass through rather than the thing that stopped them.
FAILURE_EVENT = "plan_failed"


@dataclass(frozen=True)
class Point:
    bucket: str
    value: int


@dataclass(frozen=True)
class Slice:
    label: str
    value: int


@dataclass(frozen=True)
class Stage:
    name: str
    label: str
    count: int
    # Share of the stage before it — the number that says where people leave.
    # None on the first stage, which has nothing to drop off from.
    from_previous: float | None


async def timeseries(
    db: AsyncSession,
    f: Filters,
    *,
    m: str = "events",
    granularity: str = "day",
    event: str | None = "page_view",
) -> list[Point]:
    """One row per bucket, zero-filled across the whole window.

    Zero-filling matters: a day with no traffic is a real answer, and a chart
    that simply omits it draws a straight line over the gap and reports a quiet
    Tuesday as if it never happened.
    """
    dialect = dialect_of(db)
    b = bucket(granularity, dialect)
    where = list(f.clauses())
    if event:
        where.append(AppEvent.name == event)

    rows = (
        await db.execute(
            select(b, measure(m)).where(*where).group_by(b).order_by(b)
        )
    ).all()
    seen = {normalize_bucket(k, granularity): int(v or 0) for k, v in rows}
    return [Point(bucket=k, value=seen.get(k, 0)) for k in _spine(f, granularity)]


def _spine(f: Filters, granularity: str) -> list[str]:
    """Every bucket label the window covers, in order, ending at `until`.

    Anchored on the END of the window, not the start. Anchoring on `since`
    floors it to a boundary that is up to one bucket earlier than the window
    really begins, which yields eight bars for a seven-day window — the first
    of them a partial day that looks like a collapse in traffic.
    `usage.usage_stats` counts back from today for exactly this reason, and the
    two have to agree.
    """
    from datetime import timedelta

    span = f.until - f.since
    if granularity == "hour":
        step, fmt = timedelta(hours=1), "%Y-%m-%dT%H:00"
        n = max(1, round(span.total_seconds() / 3600))
        end = f.until.replace(minute=0, second=0, microsecond=0)
    else:
        step, fmt = timedelta(days=1), "%Y-%m-%d"
        n = max(1, round(span.total_seconds() / 86400))
        end = f.until.replace(hour=0, minute=0, second=0, microsecond=0)

    # Bounded by MAX_DAYS upstream, so this cannot run away even at hourly.
    n = min(n, 24 * 92)
    start = end - step * (n - 1)
    return [(start + step * i).strftime(fmt) for i in range(n)]


async def breakdown(
    db: AsyncSession,
    f: Filters,
    dim: str,
    *,
    m: str = "events",
    event: str | None = None,
    limit: int = 10,
    null_label: str | None = None,
) -> list[Slice]:
    """Count one dimension over the segment, commonest first.

    Generalises `usage._breakdown`, which took a hard-coded column and could
    therefore only ever answer the questions already written down.

    `null_label` keeps rows where the column is NULL and names them, instead of
    dropping them. Off by default — for a bucketed dimension like `device`, a
    NULL is an absent header and counting it as a category invents a
    "browsers that didn't say" segment nobody asked about. It is on for `path`,
    where dropping the row would silently shrink the page-view total below the
    headline number sitting next to it.
    """
    col = dimension(dim)
    where = list(f.clauses())
    if null_label is None:
        where.append(col.isnot(None))
    if event:
        where.append(AppEvent.name == event)

    agg = measure(m)
    rows = (
        await db.execute(
            select(col, agg)
            .where(*where)
            .group_by(col)
            .order_by(agg.desc())
            .limit(clamp_limit(limit))
        )
    ).all()
    out: list[Slice] = []
    for value, n in rows:
        label = str(value) if value else null_label
        if label:
            out.append(Slice(label=label, value=int(n or 0)))
    return out


async def funnel(db: AsyncSession, f: Filters) -> tuple[list[Stage], int]:
    """The four stages plus the failure count, for this segment.

    Returns `(stages, failures)`. Failures are separate because they are an
    error count, not a stage — see `FAILURE_EVENT`.
    """
    rows = (
        await db.execute(
            select(AppEvent.name, func.count())
            .where(*f.clauses())
            .group_by(AppEvent.name)
        )
    ).all()
    counts = {str(name): int(n or 0) for name, n in rows}

    stages: list[Stage] = []
    previous: int | None = None
    for name, label in FUNNEL_STAGES:
        count = counts.get(name, 0)
        stages.append(
            Stage(
                name=name,
                label=label,
                count=count,
                from_previous=(count / previous) if previous else None,
            )
        )
        previous = count
    return stages, counts.get(FAILURE_EVENT, 0)


def histogram(
    values: list[float],
    edges: list[float],
    labels: list[str] | None = None,
) -> list[Slice]:
    """Bin an already-fetched numeric column.

    Deliberately binned in Python rather than as a SQL CASE: the columns worth
    a histogram here live on `trip_stats`, which is small (one row per trip
    ever planned) and already read wholesale by `services/corridors.py`. A
    generated CASE ladder would be faster and much harder to read, for a table
    that does not need it.
    """
    out = [0] * (len(edges) + 1)
    for v in values:
        placed = False
        for i, edge in enumerate(edges):
            if v < edge:
                out[i] += 1
                placed = True
                break
        if not placed:
            out[-1] += 1

    if labels is None:
        labels = []
        low = 0.0
        for edge in edges:
            labels.append(f"{low:g}-{edge:g}")
            low = edge
        labels.append(f"{low:g}+")
    return [Slice(label=lab, value=n) for lab, n in zip(labels, out)]


async def totals(db: AsyncSession, f: Filters) -> dict[str, int]:
    """The headline counters, in one round trip.

    One query with conditional sums rather than four, because these back the
    live tiles and get re-read on every SSE tick.

    `visitors` and `clients` count **browsers that viewed a page**, not
    browsers that emitted any event — which is the definition `usage_stats`,
    `scripts.usage_report` and the daily chart have always used. Counting every
    event's visitor instead is defensible in isolation and wrong here: it
    quietly includes anything that reported a plan without a page view, so the
    dashboard tile and the terminal report print different numbers under the
    same word. Same label, same query.
    """
    viewed = case((AppEvent.name == "page_view", AppEvent.visitor), else_=None)
    viewed_client = case((AppEvent.name == "page_view", AppEvent.client_id), else_=None)
    row = (
        await db.execute(
            select(
                func.count(),
                func.count(func.distinct(viewed)),
                func.count(func.distinct(viewed_client)),
                func.sum(case((AppEvent.name == "page_view", 1), else_=0)),
                func.sum(case((AppEvent.name == "plan_submitted", 1), else_=0)),
                func.sum(case((AppEvent.name == "trip_planned", 1), else_=0)),
                func.sum(case((AppEvent.name == "drive_started", 1), else_=0)),
                func.sum(case((AppEvent.name == FAILURE_EVENT, 1), else_=0)),
            ).where(*f.clauses())
        )
    ).one()
    keys = (
        "events",
        "visitors",
        "clients",
        "page_views",
        "plans_submitted",
        "trips_planned",
        "drives_started",
        "plan_failures",
    )
    return {k: int(v or 0) for k, v in zip(keys, row)}


async def compare(db: AsyncSession, f: Filters) -> dict[str, dict[str, int | float | None]]:
    """This window's totals next to the one before it, with deltas.

    Period-over-period is the difference between "412 page views" and "412 page
    views, up from 90" — the second is the only one that says whether the
    launch is working.
    """
    now = await totals(db, f)
    before = await totals(db, f.previous())
    out: dict[str, dict[str, int | float | None]] = {}
    for key, value in now.items():
        prior = before.get(key, 0)
        # No ratio against a zero baseline — "up 100%" from nothing is a
        # sentence that reads as growth and means "it started".
        change = ((value - prior) / prior) if prior else None
        out[key] = {"value": value, "previous": prior, "change": change}
    return out
