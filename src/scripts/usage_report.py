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

    lines += [
        "",
        "  From the trips table (real history, predates event counting):",
        f"    trips planned in window   {s.trips_planned:>6}",
        f"    drives started in window  {s.drives_started:>6}",
        f"    trips planned ever        {s.trips_planned_since_launch:>6}",
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
