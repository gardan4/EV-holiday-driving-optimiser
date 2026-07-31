"""OpenChargeMap corridor search, tile-cached.

Strategy: sample the route polyline every ~25 km, map samples to geohash-4
tiles (~39×20 km). Tiles with a fresh `OcmTile` row are served straight from
the local `chargers` table; stale/missing tiles trigger one OCM bbox query
each and an upsert. After the first plan on a corridor, replans cost zero
external calls until the TTL expires.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Charger, OcmTile
from app.services.geo import country_at, geohash_bbox, geohash_encode
from app.services.routing import RouteData
from app.services.simulator import ChargerNode

logger = logging.getLogger(__name__)

OCM_BASE = "https://api.openchargemap.io/v3/poi"

SAMPLE_SPACING_M = 25_000.0
TILE_PRECISION = 4
MIN_POWER_KW = 100.0          # DC fast only — below this a stop rarely wins
CCS_CONNECTION_TYPE = 33      # CCS (Type 2 Combo)
OPERATIONAL_STATUS = 50
MAX_PERP_M = 3_000.0          # ≤ 3 km off-route
MIN_OFFSET_M = 50_000.0       # no stops in the first 50 km (battery still full)
END_MARGIN_M = 10_000.0
DEDUP_RADIUS_M = 500.0
MAX_NODES = 60                # DP stays fast; prefer high power when trimming

_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=20.0)
    return _client


def _parse_poi(poi: dict) -> dict | None:
    """Trim an OCM POI into Charger column values (None → skip)."""
    addr = poi.get("AddressInfo") or {}
    lat, lon = addr.get("Latitude"), addr.get("Longitude")
    if lat is None or lon is None:
        return None
    conns = poi.get("Connections") or []
    powers = [c.get("PowerKW") or 0.0 for c in conns]
    max_kw = max(powers, default=0.0)
    if max_kw < MIN_POWER_KW:
        return None
    status = poi.get("StatusType") or {}
    operational = status.get("IsOperational")
    op = (poi.get("OperatorInfo") or {}).get("Title")
    return {
        "ocm_id": poi["ID"],
        "name": (addr.get("Title") or f"Charger {poi['ID']}")[:200],
        "operator": op[:200] if op else None,
        "lat": float(lat),
        "lon": float(lon),
        "max_power_kw": float(max_kw),
        "n_points": int(poi.get("NumberOfPoints") or len(conns) or 1),
        "country": country_at(float(lat), float(lon)) or None,
        "is_operational": bool(operational) if operational is not None else True,
        "raw": None,  # keep rows small; enable for debugging if needed
    }


async def _fetch_tile(db: AsyncSession, tile_key: str) -> None:
    lat_lo, lon_lo, lat_hi, lon_hi = geohash_bbox(tile_key)
    params = {
        "output": "json",
        "boundingbox": f"({lat_lo},{lon_lo}),({lat_hi},{lon_hi})",
        "connectiontypeid": str(CCS_CONNECTION_TYPE),
        "minpowerkw": str(int(MIN_POWER_KW)),
        "statustypeid": str(OPERATIONAL_STATUS),
        "maxresults": "200",
        "compact": "true",
        "verbose": "false",
    }
    headers = {"X-API-Key": settings.OCM_API_KEY} if settings.OCM_API_KEY else {}
    try:
        resp = await _http().get(OCM_BASE, params=params, headers=headers)
        resp.raise_for_status()
        pois = resp.json()
    except httpx.HTTPError as exc:
        # Degrade gracefully: stale/absent tile data just means fewer candidate
        # chargers this run; the tile stays unstamped and is retried next plan.
        logger.warning("OCM fetch failed for tile %s: %s", tile_key, exc)
        return

    n_new = 0
    for poi in pois:
        data = _parse_poi(poi)
        if data is None:
            continue
        row = (
            await db.execute(select(Charger).where(Charger.ocm_id == data["ocm_id"]))
        ).scalar_one_or_none()
        if row is None:
            db.add(Charger(**data, fetched_at=datetime.utcnow()))
            n_new += 1
        else:
            for k, v in data.items():
                setattr(row, k, v)
            row.fetched_at = datetime.utcnow()

    tile = await db.get(OcmTile, tile_key)
    if tile is None:
        db.add(OcmTile(tile_key=tile_key, fetched_at=datetime.utcnow()))
    else:
        tile.fetched_at = datetime.utcnow()
    await db.commit()
    logger.info("OCM tile %s: %d POIs (%d new)", tile_key, len(pois), n_new)


async def chargers_for_route(db: AsyncSession, route: RouteData) -> list[ChargerNode]:
    geom = route.geometry

    # 1. Corridor tiles.
    tiles: list[str] = []
    seen: set[str] = set()
    for lat, lon in geom.sample_every(SAMPLE_SPACING_M):
        key = geohash_encode(lat, lon, TILE_PRECISION)
        if key not in seen:
            seen.add(key)
            tiles.append(key)

    # 2. Refresh stale tiles from OCM.
    cutoff = datetime.utcnow() - timedelta(hours=settings.CHARGER_CACHE_TTL_HOURS)
    fresh = {
        t.tile_key
        for t in (
            await db.execute(select(OcmTile).where(OcmTile.tile_key.in_(tiles)))
        ).scalars()
        if t.fetched_at > cutoff
    }
    stale = [t for t in tiles if t not in fresh]
    logger.info("corridor: %d tiles (%d fresh, %d to fetch)", len(tiles), len(fresh), len(stale))
    for tile_key in stale:
        await _fetch_tile(db, tile_key)

    # 3. Pull cached chargers in the corridor bbox (coarse), then project.
    lats = [la for la, _ in geom.coords]
    lons = [lo for _, lo in geom.coords]
    pad = 0.1  # ~11 km — covers MAX_PERP_M with margin
    rows = (
        (
            await db.execute(
                select(Charger).where(
                    Charger.lat >= min(lats) - pad,
                    Charger.lat <= max(lats) + pad,
                    Charger.lon >= min(lons) - pad,
                    Charger.lon <= max(lons) + pad,
                    Charger.is_operational == True,  # noqa: E712 — MSSQL
                    Charger.max_power_kw >= MIN_POWER_KW,
                )
            )
        )
        .scalars()
        .all()
    )

    candidates: list[ChargerNode] = []
    for c in rows:
        offset_m, perp_m = geom.project(c.lat, c.lon)
        if perp_m > MAX_PERP_M:
            continue
        if offset_m < MIN_OFFSET_M or offset_m > geom.total_m - END_MARGIN_M:
            continue
        candidates.append(
            ChargerNode(
                charger_id=str(c.id),
                name=c.name,
                operator=c.operator,
                lat=c.lat,
                lon=c.lon,
                offset_m=offset_m,
                power_kw=c.max_power_kw,
                # Round trip off/on the motorway at ~40 kph average.
                detour_min=2.0 * (perp_m / 1000.0) / 40.0 * 60.0,
            )
        )

    # 4. Dedup near-identical locations (keep the most powerful site).
    candidates.sort(key=lambda n: (n.offset_m, -n.power_kw))
    deduped: list[ChargerNode] = []
    for n in candidates:
        if deduped and abs(n.offset_m - deduped[-1].offset_m) < DEDUP_RADIUS_M:
            if n.power_kw > deduped[-1].power_kw:
                deduped[-1] = n
            continue
        deduped.append(n)

    # 5. Cap the node count, preferring high power, keep route order.
    if len(deduped) > MAX_NODES:
        logger.info("trimming charger nodes %d → %d (keeping highest power)", len(deduped), MAX_NODES)
        deduped = sorted(deduped, key=lambda n: -n.power_kw)[:MAX_NODES]
        deduped.sort(key=lambda n: n.offset_m)

    logger.info("corridor chargers: %d candidates → %d nodes", len(candidates), len(deduped))
    return deduped
