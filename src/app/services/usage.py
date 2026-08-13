"""Reading the usage numbers back out.

Lives here rather than in the route because three callers need it: the
token-gated `GET /api/events/stats`, `scripts.usage_report` (which prints the
same summary from a terminal against `DATABASE_URL`), and the admin dashboard's
overview. One implementation means the number on the dashboard and the number
in the terminal cannot disagree.

This is now a **composition over `services.analytics`** rather than fifteen
bespoke queries. The dashboard asks arbitrary filtered questions through those
primitives; this asks the fixed unfiltered ones. Both going through the same
code is what stops "page views" quietly meaning two different things in two
places — the rule `services/corridors.py` already lives by.

Every query is bounded by a window and, for the top-N lists, a limit — this runs
against the live Azure SQL instance, and an unbounded scan of the busiest table
in the app is not something an admin endpoint should be able to trigger.
"""

from __future__ import annotations

from datetime import datetime

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
from app.services.analytics import Filters, breakdown, retention, timeseries


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
    f = Filters.window(days)
    now = f.until
    cutoff = f.since

    # Two grouped queries rather than one per day. This used to be a loop of
    # `days` separate SELECTs because MSSQL and SQLite share no date-truncation
    # function — `analytics.filters.day_bucket` is where that branch now lives,
    # written once, so the dashboard can redraw a 90-day chart on every filter
    # change without firing 90 round trips per panel.
    per_day_visitors = await timeseries(db, f, m="visitors", granularity="day")
    per_day_views = await timeseries(db, f, m="events", granularity="day")
    views_by_day = {p.bucket: p.value for p in per_day_views}
    daily: list[DayCount] = [
        DayCount(
            day=p.bucket,
            visitors=p.value,
            page_views=views_by_day.get(p.bucket, 0),
        )
        for p in per_day_visitors
    ]

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

    async def _counts(dim: str, **kw) -> list[LabelCount]:
        """`analytics.breakdown` in the shape the schema wants.

        Every dimension this is used on is low-cardinality by construction (see
        `core.client_context`), so the GROUP BY is over a handful of values and
        the limit is a backstop rather than a real truncation.
        """
        return [
            LabelCount(label=s.label, count=s.value)
            for s in await breakdown(db, f, dim, **kw)
        ]

    # NULL paths are kept and named rather than dropped: this list sits next to
    # the page-view headline, and silently omitting rows would make the column
    # add up to less than the number above it.
    top_paths = await _counts("path", event="page_view", null_label="unknown")
    top_referrers = await _counts("referrer")

    # Why planning broke, when it broke. The most valuable list on launch day:
    # everything in the "world" group is demand we could not serve, which is a
    # roadmap rather than a bug list. `PLAN_FAILURE_REASONS` has thirteen
    # members, so the limit here is a backstop and never truncates.
    failure_reasons = await _counts("reason", event="plan_failed", limit=50)

    countries = await _counts("country")
    devices = await _counts("device")
    browsers = await _counts("browser")
    operating_systems = await _counts("os")
    viewports = await _counts("viewport")
    # More of these than of anything else above — one row per thread rather
    # than per site — so it gets a bigger limit.
    referrer_paths = await _counts("referrer_path", limit=15)

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
            kind=p.kind,
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

    # Retention: the question the daily pseudonym cannot answer. The rule it
    # encodes — "returning" means seen BEFORE the window opened, not twice
    # inside it — now lives in `analytics.retention`, with the reasoning.
    seen = await retention(db, f)

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
        identified_visitors=seen.identified,
        returning_visitors=seen.returning,
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
