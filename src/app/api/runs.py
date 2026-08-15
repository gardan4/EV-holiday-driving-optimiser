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
from dataclasses import replace
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AlternativeOut,
    AlternativesOut,
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
from app.api.trips import MAX_SWEEP_POINTS, _result_out, sim_params_for
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

    Literally `trips.sim_params_for` — the revised plan has to rest on the same
    footing as the one it is replacing, or the comparison is meaningless. This
    used to be a second copy of that mapping, which is how it came to be
    missing `motorway_cap_kph` and `ignore_speed_limits`: they were added to
    the planner and nothing here failed, so a drive silently ran under
    different speed rules than the plan it was being benchmarked against —
    both in the replan and in `live.advance`, which prices every segment
    through these same params.
    """
    return sim_params_for(req, run_factor=run_factor)


def _benchmark_timeline(run: TripRun, trip: Trip) -> list[tuple[float, float, float]]:
    """The plan the drive is measured against — the REVISED one once it exists.

    Re-planning replaces the plan of record; everything that says "the plan"
    afterwards has to mean the new one. This compared against the original
    forever, and the cost was not subtle. The original expected charging stops
    the re-plan had removed, so `soc_vs_plan` stayed pinned at the shortfall
    that triggered the re-plan in the first place — one drive sat on "battery
    24% below what the plan expected by now" with a fresh plan on screen it was
    perfectly on track for. `needs_replan` reads the same number, so the amber
    "reality has drifted" panel could never clear, and the button inside it
    offered to re-plan a journey that had just been re-planned.

    The revision's timeline is relative to where and when it was made, so it is
    placed back on the drive's own axes before anything is read off it.
    """
    plan = run.plan or None
    if plan:
        tl = (plan.get("remaining") or {}).get("timeline") or []
        if tl:
            base_x = float(plan.get("offset_base_m") or 0.0)
            base_t = float(plan.get("elapsed_min") or 0.0)
            return [
                (t["t_min"] + base_t, t["dist_m"] + base_x, t["soc"]) for t in tl
            ]
    speed = _planned_result(trip, run.planned_speed_kph)
    return _timeline_tuples(speed) if speed else []


# How many other chargers to offer when the driver turns one down. Each one
# costs a full DP over the remaining route, and a list this long is already
# more than anybody reads at the wheel.
MAX_ALTERNATIVES = 3

# …and how many to offer from BEYOND the reserve. Chargers further on that the
# planner refuses only because you would arrive under `reserve_soc` are
# invisible today, and they are exactly the ones a driver asks about: "can I
# not just push on to the services?" That is a judgement about a road they can
# see, and the model has no business hiding the option — only naming the risk.
MAX_STRETCH = 2

# The floor those are computed against. Not zero: a plan that arrives on
# vapour is not an option, it is a breakdown with extra steps. Applied to the
# leg being driven and no other, so one accepted risk cannot become a journey
# planned on fumes — see `SimParams.first_leg_reserve_soc`.
STRETCH_FLOOR_SOC = 4.0


def _excluded(st: dict) -> list[str]:
    """Chargers this drive has turned down.

    Carried in `run.state` rather than a column of its own: it is JSON,
    `live.advance` and `live.apply_reading` both rebuild the state with
    `dict(state)` so unknown keys survive every ping, and a preference this
    small is not worth a migration on a table with live drives in it.
    """
    return [str(c) for c in (st.get("excluded_charger_ids") or [])]


def _merge_exclusions(run: TripRun, incoming: list[str]) -> set[str]:
    """The rejections in force, recorded on the run so they outlive the call.

    `run.state` is a plain `JSON` column, so the assignment is the whole point:
    an in-place `st[...] = ...` is invisible to SQLAlchemy and would be thrown
    away on commit — the rejection would appear to work and be forgotten by
    the next re-plan (see the note at the top of `models/__init__.py`).
    """
    merged = set(_excluded(run.state)) | {str(c) for c in incoming}
    run.state = {**run.state, "excluded_charger_ids": sorted(merged)}
    return merged


def _first_leg_floor(st: dict) -> float | None:
    """The reserve the driver accepted for the leg they are on, if any.

    Kept beside the exclusions and for the same reason: a decision that lasts
    one request is not a decision. Having chosen to push past the reserve to
    reach a particular charger, the next press of "Re-plan" would otherwise
    apply the full reserve again and refuse the stop they just picked.
    """
    v = st.get("first_leg_floor_soc")
    return float(v) if v is not None else None


def _set_first_leg_floor(run: TripRun, floor: float | None) -> float | None:
    """Record it, or leave the standing one alone when the caller says nothing.

    Assigned, never mutated: `run.state` is a plain `JSON` column (see
    `models/__init__.py`).
    """
    if floor is None:
        return _first_leg_floor(run.state)
    run.state = {**run.state, "first_leg_floor_soc": floor}
    return floor


def _resolve_hold_speed(run: TripRun, body: ReplanRequest) -> float | None:
    """The speed the driver intends to hold, and whether that intention changed.

    Three distinct requests, and the field alone cannot tell them apart:
    "re-plan, leave my speed alone" (the standing button, field absent),
    "hold 135" (a number), and "go back to whatever is fastest" (an explicit
    null). `model_fields_set` is what separates the last two — without it,
    every ordinary re-plan would look like a request to clear the choice, and
    the driver's decision would evaporate the next time they pressed a button
    that says nothing about speed.
    """
    if "hold_speed_kph" not in body.model_fields_set:
        v = run.state.get("hold_speed_kph")
        return float(v) if v is not None else None
    run.state = {**run.state, "hold_speed_kph": body.hold_speed_kph}
    return body.hold_speed_kph


def _remaining_slice(snapshot: dict, offset_m: float, exclude: set[str]):
    chargers = [
        c for c in live.snapshot_chargers(snapshot) if c.charger_id not in exclude
    ]
    return slice_route(live.snapshot_segments(snapshot), chargers, offset_m)


def _state_out(run: TripRun, trip: Trip, usable_kwh: float) -> LiveStateOut:
    st = run.state
    soc = live.current_soc(st, usable_kwh)
    timeline = _benchmark_timeline(run, trip)

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
                "n_points": c.n_points,
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
            detail="You're off the planned route. Rejoin it before re-planning.",
        )

    snapshot = run.route_snapshot
    plan_req = PlanRequest.model_validate(trip.request)
    veh = VehicleParams.from_vehicle(vehicle)
    soc_now = live.current_soc(st, vehicle.usable_kwh)

    # Chargers this driver has turned down stay turned down. Merged and kept
    # on the run, because a rejection that lasts until the next re-plan is not
    # a rejection — the standing "Re-plan" button would hand back the stop they
    # walked away from.
    excluded = _merge_exclusions(run, body.exclude_charger_ids)
    floor = _set_first_leg_floor(run, body.min_arrival_soc)
    sl = _remaining_slice(snapshot, st["offset_m"], excluded)
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
            "first_leg_reserve_soc": floor,
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

    # A held speed has to be IN the sweep, or the driver's own choice is the
    # one speed the answer cannot be. Clamped to what the car can do rather
    # than refused: "hold 200" in a car that stops at 160 is a request for
    # everything it has.
    hold = _resolve_hold_speed(run, body)
    if hold is not None:
        hold = min(max(hold, 60.0), veh.top_speed_kph)
        if all(abs(s - hold) > 1e-6 for s in speeds):
            speeds = sorted([*speeds, hold])

    results = await asyncio.to_thread(sweep_slice, sl, veh, speeds, params)
    fastest = optimum(results)
    if fastest is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"No plan reaches the destination from {soc_now:.0f}% here. "
                "Charge at the nearest available point first."
            ),
        )

    # The plan in force is the driver's speed when they have chosen one. It is
    # reported as `optimum_speed` because that is what every reader treats as
    # "the speed to hold"; `held_speed_kph` is how they can tell the difference
    # and `hold_costs_min` is what it costs.
    best = fastest
    if hold is not None:
        picked = next(
            (r for r in results if r.feasible and abs(r.speed_kph - hold) < 1e-6),
            None,
        )
        if picked is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"No plan reaches the destination holding {hold:.0f} km/h "
                    "from here. Try a lower speed."
                ),
            )
        best = picked

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
        held_speed_kph=hold,
        hold_costs_min=round(
            (best.total_min or 0.0) - (fastest.total_min or 0.0), 1
        ),
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


@router.post("/runs/{run_id}/alternatives", response_model=AlternativesOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_REPLAN, key_func=run_key)
async def alternatives(
    request: Request, run_id: str, db: AsyncSession = Depends(get_db)
) -> AlternativesOut:
    """Other chargers the drive could stop at instead of the next one.

    The planner optimises minutes, and minutes are not the only reason a stop
    is a bad stop: the fastest charger on the corridor can be a lay-by behind a
    warehouse with nowhere to eat, at eight in the evening, with children in
    the car. None of that is in OpenChargeMap and none of it is inferable, so
    this does not try to judge — it hands back the next few candidates with the
    only thing the model CAN say about them, which is what choosing each one
    costs in arrival time.

    Ranked by successive exclusion rather than by proximity: turn the current
    stop down and re-run the DP, and what comes back is genuinely the next best
    plan, with every later stop re-optimised around the swap. That is why
    `delta_min` is against the remaining JOURNEY and not the leg — swapping a
    charger moves everything after it, and a cost quoted per-leg would be
    wrong in the direction that flatters the swap.

    Read-only: nothing here changes the plan. `exclude_charger_ids` on each
    alternative is what to send to `replan` to actually take it.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    st = run.state
    if st.get("stale"):
        raise HTTPException(
            status_code=409,
            detail="You're off the planned route. Rejoin it before swapping a stop.",
        )

    snapshot = run.route_snapshot
    plan_req = PlanRequest.model_validate(trip.request)
    veh = VehicleParams.from_vehicle(vehicle)
    soc_now = live.current_soc(st, vehicle.usable_kwh)
    base = _sim_params(plan_req, st.get("run_factor", 1.0))
    params = SimParams(
        **{
            **{f: getattr(base, f) for f in base.__dataclass_fields__},
            "depart_soc": soc_now,
            "prior_drive_min": st["at_min"],
            "prior_rest_credit_min": st.get("rest_credit_min", 0.0),
        }
    )

    # One speed, not a sweep: this question is "which charger", and re-opening
    # "how fast" alongside it would price each candidate against a different
    # cruise. The speed in force is the revision's if there is one.
    speed = float((run.plan or {}).get("optimum_speed") or run.planned_speed_kph)
    speed = min(speed, veh.top_speed_kph)

    already = set(_excluded(st))
    turned_down: list[str] = []
    baseline_min: float | None = None
    current: AlternativeOut | None = None
    found: list[AlternativeOut] = []

    async def _next_best(exclude: set[str], p: SimParams):
        sl = _remaining_slice(snapshot, st["offset_m"], exclude)
        if not sl.segments:
            return None
        results = await asyncio.to_thread(sweep_slice, sl, veh, [speed], p)
        best = optimum(results)
        if best is None or not best.stops:
            return None
        return best

    def _as_alt(best, stop, *, below_reserve: bool) -> AlternativeOut:
        return AlternativeOut(
            charger_id=stop.charger_id,
            name=stop.name,
            operator=stop.operator,
            power_kw=stop.power_kw,
            n_points=stop.n_points,
            lat=round(stop.lat, 5),
            lon=round(stop.lon, 5),
            offset_m=round(stop.offset_m + st["offset_m"]),
            dist_from_here_m=round(stop.offset_m),
            arrive_soc=round(stop.arrive_soc, 1),
            depart_soc=round(stop.depart_soc, 1),
            charge_min=round(stop.charge_min, 1),
            delta_min=(
                0.0
                if baseline_min is None
                else round((best.total_min or 0.0) - baseline_min, 1)
            ),
            exclude_charger_ids=list(turned_down),
            below_reserve=below_reserve,
            min_arrival_soc=STRETCH_FLOOR_SOC if below_reserve else None,
        )

    # 1. The ones a plan can already reach, ranked by successive exclusion.
    for _ in range(MAX_ALTERNATIVES + 1):
        best = await _next_best(already | set(turned_down), params)
        if best is None:
            break
        stop = best.stops[0]
        alt = _as_alt(best, stop, below_reserve=False)
        if baseline_min is None:
            baseline_min = best.total_min or 0.0
            current = alt
        else:
            found.append(alt)
        turned_down.append(stop.charger_id)

    # 2. The ones only a lower floor can reach. Same machinery, one parameter
    #    moved, so a stretch option is a real plan and not a suggestion — the
    #    rest of the journey after it is still planned on the full reserve.
    stretch_params = replace(params, first_leg_reserve_soc=STRETCH_FLOOR_SOC)
    for _ in range(MAX_STRETCH):
        best = await _next_best(already | set(turned_down), stretch_params)
        if best is None:
            break
        stop = best.stops[0]
        # Built BEFORE this one joins the turned-down list: `exclude_charger_ids`
        # is what makes this stop the next one, so a row that carried its own
        # id would tell `replan` to refuse the charger it is offering.
        alt = _as_alt(best, stop, below_reserve=True)
        turned_down.append(stop.charger_id)
        # Only worth showing if the reserve is what was hiding it. Anything
        # else here is an ordinary alternative the loop above ran out of room
        # for, and dressing it up as a risk would be a lie.
        if stop.arrive_soc < params.reserve_soc:
            found.append(alt)

    return AlternativesOut(
        speed_kph=speed, current=current, alternatives=found
    )


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
