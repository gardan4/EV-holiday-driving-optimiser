"""Trip simulator: for one route + one EV, find the time-optimal charging plan
at each candidate cruise speed. Pure Python, no I/O — fully unit-testable.

Model
-----
- Consumption: Wh/km(v) = a + b·v² (× a conditions factor), per vehicle.
- DC charging: piecewise-linear power-vs-SoC curve (cable-side, from public
  fast-charge tests), clamped by the charger site's max power. Charging time is
  the numeric integral of usable_kWh·dSoC / P(SoC).
- Effective speed per segment: the route's free-flow speed caps the cruise
  speed, EXCEPT on motorway-like segments (free-flow ≥ `motorway_freeflow_kph`)
  where ORS/OSM data systematically under-caps: there the cruise speed applies
  up to a per-country legal cap. (`freeflow_cap_only=True` restores the naive
  clamp for comparison.)

Optimizer
---------
Exact forward DP over (stop node along the route, arrival-SoC bucket). Nodes
are the origin, every candidate charger (sorted by route offset), and the
destination; driving past a charger without stopping is free (transitions jump
directly to any later node). Charge-target choice collapses into a prefix-min
over the per-site cumulative charge-time curve F(SoC), which keeps the DP at
O(nodes² · buckets) instead of O(nodes² · buckets²).

The DP plans on a 2.5% SoC grid; the chosen plan is then re-simulated exactly
(continuous SoC) to produce the reported itinerary, totals, and the playback
timeline, so displayed numbers carry no grid error.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass, field, replace

# SoC grid for the DP (arrival buckets AND charge targets). 2.5% keeps the
# plan-quality loss negligible while the exact re-simulation removes any
# reporting error.
SOC_BUCKET = 2.5
N_BUCKETS = int(100 / SOC_BUCKET) + 1  # 0, 2.5, …, 100

# Fine integration step for charge-time integrals (percent SoC).
_INTEGRATION_STEP = 0.5

_INF = math.inf


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VehicleParams:
    usable_kwh: float
    a_wh_km: float
    b_wh_km_per_kph2: float
    # ((soc_pct, kw), ...) sorted by soc_pct, covering 0..100.
    charge_curve: tuple[tuple[float, float], ...]
    # Kerb mass + a nominal load; only used for the elevation term.
    mass_kg: float = 1900.0
    # Electronically limited top speed. Simulating past it is fiction.
    top_speed_kph: float = 180.0
    # Plug → battery efficiency. Charge *times* come straight from the curve
    # (which is measured at the cable), so this is only used to price energy.
    charge_efficiency: float = 0.92

    @classmethod
    def from_vehicle(cls, vehicle) -> "VehicleParams":
        """Build from an `app.models.Vehicle` row (or anything shaped like it)."""
        cons = vehicle.consumption
        return cls(
            usable_kwh=vehicle.usable_kwh,
            a_wh_km=cons["a_wh_km"],
            b_wh_km_per_kph2=cons["b_wh_km_per_kph2"],
            charge_curve=tuple((float(s), float(p)) for s, p in vehicle.charge_curve),
            mass_kg=float(getattr(vehicle, "mass_kg", None) or 1900.0),
            top_speed_kph=float(getattr(vehicle, "top_speed_kph", None) or 180.0),
            charge_efficiency=float(getattr(vehicle, "charge_efficiency", None) or 0.92),
        )


@dataclass(frozen=True)
class RouteSegment:
    dist_m: float
    freeflow_kph: float
    country: str = ""  # ISO-3166 alpha-2; "" → default cap
    climb_m: float = 0.0    # metres gained over this segment
    descent_m: float = 0.0  # metres lost over this segment


@dataclass(frozen=True)
class ChargerNode:
    charger_id: str
    name: str
    offset_m: float
    power_kw: float
    detour_min: float = 0.0
    operator: str | None = None
    lat: float = 0.0
    lon: float = 0.0


def _default_country_caps() -> dict[str, float]:
    # Motorway legal caps (kph). DE: derestricted stretches → effectively no cap
    # (the cruise speed itself is the limit). Night-time NL is 130 (100 applies
    # 06-19h only — an accepted v1 simplification, disclosed in the UI).
    return {
        "DE": _INF,
        "NL": 130.0,
        "BE": 120.0,
        "AT": 130.0,
        "CH": 120.0,
        "IT": 130.0,
        "FR": 130.0,
        "LU": 130.0,
    }


# Energy recovered per joule of potential energy given back on a descent.
REGEN_EFFICIENCY = 0.65
# Battery → wheels efficiency for the climbing term.
DRIVETRAIN_EFFICIENCY = 0.90
GRAVITY = 9.81

# What the car is carrying. `VehicleParams.mass_kg` is kerb mass plus a nominal
# load, so payload enters as a *deviation* from that nominal — which keeps the
# default a no-op and every existing permalink's answer unchanged.
OCCUPANT_KG = 75.0
NOMINAL_LOAD_KG = 180.0  # the seed catalog's documented convention
# Rolling resistance coefficient for a low-rolling-resistance EV tyre.
ROLLING_CRR = 0.010
# Extra Wh/km per kilogram carried: Crr·m·g newtons dragged over 1000 m gives
# joules, /3600 gives Wh, then battery→wheels. Works out at ~3.0 Wh/km per
# 100 kg — about 1.7% of a 180 Wh/km motorway cruise, which matches what
# real-world payload tests report.
_ROLLING_WH_KM_PER_KG = (
    ROLLING_CRR * GRAVITY * 1000.0 / 3600.0 / DRIVETRAIN_EFFICIENCY
)


def payload_extra_kg(occupants: int, luggage_kg: float) -> float:
    """Payload beyond the nominal load already inside `VehicleParams.mass_kg`.

    Negative for a lone driver with an empty boot, which is real: the catalog
    mass assumes two people and a weekend bag.
    """
    return occupants * OCCUPANT_KG + luggage_kg - NOMINAL_LOAD_KG


@dataclass(frozen=True)
class SimParams:
    depart_soc: float = 100.0
    target_soc: float = 10.0     # minimum SoC on arrival at the destination
    reserve_soc: float = 10.0    # never plan to dip below this between stops
    stop_overhead_min: float = 5.0
    charge_cap_soc: float = 100.0  # ceiling for charge targets (DP may stop lower)
    consumption_factor: float = 1.0  # conditions multiplier on Wh/km
    # Cabin and battery conditioning, in kW. Deliberately NOT part of
    # `consumption_factor`: heating is a roughly constant power draw, so its
    # cost per kilometre falls as you speed up. Folding it into a multiplier on
    # Wh/km — which is dominated by the v² drag term — would claim the heater
    # pulls 4 kW at 90 km/h and 15 kW at 160, and would bias the whole answer
    # towards driving slowly in exactly the weather this app is used in.
    aux_kw: float = 0.0
    # Cold packs charge far slower than the datasheet curve. This scales the
    # whole curve; it is the single biggest lever on the winter answer.
    charge_power_factor: float = 1.0
    # People and luggage beyond the nominal load already inside the car's
    # `mass_kg`. Deliberately NOT part of `consumption_factor`: the weight
    # penalty is rolling drag, which is per-kilometre and roughly independent
    # of speed. A multiplier on `a + b·v²` would instead scale with the drag
    # term, claiming a full car costs three times as much at 160 as at 90.
    extra_mass_kg: float = 0.0
    # What fraction of a charger's RATED power you actually receive. A 350 kW
    # cabinet shared with the car next to you delivers nowhere near 350, and a
    # busy Supercharger splits a stall pair. Distinct from queue_min: this is
    # degraded throughput while plugged in, not time spent waiting for a plug.
    site_power_factor: float = 1.0
    # Minutes queuing for a free stall, added per stop. More stops = more
    # exposure, which is the honest cost of the many-short-stops strategy.
    queue_min: float = 0.0
    # Rest rules: after this much DRIVING you need a break of `rest_min`.
    # Time spent at a charger counts towards it, so charging stops usually
    # absorb the break for free. 0 disables the rule.
    rest_interval_min: float = 0.0
    rest_min: float = 0.0
    # Live re-planning: what the driver has already done before this plan
    # starts. Two numbers rather than one because driving ACCRUES the break
    # debt and standing still PAYS it, and a mid-journey plan inherits both.
    # Both 0 for a plan that starts at the origin.
    prior_drive_min: float = 0.0
    prior_rest_credit_min: float = 0.0
    # Fraction of *derestriction-eligible* German autobahn you will actually be
    # able to press on (roadworks, traffic, trucks). ~0.3 is realistic; 1.0 is
    # the old, flattering assumption.
    autobahn_open_share: float = 0.3
    # Driving style: how far above the legal cap you are willing to sit, and
    # how much above a non-motorway road's own flow speed you will push.
    over_cap_kph: float = 0.0
    over_freeflow_factor: float = 1.0
    # Price per kWh delivered at the plug, for the cost readout.
    price_per_kwh: float = 0.59
    # Free-flow ≥ this → "motorway-like": ORS/OSM under-caps there, so the
    # cruise speed applies up to the country's legal cap instead of free-flow.
    #
    motorway_freeflow_kph: float = 105.0
    # The same threshold where we don't know the country. 105 was drawn around
    # European motorways and quietly excluded American ones: ORS rates a 70 mph
    # interstate at ~98, so a third of a Las Vegas → Washington route was
    # modelled as back roads and driven at 105 while the driver had asked for
    # 170. By distance the 95-105 band is 34% of that route and 1.8% of
    # Utrecht → Innsbruck.
    #
    # Applied only OFF the modelled countries, because inside them a free-flow
    # of 95 on a motorway is real information — roadworks, a signposted zone —
    # and promoting those is how a German roadworks stretch ends up being
    # driven at 130.
    motorway_freeflow_unmodelled_kph: float = 95.0
    # DE only: free-flow ≥ this → genuinely derestricted (no cap). Between the
    # two thresholds a German segment is a signposted zone → default cap.
    derestrict_freeflow_kph: float = 118.0
    freeflow_cap_only: bool = False  # True → naive min(v, freeflow) everywhere
    # Treat EVERY motorway as derestricted, the way German autobahn already is:
    # a what-if for "how much would ignoring the limits actually save me?".
    # Ordinary roads are untouched — their own flow speed still governs, because
    # a back road can't be driven at 180 whatever the law says.
    ignore_speed_limits: bool = False
    default_country_cap_kph: float = 130.0
    country_caps: dict[str, float] = field(default_factory=_default_country_caps)


# ---------------------------------------------------------------------------
# Outputs
# ---------------------------------------------------------------------------


@dataclass
class StopPlan:
    charger_id: str
    name: str
    operator: str | None
    lat: float
    lon: float
    power_kw: float
    offset_m: float
    arrive_min: float   # minutes since departure (includes prior charging)
    arrive_soc: float
    depart_soc: float
    charge_min: float
    detour_min: float


@dataclass
class SpeedResult:
    speed_kph: float
    feasible: bool
    total_min: float | None = None
    drive_min: float | None = None
    charge_min: float | None = None
    n_stops: int | None = None
    stops: list[StopPlan] = field(default_factory=list)
    # (t_min, dist_m, soc) samples for playback: stop boundaries + ~5-min drive
    # intervals. Distance is the along-route offset.
    timeline: list[tuple[float, float, float]] = field(default_factory=list)
    # Break time the charging stops did NOT already cover.
    rest_min: float = 0.0
    # Energy bought at the plug, and what it costs.
    energy_kwh: float = 0.0
    cost_eur: float = 0.0


# ---------------------------------------------------------------------------
# Physics primitives
# ---------------------------------------------------------------------------


def wh_per_km(v_kph: float, veh: VehicleParams, factor: float = 1.0) -> float:
    return (veh.a_wh_km + veh.b_wh_km_per_kph2 * v_kph * v_kph) * factor


def _open_stretch(index: int, share: float) -> bool:
    """Is derestriction-eligible segment `index` actually open?

    Deterministic (same route + share → same plan) and interleaved along the
    route rather than all-open-then-all-closed, so a partially open autobahn
    behaves like a real one: bursts of speed between restricted stretches.
    """
    if share >= 1.0:
        return True
    if share <= 0.0:
        return False
    h = (index * 2654435761) & 0xFFFFFFFF  # Knuth multiplicative hash
    return (h / 0x100000000) < share


def effective_kph(
    v_kph: float, seg: RouteSegment, p: SimParams, index: int = 0
) -> float:
    """The speed actually held on this segment.

    `index` selects which derestriction-eligible stretches are open; pass the
    segment's position along the route.
    """
    motorway_from = (
        p.motorway_freeflow_kph if seg.country else p.motorway_freeflow_unmodelled_kph
    )
    if p.freeflow_cap_only or seg.freeflow_kph < motorway_from:
        # Ordinary road: its own flow speed governs, but an assertive driver
        # still presses a little above it.
        eff = min(v_kph, seg.freeflow_kph * p.over_freeflow_factor)
    else:
        cap = _INF if p.ignore_speed_limits else p.country_caps.get(
            seg.country, p.default_country_cap_kph
        )
        if math.isinf(cap):
            # `ignore_speed_limits` is the deliberate what-if, so it skips the
            # realism gate below — that gate exists to model signposting and
            # traffic on a real autobahn, which is exactly what this asks to
            # set aside.
            if not p.ignore_speed_limits and (
                seg.freeflow_kph < p.derestrict_freeflow_kph
                or not _open_stretch(index, p.autobahn_open_share)
            ):
                # Signposted zone, roadworks or traffic — the cap binds after all.
                cap = p.default_country_cap_kph + p.over_cap_kph
        else:
            cap = cap + p.over_cap_kph
        eff = min(v_kph, cap)
    return max(eff, 5.0)


def grade_kwh(
    seg: RouteSegment, veh: VehicleParams, extra_mass_kg: float = 0.0
) -> float:
    """Energy the elevation of this segment adds (climb) or gives back (descent).

    mgh in kWh, divided by drivetrain efficiency going up and multiplied by
    regen efficiency coming down — so a climb-and-descend pair is a net loss,
    as it is in the real car. `extra_mass_kg` is the payload above the nominal
    load; it is what makes a full car notice a mountain pass.
    """
    if seg.climb_m == 0.0 and seg.descent_m == 0.0:
        return 0.0
    m = veh.mass_kg + extra_mass_kg
    j_per_m = m * GRAVITY / 3_600_000.0  # kWh per metre climbed
    up = seg.climb_m * j_per_m / DRIVETRAIN_EFFICIENCY
    down = seg.descent_m * j_per_m * REGEN_EFFICIENCY
    return up - down


def curve_power_kw(soc: float, veh: VehicleParams) -> float:
    """Piecewise-linear interpolation of the charge curve at `soc` percent."""
    curve = veh.charge_curve
    if soc <= curve[0][0]:
        return curve[0][1]
    for (s0, p0), (s1, p1) in zip(curve, curve[1:]):
        if soc <= s1:
            if s1 == s0:
                return p1
            t = (soc - s0) / (s1 - s0)
            return p0 + t * (p1 - p0)
    return curve[-1][1]


def charge_minutes(
    veh: VehicleParams,
    soc_from: float,
    soc_to: float,
    site_kw: float,
    power_factor: float = 1.0,
) -> float:
    """Minutes to charge from `soc_from` to `soc_to` percent at a site limited
    to `site_kw`. Midpoint-rule integral over the piecewise-linear curve.

    `power_factor` derates the car's curve — a cold pack accepts far less than
    the datasheet says, which is the dominant winter effect.

    The curve is measured at the CABLE — public fast-charge tests report what
    the charger delivers — while `usable_kwh` is what reaches the battery. Not
    every kW crossing the cable gets stored, so the delivered power is derated
    by `charge_efficiency` before it is divided into battery-side energy.
    Without this the whole catalog charges ~7% faster than any published test,
    on top of whatever a given car's curve gets wrong. The derate is applied
    AFTER the site cap because the loss is real in both regimes — a car pinned
    at a 50 kW post still only banks ~93% of those 50 kW.
    """
    if soc_to <= soc_from:
        return 0.0
    minutes = 0.0
    soc = soc_from
    while soc < soc_to - 1e-9:
        step = min(_INTEGRATION_STEP, soc_to - soc)
        p = min(curve_power_kw(soc + step / 2.0, veh) * power_factor, site_kw)
        p *= max(veh.charge_efficiency, 0.5)  # cable → battery
        p = max(p, 1.0)  # guard against degenerate curves
        minutes += 60.0 * (veh.usable_kwh * step / 100.0) / p
        soc += step
    return minutes


def charge_forward(
    veh: VehicleParams,
    soc: float,
    minutes: float,
    site_kw: float,
    power_factor: float = 1.0,
) -> float:
    """SoC reached after `minutes` plugged in at a site limited to `site_kw`.

    The inverse of `charge_minutes`, marched on time instead of SoC. Written
    against the same integration grid on purpose: a live trip infers how much
    the battery gained while parked, and if that disagreed with the number the
    plan used to decide how long to stay, the two would drift apart every stop.
    """
    if minutes <= 0.0:
        return soc
    remaining = minutes
    s = soc
    while s < 100.0 - 1e-9 and remaining > 1e-9:
        step = min(_INTEGRATION_STEP, 100.0 - s)
        m = charge_minutes(veh, s, s + step, site_kw, power_factor)
        if m <= 0.0:
            break
        if m >= remaining:
            # Linear within one 0.5% step.
            return s + step * (remaining / m)
        s += step
        remaining -= m
    return min(s, 100.0)


# ---------------------------------------------------------------------------
# Per-speed route profile (cumulative distance / time / energy)
# ---------------------------------------------------------------------------


class RouteProfile:
    """Cumulative arrays at segment boundaries for one cruise speed; lerp for
    arbitrary offsets, and inverse lookup time → offset for playback."""

    def __init__(
        self,
        segments: list[RouteSegment],
        v_kph: float,
        veh: VehicleParams,
        p: SimParams,
        index_offset: int = 0,
    ):
        n = len(segments)
        self.offsets = [0.0] * (n + 1)
        self.t_min = [0.0] * (n + 1)
        self.kwh = [0.0] * (n + 1)
        d = t = e = 0.0
        for i, seg in enumerate(segments):
            # `index_offset` keeps the derestriction pattern anchored to the
            # ORIGINAL route when this profile covers only a tail of it — see
            # `slice_route`.
            eff = effective_kph(v_kph, seg, p, index_offset + i)
            dist_km = seg.dist_m / 1000.0
            d += seg.dist_m
            t += (dist_km / eff) * 60.0
            # Aero + rolling, then the elevation term (net-negative over a
            # climb-and-descend pair because regen is not free).
            e += wh_per_km(eff, veh, p.consumption_factor) * dist_km / 1000.0
            # Payload rides on top: extra rolling drag every kilometre, and
            # extra mgh on every climb. Both fall out of the same kilograms.
            e += _ROLLING_WH_KM_PER_KG * p.extra_mass_kg * dist_km / 1000.0
            e += grade_kwh(seg, veh, p.extra_mass_kg)
            # Conditioning is billed by the hour, not by the kilometre — which
            # is the whole point of keeping it out of the Wh/km term.
            e += p.aux_kw * (dist_km / eff)
            e = max(e, 0.0)
            self.offsets[i + 1] = d
            self.t_min[i + 1] = t
            self.kwh[i + 1] = e
        self.total_dist_m = d
        self.total_min = t
        self.total_kwh = e

    def _lerp(self, xs: list[float], ys: list[float], x: float) -> float:
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = bisect_right(xs, x) - 1
        x0, x1 = xs[i], xs[i + 1]
        if x1 == x0:
            return ys[i]
        f = (x - x0) / (x1 - x0)
        return ys[i] + f * (ys[i + 1] - ys[i])

    def time_at(self, offset_m: float) -> float:
        return self._lerp(self.offsets, self.t_min, offset_m)

    def kwh_at(self, offset_m: float) -> float:
        return self._lerp(self.offsets, self.kwh, offset_m)

    def offset_at_time(self, t: float) -> float:
        return self._lerp(self.t_min, self.offsets, t)


# ---------------------------------------------------------------------------
# The DP
# ---------------------------------------------------------------------------


def _site_cumulative_minutes(
    veh: VehicleParams, site_kw: float, power_factor: float = 1.0
) -> list[float]:
    """F[b] = minutes to charge from 0% to bucket b's SoC at this site.
    charge(s→t) = F[t] - F[s]."""
    f = [0.0] * N_BUCKETS
    for b in range(1, N_BUCKETS):
        f[b] = f[b - 1] + charge_minutes(
            veh, (b - 1) * SOC_BUCKET, b * SOC_BUCKET, site_kw, power_factor
        )
    return f


@dataclass(frozen=True)
class RouteSlice:
    """The remainder of a route from `from_offset_m` onwards.

    `index_offset` is the ORIGINAL index of `segments[0]`, and it is not
    optional bookkeeping: `_open_stretch` decides which derestricted autobahn
    stretches are open by hashing the segment index, and index 0 hashes to 0.0
    — which is open at every non-zero share. Renumber the segments and every
    re-plan silently inherits a free open autobahn starting exactly where the
    driver happens to be standing.
    """

    segments: list[RouteSegment]
    chargers: list[ChargerNode]
    index_offset: int
    from_offset_m: float  # the cut, on the ORIGINAL route
    total_dist_m: float  # distance remaining


def slice_route(
    segments: list[RouteSegment],
    chargers: list[ChargerNode],
    from_offset_m: float,
    index_offset: int = 0,
) -> RouteSlice:
    """The part of the route still ahead of `from_offset_m`.

    Feeds straight into `simulate`/`sweep` with the slice's `index_offset`, so
    re-planning mid-journey needs no changes to the DP at all.

    The straddled segment is split pro rata, including its climb and descent.
    That is an approximation: `routing` accumulates elevation per ORS step, so
    cutting halfway through a step that crests a pass charges part of the climb
    twice. One step is a few km at most, bounding the error at ~50 m of
    spurious climb — under half a percent of SoC on a typical car — and fixing
    it properly would mean carrying the elevation profile into `RouteSegment`,
    which would break this module's zero-I/O contract for very little.
    """
    total = sum(s.dist_m for s in segments)
    if from_offset_m <= 0.0:
        # Identity, deliberately including `index_offset`, so slicing at the
        # origin is a genuine no-op rather than merely a similar answer.
        return RouteSlice(list(segments), list(chargers), index_offset, 0.0, total)
    if from_offset_m >= total:
        return RouteSlice([], [], index_offset + len(segments), total, 0.0)

    out: list[RouteSegment] = []
    first_index = 0
    walked = 0.0
    for i, seg in enumerate(segments):
        seg_end = walked + seg.dist_m
        if seg_end <= from_offset_m:
            walked = seg_end
            continue
        if not out:
            # The straddled segment: keep the tail fraction of it.
            frac = (seg_end - from_offset_m) / seg.dist_m if seg.dist_m > 0 else 0.0
            if frac < 1e-6:
                # Nothing meaningful left — a zero-length segment would put
                # duplicate x-values into RouteProfile.offsets. Skip it.
                first_index = i + 1
                walked = seg_end
                continue
            first_index = i
            out.append(
                RouteSegment(
                    dist_m=seg.dist_m * frac,
                    freeflow_kph=seg.freeflow_kph,
                    country=seg.country,
                    climb_m=seg.climb_m * frac,
                    descent_m=seg.descent_m * frac,
                )
            )
        else:
            out.append(seg)
        walked = seg_end

    ahead = [
        replace(c, offset_m=c.offset_m - from_offset_m)
        for c in chargers
        # Strictly ahead: a charger you are standing at is not a stop you can
        # still plan. `simulate` drops offset 0 anyway; deciding how much
        # longer to stay put is a different question — see `live.py`.
        if c.offset_m > from_offset_m + 1.0
    ]
    return RouteSlice(
        segments=out,
        chargers=ahead,
        index_offset=index_offset + first_index,
        from_offset_m=from_offset_m,
        total_dist_m=sum(s.dist_m for s in out),
    )


def simulate(
    segments: list[RouteSegment],
    chargers: list[ChargerNode],
    veh: VehicleParams,
    v_kph: float,
    p: SimParams,
    _site_f_cache: dict[float, list[float]] | None = None,
    index_offset: int = 0,
) -> SpeedResult:
    """Optimal plan at one cruise speed. Returns an infeasible SpeedResult when
    no charging plan can complete the route within the SoC constraints.

    `index_offset` is the original index of `segments[0]`; pass it when
    simulating a tail of a longer route so the derestriction pattern matches.
    """
    profile = RouteProfile(segments, v_kph, veh, p, index_offset)
    total = profile.total_dist_m

    nodes = sorted(
        (c for c in chargers if 0.0 < c.offset_m < total), key=lambda c: c.offset_m
    )
    # Node 0 = origin, 1..K = chargers, K+1 = destination.
    offsets = [0.0] + [c.offset_m for c in nodes] + [total]
    n_nodes = len(offsets)
    dest = n_nodes - 1

    node_kwh = [profile.kwh_at(x) for x in offsets]
    node_min = [profile.time_at(x) for x in offsets]

    if _site_f_cache is None:
        _site_f_cache = {}
    site_f: list[list[float] | None] = [None] * n_nodes
    for i, c in enumerate(nodes, start=1):
        f = _site_f_cache.get(c.power_kw)
        if f is None:
            f = _site_cumulative_minutes(
                veh, c.power_kw * p.site_power_factor, p.charge_power_factor
            )
            _site_f_cache[c.power_kw] = f
        site_f[i] = f

    # best[i][b]: min minutes (driving + charging + overheads) to ARRIVE at node
    # i with SoC in bucket b (bucket value = floor of true SoC → conservative).
    best = [[_INF] * N_BUCKETS for _ in range(n_nodes)]
    # parent[i][b] = (prev_node, prev_arrival_bucket, depart_target_bucket)
    parent: list[list[tuple[int, int, int] | None]] = [
        [None] * N_BUCKETS for _ in range(n_nodes)
    ]

    def reserve_for(j: int) -> float:
        return p.target_soc if j == dest else p.reserve_soc

    depart_bucket = min(int(p.depart_soc / SOC_BUCKET), N_BUCKETS - 1)
    best[0][depart_bucket] = 0.0
    cap_b = min(int(p.charge_cap_soc / SOC_BUCKET), N_BUCKETS - 1)

    for i in range(dest):
        row = best[i]
        if all(c == _INF for c in row):
            continue

        # Departure cost D[t] = min total minutes to LEAVE node i with SoC at
        # bucket t (charging done, overhead+detour paid). Prefix-min over
        # (row[s] - F[s]) collapses the charge-target choice.
        if i == 0:
            depart_cost = [_INF] * N_BUCKETS
            depart_cost[depart_bucket] = row[depart_bucket]
            depart_from = [depart_bucket] * N_BUCKETS
        else:
            f = site_f[i]
            node = nodes[i - 1]
            fixed = p.stop_overhead_min + p.queue_min + node.detour_min
            depart_cost = [_INF] * N_BUCKETS
            depart_from = [0] * N_BUCKETS
            run_min, run_arg = _INF, 0
            for t in range(cap_b + 1):
                if row[t] != _INF and row[t] - f[t] < run_min:
                    run_min, run_arg = row[t] - f[t], t
                if run_min != _INF:
                    depart_cost[t] = run_min + f[t] + fixed
                    depart_from[t] = run_arg

        for j in range(i + 1, n_nodes):
            need = (node_kwh[j] - node_kwh[i]) / veh.usable_kwh * 100.0
            min_depart = need + reserve_for(j)
            if min_depart > p.charge_cap_soc + 1e-9:
                break  # farther nodes need even more — unreachable from i
            drive = node_min[j] - node_min[i]
            t_lo = max(0, math.ceil(min_depart / SOC_BUCKET - 1e-9))
            for t in range(t_lo, cap_b + 1):
                dc = depart_cost[t]
                if dc == _INF:
                    continue
                arr = dc + drive
                b_arr = int((t * SOC_BUCKET - need) / SOC_BUCKET + 1e-9)
                if arr < best[j][b_arr] - 1e-9:
                    best[j][b_arr] = arr
                    parent[j][b_arr] = (i, depart_from[t], t)

    goal = min(range(N_BUCKETS), key=lambda b: best[dest][b], default=None)
    if goal is None or best[dest][goal] == _INF:
        return SpeedResult(speed_kph=v_kph, feasible=False)

    # Reconstruct the stop sequence (node index, depart-target bucket).
    seq: list[tuple[int, int]] = []
    node_i, bucket = dest, goal
    while node_i != 0:
        prev, prev_b, t = parent[node_i][bucket]
        seq.append((prev, t))
        node_i, bucket = prev, prev_b
    seq.reverse()  # [(node, depart_target_bucket), ...] — first entry is origin

    return _exact_forward_pass(profile, nodes, veh, v_kph, p, seq, offsets)


def _exact_forward_pass(
    profile: RouteProfile,
    nodes: list[ChargerNode],
    veh: VehicleParams,
    v_kph: float,
    p: SimParams,
    seq: list[tuple[int, int]],
    offsets: list[float],
) -> SpeedResult:
    """Re-simulate the DP's chosen plan with continuous SoC for exact numbers.

    `seq` holds (node_index, depart_target_bucket) for the origin and each
    charging stop in order. Charge targets stay on the bucket grid (that's the
    plan); arrival SoCs are exact.
    """
    dest_off = profile.total_dist_m
    stop_nodes = [(ni, t) for ni, t in seq if ni != 0]

    clock = 0.0
    soc = p.depart_soc
    charge_total = 0.0
    stops: list[StopPlan] = []
    timeline: list[tuple[float, float, float]] = [(0.0, 0.0, soc)]

    def drive_leg(from_off: float, to_off: float, soc_start: float) -> float:
        """Append ~5-min timeline samples for a leg; returns arrival SoC."""
        nonlocal clock
        t0, t1 = profile.time_at(from_off), profile.time_at(to_off)
        e0 = profile.kwh_at(from_off)
        leg_min = t1 - t0
        n_samples = max(1, int(leg_min // 5.0))
        for k in range(1, n_samples + 1):
            tk = t0 + leg_min * k / n_samples
            xk = profile.offset_at_time(tk)
            sk = soc_start - (profile.kwh_at(xk) - e0) / veh.usable_kwh * 100.0
            timeline.append((clock + (tk - t0), xk, sk))
        clock += leg_min
        return soc_start - (profile.kwh_at(to_off) - e0) / veh.usable_kwh * 100.0

    # Rest rule: you owe a break every `rest_interval_min` of DRIVING. Time
    # already spent standing at a charger counts, so a plan with many stops
    # usually absorbs the whole debt for free — which is exactly why the
    # rule matters more to the slow, few-stop plans.
    extra_rest_each = 0.0
    if p.rest_interval_min > 0 and p.rest_min > 0:
        required = (
            math.floor(
                (p.prior_drive_min + profile.total_min) / p.rest_interval_min
            )
            * p.rest_min
        )
        covered = p.prior_rest_credit_min
        probe_soc = p.depart_soc
        prev_off = 0.0
        for ni, t_bucket in stop_nodes:
            node = nodes[ni - 1]
            probe_soc -= (
                profile.kwh_at(node.offset_m) - profile.kwh_at(prev_off)
            ) / veh.usable_kwh * 100.0
            pause = (
                node.detour_min
                + p.stop_overhead_min
                + p.queue_min
                + charge_minutes(
                    veh,
                    probe_soc,
                    t_bucket * SOC_BUCKET,
                    node.power_kw * p.site_power_factor,
                    p.charge_power_factor,
                )
            )
            covered += min(pause, p.rest_min)
            probe_soc = max(probe_soc, t_bucket * SOC_BUCKET)
            prev_off = node.offset_m
        shortfall = max(0.0, required - covered)
        if shortfall > 0:
            # Spread the shortfall over the stops we already make; with no
            # stops at all it becomes one standalone break (handled below).
            extra_rest_each = shortfall / len(stop_nodes) if stop_nodes else shortfall

    cur_off = 0.0
    for ni, t_bucket in stop_nodes:
        node = nodes[ni - 1]
        soc = drive_leg(cur_off, node.offset_m, soc)
        arrive_soc = soc
        arrive_clock = clock
        target = t_bucket * SOC_BUCKET
        c_min = charge_minutes(
            veh, soc, target, node.power_kw * p.site_power_factor, p.charge_power_factor
        )
        pause = (
            node.detour_min + p.stop_overhead_min + p.queue_min + c_min + extra_rest_each
        )
        clock += pause
        charge_total += c_min
        soc = max(soc, target)
        stops.append(
            StopPlan(
                charger_id=node.charger_id,
                name=node.name,
                operator=node.operator,
                lat=node.lat,
                lon=node.lon,
                # The power this plan actually assumed, so the reported kW and
                # the charge time next to it always explain each other.
                power_kw=node.power_kw * p.site_power_factor,
                offset_m=node.offset_m,
                arrive_min=arrive_clock,
                arrive_soc=arrive_soc,
                depart_soc=soc,
                charge_min=c_min,
                detour_min=node.detour_min,
            )
        )
        timeline.append((arrive_clock, node.offset_m, arrive_soc))
        timeline.append((clock, node.offset_m, soc))
        cur_off = node.offset_m

    soc = drive_leg(cur_off, dest_off, soc)
    timeline.append((clock, dest_off, soc))

    rest_total = extra_rest_each * len(stops)
    if not stops and extra_rest_each > 0:
        # No charging stop to hide the break in — take it, and show it.
        clock += extra_rest_each
        rest_total = extra_rest_each
        timeline.append((clock, dest_off, soc))

    overhead_total = sum(
        s.detour_min + p.stop_overhead_min + p.queue_min for s in stops
    )
    # Energy actually bought, priced at the plug (the curve is cable-side, so
    # the efficiency factor belongs here and nowhere else).
    charged_kwh = sum(
        (s.depart_soc - s.arrive_soc) / 100.0 * veh.usable_kwh for s in stops
    )
    plug_kwh = charged_kwh / max(veh.charge_efficiency, 0.5)
    return SpeedResult(
        speed_kph=v_kph,
        feasible=True,
        total_min=clock,
        drive_min=clock - charge_total - overhead_total - rest_total,
        charge_min=charge_total,
        n_stops=len(stops),
        stops=stops,
        timeline=timeline,
        rest_min=rest_total,
        energy_kwh=plug_kwh,
        cost_eur=plug_kwh * p.price_per_kwh,
    )


# ---------------------------------------------------------------------------
# Sweep
# ---------------------------------------------------------------------------


def sweep(
    segments: list[RouteSegment],
    chargers: list[ChargerNode],
    veh: VehicleParams,
    speeds: list[float],
    p: SimParams,
    index_offset: int = 0,
) -> list[SpeedResult]:
    """Simulate every candidate cruise speed. Site charge-time cumulatives are
    speed-independent and shared across the whole sweep."""
    site_f_cache: dict[float, list[float]] = {}
    return [
        simulate(
            segments, chargers, veh, v, p,
            _site_f_cache=site_f_cache, index_offset=index_offset,
        )
        for v in speeds
    ]


def sweep_slice(
    sl: RouteSlice, veh: VehicleParams, speeds: list[float], p: SimParams
) -> list[SpeedResult]:
    """`sweep` over a `RouteSlice`, carrying its `index_offset` for you.

    Prefer this to calling `sweep` with a slice's lists by hand — forgetting
    the offset produces a plausible answer that quietly disagrees with the
    plan it is supposed to be revising.
    """
    return sweep(sl.segments, sl.chargers, veh, speeds, p, index_offset=sl.index_offset)


def optimum(results: list[SpeedResult]) -> SpeedResult | None:
    feasible = [r for r in results if r.feasible]
    if not feasible:
        return None
    return min(feasible, key=lambda r: r.total_min)


# --- Weather ---------------------------------------------------------------
# Cold hits an EV three ways, and they do NOT share a shape:
#   1. conditioning the cabin and pack — roughly constant POWER, so its cost
#      per kilometre falls the faster you go            → `aux_kw_for_temp`
#   2. denser air and stiffer tyres/lubricants — a true per-kilometre effect
#      that scales with the existing terms              → `consumption_factor_for_temp`
#   3. the pack ACCEPTS charge more slowly, which on a long winter trip costs
#      more time than the extra kWh does                → `charge_power_factor_for_temp`
# (1) and (2) used to be a single multiplier on Wh/km. Because Wh/km is
# dominated by the v² drag term, that made the heater appear to draw four times
# more power at 160 km/h than at 90 — pushing the recommended speed down for a
# reason that isn't physical. Splitting them keeps the same total at a typical
# cruise while getting the speed-dependence right.


def consumption_factor_for_temp(temp_c: float) -> float:
    """Multiplier on Wh/km for ambient temperature — the per-distance part only.

    Cold air is denser (drag rises ~0.35%/°C) and cold tyres and lubricants
    lose more, so ~0.5%/°C below 15 °C. Cabin heating is NOT in here; it is a
    power draw, and lives in `aux_kw_for_temp`.
    """
    if temp_c < 15.0:
        return min(1.20, 1.0 + (15.0 - temp_c) * 0.005)
    return 1.0


def aux_kw_for_temp(temp_c: float) -> float:
    """Cabin and battery conditioning draw, in kW, for ambient temperature.

    Zero in the mild band — the baseline hotel load (pumps, lights, screens) is
    already inside each car's fitted `a_wh_km`, so counting it again here would
    double it. This is only the *extra* load weather imposes: heating ramps to
    ~3 kW around -10 °C and caps near a resistive heater's continuous output;
    air-conditioning is the much smaller summer equivalent.
    """
    if temp_c < 15.0:
        return min(4.5, (15.0 - temp_c) * 0.13)
    if temp_c > 28.0:
        return min(1.5, (temp_c - 28.0) * 0.12)
    return 0.0


def charge_power_factor_for_temp(temp_c: float) -> float:
    """Multiplier on DC charging power for ambient temperature.

    A cold pack limits how much current it will accept. Falls away below
    15 °C and steepens below freezing, with a floor rather than zero — cars
    warm the pack as they drive, and many precondition before a stop.
    """
    if temp_c >= 15.0:
        return 1.0
    if temp_c >= 0.0:
        return 1.0 - (15.0 - temp_c) * (0.20 / 15.0)
    if temp_c >= -10.0:
        return 0.80 - (-temp_c) * (0.25 / 10.0)
    return 0.45
