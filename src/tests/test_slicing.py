"""Tests for re-planning a journey from where the driver actually is.

Mid-journey re-planning works by slicing the route at the current position and
handing the remainder to the ordinary DP. That only gives an answer consistent
with the original plan if the slice preserves three things the simulator reads
implicitly: which autobahn stretches are open (hashed from the segment INDEX),
the break you have already earned, and the route's own arithmetic.
"""

from __future__ import annotations

import pytest

from app.services.simulator import (
    RouteSegment,
    SimParams,
    charge_forward,
    charge_minutes,
    effective_kph,
    simulate,
    slice_route,
    sweep,
    sweep_slice,
)
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from tests.fixtures import BORN_58, chargers_every, flat_motorway, nl_to_austria_route


# ---------------------------------------------------------------------------
# The derestriction pattern
# ---------------------------------------------------------------------------


class TestOpenStretchIndexing:
    def test_index_zero_is_always_open(self):
        """Pins the mechanism, so nobody 'simplifies' index_offset away.

        `_open_stretch` hashes the segment index with a multiplicative hash,
        and 0 hashes to exactly 0.0 — which is below every non-zero share. A
        slice that renumbers its first segment to 0 therefore hands itself a
        derestricted autobahn starting precisely where the driver is standing.
        """
        seg = RouteSegment(dist_m=10_000, freeflow_kph=125.0, country="DE")
        p = SimParams(autobahn_open_share=0.3)
        assert effective_kph(180.0, seg, p, 0) == 180.0  # open: no cap binds
        assert effective_kph(180.0, seg, p, 1) == 130.0  # closed: cap binds

    def test_slicing_preserves_which_stretches_are_open(self):
        segs = flat_motorway(600, freeflow=125, country="DE")
        p = SimParams(autobahn_open_share=0.3)
        sl = slice_route(segs, [], from_offset_m=200_000.0)
        assert sl.index_offset == 20
        for i, seg in enumerate(sl.segments):
            assert effective_kph(180.0, seg, p, sl.index_offset + i) == effective_kph(
                180.0, segs[20 + i], p, 20 + i
            )

    def test_the_index_offset_is_load_bearing(self):
        """Drop it and the answer really does change — this is not decoration."""
        segs = flat_motorway(600, freeflow=125, country="DE")
        chargers = chargers_every(600, 70.0, power_kw=150.0)
        p = SimParams(autobahn_open_share=0.3)
        sl = slice_route(segs, chargers, from_offset_m=200_000.0)
        right = simulate(
            sl.segments, sl.chargers, BORN_58, 180.0, p, index_offset=sl.index_offset
        )
        naive = simulate(sl.segments, sl.chargers, BORN_58, 180.0, p)
        assert naive.total_min != pytest.approx(right.total_min, rel=1e-9)
        # And the naive one is the optimistic direction: free autobahn.
        assert naive.drive_min < right.drive_min


# ---------------------------------------------------------------------------
# Slice arithmetic
# ---------------------------------------------------------------------------


class TestSliceArithmetic:
    def test_slicing_at_zero_is_the_identity(self):
        segs, chargers = nl_to_austria_route()
        sl = slice_route(segs, chargers, 0.0)
        assert sl.index_offset == 0
        assert sl.segments == segs
        assert sl.chargers == chargers
        assert sl.total_dist_m == pytest.approx(sum(s.dist_m for s in segs))

    def test_slicing_at_zero_reproduces_the_whole_plan(self):
        """The strongest statement of correctness available: not merely a
        similar total, the same plan stop for stop."""
        segs, chargers = nl_to_austria_route()
        p = SimParams(autobahn_open_share=0.3)
        full = simulate(segs, chargers, BORN_58, 130.0, p)
        sl = slice_route(segs, chargers, 0.0)
        cut = simulate(
            sl.segments, sl.chargers, BORN_58, 130.0, p, index_offset=sl.index_offset
        )
        assert cut.total_min == full.total_min
        assert cut.n_stops == full.n_stops
        assert len(cut.timeline) == len(full.timeline)
        assert [s.charger_id for s in cut.stops] == [s.charger_id for s in full.stops]
        for a, b in zip(cut.stops, full.stops):
            assert a.arrive_soc == pytest.approx(b.arrive_soc, abs=1e-9)
            assert a.depart_soc == pytest.approx(b.depart_soc, abs=1e-9)

    def test_slice_conserves_distance(self):
        segs = flat_motorway(600, freeflow=120)
        total = sum(s.dist_m for s in segs)
        for cut in (0.0, 1_000.0, 55_500.0, 200_000.0, 599_999.0):
            sl = slice_route(segs, [], cut)
            assert sl.total_dist_m == pytest.approx(total - cut, abs=1e-6)

    def test_slice_conserves_climb_pro_rata(self):
        segs = [
            RouteSegment(dist_m=10_000, freeflow_kph=120, climb_m=100.0, descent_m=40.0)
            for _ in range(10)
        ]
        # Cut half way through the third segment: 7.5 segments' worth left.
        sl = slice_route(segs, [], 25_000.0)
        assert sum(s.climb_m for s in sl.segments) == pytest.approx(750.0, rel=1e-9)
        assert sum(s.descent_m for s in sl.segments) == pytest.approx(300.0, rel=1e-9)

    def test_slice_shifts_chargers_and_drops_the_ones_behind_you(self):
        segs = flat_motorway(600, freeflow=120)
        chargers = chargers_every(600, 50.0, power_kw=150.0)
        sl = slice_route(segs, chargers, 220_000.0)
        assert all(c.offset_m > 0 for c in sl.chargers)
        ahead = [c for c in chargers if c.offset_m > 220_001.0]
        assert len(sl.chargers) == len(ahead)
        assert [c.charger_id for c in sl.chargers] == [c.charger_id for c in ahead]
        for got, orig in zip(sl.chargers, ahead):
            assert got.offset_m == pytest.approx(orig.offset_m - 220_000.0)

    def test_the_charger_you_are_standing_at_is_not_a_stop_ahead(self):
        segs = flat_motorway(600, freeflow=120)
        chargers = chargers_every(600, 50.0, power_kw=150.0)
        sl = slice_route(segs, chargers, chargers[2].offset_m)
        assert chargers[2].charger_id not in [c.charger_id for c in sl.chargers]

    def test_slicing_twice_equals_slicing_once(self):
        """Catches index-offset accumulation bugs, and is why `slice_route`
        takes an `index_offset` argument at all."""
        segs = flat_motorway(600, freeflow=120)
        chargers = chargers_every(600, 50.0, power_kw=150.0)
        once = slice_route(segs, chargers, 250_000.0)
        a = slice_route(segs, chargers, 100_000.0)
        twice = slice_route(a.segments, a.chargers, 150_000.0, a.index_offset)
        assert twice.index_offset == once.index_offset
        assert twice.total_dist_m == pytest.approx(once.total_dist_m, abs=1e-6)
        assert [c.charger_id for c in twice.chargers] == [
            c.charger_id for c in once.chargers
        ]

    def test_slice_past_the_end_is_empty(self):
        segs = flat_motorway(100, freeflow=120)
        sl = slice_route(segs, [], 200_000.0)
        assert sl.segments == []
        assert sl.chargers == []
        assert sl.total_dist_m == 0.0

    def test_a_cut_landing_on_a_boundary_makes_no_sliver_segment(self):
        segs = flat_motorway(100, freeflow=120)  # 10 km segments
        sl = slice_route(segs, [], 30_000.0)
        assert len(sl.segments) == 7
        assert sl.index_offset == 3
        assert all(s.dist_m > 0 for s in sl.segments)


# ---------------------------------------------------------------------------
# Re-planning against the original
# ---------------------------------------------------------------------------


class TestReplanConsistency:
    def test_replanning_at_a_stop_reproduces_the_tail(self):
        """Cut exactly at a planned stop, resume with that stop's departure
        SoC, and the rest of the journey must come out the same."""
        segs, chargers = nl_to_austria_route()
        p = SimParams(autobahn_open_share=0.3)
        full = simulate(segs, chargers, BORN_58, 130.0, p)
        assert full.n_stops >= 2
        stop = full.stops[1]
        # Time already spent by the moment you pull away from that stop.
        elapsed = (
            stop.arrive_min + stop.charge_min + stop.detour_min + p.stop_overhead_min
        )

        sl = slice_route(segs, chargers, stop.offset_m)
        tail = simulate(
            sl.segments, sl.chargers, BORN_58, 130.0,
            SimParams(autobahn_open_share=0.3, depart_soc=stop.depart_soc),
            index_offset=sl.index_offset,
        )
        assert tail.feasible
        remaining = [s.charger_id for s in full.stops[2:]]
        assert [s.charger_id for s in tail.stops] == remaining
        assert tail.total_min == pytest.approx(full.total_min - elapsed, abs=1e-6)

    def test_replanning_mid_leg_rejoins_the_original_plan(self):
        """Mid-leg, the re-plan may differ on its NEXT stop and can even come
        out slightly slower — neither is a bug — but it must rejoin the
        original plan after that.

        `simulate` quantises SoC onto a 2.5% grid, and that includes the SoC it
        departs from (`depart_bucket = int(depart_soc / SOC_BUCKET)`). Cut on
        39.5% and the DP plans as though you had 37.5%, because rounding down
        is the safe direction. Losing those two points can put the original's
        next charger just out of reach and push the re-plan to an earlier,
        slower one — costing a couple of minutes it then never recovers.

        Re-plan at a stop instead and there is no rounding loss at all, because
        the DP only ever leaves a charger on a bucket boundary. That case is
        pinned exactly by `test_replanning_at_a_stop_reproduces_the_tail`.
        """
        segs, chargers = nl_to_austria_route()
        p = SimParams(autobahn_open_share=0.3)
        full = simulate(segs, chargers, BORN_58, 130.0, p)
        # Cut at a real timeline sample rather than an arbitrary offset, so the
        # position, the time and the SoC all describe the same instant. Skip
        # samples sitting on a stop: those are written twice, once on arrival
        # and once on departure, and picking the wrong one of the pair would
        # start the tail with the wrong battery.
        stop_offsets = {s.offset_m for s in full.stops}
        after = full.stops[0].offset_m + 40_000.0
        cut = t_at_cut = soc_at_cut = None
        for t, x, s in full.timeline:
            if x >= after and x not in stop_offsets:
                cut, t_at_cut, soc_at_cut = x, t, s
                break
        assert cut is not None

        sl = slice_route(segs, chargers, cut)
        tail = simulate(
            sl.segments, sl.chargers, BORN_58, 130.0,
            SimParams(autobahn_open_share=0.3, depart_soc=soc_at_cut),
            index_offset=sl.index_offset,
        )
        assert tail.feasible
        remaining_min = full.total_min - t_at_cut
        remaining_ids = [s.charger_id for s in full.stops if s.offset_m > cut]
        tail_ids = [s.charger_id for s in tail.stops]

        # It rejoins: everything past the next stop is the original plan.
        assert tail_ids[1:] == remaining_ids[1:]
        # And it stays close — the whole divergence is one grid step of charge.
        assert tail.total_min == pytest.approx(remaining_min, rel=0.02)

    def test_sweep_slice_carries_the_offset(self):
        segs, chargers = nl_to_austria_route()
        p = SimParams(autobahn_open_share=0.3)
        sl = slice_route(segs, chargers, 300_000.0)
        speeds = [120.0, 130.0, 140.0]
        via_helper = sweep_slice(sl, BORN_58, speeds, p)
        by_hand = sweep(
            sl.segments, sl.chargers, BORN_58, speeds, p, index_offset=sl.index_offset
        )
        assert [r.total_min for r in via_helper] == [r.total_min for r in by_hand]


# ---------------------------------------------------------------------------
# The two distance axes
# ---------------------------------------------------------------------------


class TestDistanceAxes:
    """A GPS fix snaps onto the polyline and comes back as a haversine
    distance; the simulator measures in ORS step distances. On real routes the
    two agree to within a rounding error, but nothing guarantees that, so the
    conversion is explicit rather than assumed.
    """

    def _route(self, seg_m: list[float], geom_m: list[float]) -> RouteData:
        return RouteData(
            geometry=RouteGeometry([(52.0, 5.0), (47.0, 11.0)]),
            segments=[RouteSegment(dist_m=d, freeflow_kph=120.0) for d in seg_m],
            seg_geom_offsets=geom_m,
            total_dist_m=sum(seg_m),
            total_dur_s=1.0,
        )

    def test_boundaries_map_exactly(self):
        # Geometry axis runs 2% long relative to the segment axis.
        r = self._route([10_000.0] * 3, [0.0, 10_200.0, 20_400.0, 30_600.0])
        assert r.geom_to_segment_m(0.0) == 0.0
        assert r.geom_to_segment_m(10_200.0) == pytest.approx(10_000.0)
        assert r.geom_to_segment_m(30_600.0) == pytest.approx(30_000.0)

    def test_it_interpolates_inside_a_step(self):
        r = self._route([10_000.0] * 3, [0.0, 10_200.0, 20_400.0, 30_600.0])
        assert r.geom_to_segment_m(5_100.0) == pytest.approx(5_000.0)

    def test_it_round_trips(self):
        r = self._route([8_000.0, 12_000.0, 5_000.0], [0.0, 8_050.0, 20_150.0, 25_100.0])
        for g in (0.0, 1_234.0, 8_050.0, 15_000.0, 25_100.0):
            assert r.segment_to_geom_m(r.geom_to_segment_m(g)) == pytest.approx(g, abs=1e-6)

    def test_it_clamps_past_either_end(self):
        r = self._route([10_000.0] * 2, [0.0, 10_100.0, 20_200.0])
        assert r.geom_to_segment_m(-500.0) == 0.0
        assert r.geom_to_segment_m(99_999.0) == pytest.approx(20_000.0)

    def test_no_map_means_the_axes_are_treated_as_one(self):
        """Synthetic routes in tests carry no mapping; identity is the honest
        fallback rather than a crash."""
        r = self._route([10_000.0] * 2, [])
        assert r.geom_to_segment_m(7_321.0) == 7_321.0


# ---------------------------------------------------------------------------
# Inherited rest debt
# ---------------------------------------------------------------------------


class TestPriorRest:
    def test_the_remaining_plan_inherits_the_break_you_already_owe(self):
        segs = flat_motorway(250, freeflow=110, country="DE")
        base = dict(rest_interval_min=120.0, rest_min=25.0)
        fresh = simulate(segs, [], BORN_58, 100.0, SimParams(**base))
        carried = simulate(
            segs, [], BORN_58, 100.0, SimParams(**base, prior_drive_min=110.0)
        )
        assert carried.rest_min > fresh.rest_min

    def test_standing_still_pays_the_debt_down(self):
        segs = flat_motorway(250, freeflow=110, country="DE")
        base = dict(rest_interval_min=120.0, rest_min=25.0, prior_drive_min=110.0)
        owed = simulate(segs, [], BORN_58, 100.0, SimParams(**base))
        paid = simulate(
            segs, [], BORN_58, 100.0,
            SimParams(**base, prior_rest_credit_min=25.0),
        )
        assert paid.rest_min < owed.rest_min

    def test_no_prior_history_is_the_old_behaviour(self):
        segs = flat_motorway(400, freeflow=110, country="DE")
        chargers = chargers_every(400, 90.0, power_kw=150.0)
        p = dict(rest_interval_min=120.0, rest_min=25.0, autobahn_open_share=0.3)
        a = simulate(segs, chargers, BORN_58, 120.0, SimParams(**p))
        b = simulate(
            segs, chargers, BORN_58, 120.0,
            SimParams(**p, prior_drive_min=0.0, prior_rest_credit_min=0.0),
        )
        assert a.total_min == b.total_min
        assert a.rest_min == b.rest_min


# ---------------------------------------------------------------------------
# Charging forwards in time
# ---------------------------------------------------------------------------


class TestChargeForward:
    @pytest.mark.parametrize("site_kw", [50.0, 150.0, 300.0])
    @pytest.mark.parametrize("soc_from", [5.0, 20.0, 45.0, 70.0])
    @pytest.mark.parametrize("span", [5.0, 20.0, 40.0])
    def test_it_inverts_charge_minutes(self, site_kw, soc_from, span):
        """The two must agree by construction: the plan decides how long to
        stay with one, and the live trip infers what the battery did with the
        other. If they drifted, every stop would end on a different number."""
        soc_to = min(100.0, soc_from + span)
        minutes = charge_minutes(BORN_58, soc_from, soc_to, site_kw)
        back = charge_forward(BORN_58, soc_from, minutes, site_kw)
        assert back == pytest.approx(soc_to, abs=0.5)

    def test_no_time_means_no_change(self):
        assert charge_forward(BORN_58, 42.0, 0.0, 150.0) == 42.0

    def test_it_never_exceeds_full(self):
        assert charge_forward(BORN_58, 95.0, 600.0, 300.0) == pytest.approx(100.0)

    def test_more_time_never_means_less_charge(self):
        prev = 0.0
        for minutes in (0.0, 2.0, 5.0, 10.0, 20.0, 40.0, 80.0):
            soc = charge_forward(BORN_58, 10.0, minutes, 150.0)
            assert soc >= prev
            prev = soc

    def test_a_derated_site_charges_more_slowly(self):
        warm = charge_forward(BORN_58, 20.0, 15.0, 150.0, power_factor=1.0)
        cold = charge_forward(BORN_58, 20.0, 15.0, 150.0, power_factor=0.55)
        assert cold < warm
