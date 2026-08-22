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
    AlternativesRequest,
    ArriveOut,
    AtChargerOut,
    ArriveRequest,
    BenchmarkOut,
    ChargingOut,
    LiveOut,
    LiveRouteOut,
    LiveStateOut,
    PingRequest,
    PlanRequest,
    PlanResult,
    ReplanOut,
    ReplanRequest,
    RerouteOut,
    RerouteRequest,
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
from app.services import amenities
from app.services import chargers as chargers_svc
from app.services import live, routing
from app.services.geo import haversine_m, polyline_encode
from app.services.simulator import (
    RouteProfile,
    SimParams,
    SpeedResult,
    VehicleParams,
    charge_minutes,
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

# How far down the ranking to keep looking for somewhere you can eat. The list
# above is ranked by minutes, and the quickest few stops are regularly a lay-by,
# a car park and another lay-by — so "is there anywhere to EAT" can be a
# question none of them answers. Bounded, because each step is another DP.
MAX_FOOD_SEARCH = 5

# How near a charger the phone has to be for "I'm plugged in here" to mean that
# one. Generous: a services can be 400 m end to end, the fix is taken between
# buildings, and the alternative to matching is telling somebody standing at a
# charger that they are not at one.
ARRIVE_MATCH_M = 1_500.0

# The floor those are computed against. Not zero: a plan that arrives on
# vapour is not an option, it is a breakdown with extra steps. Applied to the
# leg being driven and no other, so one accepted risk cannot become a journey
# planned on fumes — see `SimParams.first_leg_reserve_soc`.
STRETCH_FLOOR_SOC = 4.0


def _travelled_m(st: dict) -> float:
    """How far the car has come in total, across every road it has been on.

    `offset_m` is measured along the CURRENT route, and a re-route replaces
    that road — so on its own it restarts near zero mid-journey. Everything
    that indexes into the route (the profile, charger offsets, the plan's
    `offset_base_m`) wants `offset_m`; everything that is a HISTORY — the
    breadcrumb trail, the review, "how far have you driven" — wants this, or it
    draws a journey that jumps backwards.
    """
    return float(st.get("distance_before_m", 0.0)) + float(st.get("offset_m", 0.0))


def _route_version(run: TripRun) -> int:
    """Which road this drive is on. 0 is the trip's own route."""
    return int((run.route_snapshot or {}).get("version", 0))


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


def _clear_first_leg_floor(st: dict) -> dict:
    """Forget an accepted stretch once the leg it was accepted for is over.

    `first_leg_reserve_soc` is a decision about ONE leg — "I'll arrive at that
    services under the reserve rather than stop short of it" — and it is
    persisted so the standing "Re-plan" button cannot silently refuse the stop
    the driver just chose. Nothing ever took it off again, so a risk accepted
    at nine in the morning was still lowering the reserve on the fourth leg
    that evening: every later plan was quietly computed on a floor nobody had
    agreed to, and the alternatives panel used it too, which is how it came to
    offer a charger 21 km further on than the plan's own next stop.

    Arriving at a charger ends the leg, whichever charger it is — the floor was
    about reaching one, and one has been reached.
    """
    if st.get("first_leg_floor_soc") is None:
        return st
    return {k: v for k, v in st.items() if k != "first_leg_floor_soc"}


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


def _plan_stop(run: TripRun, trip: Trip, charger_id: str) -> tuple[dict, float] | None:
    """This stop as the plan in force describes it, with its offset base.

    The re-planned slice first, the original itinerary second: the screen is
    drawn from whichever plan is current, and a stop's numbers have to come
    from the same one.
    """
    plan = run.plan or None
    if plan:
        base = float(plan.get("offset_base_m") or 0.0)
        for s in (plan.get("remaining") or {}).get("stops") or []:
            if s["charger_id"] == charger_id:
                return s, base
    speed = _planned_result(trip, run.planned_speed_kph) or {}
    for s in speed.get("stops", []):
        if s["charger_id"] == charger_id:
            return s, 0.0
    return None


def _plan_stop_offset(run: TripRun, trip: Trip, charger_id: str) -> float | None:
    """Where the plan in force puts this stop, on the route's own axis."""
    found = _plan_stop(run, trip, charger_id)
    return None if found is None else float(found[0]["offset_m"]) + found[1]


def _soc_needed_next(
    run: TripRun,
    trip: Trip,
    veh: VehicleParams,
    params: SimParams,
    from_offset_m: float,
) -> float | None:
    """Battery needed to reach the next planned stop from here, reserve in.

    The question a driver at a charger is actually asking — how much do I have
    to put in before I can go. Priced through the same profile the simulator
    uses, at the speed the plan is holding, so the number agrees with
    everything else on the screen.

    Answered at PLANNED stops too, not only at chargers the plan never chose.
    It is the floor there as well, and the useful half of the pair the stop
    card shows: the plan's own `depart_soc` says what leaving on costs the rest
    of the journey, and this says when you could leave at all.
    """
    target = None
    plan = run.plan or None
    if plan:
        base = float(plan.get("offset_base_m") or 0.0)
        for s in (plan.get("remaining") or {}).get("stops") or []:
            if s["offset_m"] + base > from_offset_m + 200.0:
                target = s["offset_m"] + base
                break
    if target is None:
        speed_res = _planned_result(trip, run.planned_speed_kph) or {}
        for s in speed_res.get("stops", []):
            if s["offset_m"] > from_offset_m + 200.0:
                target = float(s["offset_m"])
                break
    snapshot = run.route_snapshot
    if target is None:
        # No stop left: what it takes to reach the destination, on its own
        # arrival target rather than the between-stops reserve.
        target = float(snapshot.get("total_dist_m") or 0.0)
        reserve = params.target_soc
    else:
        reserve = params.reserve_soc
    sl = slice_route(live.snapshot_segments(snapshot), [], from_offset_m)
    if not sl.segments or target <= from_offset_m:
        return None
    speed = float((plan or {}).get("optimum_speed") or run.planned_speed_kph)
    prof = RouteProfile(sl.segments, min(speed, veh.top_speed_kph), veh, params)
    kwh = prof.kwh_at(target - from_offset_m)
    return round(min(100.0, kwh / max(veh.usable_kwh, 1e-6) * 100.0 + reserve), 1)


def _planned_stops_ahead(run: TripRun, trip: Trip, st: dict) -> list[str]:
    """The chargers the plan in force still intends to stop at, in order.

    The re-planned slice when there is one, the original itinerary otherwise.
    Falling through matters: on a drive nobody has re-planned, `run.plan` is
    empty and reading only that reports the plan as having no stops at all —
    which is how "never offer the stop you are already going to" quietly
    stopped applying to every drive that had not been re-planned yet.
    """
    plan = run.plan or None
    if plan:
        base = float(plan.get("offset_base_m") or 0.0)
        return [
            str(s["charger_id"])
            for s in (plan.get("remaining") or {}).get("stops") or []
            if s["offset_m"] + base > st["offset_m"] + 200.0
        ]
    speed = _planned_result(trip, run.planned_speed_kph) or {}
    return [
        str(s["charger_id"])
        for s in speed.get("stops", [])
        if float(s["offset_m"]) > st["offset_m"] + 200.0
    ]


def _planned_next_stop_id(run: TripRun, trip: Trip, st: dict) -> str | None:
    """The charger the plan in force is actually heading for, if any."""
    ahead = _planned_stops_ahead(run, trip, st)
    return ahead[0] if ahead else None


def _remaining_slice(snapshot: dict, offset_m: float, exclude: set[str]):
    chargers = [
        c for c in live.snapshot_chargers(snapshot) if c.charger_id not in exclude
    ]
    return slice_route(live.snapshot_segments(snapshot), chargers, offset_m)


def _at_charger_out(run: TripRun, trip: Trip) -> AtChargerOut | None:
    """The charger under the car, from the route snapshot rather than the plan.

    The plan's stops are four sites out of the hundreds on the corridor, and
    stopping at one of the others is an ordinary thing to do — so this is
    looked up in the snapshot, and only afterwards asked whether the plan
    happens to have chosen it.
    """
    cid = run.state.get("at_charger_id")
    if not cid:
        return None
    node = next(
        (c for c in live.snapshot_chargers(run.route_snapshot) if c.charger_id == cid),
        None,
    )
    if node is None:
        return None
    return AtChargerOut(
        charger_id=node.charger_id,
        name=node.name,
        operator=node.operator,
        power_kw=node.power_kw,
        n_points=node.n_points,
        lat=round(node.lat, 5),
        lon=round(node.lon, 5),
        food_hint=amenities.food_hint(node.name),
        planned=_plan_stop_offset(run, trip, cid) is not None,
    )


def _live_soc(run: TripRun, trip: Trip, vehicle: Vehicle) -> float:
    """The battery now — projected forward while plugged in.

    Charging is modelled from the clock rather than from pings (see
    `live.charging_soc`), so this is the one place that has to know what time
    it is: a phone in a pocket sends nothing, and the estimate has to be right
    when the page is reopened rather than frozen at the moment it locked.
    """
    st = run.state
    if st.get("charge_anchor_soc") is None:
        return live.current_soc(st, vehicle.usable_kwh)
    return live.charging_soc(
        st,
        VehicleParams.from_vehicle(vehicle),
        _sim_params(
            PlanRequest.model_validate(trip.request), st.get("run_factor", 1.0)
        ),
        _now_min(run),
    )


def _now_min(run: TripRun) -> float:
    """Minutes since departure, by the wall clock, never rewound.

    The charge projection is a function of elapsed time, so the one number it
    needs is the one a sleeping phone stops sending.
    """
    return max(
        float(run.state["at_min"]),
        (datetime.utcnow() - run.started_at).total_seconds() / 60.0,
    )


def _charging_out(
    run: TripRun, trip: Trip, vehicle: Vehicle, soc_now: float
) -> ChargingOut | None:
    """The session at the plug: how long, how fast, and how much longer.

    Assembled here rather than stored, because every number in it is a function
    of the clock — see `ChargingOut`. The two targets come from different
    places on purpose: the floor is `need_soc_next`, priced once on arrival
    against the road ahead, and the target is what the PLAN chose to leave this
    stop on, which exists only when the plan chose this stop at all.
    """
    st = run.state
    if st.get("charge_anchor_soc") is None:
        return None
    veh = VehicleParams.from_vehicle(vehicle)
    p = _sim_params(
        PlanRequest.model_validate(trip.request), st.get("run_factor", 1.0)
    )
    kw = float(st.get("charge_kw") or 0.0)

    target: float | None = None
    cid = st.get("at_charger_id")
    if cid:
        found = _plan_stop(run, trip, str(cid))
        if found is not None:
            target = float(found[0]["depart_soc"])

    def remaining(to: float | None) -> float | None:
        if to is None or kw <= 0.0 or to <= soc_now:
            return None
        return round(charge_minutes(veh, soc_now, to, kw, p.charge_power_factor), 1)

    floor = st.get("need_soc_next")
    return ChargingOut(
        since_min=round(
            max(0.0, _now_min(run) - float(st.get("charge_anchor_min", 0.0))), 1
        ),
        power_kw=round(kw, 1),
        start_soc=round(float(st["charge_anchor_soc"]), 1),
        min_soc=None if floor is None else round(float(floor), 1),
        target_soc=None if target is None else round(target, 1),
        to_min_min=remaining(None if floor is None else float(floor)),
        to_target_min=remaining(target),
    )


def _log_charge_edges(
    db: AsyncSession,
    run: TripRun,
    before: dict,
    after: dict,
    now: datetime,
    soc: float,
) -> None:
    """Write `charge_start` / `charge_end` on the edges of a plug-in.

    `services/drives.charge_stops` has always paired these two kinds to report
    how long real charge stops take — and nothing has ever written either of
    them, so the panel answered with an empty list for every drive since it
    shipped. The charge anchor IS that pair: it appears when the cable goes in
    and is banked when it comes out, by whichever route (a ping down the road,
    a correction, the driver saying what they are leaving on). Deriving the
    events from the anchor rather than from each call site is what stops the
    four of them drifting apart.

    Never a reason to fail a write on the drive screen: these are a record of
    the stop, not part of it.
    """
    def session(st: dict) -> str | None:
        return (
            str(st.get("at_charger_id"))
            if st.get("charge_anchor_soc") is not None
            else None
        )

    was, is_now = session(before), session(after)
    if was == is_now:
        return

    def event(kind: str, charger_id: str | None) -> None:
        db.add(
            TripEvent(
                run_id=run.id,
                at=now,
                kind=kind,
                offset_m=_travelled_m(after),
                lat=after["lat"],
                lon=after["lon"],
                soc=round(soc, 1),
                payload={"charger_id": charger_id},
            )
        )

    # Both can fire on one call: moving straight from one plug to another ends
    # a stop and begins a stop, and a pair keyed on the anchor alone would see
    # "still charging" and record neither.
    if was is not None:
        event("charge_end", was)
    if is_now is not None:
        event("charge_start", is_now)


def _state_out(run: TripRun, trip: Trip, vehicle: Vehicle) -> LiveStateOut:
    st = run.state
    usable_kwh = vehicle.usable_kwh
    soc = _live_soc(run, trip, vehicle)
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
        at_charger=_at_charger_out(run, trip),
        # Computed when the driver says where they are, not on every ping: it
        # is a property of the position and the route, and the car is not
        # moving while it matters.
        need_soc_next=st.get("need_soc_next"),
        # Whether "I'm plugged in here now" can still be taken back. A flag and
        # not the stored state: what the previous position WAS is nobody's
        # business but the server's, and a read of this run belongs to watchers
        # too.
        can_undo_arrive=bool(st.get("arrive_undo")),
        charging=_charging_out(run, trip, vehicle, soc),
        route_version=_route_version(run),
        distance_before_m=round(st.get("distance_before_m", 0.0)),
        # Off the route long enough that waiting for a rejoin has stopped being
        # the useful answer. Reported to every reader, acted on only by the
        # driver's device — a watcher cannot re-route anything.
        reroute_suggested=live.reroute_due(st),
    )


def _live_route_out(run: TripRun, coords: list) -> LiveRouteOut:
    snapshot = run.route_snapshot or {}
    return LiveRouteOut(
        version=_route_version(run),
        polyline=polyline_encode([(la, lo) for la, lo in coords]),
        total_dist_m=round(float(snapshot.get("total_dist_m") or 0.0)),
        distance_before_m=round(run.state.get("distance_before_m", 0.0)),
    )


async def _trip_and_vehicle(db: AsyncSession, run: TripRun) -> tuple[Trip, Vehicle]:
    trip = await db.get(Trip, run.trip_id)
    if trip is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    vehicle = await db.get(Vehicle, trip.vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    return trip, vehicle


def _snapshot_route(
    route: routing.RouteData, chargers: list, version: int = 0
) -> dict:
    """Freeze the road a drive is being followed along.

    `version` is 0 for the trip's own route and increments on every re-route.
    It is what tells a client holding a polyline that the one it is projecting
    GPS fixes onto is no longer the road the car is on.
    """
    return {
        "version": version,
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
    return routing._lerp_axis(seg_offsets, _seg_cum(snapshot), geom_m)


def _segment_to_geom(snapshot: dict, seg_m: float) -> float:
    """The inverse: a simulator offset back onto the polyline's own axis.

    The two differ by a few hundred metres over a long route, which is nothing
    while driving and everything when the answer is "where did the car leave
    the road" — `RouteGeometry.point_at` reads the geometry axis, and handing
    it a segment offset lands somewhere plausible and wrong.
    """
    seg_offsets = snapshot.get("seg_geom_offsets") or []
    if not seg_offsets:
        return seg_m
    return routing._lerp_axis(_seg_cum(snapshot), seg_offsets, seg_m)


def _seg_cum(snapshot: dict) -> list[float]:
    cum = [0.0]
    for s in snapshot["segments"]:
        cum.append(cum[-1] + s["dist_m"])
    return cum


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
            run_id=run.id, at=now, kind="start", offset_m=_travelled_m(state),
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
        state=_state_out(run, trip, vehicle),
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
        return _state_out(run, trip, vehicle)

    # A vague fix is worth recording but not worth moving the car for.
    if body.accuracy_m is not None and body.accuracy_m > live.MAX_ACCURACY_M:
        offset_m = run.state["offset_m"]

    # ANY charger on the corridor, not just the four the plan chose — see
    # `live.nearest_charger`.
    stop_id, stop_kw = live.nearest_charger(
        live.snapshot_chargers(snapshot), body.lat, body.lon
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
    # Arriving somewhere is a transition, and the one moment "how much do I
    # have to put in before I can go" becomes the question on the screen. It is
    # priced ONCE, here, rather than on every ping: it is a property of the
    # position and the route, the car is not moving while it matters, and this
    # path is deliberately cheap enough to sit on the event loop.
    if stop_id and not run.state.get("at_charger_id"):
        new_st = {
            # Reaching a charger ends the leg an accepted stretch was accepted
            # for — see `_clear_first_leg_floor`.
            **_clear_first_leg_floor(new_st),
            "need_soc_next": _soc_needed_next(
                run, trip, veh, params, float(new_st["offset_m"])
            ),
        }
    elif not new_st.get("at_charger_id"):
        new_st = {**new_st, "need_soc_next": None}

    # Plain JSON columns don't track in-place edits — always reassign.
    prev_st = run.state
    run.state = new_st
    run.last_seen_at = now
    run.n_pings = run.n_pings + 1
    _log_charge_edges(
        db, run, prev_st, new_st, now, _live_soc(run, trip, vehicle)
    )

    last_crumb = run.state.get("last_crumb_min", -BREADCRUMB_MIN)
    if at_min - last_crumb >= BREADCRUMB_MIN:
        run.state = {**new_st, "last_crumb_min": at_min}
        db.add(
            TripEvent(
                run_id=run.id, at=now, kind="breadcrumb", offset_m=_travelled_m(new_st),
                lat=body.lat, lon=body.lon,
                soc=_live_soc(run, trip, vehicle),
            )
        )
    await db.commit()
    return _state_out(run, trip, vehicle)


@router.post("/runs/{run_id}/arrive", response_model=ArriveOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_PING, key_func=run_key)
async def arrive(
    request: Request,
    run_id: str,
    body: ArriveRequest,
    db: AsyncSession = Depends(get_db),
) -> ArriveOut:
    """The driver says which stop they are standing at.

    Position comes from GPS, and GPS comes from a page that is being looked
    at: a locked phone reports nothing, which is precisely the state a phone
    is in while its car charges. So the drive would sit believing you are 69 km
    short of the charger you are plugged into, showing a plan for a stretch of
    road you have already driven — reported from a charger, mid-charge, with
    "no update for 15 minutes" above it.

    WHERE, by preference, is the phone's own position rather than the stop the
    screen happens to be showing. A driver charging somewhere the plan never
    chose — a services they liked the look of, a stop picked an hour ago, the
    only free stall in the town — would otherwise be told they are at
    Geiselwind because Geiselwind is what the card was on. The snapshot holds
    every candidate charger on the corridor and not just the planned stops, so
    the site they are standing at is usually IN it; when nothing is within
    `ARRIVE_MATCH_M` the car still moves to where they are, and the answer says
    no charger was recognised rather than inventing one.

    The missing kilometres are BILLED, not skipped. Moving the offset forward
    without pricing the drag would hand back a battery estimate that never
    spent the energy to get here, which is a worse lie than the stale position
    was: `live.advance` prices the jump exactly as it prices a ping, and the
    time since the last report is charged as driving, because that is what it
    was. The charger is then marked by hand — `advance` only recognises a stop
    the car creeps up on, and this one arrived in a single leap.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    snapshot = run.route_snapshot
    nodes = live.snapshot_chargers(snapshot)

    node = None
    offset_m: float | None = None
    if body.lat is not None and body.lon is not None:
        # Where the phone says the car is, snapped to the route.
        geom_off, _off_route = _geometry(snapshot).project(body.lat, body.lon)
        offset_m = _geom_to_segment(snapshot, geom_off)
        node = min(
            (
                c
                for c in nodes
                if haversine_m(body.lat, body.lon, c.lat, c.lon) <= ARRIVE_MATCH_M
            ),
            key=lambda c: haversine_m(body.lat, body.lon, c.lat, c.lon),
            default=None,
        )
    if node is None and body.charger_id:
        node = next((c for c in nodes if c.charger_id == body.charger_id), None)
    if node is None and offset_m is None:
        raise HTTPException(status_code=404, detail="Not a charger on this route.")

    if node is not None:
        # The PLAN's offset for this stop when it has one, not the snapshot
        # node's: the plan is what the screen is drawn from, and the two axes
        # differ by a few hundred metres over a long route. Landing the car
        # anywhere but where the list says the stop is would leave it "0 km
        # away" and still not there.
        offset_m = _plan_stop_offset(run, trip, node.charger_id) or offset_m or node.offset_m

    now = datetime.utcnow()
    at_min = max((now - run.started_at).total_seconds() / 60.0, run.state["at_min"])
    gap_s = max(0.0, (at_min - run.state["at_min"]) * 60.0)

    plan_req = PlanRequest.model_validate(trip.request)
    params = _sim_params(plan_req, run.state.get("run_factor", 1.0))
    veh = VehicleParams.from_vehicle(vehicle)

    prev_st = run.state
    new_st = live.advance(
        run.state,
        segments=live.snapshot_segments(snapshot),
        veh=veh,
        p=params,
        offset_m=max(float(offset_m), run.state["offset_m"]),
        lat=node.lat if node else (body.lat or run.state["lat"]),
        lon=node.lon if node else (body.lon or run.state["lon"]),
        at_min=at_min,
        off_route_m=0.0,
        # The gap was spent getting here. Capped like a ping's own window, so a
        # phone that was off for six hours cannot bill six hours of drag.
        moving_s=min(gap_s, 3600.0),
        stationary_s=0.0,
    )
    # The charge starts projecting from HERE, not from the next ping. A driver
    # who taps "I'm plugged in" and puts the phone away sends nothing more, and
    # waiting for a ping to anchor the projection would mean the whole stop —
    # exactly the stop this button exists for — was modelled as not charging.
    # The SITE's power, uncapped: `charge_forward` takes the car's own curve
    # from the other side, exactly as the ping path does.
    charge_kw = float(node.power_kw) if node and node.power_kw else None
    run.state = {
        # Same transition as a ping's, declared by hand — and the same end of
        # the leg an accepted stretch belonged to.
        **(_clear_first_leg_floor(new_st) if node else new_st),
        "at_charger_id": node.charger_id if node else None,
        "stale": False,
        **(
            {
                "charge_anchor_soc": live.current_soc(new_st, vehicle.usable_kwh),
                "charge_anchor_min": at_min,
                "charge_kw": charge_kw,
            }
            if charge_kw
            else {}
        ),
        "need_soc_next": _soc_needed_next(
            run, trip, veh, params, float(new_st["offset_m"])
        ),
        # Kept so the tap can be taken back. This is the one write in the whole
        # drive that is a CLAIM rather than a measurement — a person pressing a
        # button, next to a button they meant to press — and it is also the one
        # the rest of the model cannot undo on its own: `live.advance` clamps
        # the offset forward so a wobbly fix can never walk the car backwards,
        # which means a wrong arrival cannot be corrected by driving, or by
        # waiting, or by any later ping. Everything else recovers by itself.
        # Nested undos are dropped: the state before an arrival is the one to
        # go back to, and keeping a chain of them would grow a JSON column on
        # a hot path to no purpose.
        "arrive_undo": {
            k: v for k, v in run.state.items() if k != "arrive_undo"
        },
    }
    run.last_seen_at = now
    _log_charge_edges(
        db, run, prev_st, run.state, now, _live_soc(run, trip, vehicle)
    )
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="arrive", offset_m=_travelled_m(run.state),
            lat=run.state["lat"], lon=run.state["lon"],
            soc=_live_soc(run, trip, vehicle),
            payload={
                "charger_id": node.charger_id if node else None,
                "name": node.name if node else None,
            },
        )
    )
    await db.commit()
    return ArriveOut(
        state=_state_out(run, trip, vehicle),
        matched_name=node.name if node else None,
    )


@router.post("/runs/{run_id}/arrive/undo", response_model=LiveStateOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_PING, key_func=run_key)
async def undo_arrive(
    request: Request, run_id: str, db: AsyncSession = Depends(get_db)
) -> LiveStateOut:
    """"I'm not there" — take back the last hand-entered arrival.

    "I'm plugged in here now" sits one tap away from the stop card and is the
    only irreversible thing on the drive screen. Everything else the driver can
    get wrong is corrected by the next fix: a bad position is snapped, a bad
    battery reading is re-anchored by the next one, a stop turned down can be
    turned back on by re-planning. An arrival cannot, because `live.advance`
    clamps the offset forward on purpose — the same rule that stops a wobbly
    GPS fix walking the car backwards also stops a wrong arrival being walked
    back, and the drive then insists you are at a charger you are 69 km short
    of until you physically get there.

    Restoring the whole prior state rather than just the offset, because the
    arrival billed the skipped kilometres as driving: putting the position back
    and leaving the energy spent would hand back a battery that paid for a
    stretch of road it is about to drive again, which is the mirror of the bug
    the billing exists to prevent. `at_min` goes back too and the next ping
    recomputes it from the wall clock, so no time is lost or invented.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    prior = run.state.get("arrive_undo")
    if not prior:
        raise HTTPException(
            status_code=409, detail="There's no arrival to undo."
        )

    now = datetime.utcnow()
    # Plain JSON columns don't track in-place edits — always reassign.
    run.state = {**prior, "at_charger_id": None, "stale": False}
    run.last_seen_at = now
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="arrive_undo",
            offset_m=_travelled_m(run.state),
            lat=run.state["lat"], lon=run.state["lon"],
            soc=_live_soc(run, trip, vehicle),
            payload={},
        )
    )
    await db.commit()
    return _state_out(run, trip, vehicle)


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

    The POSITION comes with it, and that is not a detail. A reading is ground
    truth about a battery AT A PLACE: type 45% at a services and the app
    anchored it to wherever the phone last reported, which after a charge stop
    is tens of kilometres back. Nothing looked wrong until the next re-plan
    sent a fresh fix, which correctly moved the car forward and billed the drag
    for that gap — road the car had already covered BEFORE the reading was
    taken, and therefore energy already reflected in the figure the driver had
    just typed. The battery visibly re-adjusted itself moments after being
    corrected, always downwards, and the same understated window skewed
    `run_factor` low: the model came out of it believing the car was thriftier
    than it is.

    So the fix moves the car first and anchors afterwards. An off-route fix is
    ignored rather than refused — a reading taken at a supermarket two streets
    off the corridor is still the best number anyone has, and the position is
    the only part of it we cannot use.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    if body.lat is not None and body.lon is not None:
        snapshot = run.route_snapshot
        geom_off, off_route = _geometry(snapshot).project(body.lat, body.lon)
        if off_route <= live.OFF_ROUTE_M:
            now_fix = datetime.utcnow()
            run.state = live.resync(
                run.state,
                segments=live.snapshot_segments(snapshot),
                veh=VehicleParams.from_vehicle(vehicle),
                p=_sim_params(
                    PlanRequest.model_validate(trip.request),
                    run.state.get("run_factor", 1.0),
                ),
                offset_m=_geom_to_segment(snapshot, geom_off),
                lat=body.lat,
                lon=body.lon,
                at_min=(now_fix - run.started_at).total_seconds() / 60.0,
                off_route_m=off_route,
                speed_kph=float(
                    (run.plan or {}).get("optimum_speed") or run.planned_speed_kph
                ),
            )

    before = _live_soc(run, trip, vehicle)
    # The charge is projected from the clock, so the clock has to be current
    # before the reading settles it — a phone that slept through the whole stop
    # would otherwise bank the charge as of the last ping, which is the moment
    # it was plugged in.
    run.state = {**run.state, "at_min": _now_min(run)}
    prev_st = run.state
    new_st = live.apply_reading(
        run.state,
        body.soc,
        vehicle.usable_kwh,
        veh=VehicleParams.from_vehicle(vehicle),
        p=_sim_params(
            PlanRequest.model_validate(trip.request), run.state.get("run_factor", 1.0)
        ),
        leaving=body.leaving,
    )
    if body.leaving:
        # Back on the road: the floor to reach the next stop was a question
        # about standing still, and the answer to it has just been acted on.
        new_st = {**new_st, "need_soc_next": None}
    run.state = new_st
    run.n_soc_readings = run.n_soc_readings + 1
    now = datetime.utcnow()
    run.last_seen_at = now
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="soc_reading",
            offset_m=_travelled_m(new_st), lat=new_st["lat"], lon=new_st["lon"],
            soc=body.soc,
            payload={
                "estimated": round(before, 1),
                "error_pct": new_st.get("last_error_pct"),
                "run_factor": new_st.get("run_factor"),
                "leaving": body.leaving,
            },
        )
    )
    _log_charge_edges(db, run, prev_st, new_st, now, body.soc)
    await db.commit()
    return _state_out(run, trip, vehicle)


async def _plan_remaining(
    *,
    run: TripRun,
    trip: Trip,
    veh: VehicleParams,
    plan_req: PlanRequest,
    snapshot: dict,
    st: dict,
    soc_now: float,
    excluded: set[str],
    floor: float | None,
    hold: float | None,
    lo: float,
    hi: float,
    step: float,
) -> tuple[ReplanOut, "SpeedResult", dict]:
    """Sweep the road still ahead and build the plan of record for it.

    Shared by `/replan` and `/reroute`, which differ only in what road they
    hand over: the first slices the one the car is already on, the second has
    just fetched a new one. Everything after that — the break debt carried
    forward, the held speed being IN the sweep, the benchmark against the
    original promise — has to be identical, or the same drive gets two
    different answers to "what happens from here" depending on which button was
    pressed. It used to be one copy inside `replan`, which is the shape that
    drifts the moment a second caller appears.
    """
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

    lo = max(60.0, lo)
    hi = min(hi, veh.top_speed_kph)
    n = max(1, int((hi - lo) / step) + 1)
    # `POST /api/trips` caps its sweep; this path did not, so `full_range` with
    # the minimum step ran ~4x the work per request — on a token the caller
    # mints for themselves.
    if n > MAX_SWEEP_POINTS:
        raise HTTPException(
            status_code=422,
            detail=f"Too many speeds to simulate (max {MAX_SWEEP_POINTS}).",
        )
    speeds = [s for s in (lo + i * step for i in range(n)) if s <= hi]
    if not speeds:
        speeds = [min(run.planned_speed_kph, veh.top_speed_kph)]

    # A held speed has to be IN the sweep, or the driver's own choice is the
    # one speed the answer cannot be. Clamped to what the car can do rather
    # than refused: "hold 200" in a car that stops at 160 is a request for
    # everything it has.
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
    # Against the distance TRAVELLED, not the offset on the current road: the
    # original itinerary is measured from the original origin, and after a
    # re-route `offset_m` restarts — which would count every stop already
    # passed as still ahead.
    stops_ahead = [
        s for s in original.get("stops", []) if s["offset_m"] > _travelled_m(st)
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
    return out, best, bench


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

    "Where the car actually is" means the fix the phone sends WITH this
    request, when it sends one. That is the difference between re-planning from
    here and re-planning from the last position that happened to reach us,
    which on a phone is not the same place: GPS runs while the page is being
    looked at, so a driver who unlocks their phone at a services and presses
    Re-plan was, as far as the drive knew, still twenty minutes up the road.
    And because a re-plan is an explicit "from HERE", the fix may move the car
    BACKWARDS — the one place in the app where that is allowed. `advance`
    clamps forward for good reasons and that clamp is exactly why a mis-tapped
    "I'm plugged in here now" used to be permanent; this is the way out that
    does not involve ending the drive.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    snapshot = run.route_snapshot
    plan_req = PlanRequest.model_validate(trip.request)
    veh = VehicleParams.from_vehicle(vehicle)

    if body.lat is not None and body.lon is not None:
        geom_off, off_route = _geometry(snapshot).project(body.lat, body.lon)
        if off_route > live.OFF_ROUTE_M:
            raise HTTPException(
                status_code=409,
                detail="You're off the planned route. Rejoin it before re-planning.",
            )
        now = datetime.utcnow()
        prev_st = run.state
        run.state = live.resync(
            run.state,
            segments=live.snapshot_segments(snapshot),
            veh=veh,
            p=_sim_params(plan_req, run.state.get("run_factor", 1.0)),
            offset_m=_geom_to_segment(snapshot, geom_off),
            lat=body.lat,
            lon=body.lon,
            at_min=(now - run.started_at).total_seconds() / 60.0,
            off_route_m=off_route,
            # The plan in force is the honest stand-in: a correction has no
            # window to observe a speed over.
            speed_kph=float(
                (run.plan or {}).get("optimum_speed") or run.planned_speed_kph
            ),
        )
        run.last_seen_at = now
        _log_charge_edges(
            db, run, prev_st, run.state, now, _live_soc(run, trip, vehicle)
        )

    st = run.state
    if st.get("stale"):
        raise HTTPException(
            status_code=409,
            detail="You're off the planned route. Rejoin it before re-planning.",
        )

    soc_now = _live_soc(run, trip, vehicle)

    # Chargers this driver has turned down stay turned down. Merged and kept
    # on the run, because a rejection that lasts until the next re-plan is not
    # a rejection — the standing "Re-plan" button would hand back the stop they
    # walked away from.
    excluded = _merge_exclusions(run, body.exclude_charger_ids)
    floor = _set_first_leg_floor(run, body.min_arrival_soc)
    centre = run.planned_speed_kph
    if body.full_range:
        lo, hi = plan_req.speed_min, plan_req.speed_max
    else:
        lo, hi = centre - body.speed_span_kph, centre + body.speed_span_kph
    out, best, bench = await _plan_remaining(
        run=run,
        trip=trip,
        veh=veh,
        plan_req=plan_req,
        snapshot=snapshot,
        st=st,
        soc_now=soc_now,
        excluded=excluded,
        floor=floor,
        hold=_resolve_hold_speed(run, body),
        lo=lo,
        hi=hi,
        step=body.speed_step,
    )
    hold = out.held_speed_kph
    run.plan = out.model_dump(mode="json")
    run.plan_version = run.plan_version + 1
    run.n_replans = run.n_replans + 1
    now = datetime.utcnow()
    run.last_seen_at = now
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="replan", offset_m=_travelled_m(st),
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


@router.post("/runs/{run_id}/reroute", response_model=RerouteOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_REROUTE, key_func=run_key)
async def reroute(
    request: Request,
    run_id: str,
    body: RerouteRequest,
    db: AsyncSession = Depends(get_db),
) -> RerouteOut:
    """Off the route: fetch a road from where the car actually is, and plan it.

    Leaving the route is ordinary — a diversion, a closed slip road, a
    supermarket, a nav app that knows about a jam ORS does not — and the drive
    answered all of it the same way: freeze every figure and say "rejoin the
    route". `/replan` refused outright for the same reason, which is right for
    what it does (it slices the road the car is on, and the car is not on it)
    and useless as the only option. The plan for the road you are ACTUALLY on
    is the one thing worth having at that moment.

    Four things make it a re-route rather than a second `/replan`.

    **It is the only mid-drive call that spends UPSTREAM quota** — a fresh
    directions request, plus corridor tiles that may be cold — so on-route it
    refuses and points at `/replan`, which answers the same question for free.
    `force` exists for a driver who knows the road ahead is shut; the rate
    limit is an order of magnitude tighter than the re-plan's either way.

    **The axis moves and the history does not.** `offset_m` is measured along
    the snapshot, and the snapshot is being replaced — so the car lands at zero
    on a road that did not exist a moment ago. `live.rebase` banks what came
    before into `distance_before_m`, and every consumer takes the one it means:
    the profile and the charger offsets read `offset_m`, the trail and the
    review read `_travelled_m`.

    **The detour is billed, and labelled.** While off-route `advance` returned
    early and spent nothing — right while the position is unknown, wrong the
    moment it is known again. `rebase` prices the crow-flies gap as flat road
    and drops `soc_is_measured`, so the screen asks for a real figure rather
    than carrying an estimate flattering by however far the detour ran.

    **What the driver chose survives; what the road decided does not.**
    Rejected chargers stay rejected — that was a judgement about a site, and
    sites do not move. An accepted stretch below the reserve does NOT: it was
    about reaching one particular plug on a leg that no longer exists, and
    carrying it over would plan a new road on a floor nobody agreed to. Same
    rule as `_clear_first_leg_floor`, for the same reason.
    """
    run = await _load_run(db, run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail="This drive has finished.")
    trip, vehicle = await _trip_and_vehicle(db, run)

    plan_req = PlanRequest.model_validate(trip.request)
    veh = VehicleParams.from_vehicle(vehicle)
    old_snapshot = run.route_snapshot

    _geom_off, off_route = _geometry(old_snapshot).project(body.lat, body.lon)
    if off_route <= live.OFF_ROUTE_M and not body.force:
        raise HTTPException(
            status_code=409,
            detail=(
                "You're still on the planned route — re-plan from here instead "
                "of asking for a new road."
            ),
        )

    # The battery BEFORE the road changes. `rebase` adds the detour to it, and
    # reading it afterwards would price the remaining journey off a state that
    # is halfway through being rewritten.
    soc_before = _live_soc(run, trip, vehicle)
    speed = float((run.plan or {}).get("optimum_speed") or run.planned_speed_kph)

    # How far the car strayed, as the crow flies from where it left the road.
    # NOT from the last fix — while off-route that is wherever it wandered to,
    # and the drive's own position is the last point on the old route it can
    # vouch for.
    left_at = _geometry(old_snapshot).point_at(
        _segment_to_geom(old_snapshot, run.state["offset_m"])
    )
    detour_m = haversine_m(left_at[0], left_at[1], body.lat, body.lon)

    route = await routing.get_route(
        db, body.lat, body.lon, plan_req.dest.lat, plan_req.dest.lon
    )
    charger_nodes = await chargers_svc.chargers_for_route(db, route)
    snapshot = _snapshot_route(route, charger_nodes, _route_version(run) + 1)

    now = datetime.utcnow()
    at_min = (now - run.started_at).total_seconds() / 60.0
    # Minutes nothing was billed for. `advance` stops spending energy the
    # instant it goes stale, so the window runs from when the route was lost —
    # not from the last ping, which kept arriving and kept costing nothing.
    since = run.state.get("off_route_since_min")
    unbilled_min = at_min - float(since if since is not None else run.state["at_min"])
    prev_st = run.state
    new_st = live.rebase(
        run.state,
        veh=veh,
        p=_sim_params(plan_req, run.state.get("run_factor", 1.0)),
        lat=body.lat,
        lon=body.lon,
        at_min=at_min,
        detour_m=detour_m,
        speed_kph=speed,
        unbilled_min=unbilled_min,
    )
    # The stretch floor dies with the leg it was about — see the docstring.
    run.state = _clear_first_leg_floor(new_st)
    run.route_snapshot = snapshot
    _log_charge_edges(db, run, prev_st, run.state, now, soc_before)

    st = run.state
    soc_now = _live_soc(run, trip, vehicle)
    excluded = _merge_exclusions(run, [])

    # `model_fields_set` is what separates "leave my speed alone" from "you
    # pick" — the same three-way distinction a re-plan draws, so it goes
    # through the same resolver rather than a second reading of the rule.
    hold = _resolve_hold_speed(
        run,
        ReplanRequest(
            **(
                {"hold_speed_kph": body.hold_speed_kph}
                if "hold_speed_kph" in body.model_fields_set
                else {}
            )
        ),
    )

    defaults = ReplanRequest()
    centre = run.planned_speed_kph
    out, best, bench = await _plan_remaining(
        run=run,
        trip=trip,
        veh=veh,
        plan_req=plan_req,
        snapshot=snapshot,
        st=st,
        soc_now=soc_now,
        excluded=excluded,
        floor=None,
        hold=hold,
        lo=centre - defaults.speed_span_kph,
        hi=centre + defaults.speed_span_kph,
        step=defaults.speed_step,
    )

    run.plan = out.model_dump(mode="json")
    run.plan_version = run.plan_version + 1
    # Counted as a re-plan as well: every existing reader of `n_replans` means
    # "how often did this drive have to be re-thought", and a re-route is the
    # strongest instance of that. The `reroute` event is the finer-grained one.
    run.n_replans = run.n_replans + 1
    run.last_seen_at = now
    db.add(
        TripEvent(
            run_id=run.id, at=now, kind="reroute", offset_m=_travelled_m(st),
            lat=st["lat"], lon=st["lon"], soc=soc_now,
            payload={
                "detour_m": round(detour_m),
                "off_route_m": round(off_route),
                "route_version": _route_version(run),
                "speed_kph": best.speed_kph,
                "delta_min": round(bench["delta_min"], 1),
                "n_stops": best.n_stops,
            },
        )
    )
    await db.commit()
    logger.info(
        "live run rerouted ref=%s v=%s detour=%.0fm",
        run_ref_for(run.id), _route_version(run), detour_m,
    )
    return RerouteOut(
        state=_state_out(run, trip, vehicle),
        plan=out,
        route=_live_route_out(run, route.geometry.coords),
        detour_m=round(detour_m),
    )


@router.get("/trips/{trip_id}/live/route", response_model=LiveRouteOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_READ)
async def get_live_route(
    request: Request,
    trip_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> LiveRouteOut:
    """The road the drive is on now — geometry only.

    Its own endpoint because `GET /live` is polled every ten seconds and a
    polyline is tens of kilobytes. Clients hold this and refetch only when
    `route_version` moves, which for most drives is never. Public like every
    other read on a trip id: a watcher following the link has to see the road
    the car actually turned onto, or their screen shows it driving through
    fields.
    """
    run = await _latest_run(db, trip_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No live drive for this trip.")
    response.headers["Cache-Control"] = "no-store"
    coords = [(la, lo) for la, lo in (run.route_snapshot or {}).get("coords", [])]
    return _live_route_out(run, coords)


@router.post("/runs/{run_id}/alternatives", response_model=AlternativesOut)
@limiter.limit(settings.RATE_LIMIT_LIVE_ALTERNATIVES, key_func=run_key)
async def alternatives(
    request: Request,
    run_id: str,
    body: AlternativesRequest = AlternativesRequest(),
    db: AsyncSession = Depends(get_db),
) -> AlternativesOut:
    """Other chargers the drive could stop at instead of the next one.

    The planner optimises minutes, and minutes are not the only reason a stop
    is a bad stop: the fastest charger on the corridor can be a lay-by behind a
    warehouse with nowhere to eat, at eight in the evening, with children in
    the car. None of that is in OpenChargeMap and none of it is inferable, so
    this does not try to judge — it hands back the next few candidates with the
    only thing the model CAN say about them, which is what choosing each one
    costs in arrival time.

    Works on ANY stop in the plan, not only the next one: `reject_charger_id`
    says which. A stop four hours away is where the food question actually
    lands — by the time it is the next one, the choice has been made for you —
    and the machinery is identical, because turning a charger down and asking
    what the plan does instead is one operation whatever its position.

    Ranked by successive exclusion rather than by proximity: turn the chosen
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
    soc_now = _live_soc(run, trip, vehicle)
    base = _sim_params(plan_req, st.get("run_factor", 1.0))
    params = SimParams(
        **{
            **{f: getattr(base, f) for f in base.__dataclass_fields__},
            "depart_soc": soc_now,
            "prior_drive_min": st["at_min"],
            "prior_rest_credit_min": st.get("rest_credit_min", 0.0),
            # The floor the plan in force was made under. Without it this
            # endpoint plans a DIFFERENT journey than the one on screen: a
            # driver who accepted a stretch stop got it back as an
            # "alternative" to itself, because the full reserve cannot reach
            # it and the baseline pass therefore picked something else.
            "first_leg_reserve_soc": _first_leg_floor(st),
        }
    )

    # One speed, not a sweep: this question is "which charger", and re-opening
    # "how fast" alongside it would price each candidate against a different
    # cruise. The speed in force is the revision's if there is one.
    speed = float((run.plan or {}).get("optimum_speed") or run.planned_speed_kph)
    speed = min(speed, veh.top_speed_kph)

    # Which question this is. Swapping the NEXT stop means every candidate is
    # a `stops[0]` — the alternatives ARE the next stop. Swapping one further
    # down means the plan keeps its earlier stops and the candidate is whatever
    # now stands nearest the rejected one's position, which is a different pick
    # entirely: reporting `stops[0]` there would answer about a stop the driver
    # never mentioned.
    planned_ahead = _planned_stops_ahead(run, trip, st)
    planned_next = planned_ahead[0] if planned_ahead else None
    swapping_next = (
        body.reject_charger_id is None or body.reject_charger_id == planned_next
    )

    already = set(_excluded(st))
    turned_down: list[str] = []
    baseline_min: float | None = None
    current: AlternativeOut | None = None
    found: list[AlternativeOut] = []
    # Where on the route the swap is happening. Set from the baseline plan's
    # own stop, so a replacement is picked by POSITION rather than by being
    # first: rejecting the third stop and reporting the first one back would
    # answer a question nobody asked.
    target_offset: float | None = None

    def _pick(best):
        """The stop this swap is about."""
        if not best.stops:
            return None
        if swapping_next:
            return best.stops[0]
        if target_offset is None:
            named = next(
                (s for s in best.stops if s.charger_id == body.reject_charger_id),
                None,
            )
            return named or best.stops[0]
        return min(best.stops, key=lambda s: abs(s.offset_m - target_offset))

    async def _next_best(exclude: set[str], p: SimParams):
        sl = _remaining_slice(snapshot, st["offset_m"], exclude)
        if not sl.segments:
            return None
        results = await asyncio.to_thread(sweep_slice, sl, veh, [speed], p)
        best = optimum(results)
        if best is None or not best.stops:
            return None
        return best

    async def _plan_in_force(swap_id: str) -> tuple[float, AlternativeOut] | None:
        """The remaining journey as the plan INTENDS it, priced from here.

        The baseline used to be a free re-optimisation from the current
        position, and the row built from it was labelled "planned now" — which
        is a claim about the PLAN, and the two are not the same thing. They
        diverge for entirely ordinary reasons: the plan was made further back,
        the driver has corrected the battery upwards since, and a DP run from
        here may now prefer to push straight past the next stop. The panel then
        named a charger 64 km away as planned, directly beneath a card saying
        the next stop was 43 km away, about a different charger. Reported from
        the road, in those words.

        Restricting the DP's candidates to the plan's own stops is NOT enough:
        with a better battery than the plan assumed it simply skips the first
        of them and stops at the second, which is a different plan again. So
        the stop being swapped is FORCED — its leg is priced directly off the
        route profile, at the departure percentage the plan chose — and only
        the tail after it goes back through the DP. That tail is re-optimised
        on purpose: swapping any stop moves everything behind it, and the
        alternatives are costed the same way, so the two are comparable.

        Returns the cost of following the plan and the row describing its stop.
        `None` when the plan cannot be followed from here at all — the battery
        has moved on — and the free optimum is then the honest reference, which
        is what it always was.
        """
        found = _plan_stop(run, trip, swap_id)
        node = next(
            (
                c
                for c in live.snapshot_chargers(snapshot)
                if c.charger_id == swap_id
            ),
            None,
        )
        if found is None or node is None:
            return None
        plan_stop, base = found
        stop_at = float(plan_stop["offset_m"]) + base
        leg_m = stop_at - st["offset_m"]
        if leg_m <= 0.0:
            return None

        # The forced leg, straight off the profile — no chargers, so the DP
        # cannot decide to stop somewhere else on the way.
        head = slice_route(live.snapshot_segments(snapshot), [], st["offset_m"])
        if not head.segments or leg_m > head.total_dist_m:
            return None
        prof = RouteProfile(head.segments, speed, veh, params, head.index_offset)
        arrive = soc_now - prof.kwh_at(leg_m) / max(veh.usable_kwh, 1e-6) * 100.0
        if arrive < 0.0:
            return None
        leg_min = prof.time_at(leg_m)

        # What the plan says to leave on, never less than it arrived with.
        depart = max(arrive, float(plan_stop.get("depart_soc") or arrive))
        site_kw = float(node.power_kw) * params.site_power_factor
        stop_min = charge_minutes(
            veh, arrive, depart, site_kw, params.charge_power_factor
        ) + float(node.detour_min or 0.0)

        # …and the rest, re-optimised from that stop exactly as a swap would be.
        tail = _remaining_slice(snapshot, stop_at, already)
        tail_min = 0.0
        if tail.segments:
            tail_p = replace(
                params,
                depart_soc=depart,
                prior_drive_min=st["at_min"] + leg_min + stop_min,
                # The floor is a fact about the leg being driven, and that leg
                # ends at this stop.
                first_leg_reserve_soc=None,
            )
            best_tail = optimum(
                await asyncio.to_thread(sweep_slice, tail, veh, [speed], tail_p)
            )
            if best_tail is None or best_tail.total_min is None:
                return None
            tail_min = best_tail.total_min

        row = AlternativeOut(
            charger_id=node.charger_id,
            name=node.name,
            food_hint=amenities.food_hint(node.name),
            operator=node.operator,
            power_kw=node.power_kw,
            n_points=node.n_points,
            lat=round(node.lat, 5),
            lon=round(node.lon, 5),
            offset_m=round(stop_at),
            dist_from_here_m=round(leg_m),
            arrive_soc=round(arrive, 1),
            depart_soc=round(depart, 1),
            charge_min=round(stop_min, 1),
            delta_min=0.0,
            exclude_charger_ids=[],
            below_reserve=arrive < params.reserve_soc,
            min_arrival_soc=_first_leg_floor(st),
        )
        return leg_min + stop_min + tail_min, row

    def _as_alt(best, stop, *, below_reserve: bool) -> AlternativeOut:
        return AlternativeOut(
            charger_id=stop.charger_id,
            name=stop.name,
            food_hint=amenities.food_hint(stop.name),
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

    # 0. What the driver is doing now, priced from here. The reference every
    #    `delta_min` below is measured against, and the one row on this panel
    #    that makes a claim about the PLAN rather than about the model.
    swap_id = body.reject_charger_id or planned_next
    in_force = await _plan_in_force(swap_id) if swap_id else None
    if in_force is not None:
        baseline_min, current = in_force
        target_offset = current.dist_from_here_m
        turned_down.append(current.charger_id)

    # 1. The ones a plan can already reach, ranked by successive exclusion.
    #    The plan's own stop is already turned down when there was one, so the
    #    first pass here is genuinely "what else" — including, when a fresh DP
    #    from this position would do better, an option that costs LESS than the
    #    plan and says so with a negative delta.
    for _ in range(MAX_ALTERNATIVES if current is not None else MAX_ALTERNATIVES + 1):
        best = await _next_best(already | set(turned_down), params)
        if best is None:
            break
        stop = _pick(best)
        if stop is None:
            break
        alt = _as_alt(best, stop, below_reserve=False)
        if baseline_min is None:
            # No plan to price against — an un-replanned drive whose itinerary
            # is no longer feasible from here. The free optimum is the honest
            # reference then, exactly as it was before.
            baseline_min = best.total_min or 0.0
            current = alt
            target_offset = stop.offset_m
        else:
            found.append(alt)
        turned_down.append(stop.charger_id)

    # 2. The ones only a lower floor can reach. Same machinery, one parameter
    #    moved, so a stretch option is a real plan and not a suggestion — the
    #    rest of the journey after it is still planned on the full reserve.
    #
    #    Only when the stop being swapped is the NEXT one. The relaxed floor
    #    applies to the leg being driven and no other, so "push on past the
    #    reserve" is meaningless about a stop four hours away — and the stop it
    #    would reach is `stops[0]`, the one on that leg, not whichever now
    #    stands nearest a target further down the route.
    #    It also gets its OWN exclusion set, seeded with the stop being
    #    replaced and nothing else. "What could I reach by pushing past the
    #    reserve" is a different question from "what else is quick", and
    #    running it on the leftovers of the first list made the answer depend
    #    on how many ordinary alternatives happened to be found — which on a
    #    corridor like this one meant no answer at all.
    #    And it rules out everything up to and INCLUDING the stop being
    #    replaced, not just that stop. Leave the earlier ones available and the
    #    DP simply stops before the rejected charger — a perfectly good plan,
    #    and not an answer to "what if I push on past it", which is the only
    #    question this pass exists to ask.
    stretch_params = replace(params, first_leg_reserve_soc=STRETCH_FLOOR_SOC)
    target_abs = (target_offset or 0.0) + st["offset_m"]
    offered = {a.charger_id for a in found} | (
        {current.charger_id} if current else set()
    )
    stretch_down: list[str] = [
        *(
            c.charger_id
            for c in live.snapshot_chargers(snapshot)
            if c.offset_m <= target_abs + 1.0
        ),
        *offered,
    ]
    for _ in range(MAX_STRETCH if swapping_next else 0):
        best = await _next_best(already | set(stretch_down), stretch_params)
        if best is None or not best.stops:
            break
        stop = best.stops[0]
        stretch_down.append(stop.charger_id)
        if stop.charger_id in offered:
            continue
        # Built BEFORE this one joins the turned-down list: `exclude_charger_ids`
        # is what makes this stop the next one, so a row that carried its own
        # id would tell `replan` to refuse the charger it is offering.
        # Only worth showing if the reserve is what was hiding it. Anything
        # else here is an ordinary alternative the loop above ran out of room
        # for, and dressing it up as a risk would be a lie.
        if stop.arrive_soc < params.reserve_soc:
            found.append(_as_alt(best, stop, below_reserve=True))

    # 3. Somewhere to eat. The ranking above is by minutes, and the quickest
    #    few are regularly a lay-by, a car park and another lay-by — so the
    #    question "where can I actually eat" can go unanswered by a list that
    #    is working perfectly. Keep going until one turns up, and offer it even
    #    though it is slower, because that is the whole point of asking.
    if current is not None and not any(
        a.food_hint for a in [current, *found]
    ):
        for _ in range(MAX_FOOD_SEARCH):
            best = await _next_best(already | set(turned_down), params)
            if best is None:
                break
            stop = _pick(best)
            if stop is None:
                break
            alt = _as_alt(best, stop, below_reserve=False)
            turned_down.append(stop.charger_id)
            if alt.food_hint:
                found.append(alt)
                break

    # Never offer the stop the plan is already going to. The baseline pass
    # normally IS that stop, but it is recomputed from where the car is now and
    # the plan was made further back, so the two can disagree — and an
    # "alternative" that is the stop you already chose is the one row guaranteed
    # to look like a bug.
    rejected = body.reject_charger_id or planned_next
    if rejected:
        found = [a for a in found if a.charger_id != rejected]
    if current is not None:
        found = [a for a in found if a.charger_id != current.charger_id]

    # What is actually AROUND each of them. Last, and only for the handful that
    # survived, because this is the one part of the answer that leaves the
    # building: asking OpenStreetMap about the twelve candidates the DP tried
    # and discarded would be twelve times the request for nothing anybody sees.
    # A failure here costs the chips and nothing else — the list, the ordering
    # and the costs are all already computed.
    shortlist = [a for a in [current, *found] if a is not None]
    near = await amenities.nearby(
        db, [(a.charger_id, a.lat, a.lon) for a in shortlist]
    )
    for alt in shortlist:
        if alt.charger_id in near:
            alt.nearby = near[alt.charger_id]

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
                offset_m=_travelled_m(run.state), lat=run.state["lat"],
                lon=run.state["lon"],
                soc=_live_soc(run, trip, vehicle),
                payload={"at_min": run.state["at_min"]},
            )
        )
        await db.commit()
    return _state_out(run, trip, vehicle)


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
        state=_state_out(run, trip, vehicle),
        plan=ReplanOut.model_validate(run.plan) if run.plan else None,
        trail=trail,
        route_version=_route_version(run),
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
            distance_m=round(_travelled_m(r.state)),
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
