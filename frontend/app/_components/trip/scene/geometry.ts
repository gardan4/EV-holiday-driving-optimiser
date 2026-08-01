/** Geometry for the side-on 2.5D journey world.
 *
 * The route is straightened into a line along +X: distance along the trip maps
 * linearly to world X. Depth comes from parallax — layers sit at different Z
 * and are translated at different rates (an orthographic camera has no
 * perspective divide, so moving the camera would slide every layer at the same
 * speed; the layers have to move themselves).
 *
 * Terrain morphs along the route: flat and green near the origin, rising into
 * snow-capped Alps toward the destination.
 */

import * as THREE from "three"

/** World units spanned by the full route. */
export const WORLD_LEN = 460
/** Half-width of the orthographic frustum (how much road is on screen). */
export const VIEW_HALF_W = 24

/** Parallax rate per depth layer. 1 = moves with the road. */
export const LAYER_FACTOR = {
  farRidge: 0.14,
  midRidge: 0.34,
  treeline: 0.62,
  road: 1,
  foreground: 1.45,
} as const

/** Deterministic RNG — the landscape must be identical across re-renders. */
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

/** How alpine the landscape is at progress p (0 = origin, 1 = destination). */
export function alpineness(p: number): number {
  return Math.pow(Math.max(0, Math.min(1, p)), 1.7)
}

/** A layer's own length: it only has to cover its own (slower) travel. */
export function layerLength(factor: number): number {
  return WORLD_LEN * factor + VIEW_HALF_W * 4
}

// ---------------------------------------------------------------------------
// Ridges
// ---------------------------------------------------------------------------

export interface RidgeOptions {
  seed: number
  factor: number
  baseHeight: number
  amplitude: number
  minStep: number
  maxStep: number
  depth: number
  /** Peaks above this height get a snow cap (in the returned cap geometry). */
  snowFrom?: number
}

export interface RidgeGeometry {
  body: THREE.BufferGeometry
  caps: THREE.BufferGeometry | null
  length: number
}

/**
 * A mountain silhouette extruded in Z, so the orthographic camera's slight
 * downward tilt catches a lit top face — flat 2D art would read as a sticker.
 */
export function buildRidge(opts: RidgeOptions): RidgeGeometry {
  const rand = mulberry32(opts.seed)
  const length = layerLength(opts.factor)

  const peaks: { x: number; y: number }[] = []
  let x = -VIEW_HALF_W * 2
  while (x < length) {
    const p = Math.max(0, x) / (WORLD_LEN * opts.factor)
    const grow = 0.45 + alpineness(p) * 1.5
    peaks.push({ x, y: opts.baseHeight + rand() * opts.amplitude * grow })
    x += opts.minStep + rand() * (opts.maxStep - opts.minStep)
  }

  const shape = new THREE.Shape()
  shape.moveTo(peaks[0].x, 0)
  peaks.forEach((pk, i) => {
    // Midpoint valley between peaks keeps the silhouette from reading as a saw.
    if (i > 0) {
      const prev = peaks[i - 1]
      const mx = (prev.x + pk.x) / 2
      shape.lineTo(mx, Math.min(prev.y, pk.y) * (0.35 + rand() * 0.25))
    }
    shape.lineTo(pk.x, pk.y)
  })
  shape.lineTo(peaks[peaks.length - 1].x, 0)
  shape.closePath()

  const body = new THREE.ExtrudeGeometry(shape, {
    depth: opts.depth,
    bevelEnabled: false,
    curveSegments: 1,
  })
  body.computeVertexNormals()

  let caps: THREE.BufferGeometry | null = null
  const snowFrom = opts.snowFrom
  if (snowFrom !== undefined) {
    const capShapes: THREE.Shape[] = []
    peaks.forEach((pk) => {
      if (pk.y < snowFrom) return
      const w = 1.6 + (pk.y - snowFrom) * 0.42
      const drop = w * 0.8
      const s = new THREE.Shape()
      s.moveTo(pk.x - w, pk.y - drop)
      s.lineTo(pk.x, pk.y)
      s.lineTo(pk.x + w, pk.y - drop)
      s.closePath()
      capShapes.push(s)
    })
    if (capShapes.length) {
      caps = new THREE.ExtrudeGeometry(capShapes, {
        depth: opts.depth * 1.02,
        bevelEnabled: false,
        curveSegments: 1,
      })
      caps.computeVertexNormals()
    }
  }

  return { body, caps, length }
}

// ---------------------------------------------------------------------------
// Scatter (trees, houses)
// ---------------------------------------------------------------------------

export interface Scatter {
  x: number
  y: number
  z: number
  scale: number
  /** 0 = green lowland, 1 = snow-dusted alpine. */
  snow: number
}

export function scatterTrees(opts: {
  seed: number
  factor: number
  count: number
  zFrom: number
  zTo: number
  baseY?: number
  scale?: number
}): Scatter[] {
  const rand = mulberry32(opts.seed)
  const length = layerLength(opts.factor)
  const out: Scatter[] = []
  for (let i = 0; i < opts.count; i++) {
    const x = -VIEW_HALF_W * 2 + rand() * (length + VIEW_HALF_W * 2)
    const p = Math.max(0, x) / (WORLD_LEN * opts.factor)
    const a = alpineness(p)
    // Treeline thins out as the route climbs.
    if (rand() > 1 - a * 0.55) continue
    out.push({
      x,
      y: opts.baseY ?? 0,
      z: opts.zFrom + rand() * (opts.zTo - opts.zFrom),
      scale: (0.7 + rand() * 0.75) * (opts.scale ?? 1),
      snow: a,
    })
  }
  return out
}

// ---------------------------------------------------------------------------
// Route mapping
// ---------------------------------------------------------------------------

/** Distance in metres → world X. */
export function worldXForDistance(distM: number, totalDistM: number): number {
  if (totalDistM <= 0) return 0
  return (distM / totalDistM) * WORLD_LEN
}
