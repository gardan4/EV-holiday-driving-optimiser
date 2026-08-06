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
from app.models import RouteCache
from app.services.geo import COVERAGE_BOX, RouteGeometry, country_at, geohash_encode
from app.services.simulator import RouteSegment

logger = logging.getLogger(__name__)

ORS_BASE = "https://api.openrouteservice.org"

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=15.0)
    return _client


def _require_key() -> str:
    if not settings.ORS_API_KEY:
        raise HTTPException(
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
_GEOCODE_TTL_S = 900.0
_GEOCODE_MAX = 2000
_geocode_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}


async def geocode(q: str, size: int = 5) -> list[dict]:
    """ORS autocomplete → [{label, lat, lon, country}]. Cached in-process."""
    key = _require_key()
    ck = (q.strip().lower(), size)
    hit = _geocode_cache.get(ck)
    now = time.monotonic()
    if hit is not None and now - hit[0] < _GEOCODE_TTL_S:
        return hit[1]

    try:
        resp = await _http().get(
            f"{ORS_BASE}/geocode/autocomplete",
            # Header, not a query param: an httpx error message embeds the full
            # URL, and `logger.warning` below would then write the live API key
            # into the application log on every upstream failure.
            headers={"Authorization": key},
            params={
                "text": q,
                "size": size,
                "layers": "locality,localadmin,borough,address,venue",
                # Confine the suggestions to the corridor `PlacePoint` accepts.
                # Unbounded, the box happily offered Los Angeles or Tenerife and
                # the plan then died on a coordinate-range error.
                "boundary.rect.min_lat": COVERAGE_BOX[0],
                "boundary.rect.max_lat": COVERAGE_BOX[1],
                "boundary.rect.min_lon": COVERAGE_BOX[2],
                "boundary.rect.max_lon": COVERAGE_BOX[3],
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("ORS geocode failed: %s", exc)
        raise HTTPException(status_code=503, detail="Geocoding is temporarily unavailable.")
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
            f"{ORS_BASE}/v2/directions/driving-car/geojson",
            headers={"Authorization": key},
            json={
                "coordinates": [[o_lon, o_lat], [d_lon, d_lat]],
                "elevation": True,
            },
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=422, detail="No drivable route found between these points.")
        logger.warning("ORS directions failed: %s %s", exc.response.status_code, exc.response.text[:300])
        raise HTTPException(status_code=503, detail="Routing is temporarily unavailable.")
    except httpx.HTTPError as exc:
        logger.warning("ORS directions failed: %s", exc)
        raise HTTPException(status_code=503, detail="Routing is temporarily unavailable.")

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
        return _payload_to_route(row.payload)

    logger.info("route cache MISS %s — fetching from ORS", ck[:12])
    payload = await _fetch_route_payload(o_lat, o_lon, d_lat, d_lon)
    if row is None:
        db.add(RouteCache(cache_key=ck, payload=payload, fetched_at=datetime.utcnow()))
    else:
        row.payload = payload
        row.fetched_at = datetime.utcnow()
    await db.commit()
    return _payload_to_route(payload)
