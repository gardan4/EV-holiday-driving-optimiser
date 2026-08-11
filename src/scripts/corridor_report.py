"""What people actually plan, and where the charging network lets them down.

Reads `trip_stats`, which is derived from every trip ever planned (see
`scripts.backfill_trip_stats`), so this reaches back to the first deploy rather
than to the day analytics shipped.

Four questions, in the order they matter:

1. **Where do people drive?** Top corridors at geohash-4, which is city-region
   granularity — the demand map.
2. **Where does charging fail them?** Corridors whose optimal plan needs the
   most stops per 100 km, and corridors with no feasible plan at any speed.
   This is the part nobody else has: it is derived from real demand against
   real charge curves, not from an operator's coverage map.
3. **Where is the model weakest?** Demand for roads the simulator has no real
   speed caps for, so the next bit of data work is chosen by usage rather than
   by guesswork.
4. **What do they drive, how far, when, and how often?**

The queries live in `services.corridors`, shared with the dashboard. This file
is only the rendering. Read-only.

    cd src && uv run python -m scripts.corridor_report
    cd src && uv run python -m scripts.corridor_report --days 30
    cd src && uv run python -m scripts.corridor_report --limit 25
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta

from app.core.database import AsyncSessionLocal
from app.services import corridors as cx

MONTHS = (
    "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)


async def report(days: int | None, limit: int) -> str:
    cutoff = datetime.utcnow() - timedelta(days=days) if days else None
    out: list[str] = [
        f"# Corridor report ({f'last {days} days' if days else 'all time'})\n"
    ]

    async with AsyncSessionLocal() as db:
        trips, feasible, mean_km = await cx.headline(db, cutoff)
        if not trips:
            return "\n".join(out + ["No trips in this window."])
        out.append(
            f"{trips} trip(s) planned\n"
            f"  {feasible} with a feasible plan "
            f"({100.0 * feasible / trips:.0f}%), mean {mean_km:.0f} km"
        )

        top = await cx.top_corridors(db, cutoff, limit)
        if top:
            out.append(f"\n## Top corridors ({len(top)})\n")
            for c in top:
                stops = f"{c.avg_stops:.1f}" if c.avg_stops is not None else "–"
                out.append(
                    f"  {c.trips:>4}×  {c.label:<34}  {c.distance_km:>5} km  {stops} stops"
                )

        worst, infeasible = await cx.charging_gaps(db, cutoff, limit)
        if worst:
            out.append("\n## Hardest corridors to charge (stops per 100 km)\n")
            for c in worst:
                out.append(
                    f"  {c.stops_per_100km:>4.2f}/100km  {c.label:<34}"
                    f"  {c.distance_km:>5} km  {c.avg_stops:.1f} stops  ({c.trips}× planned)"
                )
        if infeasible:
            out.append("\n## No feasible plan at any speed\n")
            out.append("  (the car cannot complete these with the chargers we found)\n")
            for c in infeasible:
                out.append(f"  {c.trips:>4}×  {c.label:<34}  {c.distance_km:>5} km")

        unmodelled, unplaceable = await cx.coverage_gap(db, cutoff)
        if unmodelled or unplaceable:
            out.append("\n## Demand for roads we don't model\n")
            if unplaceable:
                # First and largest. `country_at` only knows western-European
                # boxes, so a US or Japanese route cannot even be named and
                # contributes nothing to the list below — an empty list under
                # this heading would read as "no gap" when it is the biggest one.
                out.append(
                    f"  {unplaceable} trip(s) start or end somewhere the app models "
                    "nothing about —\n  no country, no real speed cap, just the "
                    "generic fallback. These are invisible\n  in the list below, "
                    "because we cannot name them.\n"
                )
            if unmodelled:
                out.append("  Named countries crossed that have no real cap of their own:\n")
                for cc, n in unmodelled[:limit]:
                    out.append(f"  {n:>4}×  {cc}")

        vehicles = await cx.by_vehicle(db, cutoff)
        if vehicles:
            out.append("\n## Cars\n")
            for name, n, speed in vehicles[:limit]:
                out.append(
                    f"  {n:>4}×  {name:<38} optimum "
                    f"{f'{speed:.0f} kph' if speed is not None else '–'}"
                )

        repeat = await cx.repeat_planners(db, cutoff)
        if repeat:
            out.append("\n## Trips per planner\n")
            total = sum(n for _, n in repeat)
            for label, n in repeat:
                out.append(f"  {label:<11} {n:>4}  ({100.0 * n / total:.0f}%)")
            out.append(
                "\n  (only browsers that kept an id; backfilled history and anyone\n"
                "   who opted out are not in here, so '1 trip' is overstated and\n"
                "   repeat use is a floor)"
            )

        months = await cx.by_month(db, cutoff)
        if months:
            out.append("\n## Departure month\n")
            peak = max(n for _, n in months)
            for m, n in months:
                out.append(f"  {MONTHS[m]:<4} {n:>4}  {'█' * max(1, round(20 * n / peak))}")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=None, help="window (default: all time)")
    ap.add_argument("--limit", type=int, default=15, help="rows per list")
    args = ap.parse_args()
    print(asyncio.run(report(args.days, args.limit)))


if __name__ == "__main__":
    main()
