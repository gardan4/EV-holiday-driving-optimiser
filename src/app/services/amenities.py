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

import logging
import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Charger
from app.services import overpass, quota

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# What is actually there
# ---------------------------------------------------------------------------
#
# Everything above reads a string. This reads the ground, via
# `services/overpass.py`, and is the answer the name could never give: on a
# motorway corridor every fast charger is a network and a place, so the reading
# came back empty for an entire list while one of them was an Autohof with a
# kitchen.
#
# Cache-first, exactly like the chargers themselves. The difference is the TTL:
# a charger's power and status are volatile and a services having a bakery is a
# fact about a building, so this is months rather than hours.


async def nearby(
    db: AsyncSession, sites: Sequence[tuple[str, float, float]]
) -> dict[str, list[str]]:
    """`{charger_id: [category, ...]}` for the sites we have an answer for.

    A MISSING key means "we do not know" and an EMPTY list means "looked,
    nothing mapped". Callers must keep those apart: OSM coverage is uneven, so
    nothing mapped is not nothing there, and a chip that said otherwise would
    be the same absence claim `food_hint` refuses to make.

    Never raises, and never leaves a caller waiting on a provider that is
    unwell — see the breaker in `overpass`. On any failure this returns what
    the cache already knew, which is the behaviour the app had before OSM was
    consulted at all.
    """
    if not sites:
        return {}
    fresh_after = datetime.utcnow() - timedelta(days=settings.AMENITY_CACHE_DAYS)
    # A `ChargerNode.charger_id` is the cache row's own UUID, as a string.
    # Anything that is not one belongs to a synthetic route (the tests, the
    # demo seed) and simply has no row to read or write.
    wanted: dict[uuid.UUID, str] = {}
    for cid, _lat, _lon in sites:
        try:
            wanted[uuid.UUID(cid)] = cid
        except (ValueError, AttributeError, TypeError):
            continue
    by_id: dict[str, Charger] = {}
    if wanted:
        rows = await db.execute(select(Charger).where(Charger.id.in_(wanted)))
        by_id = {str(row.id): row for row in rows.scalars()}

    out: dict[str, list[str]] = {}
    stale: list[overpass.Point] = []
    for cid, lat, lon in sites:
        row = by_id.get(cid)
        if row is None:
            # No cache row to write the answer to. Looking anyway would mean
            # asking Overpass the same question on every request for ever,
            # which is precisely the behaviour its fair-use policy is about.
            # In practice this is a synthetic route — the tests, the demo seed.
            continue
        if (
            row.amenities is not None
            and row.amenities_at is not None
            and row.amenities_at >= fresh_after
        ):
            out[cid] = list(row.amenities)
        elif len(stale) < settings.AMENITY_MAX_PER_REQUEST:
            stale.append(overpass.Point(key=cid, lat=lat, lon=lon))

    if not stale:
        return out

    started = datetime.utcnow()
    found, calls = await overpass.fetch(stale)
    if calls:
        await quota.record(
            db,
            provider="overpass",
            kind="amenities",
            calls=calls,
            cache_hits=len(out),
            duration_ms=int((datetime.utcnow() - started).total_seconds() * 1000),
            ok=found is not None,
        )
    if found is None:
        return out

    now = datetime.utcnow()
    for point in stale:
        labels = found.get(point.key, [])
        out[point.key] = labels
        row = by_id.get(point.key)
        if row is not None:
            # Plain columns, but assigned rather than mutated in place for the
            # same reason the JSON state columns are: SQLAlchemy does not track
            # edits inside a JSON value.
            row.amenities = list(labels)
            row.amenities_at = now
    try:
        await db.commit()
    except Exception:  # noqa: BLE001 — a cache write is worth less than the answer
        logger.warning("could not cache amenity lookup", exc_info=False)
        await db.rollback()
    return out


def describe(labels: Sequence[str]) -> str | None:
    """The chip's text: at most two categories, in the order they were ranked.

    Two, because this is read at a glance in a moving car and "restaurant,
    supermarket" answers the question while "restaurant, fast food, café,
    supermarket, convenience shop, toilets" is a database dump.
    """
    picked = [str(x) for x in labels][:2]
    if not picked:
        return None
    return " · ".join(picked)
