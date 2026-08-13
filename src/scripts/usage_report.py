"""Print the usage numbers to a terminal.

Two sources, one table. Without `--remote` it reads whatever database
`DATABASE_URL` points at; with it, it calls `GET /api/events/stats` on the
deployed API using `STATS_TOKEN` from the environment. Both render through the
same formatter, so the local and production numbers are directly comparable
rather than one being a table and the other raw JSON.

Read-only either way — it touches nothing, so unlike `dev_seed_trip` it has no
reason to refuse a production database.

    cd src && uv run python -m scripts.usage_report
    cd src && uv run python -m scripts.usage_report --days 30
    cd src && uv run python -m scripts.usage_report --remote
"""

from __future__ import annotations

import argparse
import asyncio

import httpx

from app.api.schemas import UsageStats
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.services.usage import usage_stats

# The deployed API. Not `settings.API_PUBLIC_URL` — that is localhost in a dev
# .env, which would make `--remote` quietly report on the machine you are
# sitting at. Override with --url when pointing at somewhere else.
DEFAULT_REMOTE_URL = "https://api.evtrip.dev"


async def _fetch_local(days: int) -> UsageStats:
    async with AsyncSessionLocal() as db:
        return await usage_stats(db, days=days)


async def _fetch_remote(url: str, days: int) -> UsageStats:
    if not settings.STATS_TOKEN:
        raise SystemExit(
            "No STATS_TOKEN set, so the remote read route does not exist.\n"
            "Put it in src/.env or the environment, or drop --remote to read "
            "the database directly."
        )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            f"{url.rstrip('/')}/api/events/stats",
            params={"days": days},
            headers={"X-Stats-Token": settings.STATS_TOKEN},
        )
    # The route answers 404 both when it is switched off and when the token is
    # wrong — deliberately, so it gives nothing away to a stranger. That makes
    # the bare status unhelpful here, where the caller is you.
    if resp.status_code == 404:
        raise SystemExit(
            f"404 from {url} — either STATS_TOKEN is wrong, or the deployment "
            "has not picked it up yet."
        )
    resp.raise_for_status()
    return UsageStats.model_validate(resp.json())


def _format(s: UsageStats, source: str) -> str:
    lines = [
        f"Usage — last {s.days} day(s), as of {s.generated_at:%Y-%m-%d %H:%M} UTC",
        f"Source: {source}",
        "",
        "  day          visitors   page views",
    ]
    for d in s.daily:
        lines.append(f"  {d.day}   {d.visitors:>8}   {d.page_views:>10}")

    width = max((len(f.label) for f in s.funnel), default=12)
    lines += [
        "",
        f"  Window totals: {s.page_views} page views, {s.visitors} visitor-days",
        "  (the pseudonym rotates nightly, so a week's 'visitors' counts",
        "   visitor-days, not people — the daily column is the honest one)",
        "",
        "  What people did:",
    ]
    for f in s.funnel:
        lines.append(f"    {f.label:<{width}}  {f.count:>6}")

    if s.top_paths:
        lines += ["", "  Pages:"]
        for p in s.top_paths:
            lines.append(f"    {p.label:<20} {p.count:>6}")

    if s.top_referrers:
        lines += ["", "  Came from:"]
        for r in s.top_referrers:
            lines.append(f"    {r.label:<28} {r.count:>6}")
    else:
        lines += ["", "  Came from: nothing but direct visits and internal links."]

    if s.identified_visitors:
        pct = 100.0 * s.returning_visitors / s.identified_visitors
        lines += [
            "",
            "  Did they come back?",
            f"    {s.returning_visitors} of {s.identified_visitors} browsers "
            f"({pct:.0f}%) had been here before this window",
            "    (counts only browsers that keep an id — opting out, private",
            "     windows and cleared storage all read as new, so this is a",
            "     floor, not an estimate)",
        ]
    else:
        lines += [
            "",
            "  Did they come back? No browser in this window sent a persistent",
            "  id, so there is nothing to answer with yet.",
        ]

    def _block(title: str, rows, empty: str) -> None:
        # `lines.append`, never `lines += […]` — an augmented assignment in here
        # would rebind `lines` as a local and break the whole function.
        lines.append("")
        if not rows:
            lines.append(f"  {title}: {empty}")
            return
        lines.append(f"  {title}:")
        width = max(len(r.label) for r in rows)
        for r in rows:
            lines.append(f"    {r.label:<{width}}  {r.count:>6}")

    if s.failure_reasons:
        lines.append("")
        lines.append("  Why planning failed:")
        width = max(len(f.label) for f in s.failure_reasons)
        for f in s.failure_reasons:
            lines.append(f"    {f.label:<{width}}  {f.count:>5}")
        lines.append("    (no_route / no_chargers / corridor_cold are demand we")
        lines.append("     could not serve — a roadmap, not a bug list)")

    if s.providers:
        lines.append("")
        lines.append("  Free-tier headroom today (resets midnight UTC):")
        for p in s.providers:
            hit = f"{100.0 * p.hit_rate:.0f}% cached" if p.hit_rate is not None else "no traffic"
            cap = (
                f"{p.calls_today}/{p.daily_quota} ({p.headroom_pct:.0f}% left)"
                if p.headroom_pct is not None
                else f"{p.calls_today} calls (no published cap)"
            )
            warn = f"  ** {p.failures_today} FAILED **" if p.failures_today else ""
            # Provider AND service: the ceilings are per service, and printing
            # only "ORS" is how a dead autocomplete hides behind healthy routing.
            label = f"{p.provider.upper()} {p.kind}".strip()
            lines.append(f"    {label:<20} {cap:<28} {hit}, {p.avg_ms:.0f} ms{warn}")

    if s.campaigns:
        lines.append("")
        lines.append("  Which link worked:")
        width = max(len(c.label) for c in s.campaigns)
        for c in s.campaigns:
            conv = f"{c.conversion_pct:.0f}%" if c.conversion_pct is not None else "–"
            lines.append(
                f"    {c.label:<{width}}  {c.page_views:>5} arrived, "
                f"{c.plans:>4} asked for a plan ({conv})"
            )
    else:
        lines.append("")
        lines.append("  Which link worked: no tagged links yet — post with ?src=your-tag.")

    _block("Where from", s.countries,
           "nothing (needs TRUSTED_PROXY_HEADERS and a CDN in front)")
    _block("On what", s.devices, "nothing recorded yet.")
    _block("Browsers", s.browsers, "nothing recorded yet.")
    _block("Screen width", s.viewports, "nothing recorded yet.")
    _block("Which page sent them", s.top_referrer_paths, "no external links yet.")

    if s.top_corridors:
        lines += ["", "  Where people drive (all planned trips, back to launch):"]
        for c in s.top_corridors:
            stops = f"{c.avg_stops:.1f}" if c.avg_stops is not None else "–"
            lines.append(f"    {c.trips:>4}×  {c.label:<34} {c.distance_km:>5} km  {stops} stops")

    if s.hardest_corridors:
        lines += ["", "  Hardest to charge (stops per 100 km):"]
        for c in s.hardest_corridors:
            lines.append(
                f"    {c.stops_per_100km:>4.2f}  {c.label:<34} "
                f"{c.avg_stops:.1f} stops over {c.distance_km} km"
            )
    if s.infeasible_corridors:
        lines += ["", "  No feasible plan at any speed:"]
        for c in s.infeasible_corridors:
            lines.append(f"    {c.trips:>4}×  {c.label:<34} {c.distance_km:>5} km")

    if s.unplaceable_trips or s.unmodelled_countries:
        lines += ["", "  Roads we don't model:"]
        if s.unplaceable_trips:
            lines.append(
                f"    {s.unplaceable_trips} trip(s) start or end somewhere we "
                "model nothing about"
            )
        for lc in s.unmodelled_countries:
            lines.append(f"    {lc.label:<4} crossed {lc.count}×, no real cap of its own")

    _block("Trips per planner", s.repeat_planners,
           "nobody has planned a trip with a persistent id yet.")

    p = s.profiles
    lines.append("")
    if p.live or p.claimed or p.released:
        rate = f"{100.0 * p.publish_rate:.0f}%" if p.publish_rate is not None else "–"
        lines += [
            "  Usernames (stock is all-time; the rest is the window):",
            f"    names right now           {p.live:>6}   "
            f"({p.with_trips} publish something, {p.empty} hold nothing)",
            f"    claimed in window         {p.claimed:>6}",
            f"    released in window        {p.released:>6}",
            f"    trips published           {p.published:>6}   "
            f"of {p.trips} planned ({rate})",
            f"    public lists opened       {p.list_views:>6}",
        ]
        if p.per_name:
            width = max(len(b.label) for b in p.per_name)
            lines.append("    trips per name:")
            for b in p.per_name:
                lines.append(f"      {b.label:<{width}}  {b.count:>4}")
        lines.append("    (claims count names that still exist — one claimed and")
        lines.append("     released inside the window shows only in the release)")
    else:
        lines.append("  Usernames: nobody has claimed one yet.")

    lines += [
        "",
        "  From the trips table (real history, predates event counting):",
        f"    trips planned in window   {s.trips_planned:>6}",
        f"    drives started in window  {s.drives_started:>6}",
        f"    trips planned ever        {s.trips_planned_since_launch:>6}",
    ]
    if s.trips_deleted:
        lines += [
            f"    trips DELETED in window   {s.trips_deleted:>6}",
            "    (gone from the corridor numbers above too — they count rows",
            "     that still exist, so this is the only trace left)",
        ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument(
        "--remote",
        action="store_true",
        help="read the deployed API instead of the local database",
    )
    ap.add_argument("--url", default=DEFAULT_REMOTE_URL, help="API base URL for --remote")
    args = ap.parse_args()

    if args.remote:
        stats = asyncio.run(_fetch_remote(args.url, args.days))
        source = args.url
    else:
        stats = asyncio.run(_fetch_local(args.days))
        source = "local database (DATABASE_URL)"
    print(_format(stats, source))


if __name__ == "__main__":
    main()
