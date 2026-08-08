"""Print the usage numbers to a terminal.

The same summary `GET /api/events/stats` returns, for when you'd rather run a
command than carry a token around. Read-only — it touches nothing, so unlike
`dev_seed_trip` it has no reason to refuse a production database.

    cd src && uv run python -m scripts.usage_report
    cd src && uv run python -m scripts.usage_report --days 30
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.database import AsyncSessionLocal
from app.services.usage import usage_stats


async def _report(days: int) -> str:
    async with AsyncSessionLocal() as db:
        s = await usage_stats(db, days=days)

    lines = [
        f"Usage — last {s.days} day(s), as of {s.generated_at:%Y-%m-%d %H:%M} UTC",
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
    args = ap.parse_args()
    print(asyncio.run(_report(args.days)))


if __name__ == "__main__":
    main()
