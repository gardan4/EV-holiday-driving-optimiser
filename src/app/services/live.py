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
    if anchor is None:
        return out
    minutes = max(0.0, float(until_min) - float(anchor_min or until_min))
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
        return out
    out["stale"] = False

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
    still = dx < (
        LEFT_CHARGER_M if state.get("charge_anchor_soc") is not None else AT_CHARGER_M
    )
    here = at_charger_id if at_charger_id is not None else (held if still else None)
    parked = here is not None and still
    # Power for the site we are actually at: this fix's when it matched one,
    # otherwise the power the session was anchored with.
    kw = stop_power_kw if at_charger_id is not None else state.get("charge_kw")
    if parked and kw:
        # Plugged in. The charge is not integrated here — see `charging_soc`:
        # a ping-driven integral stops the moment the phone sleeps, which is
        # the whole of the time this matters. What happens instead is that the
        # first ping at a charger ANCHORS the charge, and the clock does the
        # rest.
        if out.get("charge_anchor_soc") is None:
            out["charge_anchor_soc"] = current_soc(out, veh.usable_kwh)
            out["charge_anchor_min"] = at_min
            out["charge_kw"] = kw
        out["at_charger_id"] = here
        # No aux draw: at a DC charger the cabin is served by the charger, not
        # the pack, which is what drivers actually observe.
        out["soc_is_measured"] = False
        return out

    if not parked and out.get("charge_anchor_soc") is not None:
        # Driving away. Charging stopped when the car pulled out, and the
        # minutes this ping spent MOVING are the ones it cannot have been
        # plugged in for — a phone that only wakes up on the motorway would
        # otherwise bank the whole gap as charge.
        out = bank_charge(out, veh, p, at_min - dt_move_h * 60.0)

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


def schedule_delta_min(timeline: list, offset_m: float, elapsed_min: float) -> float:
    """Minutes behind the plan; negative is ahead. Zero while inside the
    window a stop occupies."""
    lo, hi = plan_window_at(timeline, offset_m)
    if lo - 1e-9 <= elapsed_min <= hi + 1e-9:
        return 0.0
    return elapsed_min - hi if elapsed_min > hi else elapsed_min - lo


def needs_replan(
    *, delta_min: float, soc_now: float, soc_planned: float, stale: bool
) -> tuple[bool, list[str]]:
    """Whether reality has diverged enough to be worth re-optimising, and the
    plain-language reasons — which are what the driver is actually shown."""
    reasons: list[str] = []
    if stale:
        return (False, ["off the planned route, so position and battery are unknown"])
    if delta_min >= BEHIND_MIN_TO_REPLAN:
        reasons.append(f"{delta_min:.0f} min behind the plan")
    shortfall = soc_planned - soc_now
    if shortfall >= SOC_SHORTFALL_TO_REPLAN:
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
