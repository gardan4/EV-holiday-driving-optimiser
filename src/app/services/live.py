"""Following a trip while it is actually being driven.

Pure functions over plain dicts and simulator types — no database, no HTTP, no
clock of its own. `api/runs.py` supplies the persistence and the timestamps;
everything here is unit-testable the same way `simulator.py` is.

Three jobs:

* **Where are you.** A GPS fix snaps to the route polyline and becomes an
  offset along it. Off-route beyond `OFF_ROUTE_M` we stop guessing rather than
  report a confident lie.
* **What is the battery at.** No car exposes its state of charge to a web page,
  so it is inferred from progress since the last figure the driver typed in,
  and every typed figure re-anchors the estimate and calibrates the model.
* **How is it going against the plan.** Ahead or behind, and whether the
  divergence is now big enough to be worth re-planning.

The estimate is always presented with its uncertainty (`soc_uncertainty_pct`),
which grows with the age of the anchor. A bare number would imply a precision
this cannot have.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import replace

from app.services.geo import haversine_m
from app.services.simulator import (
    ChargerNode,
    RouteProfile,
    RouteSegment,
    SimParams,
    SpeedResult,
    VehicleParams,
    charge_forward,
)

# A fix further than this from the polyline means the driver has left the
# route — a diversion, a supermarket, a closed motorway. `project` would snap
# it back and report almost no progress while real energy is being spent, so
# inference stops and says so.
OFF_ROUTE_M = 500.0

# How long the car has to stay off the route before the drive stops waiting for
# it to come back and asks for a new road instead.
#
# Going off route is ordinary — a diversion, a closed slip road, a supermarket,
# a nav app that knows something ORS does not — and the drive used to answer all
# of it the same way: freeze every figure and say "rejoin the route". That is
# right for the thirty seconds it takes to drive round a services and wrong for
# everything else, because the one thing the driver wants at that moment is the
# plan for the road they are ACTUALLY on.
#
# Two minutes rather than instantly, because a re-route is a fresh directions
# call against a free-tier ceiling and a single wild fix must not spend one. Two
# minutes of sustained divergence is a decision, not a glitch — at any real
# speed the car is kilometres away by then and no longer plausibly returning to
# the same point.
REROUTE_AFTER_MIN = 2.0

# What the crow-flies distance is multiplied by when the energy spent off-route
# is billed. Roads are not straight, so the straight line between where the car
# left the route and where it reappeared is a FLOOR on what it drove — and
# under-billing an EV's battery is the dangerous direction to be wrong in.
# 1.3 is the usual road-versus-crow ratio; it is a labelled estimate, which is
# why a reading is asked for immediately afterwards.
OFF_ROUTE_WINDING = 1.3

# Fixes this vague don't move the position; they're recorded but ignored.
MAX_ACCURACY_M = 200.0

# Standing within this of a planned stop, with the wheels not turning, counts
# as plugged in.
AT_CHARGER_M = 150.0

# How near a charger's own coordinate the car has to be for a ping to call it
# "at" that one. Wider than `AT_CHARGER_M` because a services is a few hundred
# metres end to end and the fix is taken between buildings — and safe to widen,
# because being near a charger is not enough on its own: `advance` also
# requires the car to have stopped moving.
AT_CHARGER_MATCH_M = 250.0

# How far a car with a LIVE charge session has to move before it counts as
# having driven away. Wider than `AT_CHARGER_M`, which is the threshold for
# noticing an arrival, because ending a stop and spotting one are not the same
# risk: a stationary phone between the buildings of a services drifts, the
# offset is clamped forward so that drift only ever accumulates, and 150 m of
# it would close the stop under a car that is still plugged in. A car that is
# charging is not moving, so the evidence for "left" should be unambiguous —
# and it still is at any real speed, where one ping covers several hundred
# metres.
LEFT_CHARGER_M = 400.0

# Below this the observed "speed" is noise, not driving.
MIN_OBSERVED_KPH = 5.0

# How far the calibration factor may be pushed by readings. A car that really
# is 40% thirstier than the catalog is a broken model, not a data point.
RUN_FACTOR_MIN = 0.7
RUN_FACTOR_MAX = 1.4

# Below this the modelled energy is too small for a 1% SoC reading to calibrate
# anything: the reading's own noise would swamp the signal.
MIN_KWH_TO_CALIBRATE = 5.0

# Uncertainty on the inferred SoC: a floor, plus growth with anchor age.
SOC_UNCERTAINTY_FLOOR = 0.5
SOC_UNCERTAINTY_PER_30MIN = 0.8
SOC_UNCERTAINTY_CAP = 15.0

# When to suggest re-planning.
BEHIND_MIN_TO_REPLAN = 15.0
SOC_SHORTFALL_TO_REPLAN = 8.0


def new_state(offset_m: float, soc: float, lat: float, lon: float, at_min: float) -> dict:
    """The state a run starts with. `at_min` is minutes since departure."""
    return {
        "at_min": at_min,
        "offset_m": offset_m,
        "lat": lat,
        "lon": lon,
        "soc": soc,
        # The last figure a human actually read off the dashboard, and where
        # and when. Everything else is measured from here.
        "anchor_soc": soc,
        "anchor_offset_m": offset_m,
        "anchor_at_min": at_min,
        "soc_is_measured": True,
        # Accumulated since the anchor. Driving is tracked in kWh and charging
        # in SoC, because that is the space each is naturally computed in;
        # they are combined only when the estimate is read.
        "kwh_used": 0.0,
        "soc_gained": 0.0,
        # Multiplier on consumption learned from the driver's own corrections.
        "run_factor": 1.0,
        "off_route_m": 0.0,
        # When the car first went off the route, on the drive's own clock.
        # Null whenever it is on it. `reroute_due` reads the age of this.
        "off_route_since_min": None,
        # How much road was driven on EARLIER routes. `offset_m` is measured
        # along the CURRENT snapshot, which restarts at zero every time the
        # drive is re-routed; this is what keeps "how far have I come" one
        # number that only ever goes up. See `rebase`.
        "distance_before_m": 0.0,
        "at_charger_id": None,
        "stale": False,
    }


def current_soc(state: dict, usable_kwh: float) -> float:
    """The battery estimate: the anchor, less what driving has taken, plus
    what charging has put back."""
    drop = state["kwh_used"] / max(usable_kwh, 1e-6) * 100.0
    return _clamp(state["anchor_soc"] - drop + state["soc_gained"], 0.0, 100.0)


def charging_soc(
    state: dict, veh: VehicleParams, p: SimParams, at_min: float
) -> float:
    """The battery while plugged in, projected from the CLOCK.

    Charging used to be integrated per ping, over the window each fix
    reported. That works only while the phone is awake — and a phone whose car
    is charging is in a pocket, so `soc_gained` stopped growing the moment the
    screen locked. The estimate froze at whatever it was on arrival, the
    "12 min left" beneath it never counted down, and a driver who came back
    twenty minutes later saw the same numbers they had left.

    Elapsed time needs no telemetry. From the SoC at plug-in, the site's power
    and the minutes since, the same curve the plan used gives an estimate that
    is right when the page is reopened rather than merely old. Pings while
    parked now carry position and nothing else, so there is exactly one thing
    modelling the charge instead of two.

    It assumes the cable is still in, which is why every caller shows it as an
    estimate and why the driver is asked to confirm on the way out. Over-
    projecting a car that finished ten minutes ago is a smaller error than
    under-projecting one by twenty minutes of charging that did happen.
    """
    anchor = state.get("charge_anchor_soc")
    if anchor is None:
        return current_soc(state, veh.usable_kwh)
    minutes = max(0.0, at_min - float(state.get("charge_anchor_min", at_min)))
    kw = float(state.get("charge_kw") or 0.0)
    if kw <= 0.0:
        return float(anchor)
    return charge_forward(veh, float(anchor), minutes, kw, p.charge_power_factor)


def plug_in(state: dict, *, soc: float, at_min: float, kw: float) -> dict:
    """The cable went in. Anchor the projection here.

    One function because three callers reach it — the driver tapping "I'm
    plugged in", the same tap arriving as "I'm plugged in HERE now" with a
    position attached, and the tests — and an anchor set three ways is an
    anchor that ends up meaning three things. `charging_soc` reads all three
    keys together; setting two of them is a charge with no clock.

    Deliberately NOT called by `advance` any more. Being stopped beside a
    charger is evidence about where the car is and none at all about whether
    the cable is in, and the difference is the several minutes at the front of
    a real stop: the queue for a free stall, the walk to the screen, the post
    that turns out to be dead. Inferring it there meant the battery climbed
    through all of them, silently and upwards, which is the direction that
    costs somebody a leg they thought they could make.
    """
    return {
        **state,
        "charge_anchor_soc": float(soc),
        "charge_anchor_min": float(at_min),
        "charge_kw": float(kw),
        # The projection is the model's, not the driver's — even when the
        # figure it starts from was typed in, the moment it starts moving it
        # is an estimate again.
        "soc_is_measured": False,
    }


def bank_charge(
    state: dict, veh: VehicleParams, p: SimParams, until_min: float
) -> dict:
    """Turn the running charge projection into history and drop the anchor.

    Called the moment the car stops being plugged in, by whichever route that
    becomes known — a ping somewhere down the road, a correction, or the driver
    saying what they left on. Everything downstream is then back on ordinary
    anchor-plus-consumption arithmetic, with no second thing modelling the
    battery.

    `until_min` is when the cable came out, which is NOT the same as when we
    found out: a phone that wakes twenty minutes into the next leg would
    otherwise bank twenty minutes of charging that happened while the car was
    on the motorway. Callers pass the last moment the car can be shown to have
    been standing there.
    """
    out = dict(state)
    anchor = out.pop("charge_anchor_soc", None)
    anchor_min = out.pop("charge_anchor_min", None)
    kw = out.pop("charge_kw", None)
    # "Leave at 80%" is about the stop being left, exactly like the accepted
    # first-leg floor: carrying it to the next plug would sit a driver through
    # somebody else's decision four hours later.
    out.pop("leave_at_soc", None)
    if anchor is None:
        return out
    # `anchor_min is None`, not `or` — the anchor minute is a POSITION on the
    # drive's clock and zero is a real one. `or` read a charge that started at
    # minute zero as one with no start time at all, banked it as zero minutes,
    # and handed back a battery that had visibly been charging.
    start_min = float(anchor_min) if anchor_min is not None else float(until_min)
    minutes = max(0.0, float(until_min) - start_min)
    gained = (
        charge_forward(veh, float(anchor), minutes, float(kw or 0.0), p.charge_power_factor)
        - float(anchor)
        if kw
        else 0.0
    )
    out["soc_gained"] = out["soc_gained"] + max(0.0, gained)
    return out


def soc_uncertainty(state: dict) -> float:
    """How far off the estimate could plausibly be, in percentage points.

    Zero the instant the driver types a figure in, widening thereafter. Shown
    next to the number so nobody reads three significant figures into it.
    """
    if state.get("soc_is_measured"):
        return 0.0
    age = max(0.0, state["at_min"] - state["anchor_at_min"])
    band = SOC_UNCERTAINTY_FLOOR + SOC_UNCERTAINTY_PER_30MIN * (age / 30.0)
    return min(band, SOC_UNCERTAINTY_CAP)


def resync(
    state: dict,
    *,
    segments: list[RouteSegment],
    veh: VehicleParams,
    p: SimParams,
    offset_m: float,
    lat: float,
    lon: float,
    at_min: float,
    off_route_m: float,
    speed_kph: float,
) -> dict:
    """Move the car to a position the driver has just asked us to plan from —
    in EITHER direction.

    `advance` clamps the offset forward, and must: a wobbly fix walking the car
    backwards on an ordinary ping would rewrite the drive twenty times an hour.
    But that clamp is the reason a wrong position is permanent. A tap on "I'm
    plugged in here now" at the wrong stop, or a phone that reported from a
    services and then slept while the car drove on, leaves an offset no later
    ping can correct — the drive keeps planning from road the car has not
    reached, and the only ways out were to end the drive or wait until reality
    caught up.

    A re-plan is the right place to fix that, because it is the one moment the
    driver is explicitly asking "from where I am now". So this takes a fresh
    fix at face value, and prices the difference through the same profile the
    plan uses: forward, that bills the drag exactly as a ping would; backward,
    it hands back energy that was billed for road never driven. That symmetry
    is the point — it is the same arithmetic in both directions, so the battery
    after a correction is the battery the drive would have had if the wrong
    position had never been entered.

    Priced at the plan's speed rather than an observed one: a correction has no
    window to observe over, and the plan's cruise is the honest stand-in.
    """
    prev = state["offset_m"]
    out = dict(state)
    # Never rewind the clock. Time really has passed, whatever the position did.
    out["at_min"] = max(at_min, state["at_min"])
    out["lat"] = lat
    out["lon"] = lon
    out["off_route_m"] = off_route_m
    out["stale"] = False
    out["offset_m"] = offset_m

    if abs(offset_m - prev) > 1e-6:
        prof = RouteProfile(segments, speed_kph, veh, replace(p, aux_kw=0.0))
        out["kwh_used"] = max(
            0.0, out["kwh_used"] + (prof.kwh_at(offset_m) - prof.kwh_at(prev))
        )
    # Standing at a charger is a claim about a position that has just changed.
    # `advance` re-establishes it on the next ping if the car really is there.
    if abs(offset_m - prev) >= AT_CHARGER_M:
        # The car is no longer where it was plugged in, so the projection stops
        # here and becomes history. Banked to NOW rather than to a departure we
        # cannot place: a correction says where the car is, never when it left,
        # and dropping the charge outright would hand back a battery that never
        # took the energy it plainly did.
        out = bank_charge(out, veh, p, out["at_min"])
        out["at_charger_id"] = None
        # The undo would restore the state BEFORE an arrival, and that is no
        # longer the state this position follows on from.
        out.pop("arrive_undo", None)
    return out


def reroute_due(state: dict) -> bool:
    """Has the car been off the route long enough to want a new one?

    Read off the drive's own clock rather than the wall clock, so a phone that
    slept through the detour and woke up 40 minutes later is judged on the same
    scale as one that reported throughout.
    """
    since = state.get("off_route_since_min")
    if since is None or not state.get("stale"):
        return False
    return (state.get("at_min", 0.0) - float(since)) >= REROUTE_AFTER_MIN


def rebase(
    state: dict,
    *,
    veh: VehicleParams,
    p: SimParams,
    lat: float,
    lon: float,
    at_min: float,
    detour_m: float,
    speed_kph: float,
    unbilled_min: float,
) -> dict:
    """Move the drive onto a NEW route, starting from where the car is.

    Re-routing replaces the road, and `offset_m` is measured along it — so the
    car lands at zero on an axis that did not exist a moment ago. Two things
    have to survive that.

    **How far you have come only ever goes up.** `distance_before_m` banks the
    old route's driven distance, so the breadcrumb trail, the review and the
    drive's own "distance" stay one monotonic number across any number of
    re-routes. Everything that indexes INTO the route — the profile, the
    charger offsets, the plan's `offset_base_m` — keeps using `offset_m` alone,
    which is why it is rebased rather than accumulated in place.

    **The detour is billed, and bounded.** While the car was off-route
    `advance` returned early and spent nothing, which is right when the
    position is unknown and wrong the moment it is known again: the energy went
    somewhere. There is no geometry for road we were not following, so it is
    priced as flat road at the plan's cruise over `detour_m` — the crow-flies
    gap, widened by `OFF_ROUTE_WINDING`, because a straight line is a floor on
    what was actually driven.

    The bound is what stops that being reckless. A phone that slept through the
    whole detour reappears wherever it reappears, and the straight line to it
    can be hundreds of kilometres — a fix that surfaces in another country
    would otherwise bill a full battery and the re-plan would answer "no plan
    reaches the destination from 0%". So the distance is capped at what the car
    could have covered in `unbilled_min` at `speed_kph`: whatever the fix says,
    the clock says you cannot have driven further than that.

    It is an estimate either way and is treated as one: `soc_is_measured` goes
    false, so the screen asks for a reading and `apply_reading` corrects it.
    """
    out = dict(state)
    out["distance_before_m"] = float(state.get("distance_before_m", 0.0)) + float(
        state.get("offset_m", 0.0)
    )
    out["offset_m"] = 0.0
    # The anchor is a position on the OLD axis, and every later reading is
    # measured from it. Move it with the car; `kwh_used` already holds what has
    # been spent since it was set, so nothing is double-counted.
    out["anchor_offset_m"] = 0.0
    out["at_min"] = max(at_min, state.get("at_min", 0.0))
    out["lat"] = lat
    out["lon"] = lon
    out["off_route_m"] = 0.0
    out["off_route_since_min"] = None
    out["stale"] = False

    reachable_m = max(0.0, unbilled_min) / 60.0 * speed_kph * 1000.0
    billed_m = min(max(0.0, detour_m) * OFF_ROUTE_WINDING, reachable_m)
    if billed_m > 0.0:
        leg = [RouteSegment(dist_m=billed_m, freeflow_kph=speed_kph)]
        prof = RouteProfile(leg, speed_kph, veh, replace(p, aux_kw=0.0))
        out["kwh_used"] = out["kwh_used"] + max(0.0, prof.kwh_at(billed_m))
        # The hours themselves still cost the heater, whatever the distance.
        out["kwh_used"] = out["kwh_used"] + p.aux_kw * (max(0.0, unbilled_min) / 60.0)
    out["soc_is_measured"] = False

    # A charge session cannot survive a road change: the plug it was anchored
    # to is on the route we have just left. Banked at now, exactly as `resync`
    # does for the same reason.
    if state.get("charge_anchor_soc") is not None:
        out = bank_charge(out, veh, p, out["at_min"])
    out["at_charger_id"] = None
    # The undo would restore a state on the old axis, which is no longer a
    # position this drive can be put back to.
    out.pop("arrive_undo", None)
    out.pop("need_soc_next", None)
    return out


def advance(
    state: dict,
    *,
    segments: list[RouteSegment],
    veh: VehicleParams,
    p: SimParams,
    offset_m: float,
    lat: float,
    lon: float,
    at_min: float,
    off_route_m: float,
    moving_s: float,
    stationary_s: float,
    stop_power_kw: float | None = None,
    at_charger_id: str | None = None,
) -> dict:
    """Fold one GPS ping into the state and return the new one.

    Accumulates per ping rather than integrating from the anchor in one go.
    Energy is convex in speed — `a + b·v²` — so twenty minutes at 150 followed
    by twenty at 90 costs more than forty at 120, and collapsing a long window
    to its average would quietly under-bill exactly the fast stretches that
    matter. Ping by ping the window is short enough for the error to vanish,
    the work is O(1), and a dropped ping degrades instead of corrupting.
    """
    prev_offset = state["offset_m"]
    dt_move_h = max(0.0, moving_s) / 3600.0
    dt_still_h = max(0.0, stationary_s) / 3600.0

    out = dict(state)
    out["at_min"] = at_min
    out["lat"] = lat
    out["lon"] = lon
    out["off_route_m"] = off_route_m

    if off_route_m > OFF_ROUTE_M:
        # Snapping this to the polyline would report no progress while the car
        # is demonstrably burning energy somewhere else. Say "unknown" instead.
        #
        # The stop is NOT ended here. A car that is standing at a charger has
        # not gone anywhere, and a fix that wanders off the polyline while it
        # sits there — a phone carried into the services, a poor stationary fix
        # between buildings — is a fact about the fix, not about the car.
        out["stale"] = True
        if state.get("off_route_since_min") is None:
            out["off_route_since_min"] = at_min
        return out
    out["stale"] = False
    out["off_route_since_min"] = None

    # Never let a wobbly fix walk the car backwards along the route.
    dx = max(0.0, offset_m - prev_offset)
    out["offset_m"] = max(prev_offset, offset_m)

    # A stop ends when the car MOVES, or when the driver says it does. Never
    # merely because this ping's fix stopped matching a charger.
    #
    # `parked` used to be `at_charger_id is not None and …`, where
    # `at_charger_id` is what `nearest_charger` matched on THIS fix — so a
    # drift past its 250 m radius closed the stop under a car that had not
    # moved a metre. That is an ordinary thing for a fix to do at a big
    # services, and it is guaranteed after a HAND-DECLARED arrival, where the
    # driver pressed "I'm plugged in here now" precisely because the phone did
    # not know where it was: the next automatic ping then quietly overruled
    # them, the stop card vanished, and the screen went back to counting down
    # to the next charger while they were still plugged in. Reported from the
    # road, mid-charge.
    #
    # So a held arrival survives a fix that says nothing, and a fresh match
    # still wins when there is one — moving from one plug to another is a real
    # thing to do, and it is the position that knows.
    held = state.get("at_charger_id")
    # Keyed on being AT the charger rather than on the cable being in, because
    # standing here and charging here stopped being the same thing — a car
    # waiting for a free stall is just as stationary as one plugged into it,
    # and it is the standing that this radius is about.
    still = dx < (LEFT_CHARGER_M if held is not None else AT_CHARGER_M)
    here = at_charger_id if at_charger_id is not None else (held if still else None)
    parked = here is not None and still
    # Power for the site we are actually at: this fix's when it matched one,
    # otherwise the power the session was anchored with.
    kw = stop_power_kw if at_charger_id is not None else state.get("charge_kw")
    if parked:
        # Standing at a charger. Whether the CABLE IS IN is a different
        # question and this cannot answer it — arriving used to imply it, so
        # the battery started climbing while the driver was still queueing for
        # a free stall, walking to the building, or finding the post dead. The
        # projection now waits to be told (`POST /runs/{id}/charging`), which
        # is the only source that ever actually knew.
        out["at_charger_id"] = here
        if out.get("charge_anchor_soc") is not None:
            # Already plugged in. The charge is not integrated here — see
            # `charging_soc`: a ping-driven integral stops the moment the phone
            # sleeps, which is the whole of the time this matters. The anchor
            # plus the clock does it instead.
            if kw:
                out["charge_kw"] = kw
            # No aux draw: at a DC charger the cabin is served by the charger,
            # not the pack, which is what drivers actually observe.
            out["soc_is_measured"] = False
        return out

    if not parked:
        if out.get("charge_anchor_soc") is not None:
            # Driving away. Charging stopped when the car pulled out, and the
            # minutes this ping spent MOVING are the ones it cannot have been
            # plugged in for — a phone that only wakes up on the motorway would
            # otherwise bank the whole gap as charge.
            out = bank_charge(out, veh, p, at_min - dt_move_h * 60.0)
        else:
            # `bank_charge` drops the leave target too, and it returns early
            # when there is no anchor — so a target set at a plug the driver
            # then never used would have ridden along to the next one and sat
            # somebody through a decision they made about a different stop.
            out.pop("leave_at_soc", None)

    out["at_charger_id"] = here if parked else None

    # The speed to price the drag at: the average while ACTUALLY MOVING, with
    # standing time billed separately below. The planned cruise would over-bill
    # a traffic jam, and a plain distance/elapsed average would under-bill the
    # motorway either side of it.
    if dt_move_h > 1e-9 and dx > 0.0:
        v_obs = _clamp((dx / 1000.0) / dt_move_h, MIN_OBSERVED_KPH, veh.top_speed_kph)
        # `p.consumption_factor` already carries the calibration learned from
        # this drive's readings — the caller builds it that way, and the
        # re-planner uses the same params. Multiplying by `run_factor` again
        # here would square the correction: a car measured 20% thirsty would be
        # modelled 44% thirsty, and every reading would make it worse.
        prof = RouteProfile(segments, v_obs, veh, replace(p, aux_kw=0.0))
        out["kwh_used"] = out["kwh_used"] + max(
            0.0, prof.kwh_at(out["offset_m"]) - prof.kwh_at(prev_offset)
        )

    # Conditioning is billed by the hour and keeps running at a standstill —
    # which is exactly why it is not part of the per-kilometre term.
    out["kwh_used"] = out["kwh_used"] + p.aux_kw * (dt_move_h + dt_still_h)
    out["soc_is_measured"] = False
    return out


def apply_reading(
    state: dict,
    soc: float,
    usable_kwh: float,
    *,
    veh: VehicleParams | None = None,
    p: SimParams | None = None,
    leaving: bool = False,
) -> dict:
    """Re-anchor on a figure the driver read off the dashboard, and use the
    error to calibrate.

    Every correction is free ground truth about this car, on this day, in this
    wind — none of which any plan-time parameter can know. After a couple of
    them the remaining-range prediction stops being a catalog figure and starts
    being a measurement.

    A reading typed AT a charger has a running charge projection behind it, and
    that projection has to be settled before the arithmetic here can mean
    anything: `soc_gained` is what the calibration adds back to compare
    consumption with consumption, and while the projection is live it holds
    zero. So the charge is banked first (needs `veh`/`p`), the reading is
    scored against a complete picture, and the projection then restarts from
    the figure just typed rather than from what it had guessed by now.

    `leaving` is the driver saying they are pulling out: the typed figure is
    the last word on this stop, so the projection ends instead of restarting
    and the car is no longer at a charger. It is a separate flag and not
    inferred from the number, because "I'm on 62 now" and "I'm leaving on 62"
    are different sentences — one asks the plan to keep counting up, the other
    says stop.
    """
    plugged_kw = state.get("charge_kw") if state.get("charge_anchor_soc") is not None else None
    if veh is not None and p is not None and state.get("charge_anchor_soc") is not None:
        state = bank_charge(state, veh, p, state["at_min"])
    out = dict(state)
    # Compare CONSUMPTION with consumption. Charging since the anchor has to be
    # added back, or a reading taken after a stop would score the energy the
    # charger put in as energy the car failed to use.
    gained_kwh = state["soc_gained"] / 100.0 * usable_kwh
    modelled = state["kwh_used"]
    observed = (state["anchor_soc"] - soc) / 100.0 * usable_kwh + gained_kwh

    ratio = observed / modelled if modelled > 0.0 else None
    plausible = ratio is not None and RUN_FACTOR_MIN <= ratio <= RUN_FACTOR_MAX
    if modelled >= MIN_KWH_TO_CALIBRATE and plausible:
        # Half-and-half: one reading should move the model, not define it.
        blended = 0.5 * state["run_factor"] + 0.5 * state["run_factor"] * ratio
        out["run_factor"] = _clamp(blended, RUN_FACTOR_MIN, RUN_FACTOR_MAX)
        out["last_error_pct"] = (observed - modelled) / modelled * 100.0
    else:
        # Deliberately NOT clamped-and-applied. A discrepancy this large isn't
        # a thirstier car, it's something the model never saw — a charge we
        # weren't pinged through, a detour, a driver correcting a typo. Pinning
        # the factor to the edge of its range on that evidence would bake the
        # gap in permanently. Re-anchor and carry on.
        out["last_error_pct"] = None

    if leaving:
        # The stop is over. Nothing is left projecting a charge, and the drive
        # is back on the road — the next ping bills the drag from here.
        for k in ("charge_anchor_soc", "charge_anchor_min", "charge_kw"):
            out.pop(k, None)
        out["at_charger_id"] = None
        # An arrival you have already driven away from is not one to take back.
        out.pop("arrive_undo", None)
    elif plugged_kw:
        # Still plugged in: the projection restarts from the figure just read.
        out["charge_anchor_soc"] = soc
        out["charge_anchor_min"] = state["at_min"]
        out["charge_kw"] = plugged_kw

    out["anchor_soc"] = soc
    out["anchor_offset_m"] = state["offset_m"]
    out["anchor_at_min"] = state["at_min"]
    out["kwh_used"] = 0.0
    out["soc_gained"] = 0.0
    out["soc"] = soc
    out["soc_is_measured"] = True
    out["stale"] = False
    return out


# ---------------------------------------------------------------------------
# Measuring the drive against the plan it came from
# ---------------------------------------------------------------------------


def plan_window_at(timeline: list, offset_m: float) -> tuple[float, float]:
    """When the plan expected to be at this point — as an interval.

    Stops appear in the timeline TWICE at the same offset, once on arrival and
    once on departure, so a single "planned time here" does not exist while the
    car is at a charger. Returning the pair lets the caller report nothing at
    all during the stop, instead of flapping between "12 minutes ahead" and
    "8 behind" for the whole time the driver stands at the plug.
    """
    if not timeline:
        return (0.0, 0.0)
    xs = [pt[1] for pt in timeline]
    ts = [pt[0] for pt in timeline]
    if offset_m <= xs[0]:
        return (ts[0], ts[0])
    if offset_m >= xs[-1]:
        return (ts[-1], ts[-1])

    i = bisect_left(xs, offset_m)
    if i < len(xs) and xs[i] == offset_m:
        j = i
        while j + 1 < len(xs) and xs[j + 1] == offset_m:
            j += 1
        return (ts[i], ts[j])

    lo = i - 1
    x0, x1 = xs[lo], xs[lo + 1]
    if x1 == x0:
        return (ts[lo], ts[lo + 1])
    f = (offset_m - x0) / (x1 - x0)
    t = ts[lo] + f * (ts[lo + 1] - ts[lo])
    return (t, t)


def plan_soc_at(timeline: list, offset_m: float) -> float:
    """The SoC the plan predicted here. At a stop this is the DEPARTURE value —
    the useful comparison is "should I have this much leaving", not the dip on
    the way in."""
    if not timeline:
        return 0.0
    _, hi = plan_window_at(timeline, offset_m)
    best = timeline[0][2]
    for t, x, s in timeline:
        if x <= offset_m + 1e-6 and t <= hi + 1e-6:
            best = s
    return best


def schedule_delta_min(
    timeline: list,
    offset_m: float,
    elapsed_min: float,
    *,
    leaving_in_min: float | None = None,
) -> float:
    """Minutes behind the plan; negative is ahead. Zero while inside the
    window a stop occupies.

    `leaving_in_min` is what makes this true AT A PLUG, and without it the
    arrival time was blind to the one number that decides how long a charge
    stop takes. The window a stop occupies is [lo, hi] — planned arrival to
    planned departure — and inside it this answered "on schedule" whatever the
    battery said. So a driver who plugged in eight points below plan, was told
    by the card in front of them that the stop had just grown by four minutes,
    and looked up at an arrival time that had not moved: two numbers on one
    screen, derived from different things, and only one of them answering the
    question. It did not start moving until the car OVERRAN the planned
    departure, by which time the delay had already happened.

    Given the minutes still to go on the charge, the stop is priced on when it
    will END rather than on what the clock says now: `elapsed + leaving_in` is
    the projected departure, and it is late or early against `hi`. Note this
    degrades exactly to the old answer when the charge is going to plan — the
    projected departure is then `hi` and the delta is zero — which is the check
    that it is a generalisation and not a different rule.
    """
    lo, hi = plan_window_at(timeline, offset_m)
    if leaving_in_min is not None:
        return (elapsed_min + max(0.0, leaving_in_min)) - hi
    if lo - 1e-9 <= elapsed_min <= hi + 1e-9:
        return 0.0
    return elapsed_min - hi if elapsed_min > hi else elapsed_min - lo


def needs_replan(
    *,
    delta_min: float,
    soc_now: float,
    soc_planned: float,
    stale: bool,
    soc_accepted: bool = False,
) -> tuple[bool, list[str]]:
    """Whether reality has diverged enough to be worth re-optimising, and the
    plain-language reasons — which are what the driver is actually shown.

    `soc_accepted` is the driver having already answered the battery half of
    this: they were shown what they would arrive on, and chose to go anyway.
    Repeating it back as "reality has drifted from the plan" is the app asking
    a question it has been given an answer to.
    """
    reasons: list[str] = []
    if stale:
        return (False, ["off the planned route, so position and battery are unknown"])
    if delta_min >= BEHIND_MIN_TO_REPLAN:
        reasons.append(f"{delta_min:.0f} min behind the plan")
    shortfall = soc_planned - soc_now
    if shortfall >= SOC_SHORTFALL_TO_REPLAN and not soc_accepted:
        reasons.append(
            f"battery {shortfall:.0f}% below what the plan expected by now"
        )
    return (bool(reasons), reasons)


def benchmark(
    *,
    original_speed_kph: float,
    original_total_min: float,
    elapsed_min: float,
    remaining: SpeedResult,
    original_stops_remaining: int,
) -> dict:
    """The revised journey set against the promise it is replacing.

    The point of re-planning mid-drive is not a fresh number in isolation — it
    is knowing what the delay actually cost, and whether the new plan claws any
    of it back.
    """
    live_total = elapsed_min + (remaining.total_min or 0.0)
    return {
        "original_speed_kph": original_speed_kph,
        "original_total_min": original_total_min,
        "live_total_min": live_total,
        "delta_min": live_total - original_total_min,
        "original_stops_remaining": original_stops_remaining,
        "live_stops_remaining": remaining.n_stops or 0,
    }


def nearest_charger(
    chargers: list, lat: float, lon: float, max_m: float = AT_CHARGER_MATCH_M
) -> tuple[str | None, float | None]:
    """Which charger, of ANY on the corridor, the car is standing at.

    Not just the planned stops. `nearest_planned_stop` matched those four sites
    and nothing else, so a driver who stopped anywhere the plan had not chosen
    — a services they liked, the only free stall in town, a charger picked from
    the alternatives — was invisible to the drive no matter how good the fix
    was. The screen kept counting down to a charger 77 km up the road while its
    driver stood plugged in somewhere else, twice, which is how this was found.

    Matched on STRAIGHT-LINE distance rather than along the route, because the
    snapshot's chargers carry geometry-space offsets while a ping's position is
    in segment space: the two axes differ by a few tenths of a percent, which
    over 500 km is kilometres — far outside any radius that means "standing at
    this one". Coordinates have no such problem.

    This says only that the car is NEAR a charger; `advance` decides whether it
    is stopped at one, and a site passed at 130 km/h fails that test because
    the offset moves 900 m between pings.
    """
    best_id, best_kw, best_d = None, None, max_m
    for c in chargers:
        d = haversine_m(lat, lon, _stop_lat(c), _stop_lon(c))
        if d <= best_d:
            best_id, best_kw, best_d = _stop_id(c), _stop_power(c), d
    return best_id, best_kw


def nearest_planned_stop(
    stops: list, offset_m: float, lat: float, lon: float
) -> tuple[str | None, float | None]:
    """Which planned stop, if any, the car is standing at. Matched on distance
    along the route rather than straight-line distance, because the route is
    the only axis the rest of this module speaks."""
    best_id, best_kw, best_d = None, None, AT_CHARGER_M
    for s in stops:
        d = abs(_stop_offset(s) - offset_m)
        if d <= best_d:
            best_id, best_kw, best_d = _stop_id(s), _stop_power(s), d
    return best_id, best_kw


def _stop_offset(s) -> float:
    return s["offset_m"] if isinstance(s, dict) else s.offset_m


def _stop_id(s) -> str:
    return s["charger_id"] if isinstance(s, dict) else s.charger_id


def _stop_power(s) -> float:
    return s["power_kw"] if isinstance(s, dict) else s.power_kw


def _stop_lat(s) -> float:
    return s["lat"] if isinstance(s, dict) else s.lat


def _stop_lon(s) -> float:
    return s["lon"] if isinstance(s, dict) else s.lon


def snapshot_segments(snapshot: dict) -> list[RouteSegment]:
    return [RouteSegment(**s) for s in snapshot["segments"]]


def snapshot_chargers(snapshot: dict) -> list[ChargerNode]:
    return [ChargerNode(**c) for c in snapshot["chargers"]]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _isclose(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(a, b, abs_tol=tol)
