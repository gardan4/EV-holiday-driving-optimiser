"""Simulator unit tests: physics primitives, DP invariants, and the golden
Born NL→Austria scenario that motivates the whole product. Zero network."""

from __future__ import annotations

import time

import pytest

from app.services.simulator import (
    ChargerNode,
    RouteSegment,
    SimParams,
    VehicleParams,
    charge_minutes,
    effective_kph,
    optimum,
    simulate,
    sweep,
    wh_per_km,
)
from tests.fixtures import BORN_58, chargers_every, flat_motorway, nl_to_austria_route

CONSTANT_100KW = VehicleParams(
    usable_kwh=58.0,
    a_wh_km=55.0,
    b_wh_km_per_kph2=0.0105,
    charge_curve=((0.0, 100.0), (100.0, 100.0)),
)


# ---------------------------------------------------------------------------
# charge_minutes
# ---------------------------------------------------------------------------


class TestChargeMinutes:
    def test_constant_curve_matches_analytic(self):
        # 10% → 80% of 58 kWh = 40.6 kWh at a flat 100 kW = 0.406 h = 24.36 min.
        got = charge_minutes(CONSTANT_100KW, 10.0, 80.0, site_kw=150.0)
        assert got == pytest.approx(24.36, abs=0.01)

    def test_site_power_clamps_the_curve(self):
        # Site limited to 50 kW → exactly twice as slow as the flat 100 kW curve.
        got = charge_minutes(CONSTANT_100KW, 10.0, 80.0, site_kw=50.0)
        assert got == pytest.approx(48.72, abs=0.02)

    def test_monotone_in_delta_soc(self):
        prev = 0.0
        for hi in range(20, 101, 10):
            t = charge_minutes(BORN_58, 10.0, float(hi), site_kw=300.0)
            assert t > prev
            prev = t

    def test_taper_makes_top_end_slow(self):
        # Same 20% SoC span costs far more above 75% than at the flat bottom.
        low = charge_minutes(BORN_58, 10.0, 30.0, site_kw=300.0)
        high = charge_minutes(BORN_58, 75.0, 95.0, site_kw=300.0)
        assert high > 2.5 * low

    def test_zero_or_negative_span_is_free(self):
        assert charge_minutes(BORN_58, 50.0, 50.0, 150.0) == 0.0
        assert charge_minutes(BORN_58, 50.0, 40.0, 150.0) == 0.0


# ---------------------------------------------------------------------------
# Consumption + effective speed
# ---------------------------------------------------------------------------


class TestConsumption:
    def test_born_calibration_locked(self):
        # These pin the seed data: ~160 Wh/km @ 100, ≥230 @ 135.
        assert wh_per_km(100.0, BORN_58) == pytest.approx(160.0, abs=1.0)
        assert wh_per_km(135.0, BORN_58) >= 230.0

    def test_conditions_factor_scales(self):
        assert wh_per_km(100.0, BORN_58, factor=1.3) == pytest.approx(208.0, abs=1.5)


class TestEffectiveSpeed:
    def test_low_freeflow_caps_cruise_speed(self):
        seg = RouteSegment(dist_m=1000, freeflow_kph=50.0, country="DE")
        assert effective_kph(140.0, seg, SimParams()) == 50.0

    def test_motorway_uncaps_to_country_limit(self):
        # ORS free-flow 120 on an Austrian motorway: cruise 140 → legal cap 130.
        seg = RouteSegment(dist_m=1000, freeflow_kph=120.0, country="AT")
        assert effective_kph(140.0, seg, SimParams()) == 130.0

    def test_german_motorway_is_derestricted(self):
        seg = RouteSegment(dist_m=1000, freeflow_kph=120.0, country="DE")
        assert effective_kph(160.0, seg, SimParams()) == 160.0

    def test_naive_clamp_flag(self):
        seg = RouteSegment(dist_m=1000, freeflow_kph=120.0, country="DE")
        p = SimParams(freeflow_cap_only=True)
        assert effective_kph(160.0, seg, p) == 120.0


# ---------------------------------------------------------------------------
# simulate — no-charge trips
# ---------------------------------------------------------------------------


class TestNoChargeTrip:
    def test_time_is_exactly_distance_over_speed(self):
        segs = flat_motorway(200, freeflow=120, country="DE")
        r = simulate(segs, [], BORN_58, 120.0, SimParams(target_soc=10.0))
        assert r.feasible
        assert r.n_stops == 0
        assert r.total_min == pytest.approx(200 / 120 * 60, abs=0.01)
        assert r.charge_min == 0.0

    def test_faster_is_strictly_better_without_charging(self):
        segs = flat_motorway(200, freeflow=130, country="DE")
        results = sweep(segs, [], BORN_58, [90, 100, 110, 120], SimParams())
        times = [r.total_min for r in results]
        assert all(r.feasible for r in results)
        assert times == sorted(times, reverse=True)

    def test_infeasible_without_chargers(self):
        # 900 km on 58 kWh with no chargers cannot work at any speed.
        segs = flat_motorway(900, freeflow=120, country="DE")
        r = simulate(segs, [], BORN_58, 100.0, SimParams())
        assert not r.feasible


# ---------------------------------------------------------------------------
# simulate — invariants with charging
# ---------------------------------------------------------------------------


class TestInvariants:
    def _park(self):
        segs = flat_motorway(600, freeflow=125, country="DE")
        chargers = chargers_every(600, 60.0, power_kw=150.0)
        return segs, chargers

    def test_reserve_and_target_soc_respected(self):
        segs, chargers = self._park()
        p = SimParams(target_soc=20.0, reserve_soc=10.0)
        r = simulate(segs, chargers, BORN_58, 130.0, p)
        assert r.feasible
        for stop in r.stops:
            assert stop.arrive_soc >= p.reserve_soc - 1e-6
        _, _, final_soc = r.timeline[-1]
        assert final_soc >= p.target_soc - 1e-6
        for _, _, soc in r.timeline:
            assert soc >= p.reserve_soc - 1e-6 or soc >= p.target_soc - 1e-6

    def test_totals_are_consistent(self):
        segs, chargers = self._park()
        r = simulate(segs, chargers, BORN_58, 120.0, SimParams())
        overhead = sum(s.detour_min + 5.0 for s in r.stops)
        assert r.total_min == pytest.approx(r.drive_min + r.charge_min + overhead, abs=0.01)
        assert r.charge_min == pytest.approx(sum(s.charge_min for s in r.stops), abs=0.01)

    def test_timeline_monotonic_and_complete(self):
        segs, chargers = self._park()
        r = simulate(segs, chargers, BORN_58, 120.0, SimParams())
        ts = [t for t, _, _ in r.timeline]
        xs = [x for _, x, _ in r.timeline]
        assert ts == sorted(ts)
        assert xs == sorted(xs)
        assert ts[0] == 0.0 and ts[-1] == pytest.approx(r.total_min, abs=0.01)
        assert xs[-1] == pytest.approx(600_000.0, abs=1.0)

    def test_taper_avoidance_emerges(self):
        # With plentiful 150 kW chargers, the optimal plan should never charge
        # deep into the taper — the DP must discover "arrive low, leave by ~80%".
        segs = flat_motorway(900, freeflow=125, country="DE")
        chargers = chargers_every(900, 60.0, power_kw=150.0)
        r = simulate(segs, chargers, BORN_58, 130.0, SimParams(target_soc=10.0))
        assert r.feasible
        assert r.n_stops >= 2
        for stop in r.stops:
            assert stop.depart_soc <= 85.0

    def test_arrive_low_strategy_emerges(self):
        segs = flat_motorway(900, freeflow=125, country="DE")
        chargers = chargers_every(900, 60.0, power_kw=150.0)
        r = simulate(segs, chargers, BORN_58, 130.0, SimParams(target_soc=10.0))
        # At least one mid-trip arrival should be down near the reserve floor.
        assert min(s.arrive_soc for s in r.stops) <= 25.0


# ---------------------------------------------------------------------------
# The golden scenario — the product's motivating claim
# ---------------------------------------------------------------------------


class TestGoldenBornAustria:
    @pytest.fixture(scope="class")
    def results(self):
        segs, chargers = nl_to_austria_route()
        speeds = [float(v) for v in range(90, 165, 5)]
        p = SimParams(depart_soc=100.0, target_soc=10.0)
        return sweep(segs, chargers, BORN_58, speeds, p)

    def test_all_reasonable_speeds_feasible(self, results):
        assert all(r.feasible for r in results if r.speed_kph <= 150)

    def test_optimum_is_meaningfully_above_100(self, results):
        best = optimum(results)
        assert best is not None
        assert best.speed_kph >= 110.0

    def test_curve_is_flat_near_the_top(self, results):
        # The persuasive fact: you get within 5 minutes of the optimum without
        # extreme speeds. The "5-min band" must start at or below 140 kph.
        best = optimum(results)
        band = [r.speed_kph for r in results
                if r.feasible and r.total_min - best.total_min <= 5.0]
        assert min(band) <= 140.0

    def test_beats_100kph_by_over_20_minutes(self, results):
        best = optimum(results)
        at_100 = next(r for r in results if r.speed_kph == 100.0)
        assert at_100.feasible
        assert at_100.total_min - best.total_min > 20.0

    def test_sweep_is_fast_enough(self):
        segs, chargers = nl_to_austria_route()
        speeds = [float(v) for v in range(90, 165, 5)]
        t0 = time.perf_counter()
        sweep(segs, chargers, BORN_58, speeds, SimParams())
        elapsed = time.perf_counter() - t0
        assert elapsed < 1.0, f"sweep took {elapsed:.2f}s (budget 1s)"
