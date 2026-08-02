/**
 * Snapping a GPS fix onto the route, in the browser.
 *
 * The server does this too, authoritatively — `RouteGeometry.project` in
 * `src/app/services/geo.py`. This copy exists so the live view can move the
 * moment a fix arrives instead of waiting for the next 25-second ping: at
 * motorway speed a round trip is a kilometre of lag, and a position that
 * visibly stutters reads as broken even when it is merely late.
 *
 * The server's answer always wins when it comes back.
 */

import { decodePolyline } from "./polyline"

const EARTH_R = 6371000

export interface RouteIndex {
  coords: [number, number][]
  /** Cumulative metres along the polyline at each vertex. */
  cum: number[]
  totalM: number
  /** Total distance in the axis the simulator (and the plan) measures in. */
  planTotalM: number
}

export function haversineM(
  a: [number, number],
  b: [number, number]
): number {
  const la1 = (a[0] * Math.PI) / 180
  const la2 = (b[0] * Math.PI) / 180
  const dLa = la2 - la1
  const dLo = ((b[1] - a[1]) * Math.PI) / 180
  const h =
    Math.sin(dLa / 2) ** 2 +
    Math.cos(la1) * Math.cos(la2) * Math.sin(dLo / 2) ** 2
  return 2 * EARTH_R * Math.asin(Math.sqrt(h))
}

/** Build the index once per trip; `project` then runs on every fix. */
export function buildRouteIndex(polyline: string, planTotalM: number): RouteIndex {
  const coords = decodePolyline(polyline)
  const cum: number[] = [0]
  for (let i = 1; i < coords.length; i++) {
    cum.push(cum[i - 1] + haversineM(coords[i - 1], coords[i]))
  }
  return {
    coords,
    cum,
    totalM: cum[cum.length - 1] ?? 0,
    planTotalM,
  }
}

export interface Projection {
  /** Distance along the route, in the plan's own axis. */
  offsetM: number
  /** How far the fix is from the route. Beyond ~500 m, stop trusting it. */
  offRouteM: number
}

/**
 * Nearest point on the polyline to a fix.
 *
 * Brute force over every vertex. A 1000 km route decodes to a few thousand
 * points, so this is well under a millisecond and runs at most once a second —
 * an index would be more code for time nobody would notice.
 */
export function project(idx: RouteIndex, lat: number, lon: number): Projection {
  const { coords, cum } = idx
  if (coords.length < 2) return { offsetM: 0, offRouteM: 0 }

  // Local flat-earth metres per degree, good enough over a single segment.
  const mPerDegLat = 111320
  const mPerDegLon = 111320 * Math.cos((lat * Math.PI) / 180)

  let bestD2 = Infinity
  let bestGeom = 0
  for (let i = 0; i < coords.length - 1; i++) {
    const ax = (coords[i][1] - lon) * mPerDegLon
    const ay = (coords[i][0] - lat) * mPerDegLat
    const bx = (coords[i + 1][1] - lon) * mPerDegLon
    const by = (coords[i + 1][0] - lat) * mPerDegLat
    const dx = bx - ax
    const dy = by - ay
    const len2 = dx * dx + dy * dy
    let t = len2 > 0 ? -(ax * dx + ay * dy) / len2 : 0
    t = Math.max(0, Math.min(1, t))
    const px = ax + t * dx
    const py = ay + t * dy
    const d2 = px * px + py * py
    if (d2 < bestD2) {
      bestD2 = d2
      bestGeom = cum[i] + t * (cum[i + 1] - cum[i])
    }
  }

  // Geometry metres → plan metres. The two axes differ slightly (ORS reports a
  // distance per step; the polyline's own haversine sum is not quite the same
  // number), and the plan's stop offsets live on the second one.
  const scale = idx.totalM > 0 ? idx.planTotalM / idx.totalM : 1
  return { offsetM: bestGeom * scale, offRouteM: Math.sqrt(bestD2) }
}
