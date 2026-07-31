/** Pure geometry helpers for the 3D journey scene.
 *
 * Coordinate story: route lat/lon → equirectangular plane → rotated so the
 * journey flows left (origin) to right (destination) → scaled into a scene
 * box. The road curve is arc-length parameterized, so a real route offset
 * fraction maps straight to `curve.getPointAt(fraction)` — stop positions and
 * playback stay distance-truthful.
 */

import * as THREE from "three"

export const SCENE_HALF_WIDTH = 60 // scene units; the route fits in [-60, 60] on X

// ---------------------------------------------------------------------------
// Route → scene curve
// ---------------------------------------------------------------------------

/** Douglas-Peucker simplification on projected 2D points. */
function simplify(points: [number, number][], epsilon: number): [number, number][] {
  if (points.length < 3) return points
  const keep = new Array<boolean>(points.length).fill(false)
  keep[0] = keep[points.length - 1] = true
  const stack: [number, number][] = [[0, points.length - 1]]
  while (stack.length) {
    const [a, b] = stack.pop()!
    const [ax, ay] = points[a]
    const [bx, by] = points[b]
    const dx = bx - ax
    const dy = by - ay
    const len2 = dx * dx + dy * dy
    let maxD = -1
    let maxI = -1
    for (let i = a + 1; i < b; i++) {
      const [px, py] = points[i]
      let d: number
      if (len2 === 0) d = Math.hypot(px - ax, py - ay)
      else {
        const t = Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2))
        d = Math.hypot(px - (ax + t * dx), py - (ay + t * dy))
      }
      if (d > maxD) {
        maxD = d
        maxI = i
      }
    }
    if (maxD > epsilon) {
      keep[maxI] = true
      stack.push([a, maxI], [maxI, b])
    }
  }
  return points.filter((_, i) => keep[i])
}

export interface SceneRoute {
  curve: THREE.CatmullRomCurve3
  /** Ground footprint of the route in scene units. */
  bounds: { minX: number; maxX: number; minZ: number; maxZ: number }
  /** Progress (0..1, origin→destination) for an arbitrary scene position. */
  progressAt: (x: number, z: number) => number
  /** Distance from (x,z) to the nearest sampled road point. */
  roadDistance: (x: number, z: number) => number
}

export function buildSceneRoute(latlon: [number, number][]): SceneRoute {
  // Equirectangular projection.
  const midLat = latlon.reduce((s, p) => s + p[0], 0) / latlon.length
  const cos = Math.cos((midLat * Math.PI) / 180)
  let pts: [number, number][] = latlon.map(([la, lo]) => [lo * cos, la])

  // Rotate so origin→destination points along +X.
  const [sx, sy] = pts[0]
  const [ex, ey] = pts[pts.length - 1]
  const angle = -Math.atan2(ey - sy, ex - sx)
  const ca = Math.cos(angle)
  const sa = Math.sin(angle)
  pts = pts.map(([x, y]) => [
    (x - sx) * ca - (y - sy) * sa,
    (x - sx) * sa + (y - sy) * ca,
  ])

  // Simplify to ~40-70 control points (epsilon relative to route extent).
  const xs = pts.map((p) => p[0])
  const ys = pts.map((p) => p[1])
  const extent = Math.max(
    Math.max(...xs) - Math.min(...xs),
    Math.max(...ys) - Math.min(...ys)
  )
  let simplified = simplify(pts, extent / 400)
  if (simplified.length > 80) simplified = simplify(pts, extent / 150)

  // Scale into the scene box (keep aspect, X spans the full width).
  const minX = Math.min(...simplified.map((p) => p[0]))
  const maxX = Math.max(...simplified.map((p) => p[0]))
  const scale = (2 * SCENE_HALF_WIDTH) / (maxX - minX || 1)
  const midZ =
    (Math.min(...simplified.map((p) => p[1])) + Math.max(...simplified.map((p) => p[1]))) / 2
  const scenePts = simplified.map(
    ([x, y]) =>
      new THREE.Vector3(
        (x - minX) * scale - SCENE_HALF_WIDTH,
        0,
        // Negate: projected +y (north) → scene -Z so the map reads naturally.
        -(y - midZ) * scale
      )
  )

  const curve = new THREE.CatmullRomCurve3(scenePts, false, "centripetal", 0.5)
  curve.arcLengthDivisions = 600

  const samples = curve.getSpacedPoints(240)
  const bounds = {
    minX: Math.min(...samples.map((p) => p.x)),
    maxX: Math.max(...samples.map((p) => p.x)),
    minZ: Math.min(...samples.map((p) => p.z)),
    maxZ: Math.max(...samples.map((p) => p.z)),
  }

  function nearestSampleIndex(x: number, z: number): number {
    let best = 0
    let bestD = Infinity
    for (let i = 0; i < samples.length; i++) {
      const dx = samples[i].x - x
      const dz = samples[i].z - z
      const d = dx * dx + dz * dz
      if (d < bestD) {
        bestD = d
        best = i
      }
    }
    return best
  }

  return {
    curve,
    bounds,
    progressAt: (x, z) => nearestSampleIndex(x, z) / (samples.length - 1),
    roadDistance: (x, z) => {
      const i = nearestSampleIndex(x, z)
      return Math.hypot(samples[i].x - x, samples[i].z - z)
    },
  }
}

/** Ribbon road geometry along the curve, slightly above the ground. */
export function buildRoadGeometry(
  curve: THREE.CatmullRomCurve3,
  width = 1.6,
  y = 0.06,
  segments = 400
): THREE.BufferGeometry {
  const positions: number[] = []
  const indices: number[] = []
  for (let i = 0; i <= segments; i++) {
    const u = i / segments
    const p = curve.getPointAt(u)
    const t = curve.getTangentAt(u)
    // Perpendicular in the ground plane.
    const nx = -t.z
    const nz = t.x
    const n = Math.hypot(nx, nz) || 1
    const hw = width / 2
    positions.push(p.x + (nx / n) * hw, y, p.z + (nz / n) * hw)
    positions.push(p.x - (nx / n) * hw, y, p.z - (nz / n) * hw)
    if (i < segments) {
      const a = i * 2
      indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2)
    }
  }
  const geo = new THREE.BufferGeometry()
  geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3))
  geo.setIndex(indices)
  geo.computeVertexNormals()
  return geo
}

// ---------------------------------------------------------------------------
// Deterministic scatter (no Math.random — stable across renders)
// ---------------------------------------------------------------------------

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0
  return () => {
    a |= 0
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Terrain height: gentle noise that rises toward the destination (progress→1)
 * and flattens near the road. */
export function terrainHeight(
  x: number,
  z: number,
  progress: number,
  roadDist: number
): number {
  const noise =
    Math.sin(x * 0.35 + z * 0.2) * 0.5 +
    Math.sin(x * 0.13 - z * 0.31) * 0.8 +
    Math.sin(x * 0.71 + z * 0.53) * 0.25
  const amp = 0.35 + Math.pow(progress, 1.6) * 2.6
  const roadFlatten = THREE.MathUtils.smoothstep(roadDist, 1.2, 6.0)
  return Math.max(0, (noise + 1.55) * amp * 0.45) * roadFlatten
}
