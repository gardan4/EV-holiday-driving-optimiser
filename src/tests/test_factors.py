"""Tests for the real-world factors layered on top of the base physics:
cold-battery charging, partial autobahn derestriction, driving style,
elevation, rest breaks, queueing, and energy cost.
"""

from __future__ import annotations

import pytest

from app.services.simulator import (
    DRIVETRAIN_EFFICIENCY,
    GRAVITY,
    NOMINAL_LOAD_KG,
    OCCUPANT_KG,
    REGEN_EFFICIENCY,
    ROLLING_CRR,
    ChargerNode,
    RouteProfile,
    RouteSegment,
    SimParams,
    VehicleParams,
    charge_minutes,
    effective_kph,
    grade_kwh,
    optimum,
    payload_extra_kg,
    simulate,
    sweep,
)
from tests.fixtures import BORN_58, chargers_every, flat_motorway, nl_to_austria_route


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

    def test_a_full_car_notices_the_mountain(self):
        seg = RouteSegment(dist_m=10_000, freeflow_kph=100, climb_m=1000.0)
        light = grade_kwh(seg, BORN_58)
        heavy = grade_kwh(seg, BORN_58, extra_mass_kg=200.0)
        # mgh is linear in m, so 200 kg on a 1900 kg car is 200/1900 more.
        assert heavy == pytest.approx(light * (2100 / 1900), rel=1e-9)

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
# Payload: people and luggage
# ---------------------------------------------------------------------------


def _hilly(segments: list[RouteSegment], climb: float = 25.0, descent: float = 10.0):
    return [
        RouteSegment(
            dist_m=s.dist_m,
            freeflow_kph=s.freeflow_kph,
            country=s.country,
            climb_m=climb,
            descent_m=descent,
        )
        for s in segments
    ]


class TestPayload:
    def test_two_up_with_a_weekend_bag_is_the_nominal_load(self):
        """The catalog `mass_kg` already includes 180 kg of people and luggage,
        so the request defaults must measure zero against it — otherwise every
        trip planned before these fields existed would silently re-plan."""
        assert payload_extra_kg(2, 30.0) == 0.0
        assert payload_extra_kg(4, 60.0) == 4 * OCCUPANT_KG + 60.0 - NOMINAL_LOAD_KG

    def test_a_lone_driver_is_lighter_than_the_catalog_car(self):
        assert payload_extra_kg(1, 0.0) < 0.0

    def test_the_default_load_changes_nothing(self):
        """The compatibility guarantee, end to end."""
        segs, chargers = nl_to_austria_route()
        base = simulate(segs, chargers, BORN_58, 130.0, SimParams())
        same = simulate(
            segs, chargers, BORN_58, 130.0,
            SimParams(extra_mass_kg=payload_extra_kg(2, 30.0)),
        )
        assert same.total_min == base.total_min
        assert same.energy_kwh == base.energy_kwh
        assert [s.charger_id for s in same.stops] == [s.charger_id for s in base.stops]

    def test_weight_costs_energy_even_on_the_flat(self):
        """The point of the rolling term. Before it, `mass_kg` fed only mgh, so
        four passengers on a flat motorway cost exactly nothing."""
        segs = flat_motorway(100, freeflow=125, country="DE")
        p = SimParams(autobahn_open_share=0.0)
        light = RouteProfile(segs, 120.0, BORN_58, p)
        heavy = RouteProfile(segs, 120.0, BORN_58, SimParams(
            autobahn_open_share=0.0, extra_mass_kg=100.0,
        ))
        per_kg_wh_km = ROLLING_CRR * GRAVITY * 1000.0 / 3600.0 / DRIVETRAIN_EFFICIENCY
        expected = 100.0 * per_kg_wh_km * 100.0 / 1000.0  # kg × Wh/km/kg × km → kWh
        assert heavy.total_kwh - light.total_kwh == pytest.approx(expected, rel=1e-9)
        # Sanity on the magnitude: ~3 Wh/km per 100 kg, not 0.3 and not 30.
        assert 2.5 <= per_kg_wh_km * 100.0 <= 3.5

    def test_the_rolling_penalty_does_not_scale_with_speed(self):
        """Rolling drag is roughly speed-independent — which is exactly why it
        is a separate term and not a multiplier on `a + b·v²`."""
        segs = flat_motorway(100, freeflow=125, country="DE")
        p = SimParams(autobahn_open_share=0.0, over_cap_kph=30.0)
        heavy = SimParams(autobahn_open_share=0.0, over_cap_kph=30.0, extra_mass_kg=150.0)
        slow = RouteProfile(segs, 90.0, BORN_58, heavy).total_kwh - \
            RouteProfile(segs, 90.0, BORN_58, p).total_kwh
        fast = RouteProfile(segs, 150.0, BORN_58, heavy).total_kwh - \
            RouteProfile(segs, 150.0, BORN_58, p).total_kwh
        assert slow == pytest.approx(fast, rel=1e-9)

    def test_more_weight_never_uses_less_energy(self):
        segs = flat_motorway(300, freeflow=125, country="DE")
        p = SimParams(autobahn_open_share=0.3)
        for v in (90.0, 110.0, 130.0, 150.0):
            prev = None
            for extra in (0.0, 100.0, 200.0, 400.0):
                kwh = RouteProfile(
                    segs, v, BORN_58, SimParams(
                        autobahn_open_share=p.autobahn_open_share, extra_mass_kg=extra,
                    ),
                ).total_kwh
                if prev is not None:
                    assert kwh >= prev
                prev = kwh

    def test_weight_costs_more_over_a_pass_than_on_the_flat(self):
        """Both terms are live: rolling drag everywhere, mgh on the climbs."""
        flat = flat_motorway(300, freeflow=125, country="DE")
        hilly = _hilly(flat)
        p = SimParams(autobahn_open_share=0.0)
        heavy = SimParams(autobahn_open_share=0.0, extra_mass_kg=200.0)
        flat_delta = RouteProfile(flat, 120.0, BORN_58, heavy).total_kwh - \
            RouteProfile(flat, 120.0, BORN_58, p).total_kwh
        hilly_delta = RouteProfile(hilly, 120.0, BORN_58, heavy).total_kwh - \
            RouteProfile(hilly, 120.0, BORN_58, p).total_kwh
        assert flat_delta > 0
        assert hilly_delta > flat_delta

    def test_a_loaded_car_never_arrives_earlier(self):
        segs, chargers = nl_to_austria_route()
        p = SimParams(autobahn_open_share=0.3)
        light = simulate(segs, chargers, BORN_58, 130.0, p)
        heavy = simulate(
            segs, chargers, BORN_58, 130.0,
            SimParams(autobahn_open_share=0.3, extra_mass_kg=payload_extra_kg(5, 100.0)),
        )
        assert heavy.total_min >= light.total_min

    def test_loading_the_car_never_makes_the_best_plan_faster(self):
        """Monotone in TIME, not in speed.

        The optimum *speed* is deliberately not asserted here: the total-time
        curve is flat near its minimum (the UI has a callout for exactly that),
        so a few kWh flips which discrete speed wins and the optimum wanders
        non-monotonically — measured at 155/145/160/…/145/160 across this
        range. What cannot happen is a heavier car finishing sooner.
        """
        segs, chargers = nl_to_austria_route()
        speeds = [90.0 + 5.0 * i for i in range(15)]
        prev = None
        for extra in (-100.0, 0.0, 100.0, 200.0, 300.0, 400.0):
            best = optimum(sweep(segs, chargers, BORN_58, speeds, SimParams(
                autobahn_open_share=0.3, extra_mass_kg=extra,
            )))
            assert best is not None
            if prev is not None:
                assert best.total_min >= prev
            prev = best.total_min


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


class TestIgnoreSpeedLimits:
    """The what-if: every motorway derestricted, everywhere."""

    def _sim(self, **over):
        from app.services.simulator import SimParams, simulate
        from tests.fixtures import nl_to_austria_route
        from app.services.simulator import VehicleParams

        segments, chargers = nl_to_austria_route()
        veh = VehicleParams(
            usable_kwh=77.0, a_wh_km=55.0, b_wh_km_per_kph2=0.0105,
            charge_curve=((0, 50), (10, 120), (30, 120), (45, 85), (55, 68), (75, 42), (100, 10)),
            top_speed_kph=200.0,
        )
        return simulate(segments, chargers, veh, 180.0, SimParams(**over))

    def test_uncapped_drives_faster_than_capped(self):
        capped = self._sim()
        free = self._sim(ignore_speed_limits=True)
        assert free.feasible and capped.feasible
        assert free.drive_min < capped.drive_min, (
            "with no limit at all, a 180 km/h cruise must cover the motorway "
            "stretches faster than one held to 130"
        )

    def test_it_ignores_the_autobahn_realism_gate(self):
        """The open-stretch pattern models signposting and roadworks, which is
        precisely what this what-if asks to set aside — so closing the autobahn
        entirely must not slow the uncapped run."""
        shut = self._sim(ignore_speed_limits=True, autobahn_open_share=0.0)
        open_ = self._sim(ignore_speed_limits=True, autobahn_open_share=1.0)
        assert shut.drive_min == open_.drive_min

    def test_ordinary_roads_are_still_governed_by_the_road(self):
        """A back road can't be driven at 180 whatever the law says."""
        from app.services.simulator import RouteSegment, SimParams, effective_kph

        lane = RouteSegment(dist_m=10_000.0, freeflow_kph=60.0, country="NL")
        v = effective_kph(180.0, lane, SimParams(ignore_speed_limits=True))
        assert v <= 60.0
