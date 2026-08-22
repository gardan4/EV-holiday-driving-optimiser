"""OpenRouteService client: geocoding + cache-first directions.

Free-tier budget (~40 directions/min, ~2000/day) is protected by the
`route_cache` table: cache key = sha256 over geohash6-snapped (~±600 m)
origin/destination, TTL `ROUTE_CACHE_TTL_HOURS`. One ORS call per O/D pair —
the whole speed sweep reuses a single fetch.
"""

from __future__ import annotations

import hashlib
import logging
import time
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.failures import PlanError
from app.models import RouteCache
from app.services import quota
from app.services.geo import RouteGeometry, country_at, geohash_encode
from app.services.simulator import RouteSegment

logger = logging.getLogger(__name__)

# HeiGIT retired `api.openrouteservice.org` in favour of `api.heigit.org`,
# announced 2026-04-28 with the old host shut off entirely on 2026-08-24. The
# services are unchanged — same key, same request and response shapes, no
# regeneration needed — but they now sit under a per-service prefix, and
# routing and geocoding do NOT share one:
#
#     /v2/directions/…   →  /openrouteservice/v2/directions/…
#     /geocode/…         →  /pelias/v1/…
#
# which is why this is two constants rather than one base and a path.
#
# Worth knowing why this landed as an outage rather than as housekeeping: the
# deprecation notice says quota on the OLD host "might be restricted", and it
# is. Geocoding on `api.openrouteservice.org` began returning 403 "Quota
# exceeded" while the account's real allowance — the one the dashboard shows —
# was untouched and the same key worked immediately against `api.heigit.org`.
# Directions kept working throughout, so the app half-failed: you could not
# search for a place, but a permalink still planned.
#
# No trailing slashes. A base ending in "/" produces a double slash and the
# gateway answers 405 "Method 'GET' is not supported", which reads like an
# endpoint or auth problem and is neither.
ORS_DIRECTIONS_BASE = "https://api.heigit.org/openrouteservice"
ORS_GEOCODE_BASE = "https://api.heigit.org/pelias/v1"

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15.0)
    return _client


def _require_key() -> str:
    if not settings.ORS_API_KEY:
        raise PlanError(
            "not_configured",
            status_code=503,
            detail="Routing is not configured (ORS_API_KEY missing). "
            "Get a free key at openrouteservice.org.",
        )
    return settings.ORS_API_KEY


@dataclass
class RouteData:
    geometry: RouteGeometry            # decoded polyline + cumulative distances
    segments: list[RouteSegment]       # simulator inputs (dist, freeflow, country)
    # Geometry-space offset of each segment's START, plus the total — so
    # len() == len(segments) + 1. There are two distance axes on every route:
    # ORS reports a distance per step, while the polyline's own haversine sum
    # is a slightly different number (typically 0.1-0.5% apart). The simulator
    # lives on the first axis; a GPS fix snapped with `RouteGeometry.project`
    # lands on the second. This array is the map between them, exact at every
    # step boundary. `geom_to_segment_m` does the lookup.
    #
    # Empty means "no map available" and the two axes are then treated as the
    # same, which is what synthetic routes in tests want.
    seg_geom_offsets: list[float] = field(default_factory=list)
    total_dist_m: float = 0.0
    total_dur_s: float = 0.0

    def geom_to_segment_m(self, geom_offset_m: float) -> float:
        """Geometry-space offset (what `project` returns) → segment-space
        offset (what the simulator and the plan's stops are measured in)."""
        return _lerp_axis(
            self.seg_geom_offsets, _cumulative_segment_m(self.segments), geom_offset_m
        )

    def segment_to_geom_m(self, segment_offset_m: float) -> float:
        """The inverse — for putting a planned stop back on the polyline."""
        return _lerp_axis(
            _cumulative_segment_m(self.segments), self.seg_geom_offsets, segment_offset_m
        )


def _cumulative_segment_m(segments: list[RouteSegment]) -> list[float]:
    out = [0.0]
    for s in segments:
        out.append(out[-1] + s.dist_m)
    return out


def _lerp_axis(xs: list[float], ys: list[float], x: float) -> float:
    if not xs or not ys:
        return x
    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]
    i = bisect_right(xs, x) - 1
    x0, x1 = xs[i], xs[i + 1]
    if x1 == x0:
        return ys[i]
    return ys[i] + (x - x0) / (x1 - x0) * (ys[i + 1] - ys[i])


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


# Autocomplete was the one upstream call with no cache in front of it, which
# made it the first thing to exhaust the free ORS quota: directions are cached
# per O/D pair, but every keystroke burst in the search box spent a request.
# Prefixes repeat enormously across users ("ut", "utr", "amst"), so even a short
# TTL collapses most of the volume — and a dead autocomplete is what a visitor
# from a link would hit first, before anything else in the app.
#
# Six hours rather than fifteen minutes. A place name does not move, and the
# fifteen-minute window was tuned for a memory cost that never materialised:
# 4000 prefixes of a handful of results each is a couple of megabytes. The
# window that matters is a traffic spike, which lasts an evening, not a quarter
# of an hour.
#
# Still in-process, so it dies with the container and is not shared between
# instances — every deploy starts cold and re-spends the popular prefixes. A
# table would fix that, and would also mean storing what people typed into a
# search box; that is a deliberate open question rather than an oversight.
_GEOCODE_TTL_S = 6 * 3600.0
_GEOCODE_MAX = 4000
_geocode_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
# Same TTL and cap; a separate map because the keys are coordinates and a
# miss here must not evict a popular typed prefix.
_reverse_cache: dict[tuple[float, float], tuple[float, dict | None]] = {}


async def geocode(
    q: str, size: int = 5, db: AsyncSession | None = None
) -> list[dict]:
    """ORS autocomplete → [{label, lat, lon, country}]. Cached in-process.

    `db` is optional and is only used to record quota. It is threaded through
    from the route rather than omitted because this call was invisible to
    `UpstreamCall` for as long as it existed: every row the quota table held was
    written by `get_route`, so the dashboard reported healthy ORS headroom while
    autocomplete — the one call with no durable cache in front of it — was the
    thing actually being rate-limited. A meter that cannot see the biggest
    spender is not a meter.
    """
    key = _require_key()
    ck = (q.strip().lower(), size)
    hit = _geocode_cache.get(ck)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _GEOCODE_TTL_S:
        if db is not None:
            await quota.record(db, provider="ors", kind="geocode", cache_hits=1)
        return hit[1]

    started = time.monotonic()
    try:
        resp = await _http().get(
            f"{ORS_GEOCODE_BASE}/autocomplete",
            # Header, not a query param: an httpx error message embeds the full
            # URL, and `logger.warning` below would then write the live API key
            # into the application log on every upstream failure.
            headers={"Authorization": key},
            params={
                "text": q,
                "size": size,
                "layers": "locality,localadmin,borough,address,venue",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("ORS geocode failed: %s", exc)
        if db is not None:
            # Still a spent request as far as the provider is concerned, and the
            # failure is the whole signal when a quota runs out — recording only
            # successes is how a dead service looks quiet.
            await quota.record(
                db,
                provider="ors",
                kind="geocode",
                calls=1,
                duration_ms=int((time.monotonic() - started) * 1000),
                ok=False,
            )
        raise HTTPException(status_code=503, detail="Geocoding is temporarily unavailable.")

    if db is not None:
        await quota.record(
            db,
            provider="ors",
            kind="geocode",
            calls=1,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
    hits = []
    for feat in resp.json().get("features", []):
        props = feat.get("properties", {})
        lon, lat = feat["geometry"]["coordinates"]
        hits.append(
            {
                "label": props.get("label", ""),
                "lat": lat,
                "lon": lon,
                "country": (props.get("country_a") or "")[:2] or None,
            }
        )

    if len(_geocode_cache) >= _GEOCODE_MAX:
        # Unbounded growth would be a memory leak keyed on attacker-chosen
        # strings. Drop the expired entries; if that frees nothing, start over.
        for k, (t, _) in list(_geocode_cache.items()):
            if now - t >= _GEOCODE_TTL_S:
                _geocode_cache.pop(k, None)
        if len(_geocode_cache) >= _GEOCODE_MAX:
            _geocode_cache.clear()
    _geocode_cache[ck] = (now, hits)
    return hits


# How finely a reverse lookup is cached and asked. Five decimal places is
# ~1 m, which would make every fix its own cache key and every fix its own ORS
# request; three is ~110 m, which is the same doorstep and the same answer.
# Rounding is applied to the KEY only — the coordinates that reach the planner
# are the ones the browser reported.
_REVERSE_PRECISION = 3


async def reverse_geocode(
    lat: float, lon: float, db: AsyncSession | None = None
) -> dict | None:
    """A coordinate → the place it is in, or None when nothing is near.

    Backs "start from where I am". The browser has the coordinates already, so
    this exists purely to put a NAME on them: a trip whose origin reads
    "52.09, 5.12" is one nobody can check, share or recognise later in their
    own list, and the planner's whole first screen is two place names.

    Metered as `geocode`, not as a service of its own — HeiGIT counts Pelias
    against one allowance whichever endpoint spends it, and a meter split
    differently from the ceiling it is watching is the failure `quota.SERVICES`
    exists to prevent.
    """
    key = _require_key()
    ck = (round(lat, _REVERSE_PRECISION), round(lon, _REVERSE_PRECISION))
    hit = _reverse_cache.get(ck)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _GEOCODE_TTL_S:
        if db is not None:
            await quota.record(db, provider="ors", kind="geocode", cache_hits=1)
        return hit[1]

    started = time.monotonic()
    try:
        resp = await _http().get(
            f"{ORS_GEOCODE_BASE}/reverse",
            # Header rather than a query param, for the same reason as
            # `geocode`: an httpx error embeds the URL and the warning below
            # would write the live key into the log.
            headers={"Authorization": key},
            params={
                "point.lat": lat,
                "point.lon": lon,
                "size": 1,
                "layers": "address,street,locality,localadmin,borough",
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("ORS reverse geocode failed: %s", exc)
        if db is not None:
            await quota.record(
                db,
                provider="ors",
                kind="geocode",
                calls=1,
                duration_ms=int((time.monotonic() - started) * 1000),
                ok=False,
            )
        raise HTTPException(
            status_code=503, detail="Geocoding is temporarily unavailable."
        )

    if db is not None:
        await quota.record(
            db,
            provider="ors",
            kind="geocode",
            calls=1,
            duration_ms=int((time.monotonic() - started) * 1000),
        )

    out: dict | None = None
    for feat in resp.json().get("features", []):
        props = feat.get("properties", {})
        label = props.get("label") or ""
        if not label:
            continue
        out = {
            "label": label,
            # The point that comes BACK, not the one that went in. Pelias
            # answers with the feature it matched — a street, a locality — and
            # its centroid is a place, where the fix is a car park behind one.
            # Routing from the named thing is what makes the itinerary match
            # the label the driver is looking at.
            "lat": feat["geometry"]["coordinates"][1],
            "lon": feat["geometry"]["coordinates"][0],
            "country": (props.get("country_a") or "")[:2] or None,
        }
        break

    if len(_reverse_cache) >= _GEOCODE_MAX:
        for k, (t, _) in list(_reverse_cache.items()):
            if now - t >= _GEOCODE_TTL_S:
                _reverse_cache.pop(k, None)
        if len(_reverse_cache) >= _GEOCODE_MAX:
            _reverse_cache.clear()
    _reverse_cache[ck] = (now, out)
    return out


# ---------------------------------------------------------------------------
# Directions (cache-first)
# ---------------------------------------------------------------------------


# Bump when the shape of the cached payload changes, so old entries are refetched
# rather than silently missing new fields (v2 added elevation).
ROUTE_PAYLOAD_VERSION = 2


def _cache_key(o_lat: float, o_lon: float, d_lat: float, d_lon: float) -> str:
    raw = (
        f"{geohash_encode(o_lat, o_lon, 6)}|{geohash_encode(d_lat, d_lon, 6)}"
        f"|driving-car|v{ROUTE_PAYLOAD_VERSION}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _payload_to_route(payload: dict) -> RouteData:
    coords = [(la, lo) for la, lo in payload["coords"]]
    geometry = RouteGeometry(coords)
    # Per-vertex elevation, when the route was fetched with it (payload v2+).
    elev = payload.get("elev") or []
    segments = []
    seg_geom_offsets = []
    for step in payload["steps"]:
        dist_m, dur_s = step["d"], step["t"]
        if dist_m <= 0 or dur_s <= 0:
            continue
        i, j = step["wp"]
        # Where this segment starts in GEOMETRY space (haversine along the
        # polyline), which is not the same axis as the segment distances ORS
        # reports. See `RouteData.seg_geom_offsets`.
        seg_geom_offsets.append(geometry.cum_m[min(i, len(coords) - 1)])
        mid = coords[min((i + j) // 2, len(coords) - 1)]
        climb = descent = 0.0
        if elev and j < len(elev):
            # Sum only real ups and downs; the net difference would hide a
            # pass that climbs 600 m and gives most of it back.
            for k in range(i, min(j, len(elev) - 1)):
                dh = elev[k + 1] - elev[k]
                if dh > 0:
                    climb += dh
                else:
                    descent += -dh
        segments.append(
            RouteSegment(
                dist_m=dist_m,
                freeflow_kph=(dist_m / 1000.0) / (dur_s / 3600.0),
                country=country_at(*mid),
                climb_m=climb,
                descent_m=descent,
            )
        )
    seg_geom_offsets.append(geometry.total_m)
    return RouteData(
        geometry=geometry,
        segments=segments,
        seg_geom_offsets=seg_geom_offsets,
        total_dist_m=payload["total_m"],
        total_dur_s=payload["total_s"],
    )


async def _fetch_route_payload(
    o_lat: float, o_lon: float, d_lat: float, d_lon: float
) -> dict:
    key = _require_key()
    try:
        resp = await _http().post(
            f"{ORS_DIRECTIONS_BASE}/v2/directions/driving-car/geojson",
            headers={"Authorization": key},
            json={
                "coordinates": [[o_lon, o_lat], [d_lon, d_lat]],
                "elevation": True,
            },
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise PlanError(
                "no_route",
                status_code=422,
                detail="No drivable route found between these points.",
            )
        logger.warning("ORS directions failed: %s %s", exc.response.status_code, exc.response.text[:300])
        raise PlanError(
            "upstream_error", status_code=503, detail="Routing is temporarily unavailable."
        )
    except httpx.HTTPError as exc:
        logger.warning("ORS directions failed: %s", exc)
        raise PlanError(
            "upstream_error", status_code=503, detail="Routing is temporarily unavailable."
        )

    feat = resp.json()["features"][0]
    raw_coords = feat["geometry"]["coordinates"]
    # With elevation on, ORS returns [lon, lat, ele] triples.
    coords = [[c[1], c[0]] for c in raw_coords]
    elev = [c[2] for c in raw_coords] if raw_coords and len(raw_coords[0]) > 2 else []
    props = feat["properties"]
    steps = []
    for seg in props["segments"]:
        for st in seg["steps"]:
            steps.append({"d": st["distance"], "t": st["duration"], "wp": st["way_points"]})
    summary = props["summary"]
    return {
        "coords": coords,
        "elev": elev,
        "steps": steps,
        "total_m": summary["distance"],
        "total_s": summary["duration"],
    }


async def get_route(
    db: AsyncSession, o_lat: float, o_lon: float, d_lat: float, d_lon: float
) -> RouteData:
    ck = _cache_key(o_lat, o_lon, d_lat, d_lon)
    cutoff = datetime.utcnow() - timedelta(hours=settings.ROUTE_CACHE_TTL_HOURS)

    row = (
        await db.execute(select(RouteCache).where(RouteCache.cache_key == ck))
    ).scalar_one_or_none()
    if row is not None and row.fetched_at > cutoff:
        logger.info("route cache HIT %s", ck[:12])
        # A hit is the interesting half: the cache hit rate is what decides
        # whether a traffic spike stays inside the free tier.
        await quota.record(db, provider="ors", kind="directions", cache_hits=1)
        return _payload_to_route(row.payload)

    logger.info("route cache MISS %s — fetching from ORS", ck[:12])
    started = time.perf_counter()
    try:
        payload = await _fetch_route_payload(o_lat, o_lon, d_lat, d_lon)
    except Exception as exc:
        # Counted before re-raising: a burst of failures is exactly what a
        # blown quota looks like from in here, and it must not go unrecorded
        # just because the request is about to fail.
        #
        # `ok` means "the provider failed us", NOT "the answer was negative".
        # ORS answering "there is no road between these two points" is a
        # successful call with a disappointing result — it still spends a
        # request, so `calls=1` either way, but marking it a failure would make
        # the upstream-health signal fire every time somebody picks an island,
        # and an alarm that cries wolf on ordinary user error is one nobody
        # reads on the night it matters.
        await quota.record(
            db, provider="ors", kind="directions", calls=1,
            ok=getattr(exc, "reason", None) == "no_route",
            duration_ms=(time.perf_counter() - started) * 1000,
        )
        await db.commit()
        raise
    await quota.record(
        db, provider="ors", kind="directions", calls=1,
        duration_ms=(time.perf_counter() - started) * 1000,
    )
    if row is None:
        db.add(RouteCache(cache_key=ck, payload=payload, fetched_at=datetime.utcnow()))
    else:
        row.payload = payload
        row.fetched_at = datetime.utcnow()
    await db.commit()
    return _payload_to_route(payload)
