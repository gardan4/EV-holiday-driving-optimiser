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

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import delete, select
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
from app.models import Trip, TripEvent, TripRun, Vehicle
from app.services import chargers as chargers_svc
from app.services import routing
from app.services.geo import polyline_encode
from app.services.simulator import (
    aux_kw_for_temp,
    charge_power_factor_for_temp,
    consumption_factor_for_temp,
    SimParams,
    SpeedResult,
    VehicleParams,
    optimum,
    payload_extra_kg,
    sweep,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Guard against degenerate/abusive sweeps: at most this many simulated speeds.
MAX_SWEEP_POINTS = 40

# Longest route we will plan. Measured on Lisbon → Helsinki (4,379 km), which
# is what the old 2000 km cap was drawn to exclude: the sweep itself costs
# nothing (the DP is bounded by MAX_NODES, not by distance), the stored result
# is ~0.9 MB rather than the several MB feared, and the corridor needs ~172 OCM
# tiles — which one request now covers, because they are fetched concurrently.
# 5000 km is about the longest drive any single landmass offers.
MAX_ROUTE_M = 5_000_000.0


def _vehicle_out(v: Vehicle) -> VehicleOut:
    return VehicleOut(
        id=str(v.id),
        slug=v.slug,
        make=v.make,
        model=v.model,
        variant=v.variant,
        usable_kwh=v.usable_kwh,
        consumption=v.consumption,
        nameplate_slug=v.nameplate_slug,
        charge_curve=v.charge_curve,
        max_dc_kw=v.max_dc_kw,
        mass_kg=v.mass_kg,
        top_speed_kph=v.top_speed_kph,
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
        rest_min=round(r.rest_min, 1),
        energy_kwh=round(r.energy_kwh, 1),
        cost_eur=round(r.cost_eur, 2),
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
    if route.total_dist_m > MAX_ROUTE_M:
        raise HTTPException(
            status_code=422,
            detail=(
                f"That route is {route.total_dist_m / 1000:.0f} km. This plans "
                f"trips up to {MAX_ROUTE_M / 1000:.0f} km."
            ),
        )
    charger_nodes = await chargers_svc.chargers_for_route(db, route)

    veh = VehicleParams.from_vehicle(vehicle)
    params = SimParams(
        depart_soc=plan.depart_soc,
        target_soc=plan.target_soc,
        consumption_factor=(
            consumption_factor_for_temp(plan.temperature_c)
            if plan.temperature_c is not None
            else plan.conditions_factor
        ),
        charge_power_factor=(
            charge_power_factor_for_temp(plan.temperature_c)
            if plan.temperature_c is not None
            else plan.charge_power_factor
        ),
        # Only a real temperature implies a conditioning load; the legacy
        # conditions_factor path is a bare Wh/km multiplier and carries none.
        aux_kw=(
            aux_kw_for_temp(plan.temperature_c) if plan.temperature_c is not None else 0.0
        ),
        extra_mass_kg=payload_extra_kg(plan.occupants, plan.luggage_kg),
        autobahn_open_share=plan.autobahn_open_share,
        default_country_cap_kph=plan.motorway_cap_kph,
        ignore_speed_limits=plan.ignore_speed_limits,
        over_cap_kph=plan.over_cap_kph,
        over_freeflow_factor=plan.over_freeflow_factor,
        site_power_factor=plan.site_power_factor,
        queue_min=plan.queue_min,
        stop_overhead_min=plan.stop_overhead_min,
        rest_interval_min=plan.rest_interval_min,
        rest_min=plan.rest_min,
        price_per_kwh=plan.price_per_kwh,
    )
    # Never simulate past what the car can actually do — the Born tops out at
    # 160, so a "175 is optimal" answer would be fiction.
    speeds = [
        s
        for s in (plan.speed_min + i * plan.speed_step for i in range(n_points))
        if s <= vehicle.top_speed_kph
    ]
    if not speeds:
        speeds = [min(plan.speed_min, vehicle.top_speed_kph)]

    # CPU-bound sweep off the event loop (the template widens the executor).
    results = await asyncio.to_thread(
        sweep, route.segments, charger_nodes, veh, speeds, params
    )
    best = optimum(results)
    if best is None:
        # A corridor nobody has planned before needs more charger tiles than one
        # request is allowed to fetch, so the first attempt can see only part of
        # it. Saying "no chargers exist" there is simply false — and it became
        # the common case the moment routes stopped being confined to Europe,
        # where the tiles were already warm.
        if chargers_svc.corridor_incomplete.get():
            raise HTTPException(
                status_code=503,
                detail="Still gathering charger data for this route — "
                "give it a moment and try again.",
            )
        raise HTTPException(
            status_code=422,
            detail="No feasible plan at any speed — no fast chargers found along "
            "this route for the selected car and charge settings.",
        )

    # If the fastest plan is the fastest speed we were allowed to simulate, the
    # true optimum probably lies beyond the car's limiter — say so rather than
    # implying the curve bottomed out.
    feasible = [r for r in results if r.feasible]
    at_top = bool(
        feasible
        and best.speed_kph == max(r.speed_kph for r in feasible)
        and best.speed_kph >= vehicle.top_speed_kph - 1e-6
    )
    result = PlanResult(
        polyline=polyline_encode(route.geometry.coords),
        total_dist_m=round(route.total_dist_m),
        speeds=[_result_out(r) for r in results],
        optimum_speed=best.speed_kph,
        vehicle=_vehicle_out(vehicle),
        climb_m=round(sum(s.climb_m for s in route.segments)),
        optimum_at_top_speed=at_top,
        # Distinct, in the order you drive through them. Unidentified stretches
        # ("") are dropped: the list is used to say which countries' real limits
        # applied, and "" is precisely the case where none did.
        countries=list(dict.fromkeys(s.country for s in route.segments if s.country)),
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

    # Prefix only. The trip id is the capability that opens the trip, and the
    # trip holds an origin and destination one of which is usually home — a full
    # id in the log is a replayable link to somebody's address for anyone with
    # log access. `runs.py` already logs this way.
    logger.info(
        "planned trip %s…: %.0f km, optimum %.0f kph, %d speeds",
        str(trip.id)[:8], route.total_dist_m / 1000, best.speed_kph, len(speeds),
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


@router.delete("/{trip_id}", status_code=204)
@limiter.limit(settings.RATE_LIMIT_DEFAULT)
async def delete_trip(
    request: Request, trip_id: str, db: AsyncSession = Depends(get_db)
) -> Response:
    """Erase a trip and everything recorded against it.

    With no accounts there is nobody to look up, so for a long time the only
    honest answer to "please delete my data" was to ask someone to do it by
    hand. The link is the only key this app has, so the link is what authorises
    the deletion — the same capability that lets you read the trip lets you
    destroy it, which is the right trade for data nobody else can identify.

    Deliberately deletes the location trail too: a trip's drives are the most
    sensitive thing here, and leaving them behind after the trip is gone would
    orphan a GPS trace with no way left to reach it.
    """
    try:
        tid = uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found.")
    trip = await db.get(Trip, tid)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")

    run_ids = (
        (await db.execute(select(TripRun.id).where(TripRun.trip_id == tid)))
        .scalars()
        .all()
    )
    if run_ids:
        # Events first: the FK has no cascade, by design.
        await db.execute(delete(TripEvent).where(TripEvent.run_id.in_(run_ids)))
        await db.execute(delete(TripRun).where(TripRun.id.in_(run_ids)))
    await db.delete(trip)
    await db.commit()
    logger.info("deleted trip %s… and %d drive(s)", str(tid)[:8], len(run_ids))
    return Response(status_code=204)
