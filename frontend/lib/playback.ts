/** Timeline interpolation for the journey animation.
 *
 * Playback itself lives in JourneyHero: the hero's horizontal scroll position
 * IS the playback head, so there is no separate clock to keep in sync. These
 * helpers turn a sim-minute into position/charge state.
 */

import { TimelinePoint } from "./client"

// ---------------------------------------------------------------------------
// Timeline interpolation
// ---------------------------------------------------------------------------

/** Piecewise-linear (dist, soc) at sim-minute `t`. Clamps at both ends. */
export function sampleTimeline(tl: TimelinePoint[], t: number): { dist: number; soc: number } {
  if (tl.length === 0) return { dist: 0, soc: 0 }
  if (t <= tl[0].t_min) return { dist: tl[0].dist_m, soc: tl[0].soc }
  const last = tl[tl.length - 1]
  if (t >= last.t_min) return { dist: last.dist_m, soc: last.soc }
  let lo = 0
  let hi = tl.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (tl[mid].t_min <= t) lo = mid
    else hi = mid
  }
  const a = tl[lo]
  const b = tl[hi]
  const span = b.t_min - a.t_min
  const f = span > 0 ? (t - a.t_min) / span : 0
  return {
    dist: a.dist_m + f * (b.dist_m - a.dist_m),
    soc: a.soc + f * (b.soc - a.soc),
  }
}

/** First sim-minute at which the run reaches `dist` (for the race gap timer). */
export function timeAtDist(tl: TimelinePoint[], dist: number): number {
  if (tl.length === 0) return 0
  if (dist <= tl[0].dist_m) return tl[0].t_min
  const last = tl[tl.length - 1]
  if (dist >= last.dist_m) return last.t_min
  for (let i = 1; i < tl.length; i++) {
    if (tl[i].dist_m >= dist) {
      const a = tl[i - 1]
      const b = tl[i]
      const span = b.dist_m - a.dist_m
      const f = span > 0 ? (dist - a.dist_m) / span : 0
      return a.t_min + f * (b.t_min - a.t_min)
    }
  }
  return last.t_min
}

/** True while the run is stationary at a charger at sim-minute `t`. */
export function isCharging(tl: TimelinePoint[], t: number): boolean {
  const eps = 0.75
  const now = sampleTimeline(tl, t)
  const ahead = sampleTimeline(tl, t + eps)
  return t < tl[tl.length - 1]?.t_min && ahead.dist - now.dist < 1 && ahead.soc > now.soc + 0.01
}
