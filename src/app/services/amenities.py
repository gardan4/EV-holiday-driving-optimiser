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
"""

from __future__ import annotations

# (needle, what to call it). Ordered: the first match wins, so the more
# specific entries come first. Lowercase; matching is case-insensitive and
# accent-naive, hence the unaccented spellings alongside the real ones.
_FOOD_MARKERS: tuple[tuple[str, str], ...] = (
    # Motorway service areas — the strongest signal there is.
    ("raststätte", "motorway services"),
    ("raststatte", "motorway services"),
    ("rasthof", "motorway services"),
    ("rastplatz", "rest area"),
    ("tank & rast", "motorway services"),
    ("tank und rast", "motorway services"),
    ("autohof", "truck stop"),
    ("autogrill", "motorway services"),
    ("aire de", "aire"),
    ("service area", "motorway services"),
    ("servicearea", "motorway services"),
    ("services", "motorway services"),
    ("verzorgingsplaats", "motorway services"),
    ("area di servizio", "motorway services"),
    # Places whose whole business is feeding people.
    ("mcdonald", "restaurant"),
    ("burger king", "restaurant"),
    ("kfc", "restaurant"),
    ("subway", "restaurant"),
    ("starbucks", "café"),
    ("restaurant", "restaurant"),
    ("bistro", "restaurant"),
    ("café", "café"),
    ("cafe", "café"),
    ("coffee", "café"),
    ("hotel", "hotel"),
    ("gasthof", "inn"),
    ("gasthaus", "inn"),
    # Shops you can buy lunch in.
    ("supermarkt", "supermarket"),
    ("supermarket", "supermarket"),
    ("albert heijn", "supermarket"),
    ("jumbo", "supermarket"),
    ("carrefour", "supermarket"),
    ("kaufland", "supermarket"),
    ("edeka", "supermarket"),
    ("rewe", "supermarket"),
    ("lidl", "supermarket"),
    ("aldi", "supermarket"),
    ("tesco", "supermarket"),
    ("ikea", "store with a restaurant"),
    ("centre commercial", "shopping centre"),
    ("shopping", "shopping centre"),
    ("outlet", "shopping centre"),
    # Filling stations: a shop, and usually something hot.
    ("tankstelle", "filling station"),
    ("filling station", "filling station"),
    ("petrol station", "filling station"),
)


def food_hint(name: str | None) -> str | None:
    """What the site's NAME suggests you could eat there, or None.

    None means the name says nothing — not that there is nothing. See the
    module note; a caller that renders this as "no food here" is wrong.
    """
    if not name:
        return None
    haystack = name.casefold()
    for needle, label in _FOOD_MARKERS:
        if needle in haystack:
            return label
    return None
