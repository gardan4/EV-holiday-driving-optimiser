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
from dataclasses import dataclass, field

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

    @classmethod
    def from_vehicle(cls, vehicle) -> "VehicleParams":
        """Build from an `app.models.Vehicle` row (or anything shaped like it)."""
        cons = vehicle.consumption
        return cls(
            usable_kwh=vehicle.usable_kwh,
            a_wh_km=cons["a_wh_km"],
            b_wh_km_per_kph2=cons["b_wh_km_per_kph2"],
            charge_curve=tuple((float(s), float(p)) for s, p in vehicle.charge_curve),
        )


@dataclass(frozen=True)
class RouteSegment:
    dist_m: float
    freeflow_kph: float
    country: str = ""  # ISO-3166 alpha-2; "" → default cap


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


@dataclass(frozen=True)
class SimParams:
    depart_soc: float = 100.0
    target_soc: float = 10.0     # minimum SoC on arrival at the destination
    reserve_soc: float = 10.0    # never plan to dip below this between stops
    stop_overhead_min: float = 5.0
    charge_cap_soc: float = 100.0  # ceiling for charge targets (DP may stop lower)
    consumption_factor: float = 1.0  # conditions multiplier on Wh/km
    # Free-flow ≥ this → "motorway-like": ORS/OSM under-caps there, so the
    # cruise speed applies up to the country's legal cap instead of free-flow.
    motorway_freeflow_kph: float = 105.0
    # DE only: free-flow ≥ this → genuinely derestricted (no cap). Between the
    # two thresholds a German segment is a signposted zone → default cap.
    derestrict_freeflow_kph: float = 118.0
    freeflow_cap_only: bool = False  # True → naive min(v, freeflow) everywhere
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


# ---------------------------------------------------------------------------
# Physics primitives
# ---------------------------------------------------------------------------


def wh_per_km(v_kph: float, veh: VehicleParams, factor: float = 1.0) -> float:
    return (veh.a_wh_km + veh.b_wh_km_per_kph2 * v_kph * v_kph) * factor


def effective_kph(v_kph: float, seg: RouteSegment, p: SimParams) -> float:
    if p.freeflow_cap_only or seg.freeflow_kph < p.motorway_freeflow_kph:
        eff = min(v_kph, seg.freeflow_kph)
    else:
        cap = p.country_caps.get(seg.country, p.default_country_cap_kph)
        if math.isinf(cap) and seg.freeflow_kph < p.derestrict_freeflow_kph:
            # German motorway but free-flow says a signposted zone → default cap.
            cap = p.default_country_cap_kph
        eff = min(v_kph, cap)
    return max(eff, 5.0)


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
    veh: VehicleParams, soc_from: float, soc_to: float, site_kw: float
) -> float:
    """Minutes to charge from `soc_from` to `soc_to` percent at a site limited
    to `site_kw`. Midpoint-rule integral over the piecewise-linear curve."""
    if soc_to <= soc_from:
        return 0.0
    minutes = 0.0
    soc = soc_from
    while soc < soc_to - 1e-9:
        step = min(_INTEGRATION_STEP, soc_to - soc)
        p = min(curve_power_kw(soc + step / 2.0, veh), site_kw)
        p = max(p, 1.0)  # guard against degenerate curves
        minutes += 60.0 * (veh.usable_kwh * step / 100.0) / p
        soc += step
    return minutes


# ---------------------------------------------------------------------------
# Per-speed route profile (cumulative distance / time / energy)
# ---------------------------------------------------------------------------


class RouteProfile:
    """Cumulative arrays at segment boundaries for one cruise speed; lerp for
    arbitrary offsets, and inverse lookup time → offset for playback."""

    def __init__(
        self, segments: list[RouteSegment], v_kph: float, veh: VehicleParams, p: SimParams
    ):
        n = len(segments)
        self.offsets = [0.0] * (n + 1)
        self.t_min = [0.0] * (n + 1)
        self.kwh = [0.0] * (n + 1)
        d = t = e = 0.0
        for i, seg in enumerate(segments):
            eff = effective_kph(v_kph, seg, p)
            dist_km = seg.dist_m / 1000.0
            d += seg.dist_m
            t += (dist_km / eff) * 60.0
            e += wh_per_km(eff, veh, p.consumption_factor) * dist_km / 1000.0
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


def _site_cumulative_minutes(veh: VehicleParams, site_kw: float) -> list[float]:
    """F[b] = minutes to charge from 0% to bucket b's SoC at this site.
    charge(s→t) = F[t] - F[s]."""
    f = [0.0] * N_BUCKETS
    for b in range(1, N_BUCKETS):
        f[b] = f[b - 1] + charge_minutes(
            veh, (b - 1) * SOC_BUCKET, b * SOC_BUCKET, site_kw
        )
    return f


def simulate(
    segments: list[RouteSegment],
    chargers: list[ChargerNode],
    veh: VehicleParams,
    v_kph: float,
    p: SimParams,
    _site_f_cache: dict[float, list[float]] | None = None,
) -> SpeedResult:
    """Optimal plan at one cruise speed. Returns an infeasible SpeedResult when
    no charging plan can complete the route within the SoC constraints."""
    profile = RouteProfile(segments, v_kph, veh, p)
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
            f = _site_cumulative_minutes(veh, c.power_kw)
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
            fixed = p.stop_overhead_min + node.detour_min
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

    cur_off = 0.0
    for ni, t_bucket in stop_nodes:
        node = nodes[ni - 1]
        soc = drive_leg(cur_off, node.offset_m, soc)
        arrive_soc = soc
        arrive_clock = clock
        target = t_bucket * SOC_BUCKET
        c_min = charge_minutes(veh, soc, target, node.power_kw)
        pause = node.detour_min + p.stop_overhead_min + c_min
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
                power_kw=node.power_kw,
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

    overhead_total = sum(s.detour_min + p.stop_overhead_min for s in stops)
    return SpeedResult(
        speed_kph=v_kph,
        feasible=True,
        total_min=clock,
        drive_min=clock - charge_total - overhead_total,
        charge_min=charge_total,
        n_stops=len(stops),
        stops=stops,
        timeline=timeline,
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
) -> list[SpeedResult]:
    """Simulate every candidate cruise speed. Site charge-time cumulatives are
    speed-independent and shared across the whole sweep."""
    site_f_cache: dict[float, list[float]] = {}
    return [
        simulate(segments, chargers, veh, v, p, _site_f_cache=site_f_cache)
        for v in speeds
    ]


def optimum(results: list[SpeedResult]) -> SpeedResult | None:
    feasible = [r for r in results if r.feasible]
    if not feasible:
        return None
    return min(feasible, key=lambda r: r.total_min)
