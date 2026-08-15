"""What is actually around a charger, from OpenStreetMap.

`amenities.food_hint` reads the site's NAME, because that is the only text
OpenChargeMap gives us. On a German motorway that answers nothing: the fast
chargers are called "Tesla Supercharger Geiselwind", "IONITY Holzkirchen Süd",
"EweGo" — network plus place, for an entire list — and Geiselwind is an Autohof
with a kitchen. The question a driver is asking is not what the charger is
called, it is what is *there*, and that is a question about the ground.

OpenStreetMap has the ground. This asks Overpass what is mapped within a few
hundred metres of a point and reduces the answer to a handful of allowlisted
category words. Five properties keep it from becoming a liability:

* **Categories, never names or free text.** "restaurant", "supermarket",
  "toilets" — a fixed vocabulary, so a row in our cache cannot become a copy of
  somebody's POI database and a rendered chip cannot become an advertisement.
* **NULL and `[]` are different answers.** Never looked, versus looked and
  found nothing mapped. OSM coverage is uneven and rural Germany is not rural
  Sweden, so "nothing mapped" is emphatically not "nothing there" — the caller
  must be able to say which it has.
* **It never raises and never blocks for long.** This decorates a plan; it is
  not part of one. A timeout, a 429, a malformed body — all of them return
  nothing and the app behaves exactly as it did before this existed.
* **A breaker, because the failure mode is slow rather than loud.** Overpass is
  a shared public instance under a fair-use policy. If it is down or throttling
  us, retrying on every request adds the full timeout to every panel the driver
  opens. After `_BREAKER_TRIPS` consecutive failures the calls stop for
  `_BREAKER_MIN` minutes and the app quietly goes back to reading names.
* **One request for many points.** The panel asks about six or eight chargers
  at once; that is one Overpass query with several `around` clauses, attributed
  back to the points by distance. The fair-use policy is about requests, so the
  batching is the politeness.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings
from app.services.geo import haversine_m

logger = logging.getLogger(__name__)

# How far from the plug counts as "here". Far enough to cross a services car
# park or reach the shop on the far side of a filling station, short enough
# that it cannot mean the next village. A driver plugging in will walk it.
RADIUS_M = 350.0

# OSM tag → the word we store and show. The value side is deliberately small
# and plain: these words are rendered to a driver, and every one of them has to
# mean something at a glance to somebody who has never read OSM's tagging
# guide. Anything not in here is dropped, which is what stops the cache filling
# with categories nobody asked for.
_AMENITY_TAGS: dict[str, str] = {
    "restaurant": "restaurant",
    "fast_food": "fast food",
    "cafe": "café",
    "pub": "pub",
    "bar": "bar",
    "biergarten": "beer garden",
    "food_court": "food court",
    "ice_cream": "ice cream",
    "fuel": "filling station",
    "toilets": "toilets",
}
_SHOP_TAGS: dict[str, str] = {
    "supermarket": "supermarket",
    "convenience": "convenience shop",
    "bakery": "bakery",
    "deli": "deli",
    "greengrocer": "greengrocer",
}
# A motorway services is tagged as a highway feature rather than an amenity,
# and it is the single most useful answer on a long drive — it implies the
# rest of the list even when the individual outlets are unmapped.
_HIGHWAY_TAGS: dict[str, str] = {
    "services": "motorway services",
    "rest_area": "rest area",
}

# The order things are shown in: what you can eat first, then what you can buy,
# then the facilities. Sorting by this rather than by whatever order Overpass
# returned keeps two chargers with the same amenities reading identically.
_RANK = {
    label: i
    for i, label in enumerate(
        [
            "motorway services",
            "restaurant",
            "fast food",
            "café",
            "bakery",
            "food court",
            "pub",
            "beer garden",
            "bar",
            "ice cream",
            "supermarket",
            "convenience shop",
            "deli",
            "greengrocer",
            "filling station",
            "rest area",
            "toilets",
        ]
    )
}

_BREAKER_TRIPS = 3
_BREAKER_MIN = 20.0
_fails = 0
_open_until = 0.0


@dataclass(frozen=True)
class Point:
    """One place to ask about, and the key the answer comes back under."""

    key: str
    lat: float
    lon: float


def _breaker_open() -> bool:
    return _fails >= _BREAKER_TRIPS and time.monotonic() < _open_until


def _trip_breaker() -> None:
    global _fails, _open_until
    _fails += 1
    if _fails >= _BREAKER_TRIPS:
        _open_until = time.monotonic() + _BREAKER_MIN * 60.0
        logger.warning(
            "Overpass breaker open for %.0f min after %d failures",
            _BREAKER_MIN,
            _fails,
        )


def _reset_breaker() -> None:
    global _fails, _open_until
    _fails = 0
    _open_until = 0.0


def build_query(points: list[Point]) -> str:
    """One query, every point, results carrying their own position.

    Attribution is done here rather than by Overpass set gymnastics: each
    element comes back with a position (`out center` gives ways and relations
    one too), so assigning it to every query point within the radius is a
    distance test we already have the code for. A site inside two points'
    radii belongs to both, which is the truth.
    """
    amenity_re = "|".join(sorted(_AMENITY_TAGS))
    shop_re = "|".join(sorted(_SHOP_TAGS))
    highway_re = "|".join(sorted(_HIGHWAY_TAGS))
    clauses = []
    for p in points:
        around = f"around:{RADIUS_M:.0f},{p.lat:.5f},{p.lon:.5f}"
        clauses.append(f'nwr({around})["amenity"~"^({amenity_re})$"];')
        clauses.append(f'nwr({around})["shop"~"^({shop_re})$"];')
        clauses.append(f'nwr({around})["highway"~"^({highway_re})$"];')
    body = "".join(clauses)
    # `out center;` and not `out center tags;`: `out` defaults to `body`, which
    # already carries the tags, and this is the canonical spelling. The server
    # timeout sits just under the client's, so a query that runs long is ended
    # by Overpass with an error we can read rather than by us hanging up on a
    # request it is still working on.
    server_timeout = max(3, int(settings.OVERPASS_TIMEOUT_S) - 1)
    return f"[out:json][timeout:{server_timeout}];({body});out center;"


def _label(tags: dict) -> str | None:
    for key, table in (
        ("highway", _HIGHWAY_TAGS),
        ("amenity", _AMENITY_TAGS),
        ("shop", _SHOP_TAGS),
    ):
        label = table.get(str(tags.get(key) or ""))
        if label:
            return label
    return None


def _position(el: dict) -> tuple[float, float] | None:
    if el.get("lat") is not None and el.get("lon") is not None:
        return float(el["lat"]), float(el["lon"])
    center = el.get("center") or {}
    if center.get("lat") is not None and center.get("lon") is not None:
        return float(center["lat"]), float(center["lon"])
    return None


def parse(points: list[Point], elements: list[dict]) -> dict[str, list[str]]:
    """Elements → one sorted, deduplicated category list per point.

    Every point that was asked about gets a key, including the ones with
    nothing near them: an absent key would be indistinguishable from "we never
    asked", and the whole design rests on those being different.
    """
    out: dict[str, set[str]] = {p.key: set() for p in points}
    for el in elements or []:
        label = _label(el.get("tags") or {})
        if label is None:
            continue
        pos = _position(el)
        if pos is None:
            continue
        for p in points:
            if haversine_m(pos[0], pos[1], p.lat, p.lon) <= RADIUS_M:
                out[p.key].add(label)
    return {
        k: sorted(v, key=lambda label: _RANK.get(label, 99)) for k, v in out.items()
    }


async def fetch(points: list[Point]) -> tuple[dict[str, list[str]] | None, int]:
    """Ask Overpass about these points. `(answer, calls)`; None means no answer.

    None is not "nothing there" — it is "we did not find out", and it must not
    be written to the cache as an empty list. The call count is returned so the
    caller can record it against the quota table with everything else.
    """
    if not points or not settings.OVERPASS_ENABLED:
        return None, 0
    if _breaker_open():
        return None, 0
    try:
        async with httpx.AsyncClient(
            timeout=settings.OVERPASS_TIMEOUT_S,
            headers={"User-Agent": settings.OVERPASS_USER_AGENT},
        ) as client:
            resp = await client.post(
                settings.OVERPASS_URL, data={"data": build_query(points)}
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — a decoration must never propagate
        _trip_breaker()
        logger.warning("Overpass lookup failed for %d points: %s", len(points), exc)
        return None, 1
    _reset_breaker()
    if not isinstance(payload, dict):
        return None, 1
    return parse(points, payload.get("elements") or []), 1
