"""Which charging network a site belongs to, and ruling one out.

The matcher is the whole feature: a missed match sends somebody to a charger
they told us to avoid, and a wrong match quietly takes a usable charger away
from a route that may need it. Both failures are invisible on screen — the plan
that comes back is a perfectly good plan either way — so they are pinned here.
"""

from __future__ import annotations

from app.services.networks import (
    NETWORKS,
    SLUGS,
    labels,
    network_of,
    usable,
)
from app.services.simulator import ChargerNode


def node(cid: str, *, operator: str | None = None, name: str = "") -> ChargerNode:
    return ChargerNode(
        charger_id=cid, name=name, offset_m=0.0, power_kw=150.0, operator=operator
    )


class TestTheList:
    def test_slugs_are_unique_and_stable_shaped(self):
        slugs = [n.slug for n in NETWORKS]
        assert len(slugs) == len(set(slugs))
        assert SLUGS == set(slugs)
        for n in NETWORKS:
            assert n.slug == n.slug.lower()
            assert n.needles, n.slug
            assert all(x == x.lower() for x in n.needles), n.slug

    def test_labels_come_back_in_list_order_whatever_order_they_go_in(self):
        """The message names them, and "avoiding Fastned, IONITY" reading
        differently from "avoiding IONITY, Fastned" for the same request is the
        kind of wobble that makes a screenshot untrustworthy."""
        assert labels({"fastned", "ionity"}) == labels({"ionity", "fastned"})
        assert labels(["ionity", "fastned"]) == ["IONITY", "Fastned"]


class TestIdentifyingASite:
    def test_it_reads_the_operator(self):
        assert network_of("Fastned", "Zevenaar") == "fastned"
        assert network_of("IONITY GmbH", "Some Place") == "ionity"

    def test_it_reads_the_name_when_the_operator_is_missing(self):
        """OCM's operator field is absent or "Unknown" on a large minority of
        sites, while the title says the brand in plain sight. This is the
        OPPOSITE call from `amenities.food_hint`, which refuses the operator —
        there the question is what is nearby, here the question IS the brand."""
        assert network_of(None, "Tesla Supercharger Geiselwind") == "tesla"
        assert network_of("", "IONITY Holzkirchen Süd") == "ionity"

    def test_an_unknown_operator_is_none_not_a_guess(self):
        assert network_of("Stadtwerke Musterstadt", "Parkplatz Ost") is None
        assert network_of(None, None) is None
        assert network_of("", "") is None

    def test_it_does_not_fire_on_ordinary_words(self):
        """The needles are brand names, and a filter that quietly drops the
        chargers on a French coastal corridor is worse than one that has never
        heard of Mer. `total` and `bp` are excluded from the list for the same
        reason; `TotalEnergies` is spelled out."""
        assert network_of(None, "Bord de Mer") is None
        assert network_of("Total", "Total Access Nord") is None
        assert network_of(None, "Supermarché Casino") is None
        assert network_of("TotalEnergies", "Aire de Beaune") == "totalenergies"

    def test_it_matches_whole_words_only(self):
        assert network_of(None, "Ionityville Garage") is None
        assert network_of(None, "Ionity Aire de Beaune") == "ionity"


class TestRulingOneOut:
    def test_nothing_excluded_leaves_the_list_alone(self):
        nodes = [node("a", operator="Ionity"), node("b", operator="Fastned")]
        assert [c.charger_id for c in usable(nodes, [])] == ["a", "b"]
        assert [c.charger_id for c in usable(nodes, None)] == ["a", "b"]

    def test_only_the_named_network_goes(self):
        nodes = [
            node("a", operator="IONITY GmbH"),
            node("b", operator="Fastned"),
            node("c", name="Raststätte Spessart"),
            node("d", name="Tesla Supercharger Geiselwind"),
        ]
        left = [c.charger_id for c in usable(nodes, ["ionity", "tesla"])]
        assert left == ["b", "c"]

    def test_a_site_we_cannot_identify_is_never_dropped(self):
        """None means "we don't know", not "independent". Dropping the
        unidentified ones would make excluding one network take away every
        charger whose operator OCM never recorded."""
        nodes = [node("a", name="Parkplatz Ost"), node("b", operator="Ionity")]
        assert [c.charger_id for c in usable(nodes, ["ionity"])] == ["a"]

    def test_an_unknown_slug_cannot_take_chargers_away(self):
        """The request layer rejects these, so reaching here means a stored
        trip carries a slug that has since been renamed. Ignoring it plans a
        slightly worse journey; acting on it would be acting on a guess."""
        nodes = [node("a", operator="Ionity")]
        assert [c.charger_id for c in usable(nodes, ["not-a-network"])] == ["a"]
