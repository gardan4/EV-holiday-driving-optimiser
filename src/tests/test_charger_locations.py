"""One place to pull into, several badges on the forecourt.

A motorway services is several rows in OpenChargeMap — Tesla, IONITY and the
site's own posts, a hundred metres apart — and the corridor search used to keep
the most powerful of them and drop the rest. Network exclusions run much later,
on the DP's candidates, so ruling out the brand that happened to win took the
WHOLE location away: the plan drove past a forecourt full of usable chargers,
and the only visible effect was a toggle that appeared to delete chargers.

These pin the grouping that fixes it, and the two things that keep it from
costing what it fixes: no pointless extra rows, and no location lost to them.
"""

from __future__ import annotations

from app.services.chargers import (
    DEDUP_RADIUS_M,
    MAX_NODES,
    MIN_NODES,
    NODE_SPACING_M,
    VARIANT_ROOM,
    _trim_nodes,
    dedupe_locations,
    location_variants,
)
from app.services.networks import usable
from app.services.simulator import ChargerNode


def node(
    cid: str,
    offset_m: float,
    power_kw: float,
    *,
    operator: str | None = None,
    name: str = "",
) -> ChargerNode:
    return ChargerNode(
        charger_id=cid,
        name=name or cid,
        offset_m=offset_m,
        power_kw=power_kw,
        operator=operator,
    )


def ids(nodes) -> set[str]:
    return {n.charger_id for n in nodes}


class TestWhatOneLocationKeeps:
    def test_a_weaker_brand_at_the_same_place_survives(self):
        """The bug, in one assertion: the EnBW posts are 80 m from the Tesla
        stalls and 100 kW weaker, so the old rule deleted them — and with them
        every reason to stop at that services once Tesla was excluded."""
        group = [
            node("tesla", 300_000.0, 250.0, operator="Tesla", name="Supercharger Geiselwind"),
            node("enbw", 300_080.0, 150.0, operator="EnBW", name="Autohof Geiselwind"),
        ]
        assert ids(location_variants(group)) == {"tesla", "enbw"}

    def test_the_same_brand_twice_still_collapses_to_the_strongest(self):
        """Keeping a second row of a network the driver can only exclude as a
        whole buys nothing and costs the DP a node."""
        group = [
            node("ionity-a", 300_000.0, 350.0, operator="IONITY"),
            node("ionity-b", 300_120.0, 175.0, operator="IONITY GmbH"),
        ]
        assert ids(location_variants(group)) == {"ionity-a"}

    def test_a_site_we_cannot_identify_ends_the_list(self):
        """Nothing weaker than an unrecognised operator can ever be needed: no
        exclusion can take that row away, and it is the stronger one. Without
        this, every cluster in the country would keep every badge on it."""
        group = [
            node("ionity", 300_000.0, 350.0, operator="IONITY"),
            node("town", 300_100.0, 300.0, operator="Stadtwerke Musterstadt"),
            node("tesla", 300_200.0, 250.0, name="Tesla Supercharger"),
        ]
        assert ids(location_variants(group)) == {"ionity", "town"}

    def test_the_strongest_is_still_first(self):
        group = [
            node("tesla", 300_000.0, 250.0, operator="Tesla"),
            node("ionity", 300_100.0, 350.0, operator="IONITY"),
        ]
        assert location_variants(group)[0].charger_id == "ionity"


class TestGrouping:
    def test_sites_further_apart_than_the_radius_are_two_places(self):
        nodes = [
            node("a", 300_000.0, 250.0, operator="Tesla"),
            node("b", 300_000.0 + DEDUP_RADIUS_M + 1.0, 150.0, operator="Tesla"),
        ]
        assert [ids(g) for g in dedupe_locations(nodes)] == [{"a"}, {"b"}]

    def test_groups_come_back_in_route_order(self):
        nodes = [
            node("far", 800_000.0, 150.0),
            node("near", 100_000.0, 150.0),
        ]
        assert [g[0].charger_id for g in dedupe_locations(nodes)] == ["near", "far"]

    def test_excluding_a_network_leaves_the_forecourt_standing(self):
        """End to end over the two modules: what the corridor search hands the
        DP, run through the exclusion the driver actually set."""
        corridor = [
            n
            for g in dedupe_locations(
                [
                    node("tesla", 300_000.0, 250.0, name="Tesla Supercharger Geiselwind"),
                    node("enbw", 300_080.0, 150.0, operator="EnBW"),
                ]
            )
            for n in g
        ]
        assert ids(usable(corridor, ["tesla"])) == {"enbw"}


class TestTrimmingKeepsThemTogether:
    """The cap is where this is easiest to get wrong: on any route long enough
    to trim there are more locations than the budget, so variants ranked
    against locations lose every time and the grouping above buys nothing."""

    @staticmethod
    def _corridor(n_places: int, *, twin_every: int = 1) -> list[list[ChargerNode]]:
        nodes: list[ChargerNode] = []
        for i in range(n_places):
            at = float(i * 10_000)
            nodes.append(node(f"t{i}", at, 250.0, operator="Tesla"))
            if i % twin_every == 0:
                nodes.append(node(f"e{i}", at + 100.0, 150.0, operator="EnBW"))
        return dedupe_locations(nodes)

    @staticmethod
    def _budget(total_m: float) -> int:
        return int(min(MAX_NODES, max(MIN_NODES, total_m / NODE_SPACING_M)))

    def test_second_brands_survive_the_cap(self):
        total_m = 2_000_000.0
        kept = _trim_nodes(self._corridor(300, twin_every=4), total_m)
        assert {n.charger_id[0] for n in kept} == {"t", "e"}
        # Not a token few: the headroom is spent, and on real pairs.
        by_place: dict[int, set[str]] = {}
        for n in kept:
            by_place.setdefault(round(n.offset_m / 10_000), set()).add(n.charger_id[0])
        twinned = [p for p, badges in by_place.items() if badges == {"t", "e"}]
        assert len(twinned) >= self._budget(total_m) // 4

    def test_coverage_still_comes_first(self):
        """The old rule's locations are still the locations: a second badge
        never displaces a place the plan would otherwise be able to stop at."""
        total_m = 2_000_000.0
        kept = _trim_nodes(self._corridor(300), total_m)
        places = {round(n.offset_m / 10_000) for n in kept}
        assert len(places) == self._budget(total_m)
        offsets = [0.0] + sorted(n.offset_m for n in kept) + [total_m]
        widest = max(b - a for a, b in zip(offsets, offsets[1:]))
        assert widest < 200_000, f"{widest / 1000:.0f} km gap between candidates"

    def test_the_row_count_stays_bounded(self):
        """The DP is O(nodes²·buckets), so the headroom is a fixed allowance
        and not "keep every badge on the corridor"."""
        total_m = 2_000_000.0
        budget = self._budget(total_m)
        kept = _trim_nodes(self._corridor(400), total_m)
        assert len(kept) <= budget + int(budget * VARIANT_ROOM)
        assert kept == sorted(kept, key=lambda n: n.offset_m), "route order preserved"

    def test_a_corridor_that_fits_is_left_whole(self):
        groups = self._corridor(20)
        kept = _trim_nodes(groups, 500_000.0)
        assert len(kept) == sum(len(g) for g in groups)
