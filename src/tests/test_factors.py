"""Tests for the real-world factors layered on top of the base physics:
cold-battery charging, partial autobahn derestriction, driving style,
elevation, rest breaks, queueing, and energy cost.
"""

from __future__ import annotations

import pytest

from app.services.simulator import (
    DRIVETRAIN_EFFICIENCY,
    GRAVITY,
    REGEN_EFFICIENCY,
    ChargerNode,
    RouteSegment,
    SimParams,
    VehicleParams,
    charge_minutes,
    effective_kph,
    grade_kwh,
    optimum,
    simulate,
    sweep,
)
from tests.fixtures import BORN_58, chargers_every, flat_motorway


# ---------------------------------------------------------------------------
# Cold-battery charging
# ---------------------------------------------------------------------------


class TestChargeDerate:
    def test_half_power_doubles_charge_time(self):
        full = charge_minutes(BORN_58, 10.0, 60.0, 300.0, power_factor=1.0)
        half = charge_minutes(BORN_58, 10.0, 60.0, 300.0, power_factor=0.5)
        assert half == pytest.approx(full * 2, rel=0.02)

    def test_site_power_still_caps_after_derate(self):
        # A 50 kW site is the binding limit either way, so derating the car's
        # curve from 120 to 96 kW must change nothing.
        a = charge_minutes(BORN_58, 10.0, 40.0, 50.0, power_factor=1.0)
        b = charge_minutes(BORN_58, 10.0, 40.0, 50.0, power_factor=0.8)
        assert a == pytest.approx(b, rel=1e-6)

    def test_cold_charging_lowers_the_optimum(self):
        segs = flat_motorway(900, freeflow=125, country="DE")
        chargers = chargers_every(900, 60.0, power_kw=150.0)
        speeds = [float(v) for v in range(90, 181, 5)]
        warm = optimum(sweep(segs, chargers, BORN_58, speeds, SimParams(autobahn_open_share=1.0)))
        cold = optimum(
            sweep(segs, chargers, BORN_58, speeds,
                  SimParams(autobahn_open_share=1.0, charge_power_factor=0.55))
        )
        assert cold.speed_kph < warm.speed_kph


# ---------------------------------------------------------------------------
# Partial autobahn derestriction
# ---------------------------------------------------------------------------


class TestAutobahnShare:
    def _segs(self):
        return flat_motorway(600, freeflow=125, country="DE")

    def test_share_zero_means_everything_capped(self):
        p = SimParams(autobahn_open_share=0.0)
        assert all(effective_kph(180.0, s, p, i) == 130.0 for i, s in enumerate(self._segs()))

    def test_share_one_means_nothing_capped(self):
        p = SimParams(autobahn_open_share=1.0)
        assert all(effective_kph(180.0, s, p, i) == 180.0 for i, s in enumerate(self._segs()))

    def test_share_is_roughly_honoured_and_interleaved(self):
        segs = self._segs()
        p = SimParams(autobahn_open_share=0.3)
        open_flags = [effective_kph(180.0, s, p, i) > 130.0 for i, s in enumerate(segs)]
        share = sum(open_flags) / len(open_flags)
        assert 0.2 <= share <= 0.4
        # Not all the open stretches bunched at one end.
        first_half = sum(open_flags[: len(open_flags) // 2])
        assert 0 < first_half < sum(open_flags)

    def test_deterministic(self):
        segs = self._segs()
        p = SimParams(autobahn_open_share=0.3)
        a = [effective_kph(180.0, s, p, i) for i, s in enumerate(segs)]
        b = [effective_kph(180.0, s, p, i) for i, s in enumerate(segs)]
        assert a == b

    def test_more_open_autobahn_never_worsens_the_best_achievable_time(self):
        # The invariant holds for the OPTIMUM, not for a fixed cruise speed:
        # a bigger set of roads you may drive fast on cannot hurt, because you
        # can always choose to drive slower.
        segs = self._segs()
        chargers = chargers_every(600, 60.0, power_kw=150.0)
        speeds = [float(v) for v in range(90, 181, 5)]
        best = [
            optimum(sweep(segs, chargers, BORN_58, speeds, SimParams(autobahn_open_share=s))).total_min
            for s in (0.0, 0.3, 0.6, 1.0)
        ]
        assert best == sorted(best, reverse=True) or max(best) - min(best) < 1e-6

    def test_at_a_fixed_high_speed_more_open_road_can_cost_time(self):
        # Counter-intuitive but real, and worth pinning: holding 160 over more
        # of the route burns more energy, which can buy more charging stops
        # than the saved driving time is worth.
        segs = self._segs()
        chargers = chargers_every(600, 60.0, power_kw=150.0)
        wide_open = simulate(segs, chargers, BORN_58, 160.0, SimParams(autobahn_open_share=1.0))
        partly = simulate(segs, chargers, BORN_58, 160.0, SimParams(autobahn_open_share=0.6))
        assert wide_open.drive_min < partly.drive_min
        assert wide_open.n_stops >= partly.n_stops


# ---------------------------------------------------------------------------
# Driving style
# ---------------------------------------------------------------------------


class TestDrivingStyle:
    def test_over_cap_lifts_a_capped_motorway(self):
        seg = RouteSegment(dist_m=1000, freeflow_kph=120.0, country="AT")
        assert effective_kph(160.0, seg, SimParams()) == 130.0
        assert effective_kph(160.0, seg, SimParams(over_cap_kph=20.0)) == 150.0

    def test_over_freeflow_pushes_on_ordinary_roads(self):
        seg = RouteSegment(dist_m=1000, freeflow_kph=80.0, country="DE")
        assert effective_kph(160.0, seg, SimParams()) == 80.0
        assert effective_kph(160.0, seg, SimParams(over_freeflow_factor=1.15)) == pytest.approx(92.0)

    def test_style_never_exceeds_the_chosen_cruise_speed(self):
        seg = RouteSegment(dist_m=1000, freeflow_kph=80.0, country="DE")
        p = SimParams(over_freeflow_factor=1.5)
        assert effective_kph(100.0, seg, p) == 100.0


# ---------------------------------------------------------------------------
# Elevation
# ---------------------------------------------------------------------------


class TestGrade:
    def test_climb_matches_mgh(self):
        seg = RouteSegment(dist_m=10_000, freeflow_kph=100, climb_m=1000.0)
        expected = 1900 * GRAVITY * 1000 / 3_600_000 / DRIVETRAIN_EFFICIENCY
        assert grade_kwh(seg, BORN_58) == pytest.approx(expected, rel=1e-9)

    def test_descent_gives_back_only_regen_share(self):
        seg = RouteSegment(dist_m=10_000, freeflow_kph=100, descent_m=1000.0)
        expected = -(1900 * GRAVITY * 1000 / 3_600_000) * REGEN_EFFICIENCY
        assert grade_kwh(seg, BORN_58) == pytest.approx(expected, rel=1e-9)

    def test_up_and_down_is_a_net_loss(self):
        seg = RouteSegment(dist_m=10_000, freeflow_kph=100, climb_m=800.0, descent_m=800.0)
        assert grade_kwh(seg, BORN_58) > 0

    def test_climbing_costs_time_over_the_trip(self):
        flat = flat_motorway(600, freeflow=125, country="DE")
        hilly = [
            RouteSegment(dist_m=s.dist_m, freeflow_kph=s.freeflow_kph, country=s.country,
                         climb_m=25.0, descent_m=10.0)
            for s in flat
        ]
        chargers = chargers_every(600, 60.0, power_kw=150.0)
        p = SimParams(autobahn_open_share=1.0)
        a = simulate(flat, chargers, BORN_58, 130.0, p)
        b = simulate(hilly, chargers, BORN_58, 130.0, p)
        assert b.total_min > a.total_min


# ---------------------------------------------------------------------------
# Rest breaks, queueing, cost
# ---------------------------------------------------------------------------


class TestRestBreaks:
    def test_charging_stops_absorb_the_break(self):
        # Plenty of long charging stops → the rest debt is already covered.
        segs = flat_motorway(900, freeflow=125, country="DE")
        chargers = chargers_every(900, 60.0, power_kw=50.0)
        r = simulate(segs, chargers, BORN_58, 130.0,
                     SimParams(rest_interval_min=180, rest_min=20))
        assert r.n_stops >= 3
        assert r.rest_min == pytest.approx(0.0)

    def test_a_trip_with_no_stops_still_owes_a_break(self):
        # 250 km at 100 km/h needs no charge, but is over the 2 h rule.
        segs = flat_motorway(250, freeflow=110, country="DE")
        r = simulate(segs, [], BORN_58, 100.0,
                     SimParams(rest_interval_min=120, rest_min=25))
        assert r.n_stops == 0
        assert r.rest_min == pytest.approx(25.0)
        assert r.total_min == pytest.approx(r.drive_min + 25.0, abs=0.01)


class TestQueue:
    def test_queue_costs_time_per_stop(self):
        segs = flat_motorway(600, freeflow=125, country="DE")
        chargers = chargers_every(600, 60.0, power_kw=150.0)
        p0 = SimParams(autobahn_open_share=1.0)
        p1 = SimParams(autobahn_open_share=1.0, queue_min=10.0)
        a = simulate(segs, chargers, BORN_58, 130.0, p0)
        b = simulate(segs, chargers, BORN_58, 130.0, p1)
        # At least the queue time for the stops actually taken.
        assert b.total_min > a.total_min
        assert b.total_min - a.total_min >= 10.0 * min(a.n_stops, b.n_stops) - 1e-6

    def test_queueing_discourages_extra_stops(self):
        segs = flat_motorway(900, freeflow=125, country="DE")
        chargers = chargers_every(900, 40.0, power_kw=150.0)
        speeds = [float(v) for v in range(90, 181, 5)]
        free = optimum(sweep(segs, chargers, BORN_58, speeds, SimParams(autobahn_open_share=1.0)))
        queued = optimum(
            sweep(segs, chargers, BORN_58, speeds,
                  SimParams(autobahn_open_share=1.0, queue_min=15.0))
        )
        assert queued.n_stops <= free.n_stops


class TestCost:
    def test_energy_and_cost_track_the_charging(self):
        segs = flat_motorway(600, freeflow=125, country="DE")
        chargers = chargers_every(600, 60.0, power_kw=150.0)
        r = simulate(segs, chargers, BORN_58, 130.0, SimParams(price_per_kwh=0.60))
        battery_kwh = sum(
            (s.depart_soc - s.arrive_soc) / 100.0 * BORN_58.usable_kwh for s in r.stops
        )
        assert r.energy_kwh == pytest.approx(battery_kwh / BORN_58.charge_efficiency, rel=1e-6)
        assert r.cost_eur == pytest.approx(r.energy_kwh * 0.60, rel=1e-6)

    def test_driving_faster_costs_more_energy(self):
        segs = flat_motorway(600, freeflow=140, country="DE")
        chargers = chargers_every(600, 60.0, power_kw=150.0)
        p = SimParams(autobahn_open_share=1.0)
        slow = simulate(segs, chargers, BORN_58, 100.0, p)
        fast = simulate(segs, chargers, BORN_58, 160.0, p)
        assert fast.cost_eur > slow.cost_eur


# --- Temperature -> factors -------------------------------------------------


def test_temperature_reproduces_the_presets_it_replaced() -> None:
    """The old Mild/Cold/Freezing chips are still the anchors of the curve.

    The cold penalty is now split between a per-km multiplier and a constant
    conditioning draw, so the anchor is the TOTAL at a typical cruise speed
    rather than the multiplier alone.
    """
    from app.services.simulator import (
        aux_kw_for_temp,
        charge_power_factor_for_temp,
        consumption_factor_for_temp,
    )

    def total_wh_km(temp_c: float, kph: float, mild_wh_km: float) -> float:
        return (
            mild_wh_km * consumption_factor_for_temp(temp_c)
            + aux_kw_for_temp(temp_c) * 1000.0 / kph
        )

    assert consumption_factor_for_temp(15.0) == pytest.approx(1.0)
    assert aux_kw_for_temp(15.0) == pytest.approx(0.0)

    # At a typical 120 km/h the old presets were 1.00 / 1.15 / 1.30 on 200 Wh/km.
    assert total_wh_km(15.0, 120, 200.0) == pytest.approx(200.0, rel=0.02)
    assert total_wh_km(0.0, 120, 200.0) == pytest.approx(230.0, rel=0.03)
    assert total_wh_km(-10.0, 120, 200.0) == pytest.approx(260.0, rel=0.05)

    assert charge_power_factor_for_temp(20.0) == pytest.approx(1.0)
    assert charge_power_factor_for_temp(0.0) == pytest.approx(0.80, abs=0.01)
    assert charge_power_factor_for_temp(-10.0) == pytest.approx(0.55, abs=0.01)


def test_conditioning_costs_less_per_km_the_faster_you_go() -> None:
    """Heating is a power draw, not a tax on distance.

    The bug this pins: folding conditioning into the Wh/km multiplier made it
    scale with the v² drag term, so the model implied a heater four times
    stronger at 160 km/h than at 90 — and talked you out of driving fast for a
    reason that does not exist.
    """
    from app.services.simulator import RouteProfile, aux_kw_for_temp

    aux = aux_kw_for_temp(-10.0)
    assert 2.0 < aux < 4.5, "a plausible continuous heater draw"

    segs = flat_motorway(600, freeflow=130, country="DE")
    per_km, per_hour = {}, {}
    for kph in (90.0, 160.0):
        warm = RouteProfile(segs, kph, BORN_58, SimParams())
        cold = RouteProfile(segs, kph, BORN_58, SimParams(aux_kw=aux))
        extra = cold.total_kwh - warm.total_kwh
        per_km[kph] = extra / (cold.total_dist_m / 1000.0)
        per_hour[kph] = extra / (cold.total_min / 60.0)

    # An hour is an hour: the draw is the same however fast you were going.
    # (Per km it cannot be — that is the point.)
    for kph in (90.0, 160.0):
        assert per_hour[kph] == pytest.approx(aux, rel=1e-9)
    assert per_km[90.0] > per_km[160.0], "slower driving must carry more heating per km"


def test_temperature_factors_are_monotonic_and_bounded() -> None:
    from app.services.simulator import (
        charge_power_factor_for_temp,
        consumption_factor_for_temp,
    )

    temps = [t / 2 for t in range(-60, 90)]
    charge = [charge_power_factor_for_temp(t) for t in temps]
    assert charge == sorted(charge), "colder must never charge faster"
    assert all(0.4 <= c <= 1.0 for c in charge)

    cold = [consumption_factor_for_temp(t) for t in temps if t <= 10]
    assert cold == sorted(cold, reverse=True), "colder must never use less energy"
    assert all(1.0 <= consumption_factor_for_temp(t) <= 1.6 for t in temps)


def test_site_power_factor_slows_charging_and_calms_the_optimum() -> None:
    """A shared 350 kW cabinet is not a 350 kW cabinet."""
    from app.services.simulator import optimum, sweep

    segs = flat_motorway(900, freeflow=125, country="DE")
    chargers = chargers_every(900, 60.0, power_kw=150.0)
    speeds = [float(v) for v in range(90, 181, 5)]

    full = optimum(sweep(segs, chargers, BORN_58, speeds, SimParams()))
    shared = optimum(sweep(segs, chargers, BORN_58, speeds, SimParams(site_power_factor=0.5)))

    assert shared.charge_min > full.charge_min, "half the power must take longer"
    assert shared.total_min > full.total_min
    # Slower charging makes speed more expensive, so the best cruise can only
    # come down (or hold), never rise.
    assert shared.speed_kph <= full.speed_kph
    # The reported per-stop power reflects what was assumed, not the rating.
    assert all(s.power_kw == pytest.approx(0.5 * 150.0) for s in shared.stops)
