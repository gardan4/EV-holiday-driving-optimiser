"""Live journey endpoints — following a trip while it is being driven.

Two capabilities, and keeping them apart is the whole security model:

* the **trip id** is a public, read-only share link. Anyone holding it can
  watch the drive through `GET /api/trips/{trip_id}/live`.
* the **run id** is the driver's write token. It is returned exactly once, by
  run creation, appears in no read response, and never reaches a watcher.

There is no auth in this app by design, so a capability URL is the mechanism
available; the job here is to make sure the read capability cannot be escalated
into a write one. `test_live_api.py` pins that with a test that greps the
run id out of every read response.

The ping path is deliberately cheap — one route profile and two lerps, no
sweep — so it stays on the event loop. Only `/replan` does real CPU work, and
only when the driver asks for it.
"""

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    BenchmarkOut,
    LiveOut,
    LiveStateOut,
    PingRequest,
    PlanRequest,
    PlanResult,
    ReplanOut,
    ReplanRequest,
    ReviewOut,
    ReviewStopOut,
    RunSummaryOut,
    SocReadingRequest,
    StartRunOut,
    StartRunRequest,
    TimelinePoint,
)
from app.api.trips import MAX_SWEEP_POINTS, _result_out
from app.core.config import settings
from app.core.database import get_db
from app.core.rate_limit import limiter, run_key
from app.models import Trip, TripEvent, TripRun, Vehicle
from app.services import chargers as chargers_svc
from app.services import live, routing
from app.services.simulator import (
    SimParams,
    VehicleParams,
    optimum,
    payload_extra_kg,
    slice_route,
    sweep_slice,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# A breadcrumb goes in the log at most this often; pings in between only update
# the run's current state. A nine-hour drive is ~110 breadcrumbs.
BREADCRUMB_MIN = 5.0

# No word from the driving phone for this long and the drive is treated as
# over, so the trip can be driven again. Generous on purpose: a long lunch, a
# tunnel, or a phone charging in a bag are all normal.
ABANDON_AFTER_MIN = 90.0

# How quiet an active drive has to be before someone else may replace it.
# Pings land every ~25 s, so a few minutes of silence means that phone is no
# longer reporting — while a drive that IS reporting belongs to whoever is in
# the car, not to whoever the share link reached.
TAKEOVER_QUIET_MIN = 5.0


def run_ref_for(run_id: uuid.UUID) -> str:
    """A stable public handle for a run that cannot be turned back into the
    write token."""
    return hashlib.sha256(run_id.bytes).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


async def _load_run(db: AsyncSession, run_id: str) -> TripRun:
    """Resolve a write token. 404 — never 403 — for anything that isn't one,
    so a probe can't distinguish 'wrong token' from 'no such run'."""
    try:
        rid = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Run not found.")
    run = await db.get(TripRun, rid)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


async def _latest_run(db: AsyncSession, trip_id: str) -> TripRun | None:
    """The most recent drive of this trip, whatever state it ended in.

    What `GET /live` shows: an active drive if there is one, otherwise the
    drive that just finished, so the page doesn't blank out the moment someone
    arrives.
    """
    try:
        tid = uuid.UUID(trip_id)
    except ValueError:
        return None
    rows = await db.execute(
        select(TripRun)
        .where(TripRun.trip_id == tid)
        .order_by(TripRun.started_at.desc())
        .limit(1)
    )
    return rows.scalars().first()


async def _active_run(db: AsyncSession, trip_id: str) -> TripRun | None:
    """The drive currently under way, if any.

    Filtered on status — without it, a second press of "start" silently strands
    the first run: it stays `active`, keeps accepting pings from a token that
    still works, and becomes invisible to every read endpoint.

    Silence also counts as over. A phone that runs flat or loses signal for
    good would otherwise leave the trip "being driven" for ever, and the guard
    in `start_run` would refuse to let that same driver start again. Judged at
    read time rather than by a sweeper because this app runs no background
    loops, and a drive nobody has reported in an hour is not in progress.
    """
    try:
        tid = uuid.UUID(trip_id)
    except ValueError:
        return None
    cutoff = datetime.utcnow() - timedelta(minutes=ABANDON_AFTER_MIN)
    rows = await db.execute(
        select(TripRun)
        .where(
            TripRun.trip_id == tid,
            TripRun.status == "active",
            TripRun.last_seen_at >= cutoff,
        )
        .order_by(TripRun.started_at.desc())
        .limit(1)
    )
    return rows.scalars().first()


async def _run_by_ref(db: AsyncSession, trip_id: str, run_ref: str) -> TripRun | None:
    """Resolve a public run handle against EVERY drive of this trip.

    Not just the latest one: a review permalink has to keep working after the
    trip is driven again, or every drive but the most recent 404s.
    """
    try:
        tid = uuid.UUID(trip_id)
    except ValueError:
        return None
    rows = await db.execute(
        select(TripRun).where(TripRun.trip_id == tid).order_by(TripRun.started_at.desc())
    )
    for run in rows.scalars().all():
        if run_ref_for(run.id) == run_ref:
            return run
    return None


def _planned_result(trip: Trip, speed_kph: float) -> dict | None:
    for s in trip.result.get("speeds", []):
        if abs(s.get("speed_kph", -1) - speed_kph) < 1e-6:
            return s
    return None


def _timeline_tuples(speed: dict) -> list[tuple[float, float, float]]:
    return [(t["t_min"], t["dist_m"], t["soc"]) for t in speed.get("timeline", [])]


def _sim_params(req: PlanRequest, run_factor: float = 1.0) -> SimParams:
    """The plan's own assumptions, with the calibration the drive has learned.

    Mirrors `trips.plan_trip` — the revised plan has to rest on the same
    footing as the one it is replacing, or the comparison is meaningless.
    """
    from app.services.simulator import (
        aux_kw_for_temp,
        charge_power_factor_for_temp,
        consumption_factor_for_temp,
    )

    base = (
        consumption_factor_for_temp(req.temperature_c)
        if req.temperature_c is not None
        else req.conditions_factor
    )
    return SimParams(
        target_soc=req.target_soc,
        consumption_factor=base * run_factor,
        charge_power_factor=(
            charge_power_factor_for_temp(req.temperature_c)
            if req.temperature_c is not None
            else req.charge_power_factor
        ),
        aux_kw=(
            aux_kw_for_temp(req.temperature_c) if req.temperature_c is not None else 0.0
        ),
        extra_mass_kg=payload_extra_kg(req.occupants, req.luggage_kg),
        autobahn_open_share=req.autobahn_open_share,
        over_cap_kph=req.over_cap_kph,
        over_freeflow_factor=req.over_freeflow_factor,
        site_power_factor=req.site_power_factor,
        queue_min=req.queue_min,
        stop_overhead_min=req.stop_overhead_min,
        rest_interval_min=req.rest_interval_min,
        rest_min=req.rest_min,
        price_per_kwh=req.price_per_kwh,
    )


def _state_out(run: TripRun, trip: Trip, usable_kwh: float) -> LiveStateOut:
    st = run.state
    soc = live.current_soc(st, usable_kwh)
    speed = _planned_result(trip, run.planned_speed_kph)
    timeline = _timeline_tuples(speed) if speed else []

    delta = live.schedule_delta_min(timeline, st["offset_m"], st["at_min"])
    planned_soc = live.plan_soc_at(timeline, st["offset_m"]) if timeline else soc
    flag, reasons = live.needs_replan(
        delta_min=delta,
        soc_now=soc,
        soc_planned=planned_soc,
        stale=bool(st.get("stale")),
    )
    return LiveStateOut(
        at_min=round(st["at_min"], 1),
        offset_m=round(st["offset_m"]),
        lat=round(st["lat"], 5),
        lon=round(st["lon"], 5),
        soc=round(soc, 1),
        soc_is_measured=bool(st.get("soc_is_measured")),
        soc_uncertainty_pct=round(live.soc_uncertainty(st), 1),
        anchor_age_min=round(max(0.0, st["at_min"] - st["anchor_at_min"]), 1),
        off_route_m=round(st.get("off_route_m", 0.0)),
        stale=bool(st.get("stale")),
        at_charger_id=st.get("at_charger_id"),
        run_factor=round(st.get("run_factor", 1.0), 3),
        ahead_behind_min=round(delta, 1),
        soc_vs_plan=round(soc - planned_soc, 1),
        needs_replan=flag,
        replan_reasons=reasons,
        status=run.status,
    )


async def _trip_and_vehicle(db: AsyncSession, run: TripRun) -> tuple[Trip, Vehicle]:
    trip = await db.get(Trip, run.trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    vehicle = await db.get(Vehicle, trip.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return trip, vehicle


def _snapshot_route(route: routing.RouteData, chargers: list) -> dict:
    return {
        "segments": [
            {
                "dist_m": s.dist_m,
                "freeflow_kph": s.freeflow_kph,
                "country": s.country,
                "climb_m": s.climb_m,
                "descent_m": s.descent_m,
            }
            for s in route.segments
        ],
        "chargers": [
            {
                "charger_id": c.charger_id,
                "name": c.name,
                "offset_m": c.offset_m,
                "power_kw": c.power_kw,
                "detour_min": c.detour_min,
                "operator": c.operator,
                "lat": c.lat,
                "lon": c.lon,
            }
            for c in chargers
        ],
        "seg_geom_offsets": route.seg_geom_offsets,
        "coords": route.geometry.coords,
        "total_dist_m": sum(s.dist_m for s in route.segments),
    }


def _geometry(snapshot: dict):
    from app.services.geo import RouteGeometry

    return RouteGeometry([(la, lo) for la, lo in snapshot["coords"]])


def _geom_to_segment(snapshot: dict, geom_m: float) -> float:
    """Map a snapped GPS distance onto the axis the simulator measures in."""
    seg_offsets = snapshot.get("seg_geom_offsets") or []
    if not seg_offsets:
        return geom_m
    cum = [0.0]
    for s in snapshot["segments"]:
        cum.append(cum[-1] + s["dist_m"])
    return routing._lerp_axis(seg_offsets, cum, geom_m)


# ---------------------------------------------------------------------------
# Starting a drive
# ---------------------------------------------------------------------------


@router.post("/trips/{trip_id}/runs", response_model=StartRunOut)
@limiter.limit(settings.RATE_LIMIT_RUN_START)
async def start_run(
    request: Request,
    trip_id: str,
    body: StartRunRequest,
    db: AsyncSession = Depends(get_db),
) -> StartRunOut:
    try:
        tid = uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found.")
    trip = await db.get(Trip, tid)
    if trip is None:
        raise HTTPException(status_code=404, detail="Trip not found.")

    if _planned_result(trip, body.planned_speed_kph) is None:
        raise HTTPException(
            status_code=422,
            detail="That speed was not simulated for this trip.",
        )

    # One drive at a time. Without this a second press — the laptop, then the
    # phone — leaves two live runs: the first keeps accepting pings from a
    # token that still works, while every reader follows the second. The
    # driver sees a plausible screen and their real drive is logged nowhere.
    existing = await _active_run(db, trip_id)
    if existing is not None:
        if not body.supersede:
            raise HTTPException(
                status_code=409,
                detail=(
                    "This trip is already being driven. Continue that drive, "
                    "or start again to replace it."
                ),
            )
        # `supersede` exists so a driver whose phone lost its token can get back
        # in — not so a passenger, or anyone the share link was forwarded to,
        # can end a drive that is happening right now. Superseding kills the
        # real driver's token (their pings 409 from here on) and hands every
        # watcher the newcomer's position as if it were the car's, so it has to
        # be limited to runs that have visibly stopped reporting.
        quiet_min = (datetime.utcnow() - existing.last_seen_at).total_seconds() / 60.0
        if quiet_min < TAKEOVER_QUIET_MIN:
            raise HTTPException(
                status_code=409,
                detail=(
                    "That drive is still sending its position. If it's yours, "
                    "close the other tab and try again in a few minutes."
                ),
            )
        existing.status = "superseded"
        existing.finished_at = datetime.utcnow()

    plan_req = PlanRequest.model_validate(trip.request)
    # Snapshot the route now: `Trip` keeps the plan but not the segments and
    # chargers behind it, and re-fetching them mid-drive risks a cache expiry
    # handing back a different road than the one being benchmarked against.
    route = await routing.get_route(
        db, plan_req.origin.lat, plan_req.origin.lon, plan_req.dest.lat, plan_req.dest.lon
    )
    charger_nodes = await chargers_svc.chargers_for_route(db, route)
    snapshot = _snapshot_route(route, charger_nodes)

    geom_off, off_route = route.geometry.project(body.lat, body.lon)
    offset_m = _geom_to_segment(snapshot, geom_off)

    state = live.new_state(
        offset_m=offset_m, soc=body.depart_soc, lat=body.lat, lon=body.lon, at_min=0.0
    )
    state["off_route_m"] = off_route

    now = datetime.utcnow()
    run = TripRun(
        trip_id=trip.id,
        status="active",
        started_at=now,
        last_seen_at=now,
        planned_speed_kph=body.planned_speed_kph,
        route_snapshot=snapshot,
        state=state,
        created_at=now,
    )
    db.add(run)
    await db.flush()
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="start", offset_m=offset_m,
            lat=body.lat, lon=body.lon, soc=body.depart_soc,
            payload={"planned_speed_kph": body.planned_speed_kph},
        )
    )
    await db.commit()
    await db.refresh(run)

    vehicle = await db.get(Vehicle, trip.vehicle_id)
    logger.info("live run started ref=%s trip=%s", run_ref_for(run.id), str(trip.id)[:8])
    return StartRunOut(
        run_id=str(run.id),
        run_ref=run_ref_for(run.id),
        trip_id=str(trip.id),
        state=_state_out(run, trip, vehicle.usable_kwh if vehicle else 58.0),
    )


# ---------------------------------------------------------------------------
# Driving
# ---------------------------------------------------------------------------


@router.post("/runs/{run_id}/ping", response_model=LiveStateOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_PING, key_func=run_key)
async def ping(
    request: Request,
    run_id: str,
    body: PingRequest,
    db: AsyncSession = Depends(get_db),
) -> LiveStateOut:
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    snapshot = run.route_snapshot
    geometry = _geometry(snapshot)
    geom_off, off_route = geometry.project(body.lat, body.lon)
    offset_m = _geom_to_segment(snapshot, geom_off)

    now = datetime.utcnow()
    at_min = (now - run.started_at).total_seconds() / 60.0
    if at_min < run.state["at_min"]:
        # A retry or a reordered request; replaying it would rewind the drive.
        return _state_out(run, trip, vehicle.usable_kwh)

    # A vague fix is worth recording but not worth moving the car for.
    if body.accuracy_m is not None and body.accuracy_m > live.MAX_ACCURACY_M:
        offset_m = run.state["offset_m"]

    speed = _planned_result(trip, run.planned_speed_kph) or {}
    stop_id, stop_kw = live.nearest_planned_stop(
        speed.get("stops", []), offset_m, body.lat, body.lon
    )

    plan_req = PlanRequest.model_validate(trip.request)
    params = _sim_params(plan_req, run.state.get("run_factor", 1.0))
    veh = VehicleParams.from_vehicle(vehicle)

    new_st = live.advance(
        run.state,
        segments=live.snapshot_segments(snapshot),
        veh=veh,
        p=params,
        offset_m=offset_m,
        lat=body.lat,
        lon=body.lon,
        at_min=at_min,
        off_route_m=off_route,
        moving_s=body.moving_s,
        stationary_s=body.stationary_s,
        stop_power_kw=stop_kw,
        at_charger_id=stop_id,
    )
    # Plain JSON columns don't track in-place edits — always reassign.
    run.state = new_st
    run.last_seen_at = now
    run.n_pings = run.n_pings + 1

    last_crumb = run.state.get("last_crumb_min", -BREADCRUMB_MIN)
    if at_min - last_crumb >= BREADCRUMB_MIN:
        run.state = {**new_st, "last_crumb_min": at_min}
        db.add(
            TripEvent(
                run_id=run.id, at=now, kind="breadcrumb", offset_m=offset_m,
                lat=body.lat, lon=body.lon,
                soc=live.current_soc(new_st, vehicle.usable_kwh),
            )
        )
    await db.commit()
    return _state_out(run, trip, vehicle.usable_kwh)


@router.post("/runs/{run_id}/soc", response_model=LiveStateOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_PING, key_func=run_key)
async def record_soc(
    request: Request,
    run_id: str,
    body: SocReadingRequest,
    db: AsyncSession = Depends(get_db),
) -> LiveStateOut:
    """The driver types in what the dashboard actually says.

    This both re-anchors the estimate and calibrates the consumption model
    against reality — see `live.apply_reading`.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    before = live.current_soc(run.state, vehicle.usable_kwh)
    new_st = live.apply_reading(run.state, body.soc, vehicle.usable_kwh)
    run.state = new_st
    run.n_soc_readings = run.n_soc_readings + 1
    now = datetime.utcnow()
    run.last_seen_at = now
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="soc_reading",
            offset_m=new_st["offset_m"], lat=new_st["lat"], lon=new_st["lon"],
            soc=body.soc,
            payload={
                "estimated": round(before, 1),
                "error_pct": new_st.get("last_error_pct"),
                "run_factor": new_st.get("run_factor"),
            },
        )
    )
    await db.commit()
    return _state_out(run, trip, vehicle.usable_kwh)


@router.post("/runs/{run_id}/replan", response_model=ReplanOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_REPLAN, key_func=run_key)
async def replan(
    request: Request,
    run_id: str,
    body: ReplanRequest,
    db: AsyncSession = Depends(get_db),
) -> ReplanOut:
    """Re-optimise the rest of the journey from where the car actually is.

    Slices the route at the current position and hands the remainder to the
    ordinary sweep — the DP is untouched. What makes the answer comparable to
    the original is carrying the slice's `index_offset` (so the same autobahn
    stretches count as open) and the break already earned.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    st = run.state
    if st.get("stale"):
        raise HTTPException(
            status_code=409,
            detail="You're off the planned route — rejoin it before re-planning.",
        )

    snapshot = run.route_snapshot
    plan_req = PlanRequest.model_validate(trip.request)
    veh = VehicleParams.from_vehicle(vehicle)
    soc_now = live.current_soc(st, vehicle.usable_kwh)

    sl = slice_route(
        live.snapshot_segments(snapshot),
        live.snapshot_chargers(snapshot),
        st["offset_m"],
    )
    if not sl.segments:
        raise HTTPException(status_code=409, detail="You're already there.")

    base = _sim_params(plan_req, st.get("run_factor", 1.0))
    params = SimParams(
        **{
            **{f: getattr(base, f) for f in base.__dataclass_fields__},
            "depart_soc": soc_now,
            # Carry the break debt: two hours already driven doesn't reset
            # because the plan did.
            "prior_drive_min": st["at_min"],
            "prior_rest_credit_min": st.get("rest_credit_min", 0.0),
        }
    )

    centre = run.planned_speed_kph
    if body.full_range:
        lo, hi = plan_req.speed_min, plan_req.speed_max
    else:
        lo, hi = centre - body.speed_span_kph, centre + body.speed_span_kph
    lo = max(60.0, lo)
    hi = min(hi, veh.top_speed_kph)
    n = max(1, int((hi - lo) / body.speed_step) + 1)
    # `POST /api/trips` caps its sweep; this path did not, so `full_range` with
    # the minimum step ran ~4x the work per request — on a token the caller
    # mints for themselves.
    if n > MAX_SWEEP_POINTS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many speeds to simulate (max {MAX_SWEEP_POINTS}).",
        )
    speeds = [s for s in (lo + i * body.speed_step for i in range(n)) if s <= hi]
    if not speeds:
        speeds = [min(centre, veh.top_speed_kph)]

    results = await asyncio.to_thread(sweep_slice, sl, veh, speeds, params)
    best = optimum(results)
    if best is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No plan reaches the destination from {soc_now:.0f}% here — "
                "charge at the nearest available point first."
            ),
        )

    original = _planned_result(trip, run.planned_speed_kph) or {}
    stops_ahead = [
        s for s in original.get("stops", []) if s["offset_m"] > st["offset_m"]
    ]
    bench = live.benchmark(
        original_speed_kph=run.planned_speed_kph,
        original_total_min=original.get("total_min") or 0.0,
        elapsed_min=st["at_min"],
        remaining=best,
        original_stops_remaining=len(stops_ahead),
    )

    out = ReplanOut(
        plan_version=run.plan_version + 1,
        offset_base_m=round(st["offset_m"]),
        elapsed_min=round(st["at_min"], 1),
        remaining=_result_out(best),
        speeds=[_result_out(r) for r in results],
        optimum_speed=best.speed_kph,
        benchmark=BenchmarkOut(**bench),
    )
    run.plan = out.model_dump(mode="json")
    run.plan_version = run.plan_version + 1
    run.n_replans = run.n_replans + 1
    now = datetime.utcnow()
    run.last_seen_at = now
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="replan", offset_m=st["offset_m"],
            lat=st["lat"], lon=st["lon"], soc=soc_now,
            payload={
                "speed_kph": best.speed_kph,
                "delta_min": round(bench["delta_min"], 1),
                "n_stops": best.n_stops,
            },
        )
    )
    await db.commit()
    return out


@router.post("/runs/{run_id}/finish", response_model=LiveStateOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_PING, key_func=run_key)
async def finish(
    request: Request, run_id: str, db: AsyncSession = Depends(get_db)
) -> LiveStateOut:
    run = await _load_run(db, run_id)
    trip, vehicle = await _trip_and_vehicle(db, run)
    if run.status == "active":
        now = datetime.utcnow()
        run.status = "finished"
        run.finished_at = now
        run.last_seen_at = now
        db.add(
            TripEvent(
                run_id=run.id, at=now, kind="finish",
                offset_m=run.state["offset_m"], lat=run.state["lat"],
                lon=run.state["lon"],
                soc=live.current_soc(run.state, vehicle.usable_kwh),
                payload={"at_min": run.state["at_min"]},
            )
        )
        await db.commit()
    return _state_out(run, trip, vehicle.usable_kwh)


# ---------------------------------------------------------------------------
# Watching (read-only — no run id in anything below)
# ---------------------------------------------------------------------------


@router.get("/trips/{trip_id}/live", response_model=LiveOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_READ)
async def get_live(
    request: Request,
    trip_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LiveOut:
    # Latest, not active: a drive that has just finished should keep rendering
    # for the people who were following it, rather than 404-ing the instant the
    # driver arrives.
    run = await _latest_run(db, trip_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No live drive for this trip.")
    trip, vehicle = await _trip_and_vehicle(db, run)
    # A 20-second-stale position is worse than none; make sure no cache keeps
    # one around.
    response.headers["Cache-Control"] = "no-store"

    crumbs = await db.execute(
        select(TripEvent)
        .where(TripEvent.run_id == run.id)
        .order_by(TripEvent.at)
    )
    trail = [
        TimelinePoint(
            t_min=round((e.at - run.started_at).total_seconds() / 60.0, 1),
            dist_m=round(e.offset_m),
            soc=round(e.soc, 1) if e.soc is not None else 0.0,
        )
        for e in crumbs.scalars().all()
        if e.kind in ("start", "breadcrumb", "soc_reading", "finish")
    ]
    # The EFFECTIVE status, not the stored one. `_active_run` already treats a
    # long silence as over, so reporting the raw column here would have a
    # watcher's page say "being driven right now" about a drive the rest of the
    # API has already given up on.
    silent_s = max(0.0, (datetime.utcnow() - run.last_seen_at).total_seconds())
    status = (
        "abandoned"
        if run.status == "active" and silent_s > ABANDON_AFTER_MIN * 60
        else run.status
    )
    return LiveOut(
        run_ref=run_ref_for(run.id),
        status=status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        planned_speed_kph=run.planned_speed_kph,
        state=_state_out(run, trip, vehicle.usable_kwh),
        plan=ReplanOut.model_validate(run.plan) if run.plan else None,
        trail=trail,
        seconds_since_ping=max(
            0.0, (datetime.utcnow() - run.last_seen_at).total_seconds()
        ),
    )


@router.get("/trips/{trip_id}/runs", response_model=list[RunSummaryOut])
@limiter.limit(settings.RATE_LIMIT_LIVE_READ)
async def list_runs(
    request: Request,
    trip_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> list[RunSummaryOut]:
    """Every drive of this trip, newest first — so the journey log stays
    findable from the trip itself rather than only from a link you kept."""
    try:
        tid = uuid.UUID(trip_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Trip not found.")
    response.headers["Cache-Control"] = "no-store"
    rows = await db.execute(
        select(TripRun)
        .where(TripRun.trip_id == tid)
        .order_by(TripRun.started_at.desc())
    )
    return [
        RunSummaryOut(
            run_ref=run_ref_for(r.id),
            status=r.status,
            started_at=r.started_at,
            finished_at=r.finished_at,
            planned_speed_kph=r.planned_speed_kph,
            distance_m=round(r.state.get("offset_m", 0.0)),
            n_replans=r.n_replans,
        )
        for r in rows.scalars().all()
    ]


@router.get("/trips/{trip_id}/runs/{run_ref}/review", response_model=ReviewOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_READ)
async def get_review(
    request: Request,
    trip_id: str,
    run_ref: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> ReviewOut:
    """What actually happened, against what was promised.

    Resolved across every drive of this trip, so a review link keeps working
    after the same trip is driven again.
    """
    run = await _run_by_ref(db, trip_id, run_ref)
    if run is None:
        raise HTTPException(status_code=404, detail="Drive not found.")
    trip, vehicle = await _trip_and_vehicle(db, run)
    response.headers["Cache-Control"] = "no-store"

    rows = await db.execute(
        select(TripEvent).where(TripEvent.run_id == run.id).order_by(TripEvent.at)
    )
    events = rows.scalars().all()
    planned = _planned_result(trip, run.planned_speed_kph) or {}

    def minutes(e: TripEvent) -> float:
        return (e.at - run.started_at).total_seconds() / 60.0

    stops: list[ReviewStopOut] = []
    for s in planned.get("stops", []):
        # Match the drive to the plan by proximity along the route.
        near = [
            e for e in events
            if e.kind in ("breadcrumb", "soc_reading")
            and abs(e.offset_m - s["offset_m"]) <= live.AT_CHARGER_M
        ]
        stops.append(
            ReviewStopOut(
                charger_id=s["charger_id"],
                name=s["name"],
                planned_arrive_min=s.get("arrive_min"),
                actual_arrive_min=round(minutes(near[0]), 1) if near else None,
                planned_charge_min=s.get("charge_min"),
                actual_charge_min=(
                    round(minutes(near[-1]) - minutes(near[0]), 1)
                    if len(near) > 1
                    else None
                ),
                planned_soc=s.get("arrive_soc"),
                actual_soc=near[0].soc if near else None,
            )
        )

    finished = next((e for e in events if e.kind == "finish"), None)
    actual_total = round(minutes(finished), 1) if finished else None
    planned_total = planned.get("total_min") or 0.0
    return ReviewOut(
        run_ref=run_ref,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        planned_speed_kph=run.planned_speed_kph,
        planned_total_min=planned_total,
        actual_total_min=actual_total,
        delta_min=(
            round(actual_total - planned_total, 1) if actual_total is not None else None
        ),
        n_replans=run.n_replans,
        n_soc_readings=run.n_soc_readings,
        final_run_factor=round(run.state.get("run_factor", 1.0), 3),
        stops=stops,
        trail=[
            TimelinePoint(
                t_min=round(minutes(e), 1),
                dist_m=round(e.offset_m),
                soc=round(e.soc, 1) if e.soc is not None else 0.0,
            )
            for e in events
            if e.kind in ("start", "breadcrumb", "soc_reading", "finish")
        ],
    )
