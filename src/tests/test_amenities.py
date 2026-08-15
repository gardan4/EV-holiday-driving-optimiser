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
        ],
    )
    def test_names_that_mean_food(self, name, expected):
        assert food_hint(name) == expected

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
