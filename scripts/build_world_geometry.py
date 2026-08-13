"""Turn Natural Earth country boundaries into a compact TS module for the map.

Natural Earth is public domain, so the output can simply live in the repo — no
attribution requirement, no runtime fetch (which the dashboard's
`connect-src 'self'` would block anyway).

Whole world, because the planner is worldwide: ORS will route between any two
points it knows roads for, and `trip_stats` has no idea Europe is special. A
Europe-only map silently dropped every corridor outside it.

Usage (run once; the output is committed):

    python3 scripts/build_world_geometry.py ne_50m.geojson dashboard/src/lib/world.ts 0.18

Download the source from
https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson
"""

import json
import sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "ne50m.json"
OUT = sys.argv[2] if len(sys.argv) > 2 else "world.ts"
TOLERANCE = float(sys.argv[3]) if len(sys.argv) > 3 else 0.18  # degrees
PRECISION = 2

# Antarctica is a quarter of the world's coastline, has no roads, and under any
# world projection eats the bottom third of the frame. Greenland stays — people
# recognise it, and it costs little.
SKIP = {"ATA"}

# Rings smaller than this (square degrees, after simplification) are dropped —
# but only SECONDARY rings. Every country keeps its largest ring however small
# it is, because a blanket threshold silently deletes small countries: at 0.35
# it removed Luxembourg, which the simulator models, so the map would have had
# a hole exactly where it claims coverage. The assert below is what caught it.
MIN_AREA = 0.35

# Countries the simulator has real motorway caps for
# (`simulator._default_country_caps`). Only checked here so a bad filter shows
# up immediately — the dashboard gets the authoritative list from the API.
MODELLED = {"DE", "NL", "BE", "AT", "CH", "IT", "FR", "LU"}


def dp(points, tol):
    """Douglas-Peucker. Iterative, because a recursive version blows the stack
    on the long Russian and Canadian coastlines."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        ax, ay = points[lo]
        bx, by = points[hi]
        dx, dy = bx - ax, by - ay
        norm = (dx * dx + dy * dy) ** 0.5
        worst, idx = 0.0, -1
        for i in range(lo + 1, hi):
            px, py = points[i]
            if norm == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                d = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if d > worst:
                worst, idx = d, i
        if worst > tol and idx > 0:
            keep[idx] = True
            stack.append((lo, idx))
            stack.append((idx, hi))
    return [p for p, k in zip(points, keep) if k]


def rings(geom):
    if geom["type"] == "Polygon":
        return [geom["coordinates"][0]]
    if geom["type"] == "MultiPolygon":
        return [poly[0] for poly in geom["coordinates"]]
    return []


def area(ring):
    """Shoelace, for dropping specks that survive simplification."""
    s = 0.0
    for i in range(len(ring)):
        x0, y0 = ring[i]
        x1, y1 = ring[(i + 1) % len(ring)]
        s += x0 * y1 - x1 * y0
    return abs(s) / 2


def main() -> None:
    data = json.load(open(SRC))
    out = []
    seen_iso = set()

    for f in data["features"]:
        p = f["properties"]
        iso3 = p.get("ADM0_A3") or p.get("ISO_A3") or ""
        if iso3 in SKIP:
            continue
        iso2 = (p.get("ISO_A2_EH") or p.get("ISO_A2") or "").strip()
        if iso2 in ("-99", ""):
            iso2 = {"FRA": "FR", "NOR": "NO", "GBR": "GB", "KOS": "XK"}.get(iso3, "")

        simplified = []
        for ring in rings(f["geometry"]):
            simple = dp([(float(x), float(y)) for x, y in ring], TOLERANCE)
            if len(simple) >= 4:
                simplified.append(simple)
        if not simplified:
            continue

        # Largest first, then keep it unconditionally and filter the rest.
        simplified.sort(key=area, reverse=True)
        parts = [
            [[round(x, PRECISION), round(y, PRECISION)] for x, y in ring]
            for i, ring in enumerate(simplified)
            if i == 0 or area(ring) >= MIN_AREA
        ]
        seen_iso.add(iso2)
        out.append({"iso": iso2, "name": p.get("NAME", ""), "rings": parts})

    out.sort(key=lambda c: c["iso"] or "ZZ")
    missing = MODELLED - seen_iso
    assert not missing, f"modelled countries missing from the geometry: {missing}"

    body = ",\n".join(
        "  { iso: %s, name: %s, rings: %s }"
        % (
            json.dumps(c["iso"]),
            json.dumps(c["name"]),
            json.dumps(c["rings"], separators=(",", ":")),
        )
        for c in out
    )

    header = f'''/** The world, as country outlines. Generated — do not hand-edit.
 *
 * Source: Natural Earth admin-0 countries (naturalearthdata.com), which is
 * **public domain** — so it can simply live in the repo. That matters here:
 * the dashboard's CSP is `connect-src 'self'`, so a map that fetched its
 * geometry at runtime would be blocked, and a tile server would break the
 * app's "no external origins" rule outright.
 *
 * The whole world, not just Europe, because the planner is worldwide — ORS
 * routes between any two points it has roads for, and `trip_stats` has no idea
 * Europe is special. Antarctica is dropped (no roads, and it eats a third of
 * any world projection).
 *
 * Douglas-Peucker simplified at {TOLERANCE}° and rounded to {PRECISION} decimals.
 * Coarse on purpose: this is a backdrop for corridor arcs whose endpoints are
 * geohash-4 centres (~20 x 25 km), so the outline is never the limiting factor
 * in what the map can show.
 *
 * Regenerate with scripts/build_world_geometry.py.
 */

export type Country = {{ iso: string; name: string; rings: [number, number][][] }}

/** [lon, lat] — GeoJSON order, NOT the [lat, lon] the rest of this app uses. */
export const WORLD: Country[] = [
'''
    open(OUT, "w").write(header + body + "\n]\n")

    n_rings = sum(len(c["rings"]) for c in out)
    n_pts = sum(len(r) for c in out for r in c["rings"])
    size = len(open(OUT).read())
    print(f"{len(out)} countries, {n_rings} rings, {n_pts} points, {size / 1024:.1f} kB")


main()
