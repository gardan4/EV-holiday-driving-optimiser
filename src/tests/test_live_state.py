"""Tests for following a drive: inferring the battery, and measuring reality
against the plan.

No database and no network — `app.services.live` is pure, the same way the
simulator is.
"""

from __future__ import annotations

import pytest

from app.services.live import (
    OFF_ROUTE_M,
    RUN_FACTOR_MAX,
    RUN_FACTOR_MIN,
    advance,
    apply_reading,
    current_soc,
    needs_replan,
    new_state,
    plan_soc_at,
    plan_window_at,
    schedule_delta_min,
    soc_uncertainty,
)
from app.services.simulator import SimParams, simulate
from tests.fixtures import BORN_58, flat_motorway, nl_to_austria_route

SEGMENTS = flat_motorway(600, freeflow=125, country="DE")
PARAMS = SimParams(autobahn_open_share=0.3)


def _ping(state, *, offset_m, at_min, moving_s, stationary_s=0.0, **kw):
    return advance(
        state,
        segments=SEGMENTS,
        veh=BORN_58,
        p=kw.pop("p", PARAMS),
        offset_m=offset_m,
        lat=50.0,
        lon=8.0,
        at_min=at_min,
        off_route_m=kw.pop("off_route_m", 0.0),
        moving_s=moving_s,
        stationary_s=stationary_s,
        **kw,
    )


class TestInference:
    def test_a_fresh_state_is_a_measurement(self):
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        assert current_soc(st, 58.0) == 90.0
        assert st["soc_is_measured"]
        assert soc_uncertainty(st) == 0.0

    def test_driving_drains_the_battery(self):
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=50_000.0, at_min=25.0, moving_s=1500.0)
        assert current_soc(st, 58.0) < 90.0
        assert not st["soc_is_measured"]

    def test_it_never_walks_backwards_on_a_wobbly_fix(self):
        st = new_state(offset_m=50_000.0, soc=80.0, lat=50.0, lon=8.0, at_min=25.0)
        st = _ping(st, offset_m=49_000.0, at_min=26.0, moving_s=60.0)
        assert st["offset_m"] == 50_000.0

    def test_standing_still_still_drains_with_the_heater_on(self):
        cold = SimParams(autobahn_open_share=0.3, aux_kw=3.0)
        st = new_state(offset_m=10_000.0, soc=80.0, lat=50.0, lon=8.0, at_min=10.0)
        parked = _ping(
            st, offset_m=10_000.0, at_min=70.0, moving_s=0.0, stationary_s=3600.0,
            p=cold,
        )
        assert current_soc(parked, 58.0) < 80.0

    def test_standing_still_costs_nothing_with_no_conditioning(self):
        st = new_state(offset_m=10_000.0, soc=80.0, lat=50.0, lon=8.0, at_min=10.0)
        parked = _ping(
            st, offset_m=10_000.0, at_min=70.0, moving_s=0.0, stationary_s=3600.0
        )
        assert current_soc(parked, 58.0) == pytest.approx(80.0)

    def test_going_faster_over_the_same_ground_uses_more(self):
        """Energy is convex in speed, which is the whole reason a ping carries
        its moving time rather than just a position."""
        base = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        slow = _ping(base, offset_m=100_000.0, at_min=66.0, moving_s=4000.0)
        fast = _ping(base, offset_m=100_000.0, at_min=40.0, moving_s=2400.0)
        assert current_soc(fast, 58.0) < current_soc(slow, 58.0)

    def test_uncertainty_grows_with_the_age_of_the_anchor(self):
        st = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        a = _ping(st, offset_m=20_000.0, at_min=10.0, moving_s=600.0)
        b = _ping(a, offset_m=200_000.0, at_min=100.0, moving_s=5400.0)
        assert soc_uncertainty(b) > soc_uncertainty(a) > 0.0

    def test_leaving_the_route_stops_the_guessing(self):
        """Snapping a supermarket detour back to the polyline would report no
        progress while real energy is being spent. Say unknown instead."""
        st = new_state(offset_m=50_000.0, soc=80.0, lat=50.0, lon=8.0, at_min=25.0)
        off = _ping(
            st, offset_m=50_000.0, at_min=40.0, moving_s=900.0,
            off_route_m=OFF_ROUTE_M + 1.0,
        )
        assert off["stale"]
        assert current_soc(off, 58.0) == 80.0  # unchanged, not invented

    def test_rejoining_the_route_resumes_inference(self):
        st = new_state(offset_m=50_000.0, soc=80.0, lat=50.0, lon=8.0, at_min=25.0)
        st = _ping(st, offset_m=50_000.0, at_min=40.0, moving_s=900.0,
                   off_route_m=OFF_ROUTE_M + 1.0)
        back = _ping(st, offset_m=70_000.0, at_min=50.0, moving_s=600.0)
        assert not back["stale"]
        assert current_soc(back, 58.0) < 80.0


class TestChargingInference:
    def test_parked_at_a_charger_the_battery_climbs(self):
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        st = _ping(
            st, offset_m=100_000.0, at_min=80.0, moving_s=0.0, stationary_s=1200.0,
            stop_power_kw=150.0, at_charger_id="chg-3",
        )
        assert current_soc(st, 58.0) > 15.0
        assert st["at_charger_id"] == "chg-3"

    def test_longer_plugged_in_means_more_charge(self):
        base = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        short = _ping(base, offset_m=100_000.0, at_min=70.0, moving_s=0.0,
                      stationary_s=600.0, stop_power_kw=150.0, at_charger_id="c")
        long_ = _ping(base, offset_m=100_000.0, at_min=90.0, moving_s=0.0,
                      stationary_s=1800.0, stop_power_kw=150.0, at_charger_id="c")
        assert current_soc(long_, 58.0) > current_soc(short, 58.0)

    def test_the_heater_is_not_billed_to_the_pack_while_plugged_in(self):
        """At a DC charger the cabin runs off the charger, not the battery —
        which is what drivers actually observe."""
        cold = SimParams(autobahn_open_share=0.3, aux_kw=4.0)
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        plugged = _ping(st, offset_m=100_000.0, at_min=80.0, moving_s=0.0,
                        stationary_s=1200.0, stop_power_kw=150.0,
                        at_charger_id="c", p=cold)
        idle = _ping(st, offset_m=100_000.0, at_min=80.0, moving_s=0.0,
                     stationary_s=1200.0, p=cold)
        assert current_soc(plugged, 58.0) > current_soc(idle, 58.0)


class TestCalibration:
    def test_a_reading_re_anchors_and_clears_uncertainty(self):
        st = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=150_000.0, at_min=75.0, moving_s=4500.0)
        st = apply_reading(st, 55.0, 58.0)
        assert current_soc(st, 58.0) == 55.0
        assert st["soc_is_measured"]
        assert soc_uncertainty(st) == 0.0
        assert st["kwh_used"] == 0.0

    def test_a_thirstier_car_than_modelled_raises_the_factor(self):
        st = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=200_000.0, at_min=100.0, moving_s=6000.0)
        modelled = current_soc(st, 58.0)
        st = apply_reading(st, modelled - 8.0, 58.0)  # really used more
        assert st["run_factor"] > 1.0

    def test_a_thriftier_car_lowers_the_factor(self):
        st = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=200_000.0, at_min=100.0, moving_s=6000.0)
        modelled = current_soc(st, 58.0)
        st = apply_reading(st, modelled + 8.0, 58.0)
        assert st["run_factor"] < 1.0

    def test_one_wild_reading_cannot_break_the_model(self):
        st = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=200_000.0, at_min=100.0, moving_s=6000.0)
        st = apply_reading(st, 1.0, 58.0)
        assert RUN_FACTOR_MIN <= st["run_factor"] <= RUN_FACTOR_MAX

    def test_the_calibration_is_applied_exactly_once(self):
        """`advance` must not re-apply `run_factor` on top of params that
        already carry it.

        The caller builds `consumption_factor` as base × run_factor, because
        the re-planner needs it that way. If `advance` multiplied by the factor
        again, a car measured 20% thirsty would be modelled 44% thirsty, and
        each reading would push it further out — the calibration would diverge
        instead of converging.
        """
        calibrated = SimParams(autobahn_open_share=0.3, consumption_factor=1.2)
        st = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        st = {**st, "run_factor": 1.2}
        used = _ping(st, offset_m=100_000.0, at_min=46.0, moving_s=2770.0,
                     p=calibrated)["kwh_used"]

        plain = SimParams(autobahn_open_share=0.3)
        base = _ping(
            new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0),
            offset_m=100_000.0, at_min=46.0, moving_s=2770.0, p=plain,
        )["kwh_used"]
        assert used == pytest.approx(base * 1.2, rel=1e-9)

    def test_a_reading_too_early_to_mean_anything_does_not_calibrate(self):
        """A 1% dashboard reading over 2 kWh of driving is noise, not signal."""
        st = new_state(offset_m=0.0, soc=100.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=5_000.0, at_min=3.0, moving_s=180.0)
        st = apply_reading(st, 98.0, 58.0)
        assert st["run_factor"] == 1.0


class TestAgainstThePlan:
    """The plan's own timeline is the yardstick — and it has a trap in it."""

    def _plan(self):
        segs, chargers = nl_to_austria_route()
        return simulate(segs, chargers, BORN_58, 130.0, PARAMS)

    def test_a_stop_occupies_an_interval_not_an_instant(self):
        """Stops appear TWICE at the same offset, once arriving and once
        leaving. Collapsing that to one number makes the ahead/behind readout
        flap for the whole time the driver stands at the plug."""
        plan = self._plan()
        stop = plan.stops[0]
        lo, hi = plan_window_at(plan.timeline, stop.offset_m)
        assert hi > lo
        assert hi - lo == pytest.approx(
            stop.charge_min + stop.detour_min + PARAMS.stop_overhead_min, abs=1e-6
        )

    def test_standing_at_the_plug_reads_as_on_time_throughout(self):
        plan = self._plan()
        stop = plan.stops[0]
        lo, hi = plan_window_at(plan.timeline, stop.offset_m)
        for t in (lo, (lo + hi) / 2.0, hi):
            assert schedule_delta_min(plan.timeline, stop.offset_m, t) == 0.0

    def test_running_late_reads_as_behind(self):
        plan = self._plan()
        stop = plan.stops[0]
        _, hi = plan_window_at(plan.timeline, stop.offset_m)
        assert schedule_delta_min(plan.timeline, stop.offset_m, hi + 12.0) == pytest.approx(12.0)

    def test_running_early_reads_as_ahead(self):
        plan = self._plan()
        stop = plan.stops[0]
        lo, _ = plan_window_at(plan.timeline, stop.offset_m)
        assert schedule_delta_min(plan.timeline, stop.offset_m, lo - 9.0) == pytest.approx(-9.0)

    def test_mid_leg_is_a_single_instant(self):
        plan = self._plan()
        x = plan.stops[0].offset_m + 30_000.0
        lo, hi = plan_window_at(plan.timeline, x)
        assert lo == hi

    def test_the_planned_soc_falls_along_the_route(self):
        plan = self._plan()
        first = plan_soc_at(plan.timeline, 1_000.0)
        later = plan_soc_at(plan.timeline, plan.stops[0].offset_m - 1_000.0)
        assert later < first


class TestReplanTrigger:
    def test_well_behind_suggests_a_replan(self):
        flag, reasons = needs_replan(
            delta_min=25.0, soc_now=50.0, soc_planned=52.0, stale=False
        )
        assert flag and "behind" in reasons[0]

    def test_a_battery_well_under_plan_suggests_a_replan(self):
        flag, reasons = needs_replan(
            delta_min=1.0, soc_now=30.0, soc_planned=45.0, stale=False
        )
        assert flag and "battery" in reasons[0]

    def test_on_plan_suggests_nothing(self):
        flag, reasons = needs_replan(
            delta_min=2.0, soc_now=50.0, soc_planned=51.0, stale=False
        )
        assert not flag and reasons == []

    def test_being_ahead_never_suggests_a_replan(self):
        flag, _ = needs_replan(
            delta_min=-30.0, soc_now=70.0, soc_planned=50.0, stale=False
        )
        assert not flag

    def test_off_route_says_so_rather_than_suggesting_a_replan(self):
        flag, reasons = needs_replan(
            delta_min=99.0, soc_now=10.0, soc_planned=60.0, stale=True
        )
        assert not flag
        assert "off the planned route" in reasons[0]


class TestInferenceAgainstThePlanItCameFrom:
    def test_replaying_the_plan_reproduces_its_own_battery(self):
        """The strongest check available without a real car.

        The plan is a perfect synthetic driver: its timeline says where the car
        is, when, and what the battery should read. Replay those samples as
        pings and the inference must land back on the plan's own SoC. If it
        can't reproduce the model that generated it, it cannot be trusted on a
        real drive either.
        """
        segs, chargers = nl_to_austria_route()
        p = SimParams(autobahn_open_share=0.3)
        plan = simulate(segs, chargers, BORN_58, 130.0, p)

        st = new_state(offset_m=0.0, soc=p.depart_soc, lat=50.0, lon=8.0, at_min=0.0)
        prev_t, prev_x = 0.0, 0.0
        worst = 0.0

        for t, x, soc in plan.timeline:
            dt_min = t - prev_t
            # A metre of tolerance, not exact equality: the last drive sample
            # of a leg lands on the stop by interpolation and comes out one ULP
            # short of the node's own offset. Compared exactly, the 18-minute
            # charging stop replays as driving 6e-8 m and the battery never
            # gets its charge.
            if x > prev_x + 1.0 and dt_min > 0:
                st = advance(
                    st, segments=segs, veh=BORN_58, p=p,
                    offset_m=x, lat=50.0, lon=8.0, at_min=t,
                    off_route_m=0.0, moving_s=dt_min * 60.0, stationary_s=0.0,
                )
                worst = max(worst, abs(current_soc(st, BORN_58.usable_kwh) - soc))
            elif soc > current_soc(st, BORN_58.usable_kwh) + 1e-9:
                # Standing at a plug. Charging is exercised separately by
                # `TestChargingInference`; here just re-anchor, the way a
                # driver typing in the dashboard figure would.
                st = apply_reading(st, soc, BORN_58.usable_kwh)
            prev_t, prev_x = t, x

        # ~1% is the honest bar: samples are minutes apart and the profile
        # lerps between segment boundaries.
        assert worst < 1.0
