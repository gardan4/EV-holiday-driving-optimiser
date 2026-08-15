"""Chargers you would have to double back for.

A site on the far carriageway of a motorway is a hundred metres away and
unreachable without carrying on to the next junction and driving back. Every
distance-based measure calls it the closest charger on the route, which is
exactly how an itinerary comes to instruct somebody to drive back the way they
came. These tests pin the geometry that tells the two apart, and the three
conditions that keep the rule from firing where it would only take chargers
away.
"""

from __future__ import annotations

from app.services.chargers import (
    DIVIDED_ROAD_KPH,
    SIDE_MATTERS_M,
    needs_u_turn,
    reachable_side,
    road_at,
)
from app.services.geo import RouteGeometry
from app.services.routing import RouteData
from app.services.simulator import RouteSegment

# Due east along a parallel, so "north of the line" is unambiguously left of
# the direction of travel and south is right.
EASTBOUND = RouteGeometry([(50.0, 6.0), (50.0, 7.0)])

# ~111 m per 0.001° of latitude.
NORTH_OF_LINE = (50.003, 6.5)   # left of travel
SOUTH_OF_LINE = (49.997, 6.5)   # right of travel


def _route(freeflow_kph: float, country: str) -> RouteData:
    return RouteData(
        geometry=EASTBOUND,
        segments=[
            RouteSegment(
                dist_m=EASTBOUND.total_m,
                freeflow_kph=freeflow_kph,
                country=country,
            )
        ],
    )


class TestWhichSide:
    def test_left_and_right_of_travel_are_distinguished(self):
        _, perp_l, side_l = EASTBOUND.project_detailed(*NORTH_OF_LINE)
        _, perp_r, side_r = EASTBOUND.project_detailed(*SOUTH_OF_LINE)
        assert side_l == 1
        assert side_r == -1
        # The whole problem in one assertion: the two are the same distance
        # away, so nothing but the sign can tell them apart.
        assert abs(perp_l - perp_r) < 5.0

    def test_driving_the_other_way_swaps_the_sides(self):
        westbound = RouteGeometry([(50.0, 7.0), (50.0, 6.0)])
        _, _, side = westbound.project_detailed(*NORTH_OF_LINE)
        assert side == -1

    def test_a_point_on_the_line_has_no_side(self):
        _, perp, side = EASTBOUND.project_detailed(50.0, 6.5)
        assert perp < 1.0
        assert side == 0

    def test_project_still_returns_the_pair_it_always_did(self):
        assert EASTBOUND.project(*NORTH_OF_LINE) == (
            EASTBOUND.project_detailed(*NORTH_OF_LINE)[0],
            EASTBOUND.project_detailed(*NORTH_OF_LINE)[1],
        )


class TestTrafficSide:
    def test_right_hand_traffic_countries_reach_the_right(self):
        assert reachable_side("DE") == -1
        assert reachable_side("NL") == -1

    def test_left_hand_traffic_countries_reach_the_left(self):
        assert reachable_side("GB") == 1
        assert reachable_side("JP") == 1

    def test_an_unknown_country_switches_the_rule_off(self):
        """Guessing would be worse than not applying it: in a left-hand-traffic
        country a wrong guess drops the reachable chargers and keeps the
        unreachable ones."""
        assert reachable_side("") is None


class TestTheRule:
    def test_the_far_carriageway_of_a_motorway_is_dropped(self):
        route = _route(130.0, "DE")
        off, perp, side = EASTBOUND.project_detailed(*NORTH_OF_LINE)
        assert needs_u_turn(route, off, perp, side) is True

    def test_your_own_carriageway_is_kept(self):
        route = _route(130.0, "DE")
        off, perp, side = EASTBOUND.project_detailed(*SOUTH_OF_LINE)
        assert needs_u_turn(route, off, perp, side) is False

    def test_a_slow_road_can_be_turned_on(self):
        """Below the motorway threshold, the far side is a junction away, not a
        20 km penance — and this is where dropping chargers would hurt."""
        route = _route(DIVIDED_ROAD_KPH - 10.0, "DE")
        off, perp, side = EASTBOUND.project_detailed(*NORTH_OF_LINE)
        assert needs_u_turn(route, off, perp, side) is False

    def test_far_enough_off_route_the_carriageway_stops_deciding(self):
        route = _route(130.0, "DE")
        far = (50.0 + (SIDE_MATTERS_M * 2) / 111_320.0, 6.5)
        off, perp, side = EASTBOUND.project_detailed(*far)
        assert perp > SIDE_MATTERS_M
        assert needs_u_turn(route, off, perp, side) is False

    def test_an_unmapped_country_keeps_everything(self):
        route = _route(130.0, "")
        off, perp, side = EASTBOUND.project_detailed(*NORTH_OF_LINE)
        assert needs_u_turn(route, off, perp, side) is False

    def test_left_hand_traffic_drops_the_other_one(self):
        route = _route(130.0, "GB")
        off_l, perp_l, side_l = EASTBOUND.project_detailed(*NORTH_OF_LINE)
        off_r, perp_r, side_r = EASTBOUND.project_detailed(*SOUTH_OF_LINE)
        assert needs_u_turn(route, off_l, perp_l, side_l) is False
        assert needs_u_turn(route, off_r, perp_r, side_r) is True


class TestRoadLookup:
    def test_it_reads_the_segment_under_the_point(self):
        geom = RouteGeometry([(50.0, 6.0), (50.0, 7.0)])
        route = RouteData(
            geometry=geom,
            segments=[
                RouteSegment(dist_m=geom.total_m / 2, freeflow_kph=50.0, country="NL"),
                RouteSegment(dist_m=geom.total_m / 2, freeflow_kph=130.0, country="DE"),
            ],
            seg_geom_offsets=[0.0, geom.total_m / 2, geom.total_m],
        )
        assert road_at(route, 10.0) == (50.0, "NL")
        assert road_at(route, geom.total_m - 10.0) == (130.0, "DE")

    def test_a_route_with_no_segments_answers_nothing(self):
        route = RouteData(geometry=EASTBOUND, segments=[])
        assert road_at(route, 0.0) == (0.0, "")
