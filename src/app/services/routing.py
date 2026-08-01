"""OpenRouteService client: geocoding + cache-first directions.

Free-tier budget (~40 directions/min, ~2000/day) is protected by the
`route_cache` table: cache key = sha256 over geohash6-snapped (~±600 m)
origin/destination, TTL `ROUTE_CACHE_TTL_HOURS`. One ORS call per O/D pair —
the whole speed sweep reuses a single fetch.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import RouteCache
from app.services.geo import RouteGeometry, country_at, geohash_encode
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
    total_dist_m: float
    total_dur_s: float


# ---------------------------------------------------------------------------
# Geocoding
# ---------------------------------------------------------------------------


async def geocode(q: str, size: int = 5) -> list[dict]:
    """ORS autocomplete → [{label, lat, lon, country}]."""
    key = _require_key()
    try:
        resp = await _http().get(
            f"{ORS_BASE}/geocode/autocomplete",
            params={
                "api_key": key,
                "text": q,
                "size": size,
                "layers": "locality,localadmin,borough,address,venue",
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
    for step in payload["steps"]:
        dist_m, dur_s = step["d"], step["t"]
        if dist_m <= 0 or dur_s <= 0:
            continue
        i, j = step["wp"]
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
    return RouteData(
        geometry=geometry,
        segments=segments,
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
