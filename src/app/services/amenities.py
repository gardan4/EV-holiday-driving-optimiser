"""Guessing, out loud, whether you can eat where you charge.

OpenChargeMap has no amenity data worth the name — no restaurants, no toilets,
no opening hours — and `_parse_poi` keeps only what a plan needs: a title, an
operator, a power and a position. So there is nothing here to look up, and the
honest options are to say nothing at all or to read the one field that
frequently gives the game away: what the site is CALLED.

"Raststätte Spessart Nord" is a motorway service area with a restaurant in it.
"Autohof Strohofer" is a truck stop that serves hot food at four in the
morning. "Lidl Venlo" is a supermarket. "Parkplatz Ost" is a lay-by. A driver
reads those names and knows; the app can do the same reading, and the value of
saying so is high precisely because the alternative — a stop at nine in the
evening with three hungry people and nowhere to go — is the thing that makes
somebody abandon a plan.

Three rules keep this from becoming a lie:

* **It is a HINT, never a fact.** Every caller must label it as coming from the
  name. A site called "Autohof" that has closed its kitchen is a wrong guess
  the driver can absorb; a wrong guess presented as amenity data is not.
* **The NAME only, never the operator.** "Shell Recharge" is a charging network
  that puts chargers in car parks; "Shell" in a site's own title is a filling
  station with a shop attached. Matching the operator would mark half of
  Europe's chargers as having a bakery.
* **No absence claim.** A null hint means "the name says nothing", not "there
  is nothing here" — most chargers are named after a street or a car park, and
  plenty of them are next to a café we will never hear about.

The ceiling on all of this is worth writing down, because it is easy to mistake
for a bug. On a German motorway corridor the fast chargers are typically called
"Tesla Supercharger Geiselwind", "IONITY Holzkirchen Süd", "EweGo" — network
plus place, no matter what is actually there. Geiselwind is an Autohof with a
kitchen; the name does not say so and nothing in the data we keep does either,
so the honest answer for that whole list is "the names say nothing". A caller
showing this must be able to say THAT, or its silence reads as "nowhere to eat"
— which is the absence claim this module refuses to make.
"""

from __future__ import annotations

import re

# (needle, what to call it). Ordered: the first match wins, so the more
# specific entries come first. Lowercase; matching is case-insensitive and
# accent-naive, hence the unaccented spellings alongside the real ones.
_FOOD_MARKERS: tuple[tuple[str, str], ...] = (
    # Motorway service areas — the strongest signal there is.
    ("raststätte", "motorway services"),
    ("raststatte", "motorway services"),
    ("raststation", "motorway services"),
    ("rasthof", "motorway services"),
    ("rasthaus", "motorway services"),
    ("rastplatz", "rest area"),
    ("tank & rast", "motorway services"),
    ("tank und rast", "motorway services"),
    ("serways", "motorway services"),
    ("sanifair", "motorway services"),
    ("autohof", "truck stop"),
    ("truckstop", "truck stop"),
    ("truck stop", "truck stop"),
    ("autogrill", "motorway services"),
    ("aire de", "aire"),
    ("aire du", "aire"),
    ("service area", "motorway services"),
    ("servicearea", "motorway services"),
    ("services", "motorway services"),
    ("welcome break", "motorway services"),
    ("roadchef", "motorway services"),
    ("verzorgingsplaats", "motorway services"),
    ("area di servizio", "motorway services"),
    ("área de servicio", "motorway services"),
    ("area de servicio", "motorway services"),
    # Places whose whole business is feeding people.
    ("mcdonald", "restaurant"),
    ("burger king", "restaurant"),
    ("burgerking", "restaurant"),
    ("kfc", "restaurant"),
    ("subway", "restaurant"),
    ("nordsee", "restaurant"),
    ("pizzeria", "restaurant"),
    ("starbucks", "café"),
    ("wegrestaurant", "restaurant"),
    ("restaurant", "restaurant"),
    ("bistro", "restaurant"),
    ("imbiss", "snack bar"),
    ("café", "café"),
    ("cafe", "café"),
    ("coffee", "café"),
    ("bäckerei", "bakery"),
    ("backerei", "bakery"),
    ("bakery", "bakery"),
    ("van der valk", "hotel"),
    ("hotel", "hotel"),
    ("gasthof", "inn"),
    ("gasthaus", "inn"),
    ("gaststätte", "inn"),
    ("gaststatte", "inn"),
    # Shops you can buy lunch in.
    ("supermarkt", "supermarket"),
    ("supermarket", "supermarket"),
    ("albert heijn", "supermarket"),
    ("jumbo", "supermarket"),
    ("carrefour", "supermarket"),
    ("intermarché", "supermarket"),
    ("intermarche", "supermarket"),
    ("leclerc", "supermarket"),
    ("auchan", "supermarket"),
    ("mercadona", "supermarket"),
    ("colruyt", "supermarket"),
    ("delhaize", "supermarket"),
    ("migros", "supermarket"),
    ("kaufland", "supermarket"),
    ("marktkauf", "supermarket"),
    ("famila", "supermarket"),
    ("edeka", "supermarket"),
    ("rewe", "supermarket"),
    ("lidl", "supermarket"),
    ("aldi", "supermarket"),
    ("tesco", "supermarket"),
    ("sainsbury", "supermarket"),
    ("morrisons", "supermarket"),
    ("waitrose", "supermarket"),
    ("biedronka", "supermarket"),
    ("ikea", "store with a restaurant"),
    ("centre commercial", "shopping centre"),
    ("einkaufszentrum", "shopping centre"),
    ("shopping", "shopping centre"),
    ("outlet", "shopping centre"),
    # Filling stations: a shop, and usually something hot.
    ("tankstelle", "filling station"),
    ("filling station", "filling station"),
    ("petrol station", "filling station"),
    ("station service", "filling station"),
    ("station-service", "filling station"),
)

# Same idea, WHOLE WORDS only. These are short enough to hide inside ordinary
# German and French place names, and a substring match on them is not a weak
# guess but a wrong one: "Spar" is in "Sparkasse" (a bank), "Norma" in
# "Normandie", "Hofer" in "Strohofer", "Mall" in "Mallorca", "Moto" in
# "Motorway". Each of those would put a food chip on a charger that has none,
# which is the one failure mode this module is built to avoid.
_FOOD_WORDS: tuple[tuple[str, str], ...] = (
    ("moto", "motorway services"),
    ("spar", "supermarket"),
    ("coop", "supermarket"),
    ("billa", "supermarket"),
    ("hofer", "supermarket"),
    ("penny", "supermarket"),
    ("netto", "supermarket"),
    ("norma", "supermarket"),
    ("asda", "supermarket"),
    ("mall", "shopping centre"),
)

_WORD_RE: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)"), label)
    for needle, label in _FOOD_WORDS
)


def food_hint(name: str | None) -> str | None:
    """What the site's NAME suggests you could eat there, or None.

    None means the name says nothing — not that there is nothing. See the
    module note; a caller that renders this as "no food here" is wrong.
    """
    if not name:
        return None
    haystack = name.casefold()
    # Substrings first: they are the specific ones, and German compounds mean a
    # word boundary would miss "Autobahnraststätte" entirely.
    for needle, label in _FOOD_MARKERS:
        if needle in haystack:
            return label
    for pattern, label in _WORD_RE:
        if pattern.search(haystack):
            return label
    return None
