"""Small geo helpers: haversine, geohash, polyline5 codec, point-to-route
projection, and a coarse country lookup for the EU corridor. Pure Python."""

from __future__ import annotations

import math
from bisect import bisect_right

EARTH_R_M = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R_M * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Geohash (encode + bbox) — enough for tile keys; no external dependency
# ---------------------------------------------------------------------------

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash_encode(lat: float, lon: float, precision: int) -> str:
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    bits = []
    even = True
    while len(bits) < precision * 5:
        if even:
            mid = (lon_lo + lon_hi) / 2
            if lon >= mid:
                bits.append(1)
                lon_lo = mid
            else:
                bits.append(0)
                lon_hi = mid
        else:
            mid = (lat_lo + lat_hi) / 2
            if lat >= mid:
                bits.append(1)
                lat_lo = mid
            else:
                bits.append(0)
                lat_hi = mid
        even = not even
    chars = []
    for i in range(0, len(bits), 5):
        n = 0
        for b in bits[i : i + 5]:
            n = (n << 1) | b
        chars.append(_BASE32[n])
    return "".join(chars)


def geohash_bbox(gh: str) -> tuple[float, float, float, float]:
    """(lat_lo, lon_lo, lat_hi, lon_hi) of a geohash cell."""
    lat_lo, lat_hi = -90.0, 90.0
    lon_lo, lon_hi = -180.0, 180.0
    even = True
    for ch in gh:
        n = _BASE32.index(ch)
        for shift in range(4, -1, -1):
            bit = (n >> shift) & 1
            if even:
                mid = (lon_lo + lon_hi) / 2
                if bit:
                    lon_lo = mid
                else:
                    lon_hi = mid
            else:
                mid = (lat_lo + lat_hi) / 2
                if bit:
                    lat_lo = mid
                else:
                    lat_hi = mid
            even = not even
    return lat_lo, lon_lo, lat_hi, lon_hi


# ---------------------------------------------------------------------------
# Polyline5 (Google encoded polyline, precision 5)
# ---------------------------------------------------------------------------


def polyline_encode(coords: list[tuple[float, float]]) -> str:
    """Encode [(lat, lon), ...] to a polyline5 string."""
    out = []
    prev_lat = prev_lon = 0
    for lat, lon in coords:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for v in (ilat - prev_lat, ilon - prev_lon):
            v = ~(v << 1) if v < 0 else v << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lon = ilat, ilon
    return "".join(out)


def polyline_decode(s: str) -> list[tuple[float, float]]:
    coords = []
    lat = lon = 0
    i = 0
    while i < len(s):
        for is_lon in (False, True):
            shift = result = 0
            while True:
                b = ord(s[i]) - 63
                i += 1
                result |= (b & 0x1F) << shift
                shift += 5
                if b < 0x20:
                    break
            d = ~(result >> 1) if result & 1 else result >> 1
            if is_lon:
                lon += d
            else:
                lat += d
        coords.append((lat / 1e5, lon / 1e5))
    return coords


# ---------------------------------------------------------------------------
# Point → route projection
# ---------------------------------------------------------------------------


class RouteGeometry:
    """Route polyline with cumulative distances; supports projecting a point
    onto the route (offset along + perpendicular distance) and sampling."""

    def __init__(self, coords: list[tuple[float, float]]):
        self.coords = coords
        self.cum_m = [0.0] * len(coords)
        for i in range(1, len(coords)):
            self.cum_m[i] = self.cum_m[i - 1] + haversine_m(*coords[i - 1], *coords[i])
        self.total_m = self.cum_m[-1] if coords else 0.0

    def sample_every(self, spacing_m: float) -> list[tuple[float, float]]:
        """Points along the route every `spacing_m` (plus both endpoints)."""
        out = [self.coords[0]]
        target = spacing_m
        while target < self.total_m:
            out.append(self.point_at(target))
            target += spacing_m
        out.append(self.coords[-1])
        return out

    def point_at(self, offset_m: float) -> tuple[float, float]:
        if offset_m <= 0:
            return self.coords[0]
        if offset_m >= self.total_m:
            return self.coords[-1]
        i = bisect_right(self.cum_m, offset_m) - 1
        seg_len = self.cum_m[i + 1] - self.cum_m[i]
        f = (offset_m - self.cum_m[i]) / seg_len if seg_len > 0 else 0.0
        (la1, lo1), (la2, lo2) = self.coords[i], self.coords[i + 1]
        return la1 + f * (la2 - la1), lo1 + f * (lo2 - lo1)

    def project(self, lat: float, lon: float) -> tuple[float, float]:
        """(offset_m along route, perpendicular distance m) for a point.

        Uses a local equirectangular approximation per segment — accurate to
        well under 1% at charger-search distances (< a few km).
        """
        best_off, best_d2 = 0.0, math.inf
        cos_lat = math.cos(math.radians(lat))
        px, py = lon * cos_lat, lat
        for i in range(len(self.coords) - 1):
            (la1, lo1), (la2, lo2) = self.coords[i], self.coords[i + 1]
            ax, ay = lo1 * cos_lat, la1
            bx, by = lo2 * cos_lat, la2
            dx, dy = bx - ax, by - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
            cx, cy = ax + t * dx, ay + t * dy
            d2 = (px - cx) ** 2 + (py - cy) ** 2
            if d2 < best_d2:
                best_d2 = d2
                best_off = self.cum_m[i] + t * (self.cum_m[i + 1] - self.cum_m[i])
        # Degrees → meters (1° ≈ 111.32 km on the scaled plane).
        perp_m = math.sqrt(best_d2) * 111_320.0
        return best_off, perp_m


# ---------------------------------------------------------------------------
# Coarse country lookup (EU corridor) — good enough for speed-cap decisions
# ---------------------------------------------------------------------------

# (iso2, lat_lo, lat_hi, lon_lo, lon_hi) — checked in order; first hit wins.
# Deliberately coarse but tuned so the big corridors resolve correctly
# (Ruhr/Bavaria stay DE, Tyrol/Salzburg stay AT). Caps only differ by
# ~10-20 kph between neighbours, so residual border fuzz costs little —
# EXCEPT around DE (derestricted), where these splits matter most.
_COUNTRY_BOXES: list[tuple[str, float, float, float, float]] = [
    ("NL", 51.9, 53.7, 3.3, 7.25),    # northern NL
    ("NL", 50.75, 51.9, 3.3, 6.25),   # southern NL incl. Limburg (Venlo 6.17)
    ("BE", 49.5, 51.55, 2.5, 6.4),
    ("LU", 49.4, 50.2, 5.7, 6.6),
    ("CH", 45.8, 47.9, 5.9, 10.5),
    ("AT", 46.3, 47.7, 9.5, 17.2),    # Tyrol/Inn valley (border ~47.6)
    ("AT", 47.7, 48.55, 12.9, 17.2),  # Salzburg/Linz/Vienna
    ("AT", 48.55, 49.05, 14.0, 17.2), # Waldviertel (east of Passau)
    ("DE", 47.2, 55.1, 5.8, 15.1),
    ("IT", 36.5, 47.1, 6.6, 18.6),
    ("FR", 42.3, 51.2, -5.2, 8.3),
]


def country_at(lat: float, lon: float) -> str:
    for iso, la0, la1, lo0, lo1 in _COUNTRY_BOXES:
        if la0 <= lat <= la1 and lo0 <= lon <= lo1:
            return iso
    return ""
