"""Reading the usage numbers back out.

Lives here rather than in the route because two callers need it: the token-gated
`GET /api/events/stats` and `scripts.usage_report`, which prints the same
summary from a terminal against `DATABASE_URL`. One implementation means the
number on the dashboard and the number in the terminal cannot disagree.

Every query is bounded by a window and, for the top-N lists, a limit — this runs
against the live Azure SQL instance, and an unbounded scan of the busiest table
in the app is not something an admin endpoint should be able to trigger.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import DayCount, LabelCount, UsageStats
from app.models import AppEvent, Trip, TripRun

# The funnel, and nothing else. Adding one here is deliberate; it should stay
# possible to read this list and know everything the app records.
ALLOWED_EVENTS = frozenset(
    {
        "page_view",       # a page was opened
        "plan_submitted",  # the form was sent
        "trip_planned",    # …and a plan came back
        "plan_failed",     # …or it didn't (how often planning breaks is worth knowing)
        "drive_started",   # somebody actually drove one
    }
)

MAX_DAYS = 90


async def usage_stats(db: AsyncSession, days: int = 7) -> UsageStats:
    days = max(1, min(days, MAX_DAYS))
    now = datetime.utcnow()
    cutoff = now - timedelta(days=days)

    # One aggregate per day rather than a GROUP BY on a truncated date: MSSQL
    # and SQLite share no date-truncation function, and CAST(x AS DATE) means
    # different things on each. `days` is capped, and the (at, name) index makes
    # each of these a range scan.
    daily: list[DayCount] = []
    for i in range(days - 1, -1, -1):
        day_start = (now - timedelta(days=i)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        day_end = day_start + timedelta(days=1)
        row = (
            await db.execute(
                select(func.count(distinct(AppEvent.visitor)), func.count()).where(
                    AppEvent.at >= day_start,
                    AppEvent.at < day_end,
                    AppEvent.name == "page_view",
                )
            )
        ).one()
        daily.append(
            DayCount(
                day=day_start.date().isoformat(),
                visitors=int(row[0]),
                page_views=int(row[1]),
            )
        )

    # Window totals. `visitors` is NOT the sum of the daily column, and it is
    # also not "people": the salt rotates at midnight, so somebody who visits on
    # three days is three unrelated pseudonyms. Over a week this number means
    # visitor-days, and it drifts further from a headcount the longer the
    # window. That is a property of not tracking people, not a bug — but it is
    # why the honest headline number is the daily figure, not this one.
    totals = (
        await db.execute(
            select(func.count(distinct(AppEvent.visitor)), func.count()).where(
                AppEvent.at >= cutoff, AppEvent.name == "page_view"
            )
        )
    ).one()

    by_name = (
        await db.execute(
            select(AppEvent.name, func.count())
            .where(AppEvent.at >= cutoff)
            .group_by(AppEvent.name)
        )
    ).all()
    counts = {name: int(n) for name, n in by_name}
    funnel = [LabelCount(label=n, count=counts.get(n, 0)) for n in sorted(ALLOWED_EVENTS)]

    top_paths = [
        LabelCount(label=p or "unknown", count=int(n))
        for p, n in (
            await db.execute(
                select(AppEvent.path, func.count())
                .where(AppEvent.at >= cutoff, AppEvent.name == "page_view")
                .group_by(AppEvent.path)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()
    ]

    top_referrers = [
        LabelCount(label=r, count=int(n))
        for r, n in (
            await db.execute(
                select(AppEvent.referrer, func.count())
                .where(AppEvent.at >= cutoff, AppEvent.referrer.isnot(None))
                .group_by(AppEvent.referrer)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()
        if r
    ]

    # These come from the domain tables, not from events — so they are accurate
    # for the whole window even before events existed, they reach back to the
    # first deploy, and they count what actually happened rather than what a
    # browser with an ad blocker managed to report.
    trips_planned = (
        await db.execute(
            select(func.count()).select_from(Trip).where(Trip.created_at >= cutoff)
        )
    ).scalar_one()
    drives_started = (
        await db.execute(
            select(func.count()).select_from(TripRun).where(TripRun.started_at >= cutoff)
        )
    ).scalar_one()
    trips_all_time = (
        await db.execute(select(func.count()).select_from(Trip))
    ).scalar_one()

    return UsageStats(
        days=days,
        generated_at=now,
        daily=daily,
        visitors=int(totals[0]),
        page_views=int(totals[1]),
        funnel=funnel,
        top_paths=top_paths,
        top_referrers=top_referrers,
        trips_planned=int(trips_planned),
        drives_started=int(drives_started),
        trips_planned_since_launch=int(trips_all_time),
    )
