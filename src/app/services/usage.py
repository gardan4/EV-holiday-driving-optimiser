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

from sqlalchemy import case, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    CampaignOut,
    CorridorOut,
    DayCount,
    LabelCount,
    ProviderOut,
    UsageStats,
)
from app.models import AppEvent, Trip, TripRun
from app.services import corridors, quota


def _corridor_out(c: corridors.Corridor) -> CorridorOut:
    return CorridorOut(
        label=c.label,
        trips=c.trips,
        distance_km=c.distance_km,
        avg_stops=c.avg_stops,
        stops_per_100km=c.stops_per_100km,
    )

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

    async def _breakdown(column, limit: int = 10) -> list[LabelCount]:
        """Count one coarse column over the window, commonest first.

        Every column this is used on is low-cardinality by construction (see
        `core.client_context`), so the GROUP BY is over a handful of values and
        the limit is a backstop rather than a real truncation.
        """
        rows = (
            await db.execute(
                select(column, func.count())
                .where(AppEvent.at >= cutoff, column.isnot(None))
                .group_by(column)
                .order_by(func.count().desc())
                .limit(limit)
            )
        ).all()
        return [LabelCount(label=v, count=int(n)) for v, n in rows if v]

    # Why planning broke, when it broke. The most valuable list on launch day:
    # everything in the "world" group is demand we could not serve, which is a
    # roadmap rather than a bug list.
    failure_reasons = [
        LabelCount(label=r, count=int(n))
        for r, n in (
            await db.execute(
                select(AppEvent.reason, func.count())
                .where(
                    AppEvent.at >= cutoff,
                    AppEvent.name == "plan_failed",
                    AppEvent.reason.isnot(None),
                )
                .group_by(AppEvent.reason)
                .order_by(func.count().desc())
            )
        ).all()
        if r
    ]

    countries = await _breakdown(AppEvent.country)
    devices = await _breakdown(AppEvent.device)
    browsers = await _breakdown(AppEvent.browser)
    operating_systems = await _breakdown(AppEvent.os)
    viewports = await _breakdown(AppEvent.viewport)
    # More of these than of anything else above — one row per thread rather
    # than per site — so it gets a bigger limit.
    referrer_paths = await _breakdown(AppEvent.referrer_path, limit=15)

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

    # Campaign attribution. Two counts per tag rather than one: ranking by
    # arrivals just ranks channels by size, and the question a launch actually
    # has is which audience went on to use the thing.
    campaign_rows = (
        await db.execute(
            select(
                AppEvent.campaign,
                func.sum(case((AppEvent.name == "page_view", 1), else_=0)),
                func.sum(case((AppEvent.name == "plan_submitted", 1), else_=0)),
            )
            .where(AppEvent.at >= cutoff, AppEvent.campaign.isnot(None))
            .group_by(AppEvent.campaign)
            .order_by(func.count().desc())
            .limit(15)
        )
    ).all()
    campaigns = [
        CampaignOut(label=tag, page_views=int(views or 0), plans=int(plans or 0))
        for tag, views, plans in campaign_rows
        if tag
    ]

    providers = [
        ProviderOut(
            provider=p.provider,
            calls_today=p.calls_today,
            cache_hits_today=p.cache_hits_today,
            failures_today=p.failures_today,
            avg_ms=round(p.avg_ms, 1),
            calls_window=p.calls_window,
            daily_quota=p.daily_quota,
            hit_rate=p.hit_rate,
            headroom_pct=p.headroom_pct,
        )
        for p in await quota.quota_report(db, days=days)
    ]

    # Corridor data, through the same functions `scripts.corridor_report` uses.
    # Note these take the SAME cutoff as everything above, so the dashboard's
    # window means one thing throughout — but `trip_stats` is backfilled, so a
    # wide window here reaches back to the first deploy while the event-derived
    # numbers above start the day counting shipped.
    top_corr = await corridors.top_corridors(db, cutoff, 10)
    hardest, infeasible = await corridors.charging_gaps(db, cutoff, 10)
    unmodelled, unplaceable = await corridors.coverage_gap(db, cutoff)
    repeat = await corridors.repeat_planners(db, cutoff)
    cars = await corridors.by_vehicle(db, cutoff, limit=10)

    # Retention: the question the daily pseudonym cannot answer.
    #
    # "Returning" means this browser was seen BEFORE the window opened, not
    # merely twice inside it — someone who lands from a link and reads two
    # pages is one visit, and counting them as retained would flatter every
    # launch spike into looking like a product.
    #
    # The denominator is only browsers that sent an id, so it excludes anyone
    # on Global Privacy Control, in a private window, or with storage cleared.
    # That biases toward under-counting returns (a real returner who cleared
    # storage reads as new), which is the right direction for a number whose
    # whole job is to stop you believing your own launch.
    # GROUP BY rather than DISTINCT so both sides are subqueries of the same
    # shape and the join below is plain SQL on either engine.
    def _ids(*where):
        return (
            select(AppEvent.client_id)
            .where(AppEvent.client_id.isnot(None), *where)
            .group_by(AppEvent.client_id)
            .subquery()
        )

    in_window = _ids(AppEvent.at >= cutoff)
    before_window = _ids(AppEvent.at < cutoff)

    identified = (
        await db.execute(select(func.count()).select_from(in_window))
    ).scalar_one()
    returning = (
        await db.execute(
            select(func.count())
            .select_from(in_window)
            .join(before_window, in_window.c.client_id == before_window.c.client_id)
        )
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
        identified_visitors=int(identified),
        returning_visitors=int(returning),
        countries=countries,
        devices=devices,
        browsers=browsers,
        operating_systems=operating_systems,
        viewports=viewports,
        top_referrer_paths=referrer_paths,
        top_corridors=[_corridor_out(c) for c in top_corr],
        hardest_corridors=[_corridor_out(c) for c in hardest],
        infeasible_corridors=[_corridor_out(c) for c in infeasible],
        unmodelled_countries=[
            LabelCount(label=cc, count=n) for cc, n in unmodelled[:10]
        ],
        unplaceable_trips=unplaceable,
        repeat_planners=[LabelCount(label=k, count=n) for k, n in repeat],
        top_cars=[LabelCount(label=name, count=n) for name, n, _ in cars[:10]],
        campaigns=campaigns,
        providers=providers,
        failure_reasons=failure_reasons,
    )
