"""Reading a charger's name for whether you can eat there.

OpenChargeMap has no amenity data and we store none, so the site's own title is
the only signal available. It is a guess, and these tests pin the two ways a
guess like this goes wrong: claiming too much, and claiming it about the
operator instead of the place.
"""

from __future__ import annotations

import pytest

from app.services.amenities import food_hint


class TestWhatANameSuggests:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("Raststätte Spessart Nord", "motorway services"),
            ("Raststatte Spessart Nord", "motorway services"),  # unaccented
            ("Autohof Strohofer", "truck stop"),
            ("Aire de Beaune Tailly", "aire"),
            ("Moto Services Reading", "motorway services"),
            ("Lidl Venlo", "supermarket"),
            ("IKEA Duiven", "store with a restaurant"),
            ("McDonald's A2", "restaurant"),
            ("Hotel Post", "hotel"),
            # German compounds: the marker is inside the word, which is why
            # matching is substring-first and not word-boundary-first.
            ("Autobahnraststätte Spessart Nord", "motorway services"),
            ("Raststation Angath", "motorway services"),
            ("Serways Fürholzen West", "motorway services"),
            ("Imbiss Süd", "snack bar"),
            ("Bäckerei Müller", "bakery"),
            ("Intermarché Beaune", "supermarket"),
            ("Station service Total", "filling station"),
            ("Welcome Break Oxford", "motorway services"),
            # Whole-word markers, matched as words.
            ("SPAR Kufstein", "supermarket"),
            ("Hofer Wörgl", "supermarket"),
            ("Moto Toddington", "motorway services"),
        ],
    )
    def test_names_that_mean_food(self, name, expected):
        assert food_hint(name) == expected

    @pytest.mark.parametrize(
        "name",
        [
            "Sparkasse Rosenheim",     # a bank, not a SPAR
            "Autohaus Strohofer P2",   # not a Hofer
            "Mallorca Plaza",          # not a mall
            "Motorway Bridge Car Park",  # not a Moto
            "Hofergasse 5",
            "Normannenweg 3",          # not a Norma
        ],
    )
    def test_short_names_do_not_match_inside_words(self, name):
        """The short markers are matched as WORDS. A substring match on them is
        not a weak guess but a wrong one, and a chip that says "supermarket"
        over a savings bank is the failure this module exists to avoid."""
        assert food_hint(name) is None

    def test_the_corridor_that_prompted_this(self):
        """What a German motorway actually looks like in OpenChargeMap.

        Network plus place, every one of them — and Geiselwind is an Autohof
        with a kitchen. The reading has no way to know that, so the honest
        answer for the whole list is silence, and the panel showing it has to
        be able to SAY that rather than just showing no chips.
        """
        for name in (
            "Tesla Supercharger Feucht",
            "Tesla Supercharger Geiselwind",
            "IONITY Holzkirchen Süd",
            "EweGo",
            "Im Gewerbepark",
        ):
            assert food_hint(name) is None

    @pytest.mark.parametrize(
        "name",
        [
            "Parkplatz Ost",
            "Fastned Venlo",
            "Ionity Bad Camberg",
            "P+R Zuid",
            "Bahnhofstrasse 14",
            "",
        ],
    )
    def test_names_that_say_nothing(self, name):
        """Null is "the name says nothing", NOT "there is nothing here" — most
        chargers are named after a street, and plenty sit next to a café we
        will never hear about."""
        assert food_hint(name) is None

    def test_it_is_case_and_position_insensitive(self):
        assert food_hint("SHELL TANKSTELLE HEERLEN") == "filling station"
        assert food_hint("Charging point at Tesco Extra") == "supermarket"

    def test_no_name_is_no_hint(self):
        assert food_hint(None) is None
