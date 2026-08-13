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
from app.core.failures import PlanError, reason_of
from app.core.rate_limit import limiter
from app.core.visitor import request_client_id, request_owner_hash, request_visitor
from app.models import AppEvent, Profile, Trip, TripEvent, TripRun, TripStat, Vehicle
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
from app.services.trip_stats import build_trip_stat

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

# Below this, an infeasible plan with no candidate chargers means the hop is
# too short to put a stop into, not that the corridor has none. See the
# scaled `chargers.stop_window`.
SHORT_ROUTE_M = 60_000.0


def _vehicle_out(v: Vehicle) -> VehicleOut:
    return VehicleOut(
        id=str(v.id),
        slug=v.slug,
        make=v.make,
        model=v.model,
        variant=v.variant,
        nameplate_slug=v.nameplate_slug,
        usable_kwh=v.usable_kwh,
        consumption=v.consumption,
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


async def _record_plan_failure(db: AsyncSession, request: Request, reason: str) -> None:
    """Write the `plan_failed` row, server-side, with the reason attached.

    Recorded HERE rather than in the browser's catch block for two reasons. The
    server is the only party that knows *why* — the client sees a message
    written for a human. And an event fired from a page can be blocked, so the
    old client-side count was measured against a different population than the
    trips table it was compared with.

    Rolls back first: the request is failing, whatever partial work the session
    is holding is being discarded anyway, and a session left in a failed state
    cannot write this row.

    Never raises. A missing telemetry row is worth less than a clean error
    response, and turning a 422 into a 500 because the counter broke would be
    the counter causing the outage it exists to warn about.
    """
    try:
        await db.rollback()
        db.add(
            AppEvent(
                name="plan_failed",
                # No path: this is an API call, not a page, and `KNOWN_PATHS`
                # is an allowlist of pages. Leaving it null keeps the page
                # breakdown honest instead of inventing a "/api/trips" entry.
                path=None,
                visitor=request_visitor(request),
                client_id=request_client_id(request),
                reason=reason,
            )
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — never turn a handled failure into a 500
        logger.warning("could not record plan failure (%s)", reason, exc_info=True)


@router.post("", response_model=TripOut)
@limiter.limit(settings.RATE_LIMIT_PLAN)
async def plan_trip(
    request: Request, plan: PlanRequest, db: AsyncSession = Depends(get_db)
) -> TripOut:
    """Plan a trip, and count it if it fails.

    A thin wrapper so every exit path through `_plan` is recorded from one
    place — eight raise sites each remembering to log for themselves is eight
    chances to forget, and the one that gets forgotten is always the new one.
    """
    try:
        return await _plan(request, plan, db)
    except HTTPException as exc:
        await _record_plan_failure(db, request, reason_of(exc))
        raise
    except Exception:
        # An unhandled error is still a failed plan, and it is the kind most
        # worth noticing — `server_error` climbing is the signal that something
        # broke in a way nobody anticipated.
        await _record_plan_failure(db, request, "server_error")
        raise


async def _plan(request: Request, plan: PlanRequest, db: AsyncSession) -> TripOut:
    if plan.speed_max < plan.speed_min:
        raise PlanError("bad_speed_range", 422, "speed_max must be ≥ speed_min.")
    n_points = int((plan.speed_max - plan.speed_min) / plan.speed_step) + 1
    if n_points > MAX_SWEEP_POINTS:
        raise PlanError(
            "sweep_too_large", 422, f"Speed sweep too large (max {MAX_SWEEP_POINTS} points)."
        )
    if plan.depart_soc < plan.target_soc:
        raise PlanError(
            "bad_soc", 422, "Departure charge must be at least the arrival target."
        )

    try:
        vehicle_uuid = uuid.UUID(plan.vehicle_id)
    except ValueError:
        raise PlanError("bad_vehicle", 422, "Invalid vehicle id.")
    vehicle = await db.get(Vehicle, vehicle_uuid)
    if vehicle is None:
        raise PlanError("unknown_vehicle", 404, "Unknown vehicle.")

    route = await routing.get_route(
        db, plan.origin.lat, plan.origin.lon, plan.dest.lat, plan.dest.lon
    )
    if route.total_dist_m > MAX_ROUTE_M:
        raise PlanError(
            "route_too_long",
            422,
            f"That route is {route.total_dist_m / 1000:.0f} km. This plans "
            f"trips up to {MAX_ROUTE_M / 1000:.0f} km.",
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
            raise PlanError(
                "corridor_cold",
                503,
                "Still gathering charger data for this route. "
                "Give it a moment and try again.",
            )
        # A short hop that cannot be completed is not a charging desert, and
        # saying so was actively misleading — the first version of this told
        # people in the Netherlands there were no fast chargers on a 26 km
        # drive. On a route this short the binding constraint is always the
        # charge you left with: either nothing is far enough along to stop at,
        # or you cannot reach what is. Both mean "charge before you go", so
        # deliberately NOT conditioned on whether candidates were found.
        if route.total_dist_m < SHORT_ROUTE_M:
            raise PlanError(
                "route_too_short",
                422,
                f"That route is only {route.total_dist_m / 1000:.0f} km, and no plan "
                f"works starting from {plan.depart_soc:.0f}%. On a hop this short "
                "there is no room to charge on the way, so you would need to set "
                "off with more.",
            )
        # Same reason either way (both are a charging gap, and splitting them
        # would split the number that measures it), but not the same sentence:
        # "no fast chargers found" is simply false when we found some and the
        # car could not reach them.
        raise PlanError(
            "no_chargers",
            422,
            "No feasible plan at any speed: no fast chargers found along this "
            "route for the selected car and charge settings."
            if not charger_nodes
            else "No feasible plan at any speed: the fast chargers on this route "
            "are too far apart for this car at this starting charge.",
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

    request_json = plan.model_dump(mode="json")
    result_json = result.model_dump(mode="json")

    # Publish this trip under the planner's username, if they have one.
    #
    # Only when the secret ALREADY belongs to a claimed name: claiming is the
    # consent moment, so an unclaimed secret must leave no trace on any row and
    # trips planned before the claim must not be swept up by it. That is why
    # this is a lookup rather than an unconditional write.
    #
    # A header, never `PlanRequest` — that model is persisted whole into
    # `Trip.request` alongside exact coordinates, and the same reasoning as the
    # client id below applies with more force here, because this identifier is
    # attached to a name a person chose.
    owner = request_owner_hash(request)
    if owner is not None:
        claimed = (
            await db.execute(select(Profile.id).where(Profile.owner_hash == owner))
        ).scalar_one_or_none()
        if claimed is None:
            owner = None

    trip = Trip(
        vehicle_id=vehicle.id,
        request=request_json,
        result=result_json,
        result_version=1,
        owner_hash=owner,
        created_at=datetime.utcnow(),
    )
    db.add(trip)
    # Flush rather than commit: the trip's id is a Python-side default assigned
    # here, and the stat needs it. One commit still covers both, so a trip can
    # never exist without its stat row.
    await db.flush()

    # Derived from what was just stored, never from the local variables — the
    # backfill reads those same JSON columns, and one shared input is what
    # stops live rows and historical rows from meaning subtly different things.
    stat = build_trip_stat(
        trip_id=trip.id,
        vehicle_id=vehicle.id,
        request=request_json,
        result=result_json,
        created_at=trip.created_at,
        # Deliberately NOT part of `PlanRequest`: that model is persisted whole
        # into `Trip.request`, so a field there would write the person onto the
        # row holding their exact coordinates — the one join this design exists
        # to avoid. A header reaches the coarsened row and nothing else.
        client_id=request_client_id(request),
    )
    if stat is not None:
        db.add(stat)

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

    The derived `TripStat` goes with it, for the same reason and one more. The
    privacy page promises this removes "the whole record", and names the
    coarsened summary in that list — so the moment a second table derives from
    trips, that sentence is only true if the delete follows it. A coarsened row
    is still a row about a journey somebody asked us to forget, and a derived
    table nobody deletes from is exactly how a promise becomes a policy that
    isn't kept.
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
    await db.execute(delete(TripStat).where(TripStat.trip_id == tid))
    await db.delete(trip)
    await db.commit()
    logger.info("deleted trip %s… and %d drive(s)", str(tid)[:8], len(run_ids))
    return Response(status_code=204)
