"""Trip planning endpoints.

POST /api/trips runs the full pipeline — route (cached) → corridor chargers
(cached) → speed sweep (CPU, off the event loop) — persists the result, and
returns it. The Trip row's UUID is the permalink token; planning always
creates the permalink, there is no separate save step.
"""

import asyncio
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    PlanRequest,
    PlanResult,
    SpeedResultOut,
    StopOut,
    TimelinePoint,
    TripOut,
    VehicleOut,
)
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter
from app.models import Trip, Vehicle
from app.services import chargers as chargers_svc
from app.services import routing
from app.services.geo import polyline_encode
from app.services.simulator import (
    SimParams,
    SpeedResult,
    VehicleParams,
    optimum,
    sweep,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Guard against degenerate/abusive sweeps: at most this many simulated speeds.
MAX_SWEEP_POINTS = 40


def _vehicle_out(v: Vehicle) -> VehicleOut:
    return VehicleOut(
        id=str(v.id),
        slug=v.slug,
        make=v.make,
        model=v.model,
        variant=v.variant,
        usable_kwh=v.usable_kwh,
        consumption=v.consumption,
        charge_curve=v.charge_curve,
        max_dc_kw=v.max_dc_kw,
        source_note=v.source_note,
    )


def _result_out(r: SpeedResult) -> SpeedResultOut:
    if not r.feasible:
        return SpeedResultOut(speed_kph=r.speed_kph, feasible=False)
    return SpeedResultOut(
        speed_kph=r.speed_kph,
        feasible=True,
        total_min=round(r.total_min, 1),
        drive_min=round(r.drive_min, 1),
        charge_min=round(r.charge_min, 1),
        n_stops=r.n_stops,
        stops=[
            StopOut(
                charger_id=s.charger_id,
                name=s.name,
                operator=s.operator,
                lat=round(s.lat, 5),
                lon=round(s.lon, 5),
                power_kw=s.power_kw,
                offset_m=round(s.offset_m),
                arrive_min=round(s.arrive_min, 1),
                arrive_soc=round(s.arrive_soc, 1),
                depart_soc=round(s.depart_soc, 1),
                charge_min=round(s.charge_min, 1),
            )
            for s in r.stops
        ],
        timeline=[
            TimelinePoint(t_min=round(t, 1), dist_m=round(d), soc=round(soc, 1))
            for t, d, soc in r.timeline
        ],
    )


@router.post("", response_model=TripOut)
@limiter.limit(settings.RATE_LIMIT_PLAN)
async def plan_trip(
    request: Request, plan: PlanRequest, db: AsyncSession = Depends(get_db)
) -> TripOut:
    if plan.speed_max < plan.speed_min:
        raise HTTPException(status_code=422, detail="speed_max must be ≥ speed_min.")
    n_points = int((plan.speed_max - plan.speed_min) / plan.speed_step) + 1
    if n_points > MAX_SWEEP_POINTS:
        raise HTTPException(
            status_code=422, detail=f"Speed sweep too large (max {MAX_SWEEP_POINTS} points)."
        )
    if plan.depart_soc < plan.target_soc:
        raise HTTPException(
            status_code=422, detail="Departure charge must be at least the arrival target."
        )

    try:
        vehicle_uuid = uuid.UUID(plan.vehicle_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid vehicle id.")
    vehicle = await db.get(Vehicle, vehicle_uuid)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Unknown vehicle.")

    route = await routing.get_route(
        db, plan.origin.lat, plan.origin.lon, plan.dest.lat, plan.dest.lon
    )
    charger_nodes = await chargers_svc.chargers_for_route(db, route)

    veh = VehicleParams.from_vehicle(vehicle)
    params = SimParams(
        depart_soc=plan.depart_soc,
        target_soc=plan.target_soc,
        consumption_factor=plan.conditions_factor,
    )
    speeds = [
        plan.speed_min + i * plan.speed_step
        for i in range(n_points)
    ]

    # CPU-bound sweep off the event loop (the template widens the executor).
    results = await asyncio.to_thread(
        sweep, route.segments, charger_nodes, veh, speeds, params
    )
    best = optimum(results)
    if best is None:
        raise HTTPException(
            status_code=422,
            detail="No feasible plan at any speed — no fast chargers found along "
            "this route for the selected car and charge settings.",
        )

    result = PlanResult(
        polyline=polyline_encode(route.geometry.coords),
        total_dist_m=round(route.total_dist_m),
        speeds=[_result_out(r) for r in results],
        optimum_speed=best.speed_kph,
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

    logger.info(
        "planned trip %s: %.0f km, optimum %.0f kph, %d speeds",
        trip.id, route.total_dist_m / 1000, best.speed_kph, len(speeds),
    )
    return TripOut(
        id=str(trip.id),
        request=plan,
        result=result,
        created_at=trip.created_at,
    )


@router.get("/{trip_id}", response_model=TripOut)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def get_trip(
    request: Request, trip_id: str, db: AsyncSession = Depends(get_db)
) -> TripOut:
    try:
        tid = uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found.")
    trip = await db.get(Trip, tid)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")
    return TripOut(
        id=str(trip.id),
        request=PlanRequest.model_validate(trip.request),
        result=PlanResult.model_validate(trip.result),
        created_at=trip.created_at,
    )
