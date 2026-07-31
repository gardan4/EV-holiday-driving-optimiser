"""Dev helper: seed a realistic demo Trip (Utrecht → Innsbruck, Cupra Born 58)
without touching ORS/OCM — synthetic segments + chargers over a real-shaped
corridor polyline. Prints the permalink id.

    uv run python -m scripts.dev_seed_trip
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import select

from app.api.schemas import PlanRequest, PlacePoint
from app.api.trips import _result_out, _vehicle_out
from app.api.schemas import PlanResult
from app.core.database import AsyncSessionLocal
from app.models import Trip, Vehicle
from app.services.geo import RouteGeometry, polyline_encode
from app.services.simulator import (
    ChargerNode,
    RouteSegment,
    SimParams,
    VehicleParams,
    optimum,
    sweep,
)

# Real corridor waypoints (Utrecht → Innsbruck via Köln/Frankfurt/München).
WAYPOINTS = [
    (52.090, 5.121), (51.985, 5.898), (51.470, 6.850), (50.936, 6.960),
    (50.110, 8.682), (49.791, 9.953), (49.447, 11.077), (48.766, 11.425),
    (48.137, 11.575), (47.856, 12.128), (47.583, 12.169), (47.487, 12.060),
    (47.265, 11.393),
]

# (freeflow_kph, country) per leg between consecutive waypoints — approximates
# the real mix of derestricted autobahn, 120/130 zones, and slow ends.
LEG_PROFILE = [
    (95, "NL"), (110, "NL"), (110, "DE"), (108, "DE"), (125, "DE"), (125, "DE"),
    (110, "DE"), (125, "DE"), (108, "DE"), (110, "DE"), (112, "AT"), (100, "AT"),
]

CHARGER_SPECS = [  # (offset_km, power_kw, name, operator)
    (95, 300, "Ionity Kamen", "Ionity"),
    (170, 150, "Fastned Köln-Nord", "Fastned"),
    (250, 300, "Ionity Limburg", "Ionity"),
    (330, 150, "Allego Frankfurt Ost", "Allego"),
    (415, 300, "Ionity Würzburg", "Ionity"),
    (500, 150, "EnBW Nürnberg-Feucht", "EnBW"),
    (585, 300, "Ionity Ingolstadt", "Ionity"),
    (655, 150, "Fastned München-Nord", "Fastned"),
    (720, 300, "Ionity Irschenberg", "Ionity"),
    (780, 150, "SMATRICS Kufstein", "SMATRICS"),
]


async def main() -> None:
    geometry = RouteGeometry(WAYPOINTS)

    # Build segments per leg, matching the geometry's real leg lengths.
    segments: list[RouteSegment] = []
    for i, (ff, country) in enumerate(LEG_PROFILE):
        leg_m = geometry.cum_m[i + 1] - geometry.cum_m[i]
        # Chop legs into ~10 km segments so charging offsets interpolate nicely.
        n = max(1, int(leg_m // 10_000))
        for _ in range(n):
            segments.append(RouteSegment(dist_m=leg_m / n, freeflow_kph=ff, country=country))

    chargers = []
    for i, (km, kw, name, operator) in enumerate(CHARGER_SPECS):
        off = km * 1000.0
        if off >= geometry.total_m - 10_000:
            continue
        lat, lon = geometry.point_at(off)
        chargers.append(
            ChargerNode(
                charger_id=f"demo-{i}",
                name=name,
                operator=operator,
                lat=round(lat, 5),
                lon=round(lon, 5),
                offset_m=off,
                power_kw=float(kw),
                detour_min=2.0,
            )
        )

    async with AsyncSessionLocal() as db:
        vehicle = (
            await db.execute(select(Vehicle).where(Vehicle.slug == "cupra-born-58"))
        ).scalar_one()
        veh = VehicleParams.from_vehicle(vehicle)

        plan = PlanRequest(
            origin=PlacePoint(label="Utrecht, Netherlands", lat=52.09, lon=5.12),
            dest=PlacePoint(label="Innsbruck, Austria", lat=47.27, lon=11.39),
            vehicle_id=str(vehicle.id),
            departure_iso=datetime(2026, 8, 14, 22, 0),
            depart_soc=100.0,
            target_soc=10.0,
        )
        speeds = [90.0 + 5 * i for i in range(15)]
        results = sweep(segments, chargers, veh, speeds, SimParams())
        best = optimum(results)

        result = PlanResult(
            polyline=polyline_encode(geometry.coords),
            total_dist_m=round(geometry.total_m),
            speeds=[_result_out(r) for r in results],
            optimum_speed=best.speed_kph if best else None,
            vehicle=_vehicle_out(vehicle),
        )
        trip = Trip(
            vehicle_id=vehicle.id,
            request=plan.model_dump(mode="json"),
            result=result.model_dump(mode="json"),
            result_version=1,
            created_at=datetime.utcnow(),
        )
        db.add(trip)
        await db.commit()
        await db.refresh(trip)
        print(f"Demo trip seeded: http://localhost:3100/trip/{trip.id}")
        print(f"  distance {geometry.total_m / 1000:.0f} km, optimum {best.speed_kph:.0f} kph")


if __name__ == "__main__":
    asyncio.run(main())
