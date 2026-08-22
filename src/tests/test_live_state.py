"""Tests for following a drive: inferring the battery, and measuring reality
against the plan.

No database and no network — `app.services.live` is pure, the same way the
simulator is.
"""

from __future__ import annotations

import pytest

from app.services.live import (
    OFF_ROUTE_M,
    OFF_ROUTE_WINDING,
    REROUTE_AFTER_MIN,
    RUN_FACTOR_MAX,
    RUN_FACTOR_MIN,
    advance,
    apply_reading,
    charging_soc,
    current_soc,
    needs_replan,
    new_state,
    plan_soc_at,
    plug_in,
    plan_window_at,
    rebase,
    reroute_due,
    schedule_delta_min,
    soc_uncertainty,
)
from app.services.simulator import SimParams, simulate
from tests.fixtures import BORN_58, flat_motorway, nl_to_austria_route

SEGMENTS = flat_motorway(600, freeflow=125, country="DE")
PARAMS = SimParams(autobahn_open_share=0.3)


def _plug(state, *, offset_m, at_min, kw_site=150.0, charger_id="c", **kw):
    """Pull in, then plug in — which are now two separate things.

    `advance` used to anchor the charge on the first ping that found the car
    stopped near a plug, so arriving WAS charging. It no longer does: a car
    queueing for a free stall is standing at the charger and not charging, and
    the projection that assumed otherwise climbed through the whole wait. Every
    test below is about the charge MODEL rather than about how it starts, so
    they take the two steps and go on asserting what they always did.
    """
    st = _ping(
        state, offset_m=offset_m, at_min=at_min, moving_s=0.0,
        stationary_s=kw.pop("stationary_s", 60.0),
        stop_power_kw=kw_site, at_charger_id=charger_id, **kw,
    )
    return plug_in(st, soc=current_soc(st, 58.0), at_min=at_min, kw=kw_site)


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


def _soc(st, at_min=None):
    """What the screen shows: the charge projection when there is one."""
    return charging_soc(st, BORN_58, PARAMS, at_min if at_min is not None else st["at_min"])


class TestChargingInference:
    def test_parked_at_a_charger_the_battery_climbs(self):
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        st = _plug(
            st, offset_m=100_000.0, at_min=80.0, stationary_s=1200.0,
            charger_id="chg-3",
        )
        assert _soc(st, 100.0) > 15.0
        assert st["at_charger_id"] == "chg-3"

    def test_longer_plugged_in_means_more_charge(self):
        base = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        st = _plug(base, offset_m=100_000.0, at_min=70.0, stationary_s=600.0)
        assert _soc(st, 100.0) > _soc(st, 80.0)

    def test_the_charge_keeps_climbing_with_no_pings_at_all(self):
        """The whole reason it is projected from the clock.

        A phone whose car is charging is in a pocket. Integrating the charge
        per ping froze the estimate the moment the screen locked, so the number
        on screen and the countdown under it were both stuck at the value they
        had on arrival — for the entire stop, which is exactly the window they
        exist for.
        """
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        st = _plug(st, offset_m=100_000.0, at_min=61.0)
        # Not one further ping — just time passing.
        assert _soc(st, 61.0) == pytest.approx(15.0, abs=0.5)
        assert _soc(st, 81.0) > _soc(st, 71.0) > _soc(st, 61.0)

    def test_driving_away_banks_the_charge_that_happened(self):
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        st = _plug(st, offset_m=100_000.0, at_min=61.0)
        at_plug = _soc(st, 91.0)
        gone = _ping(st, offset_m=110_000.0, at_min=96.0, moving_s=300.0)
        assert gone.get("charge_anchor_soc") is None
        assert gone["at_charger_id"] is None
        # Charged, then drove 10 km: below what it left on, well above arrival.
        assert 15.0 < current_soc(gone, 58.0) < at_plug

    def test_a_phone_that_wakes_on_the_motorway_is_not_billed_as_charging(self):
        """The minutes a ping spent MOVING cannot have been minutes plugged in.

        Banking the whole gap would credit a car that left half an hour ago
        with half an hour of charging it did on the autobahn.
        """
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        st = _plug(st, offset_m=100_000.0, at_min=61.0)
        # Same wake-up time, same distance; one reports it drove the whole gap.
        drove = _ping(st, offset_m=140_000.0, at_min=101.0, moving_s=2400.0)
        dawdled = _ping(st, offset_m=140_000.0, at_min=101.0, moving_s=1200.0,
                        stationary_s=1200.0)
        assert current_soc(drove, 58.0) < current_soc(dawdled, 58.0)

    def test_the_heater_is_not_billed_to_the_pack_while_plugged_in(self):
        """At a DC charger the cabin runs off the charger, not the battery —
        which is what drivers actually observe."""
        cold = SimParams(autobahn_open_share=0.3, aux_kw=4.0)
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        plugged = _plug(st, offset_m=100_000.0, at_min=80.0,
                        stationary_s=1200.0, p=cold)
        idle = _ping(st, offset_m=100_000.0, at_min=80.0, moving_s=0.0,
                     stationary_s=1200.0, p=cold)
        assert charging_soc(plugged, BORN_58, cold, 80.0) > current_soc(idle, 58.0)


class TestPullingInIsNotPluggingIn:
    """Arriving used to start the charge, and it is the wrong moment.

    The first ping that found the car stopped beside a plug anchored the
    projection, so the battery climbed through the queue for a free stall, the
    walk to the screen, and the post that turned out to be dead. Wrong
    silently, and upwards — the direction that costs somebody a leg they
    thought they could make.
    """

    def _parked(self):
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        return _ping(
            st, offset_m=100_000.0, at_min=61.0, moving_s=0.0, stationary_s=60.0,
            stop_power_kw=150.0, at_charger_id="c",
        )

    def test_standing_at_a_charger_does_not_start_the_charge(self):
        st = self._parked()
        assert st["at_charger_id"] == "c"
        assert st.get("charge_anchor_soc") is None
        # Half an hour of waiting buys nothing, which is the whole point.
        assert _soc(st, 91.0) == pytest.approx(15.0, abs=0.01)

    def test_saying_the_cable_is_in_starts_it(self):
        st = self._parked()
        st = plug_in(st, soc=current_soc(st, 58.0), at_min=61.0, kw=150.0)
        assert _soc(st, 91.0) > 15.0

    def test_a_wait_before_plugging_in_is_not_charged_for(self):
        """The queue is the reason this split exists: twenty minutes standing
        there must leave the same battery as twenty minutes anywhere else."""
        st = self._parked()
        waited = plug_in(st, soc=current_soc(st, 58.0), at_min=81.0, kw=150.0)
        straight = plug_in(st, soc=current_soc(st, 58.0), at_min=61.0, kw=150.0)
        # Same wall-clock moment, twenty minutes of it spent queueing.
        assert _soc(waited, 91.0) < _soc(straight, 91.0)

    def test_a_drifting_fix_still_cannot_end_a_stop(self):
        """The wider radius used to be keyed on the charge anchor, which no
        longer exists for a car that has merely pulled in. Keyed on being AT
        the charger now, or this change would have quietly reintroduced the
        bug it was written for."""
        st = self._parked()
        drifted = _ping(
            st, offset_m=100_000.0 + 300.0, at_min=63.0, moving_s=0.0,
            stationary_s=120.0,
        )
        assert drifted["at_charger_id"] == "c"


class TestLeavingACharger:
    def _plugged(self):
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        return _plug(st, offset_m=100_000.0, at_min=61.0)

    def test_a_reading_at_the_plug_restarts_the_projection_from_it(self):
        st = self._plugged()
        st = {**st, "at_min": 81.0}
        st = apply_reading(st, 62.0, 58.0, veh=BORN_58, p=PARAMS)
        assert st["charge_anchor_soc"] == 62.0
        assert _soc(st, 81.0) == pytest.approx(62.0, abs=0.1)
        # …and keeps counting up from there.
        assert _soc(st, 91.0) > 62.0

    def test_leaving_ends_the_projection_and_takes_the_car_off_the_charger(self):
        st = self._plugged()
        st = {**st, "at_min": 81.0}
        st = apply_reading(st, 62.0, 58.0, veh=BORN_58, p=PARAMS, leaving=True)
        assert st.get("charge_anchor_soc") is None
        assert st["at_charger_id"] is None
        assert current_soc(st, 58.0) == 62.0
        # Time passing no longer adds anything: the cable is out.
        assert _soc(st, 141.0) == 62.0

    def test_the_typed_figure_wins_over_the_projection(self):
        """The driver read the dashboard; the app was guessing."""
        st = self._plugged()
        st = {**st, "at_min": 111.0}
        assert _soc(st) > 70.0  # the projection had it much higher
        st = apply_reading(st, 55.0, 58.0, veh=BORN_58, p=PARAMS, leaving=True)
        assert current_soc(st, 58.0) == 55.0


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

    def test_a_charge_running_long_shows_up_before_it_overruns(self):
        """The bug the driver reported, at its root.

        Standing at a plug read as "on time" for the whole window whatever the
        battery said, so the arrival clock did not move until the car had
        ALREADY overrun the planned departure — by which point the delay had
        happened. Priced on the projected departure instead, a charge that is
        going to take four minutes longer says so four minutes before it does.
        """
        plan = self._plan()
        stop = plan.stops[0]
        lo, hi = plan_window_at(plan.timeline, stop.offset_m)
        mid = (lo + hi) / 2.0
        # Still inside the window, so the old rule said 0.0 whatever happened.
        assert schedule_delta_min(plan.timeline, stop.offset_m, mid) == 0.0
        late = schedule_delta_min(
            plan.timeline, stop.offset_m, mid, leaving_in_min=(hi - mid) + 4.0
        )
        assert late == pytest.approx(4.0)

    def test_a_charge_going_to_plan_is_the_answer_it_always_was(self):
        """The check that this is a generalisation and not a second rule: hand
        it the minutes the plan itself allowed and it must still say zero."""
        plan = self._plan()
        stop = plan.stops[0]
        lo, hi = plan_window_at(plan.timeline, stop.offset_m)
        for t in (lo, (lo + hi) / 2.0, hi):
            assert schedule_delta_min(
                plan.timeline, stop.offset_m, t, leaving_in_min=hi - t
            ) == pytest.approx(0.0, abs=1e-9)

    def test_a_charge_finishing_early_reads_as_ahead(self):
        plan = self._plan()
        stop = plan.stops[0]
        lo, hi = plan_window_at(plan.timeline, stop.offset_m)
        mid = (lo + hi) / 2.0
        assert schedule_delta_min(
            plan.timeline, stop.offset_m, mid, leaving_in_min=(hi - mid) - 6.0
        ) == pytest.approx(-6.0)

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


class TestGoingOffTheRoute:
    """Off-route used to freeze the drive and wait. It still freezes — the
    position genuinely is unknown — but it now also remembers WHEN it lost the
    road, so the drive can stop waiting and ask for a new one."""

    def test_it_remembers_when_the_route_was_lost(self):
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=50_000.0, at_min=25.0, moving_s=1500.0)
        st = _ping(
            st, offset_m=50_000.0, at_min=30.0, moving_s=300.0,
            off_route_m=OFF_ROUTE_M + 1.0,
        )
        assert st["stale"]
        assert st["off_route_since_min"] == 30.0

        # Still off five minutes later: the episode started when it started.
        st = _ping(
            st, offset_m=50_000.0, at_min=35.0, moving_s=300.0,
            off_route_m=OFF_ROUTE_M + 900.0,
        )
        assert st["off_route_since_min"] == 30.0

    def test_rejoining_forgets_it(self):
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(
            st, offset_m=10_000.0, at_min=5.0, moving_s=300.0,
            off_route_m=OFF_ROUTE_M + 1.0,
        )
        assert st["off_route_since_min"] == 5.0
        st = _ping(st, offset_m=20_000.0, at_min=15.0, moving_s=600.0)
        assert st["off_route_since_min"] is None
        assert not reroute_due(st)

    def test_a_new_road_is_only_wanted_after_a_sustained_absence(self):
        """A single wild fix must not spend a directions call."""
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(
            st, offset_m=10_000.0, at_min=5.0, moving_s=300.0,
            off_route_m=OFF_ROUTE_M + 1.0,
        )
        assert not reroute_due(st)
        st = _ping(
            st, offset_m=10_000.0, at_min=5.0 + REROUTE_AFTER_MIN, moving_s=120.0,
            off_route_m=OFF_ROUTE_M + 1.0,
        )
        assert reroute_due(st)

    def test_a_car_on_the_route_never_wants_one(self):
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=600.0)
        assert not reroute_due(st)


class TestRebasingOntoANewRoad:
    def test_distance_already_driven_is_banked_not_lost(self):
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=120_000.0, at_min=60.0, moving_s=3600.0)
        st = rebase(
            st, veh=BORN_58, p=PARAMS, lat=51.0, lon=9.0, at_min=70.0,
            detour_m=0.0, speed_kph=130.0, unbilled_min=10.0,
        )
        assert st["offset_m"] == 0.0
        assert st["distance_before_m"] == pytest.approx(120_000.0)
        # …and it keeps banking across a second one.
        st = _ping(st, offset_m=30_000.0, at_min=85.0, moving_s=900.0)
        st = rebase(
            st, veh=BORN_58, p=PARAMS, lat=52.0, lon=10.0, at_min=90.0,
            detour_m=0.0, speed_kph=130.0, unbilled_min=10.0,
        )
        assert st["distance_before_m"] == pytest.approx(150_000.0)

    def test_a_detour_cannot_cost_more_road_than_the_clock_allows(self):
        """A phone that slept through the whole thing reappears wherever it
        reappears, and the straight line to it can be hundreds of kilometres.
        Billing that literally empties the battery and the re-plan then answers
        "no plan reaches the destination from 0%"."""
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        far = rebase(
            st, veh=BORN_58, p=PARAMS, lat=51.0, lon=9.0, at_min=3.0,
            detour_m=500_000.0, speed_kph=130.0, unbilled_min=3.0,
        )
        # Three minutes at 130 is ~6.5 km, not 500.
        capped = rebase(
            st, veh=BORN_58, p=PARAMS, lat=51.0, lon=9.0, at_min=3.0,
            detour_m=6_500.0 / OFF_ROUTE_WINDING, speed_kph=130.0, unbilled_min=3.0,
        )
        assert current_soc(far, 58.0) == pytest.approx(current_soc(capped, 58.0), abs=0.2)
        assert current_soc(far, 58.0) > 85.0

    def test_the_detour_is_billed_rather_than_given_away(self):
        """While off-route nothing is spent, because the position is unknown.
        The moment it is known again the energy has to land somewhere — an
        EV estimate that is quietly optimistic is the dangerous kind."""
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        st = _ping(st, offset_m=100_000.0, at_min=50.0, moving_s=3000.0)
        before = current_soc(st, 58.0)

        free = rebase(
            st, veh=BORN_58, p=PARAMS, lat=51.0, lon=9.0, at_min=60.0,
            detour_m=0.0, speed_kph=130.0, unbilled_min=10.0,
        )
        strayed = rebase(
            st, veh=BORN_58, p=PARAMS, lat=51.0, lon=9.0, at_min=60.0,
            detour_m=20_000.0, speed_kph=130.0, unbilled_min=10.0,
        )
        assert current_soc(free, 58.0) == pytest.approx(before)
        assert current_soc(strayed, 58.0) < before - 2.0

    def test_the_battery_becomes_an_estimate_again(self):
        st = new_state(offset_m=0.0, soc=90.0, lat=50.0, lon=8.0, at_min=0.0)
        assert st["soc_is_measured"]
        st = rebase(
            st, veh=BORN_58, p=PARAMS, lat=51.0, lon=9.0, at_min=10.0,
            detour_m=5_000.0, speed_kph=130.0, unbilled_min=10.0,
        )
        assert not st["soc_is_measured"]

    def test_it_lands_on_the_road_and_not_at_a_plug(self):
        """A charge session is anchored to a plug on the road being left."""
        st = new_state(offset_m=0.0, soc=40.0, lat=50.0, lon=8.0, at_min=0.0)
        st = {**st, "at_charger_id": "ocm-1", "charge_anchor_soc": 40.0,
              "charge_anchor_min": 0.0, "charge_kw": 150.0}
        st = rebase(
            st, veh=BORN_58, p=PARAMS, lat=51.0, lon=9.0, at_min=20.0,
            detour_m=0.0, speed_kph=130.0, unbilled_min=10.0,
        )
        assert st["at_charger_id"] is None
        assert st.get("charge_anchor_soc") is None
        assert not st["stale"]
        assert st["off_route_m"] == 0.0
        # The twenty minutes at the plug are not thrown away.
        assert st["soc_gained"] > 0.0


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


class TestTheLeaveTargetIsAboutOneStop:
    """"Wake me at 80%" is a decision about the coffee, the queue and the
    mountains after THIS charger. Carrying it on would sit somebody through it
    again four hours later."""

    def _at_a_charger(self):
        st = new_state(offset_m=100_000.0, soc=15.0, lat=50.0, lon=8.0, at_min=60.0)
        return _ping(
            st, offset_m=100_000.0, at_min=61.0, moving_s=0.0, stationary_s=60.0,
            stop_power_kw=150.0, at_charger_id="c",
        )

    def test_driving_away_drops_it(self):
        st = {**self._at_a_charger(), "leave_at_soc": 80.0}
        st = plug_in(st, soc=current_soc(st, 58.0), at_min=61.0, kw=150.0)
        gone = _ping(st, offset_m=140_000.0, at_min=101.0, moving_s=2400.0)
        assert "leave_at_soc" not in gone

    def test_it_is_dropped_even_if_the_cable_never_went_in(self):
        """`bank_charge` is what drops it, and it returns early with no anchor
        — so a target set at a plug that turned out to be dead used to ride
        along to the next charger untouched."""
        st = {**self._at_a_charger(), "leave_at_soc": 80.0}
        gone = _ping(st, offset_m=140_000.0, at_min=101.0, moving_s=2400.0)
        assert "leave_at_soc" not in gone
