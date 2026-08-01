import { SpeedResult } from "./client"

/** Speeds that land within `toleranceMin` of the best plan. */
export interface Band {
  /** Slowest speed still within tolerance of the optimum. */
  lo: number
  /** Fastest speed still within tolerance. */
  hi: number
  /** The optimum itself. */
  best: number
  /** Minutes the slow end gives up against the optimum. */
  loCostMin: number
  /** Euros the slow end saves against the optimum (may be 0 if no pricing). */
  loSavesEur: number
  /** True when no other speed comes within tolerance — the optimum is sharp. */
  sharp: boolean
  toleranceMin: number
}

/**
 * The honest headline isn't "drive exactly 140" — it's how wide the near-optimal
 * plateau is. Total time against cruise speed is a shallow curve, so a range of
 * speeds usually ties within a few minutes, and the slow end of that range is
 * cheaper and calmer for the same arrival time.
 *
 * Spans the full range of speeds within tolerance, then reports the WORST
 * deviation actually found inside that range. The curve isn't perfectly smooth
 * — the stop count steps, so an individual speed can poke above the tolerance
 * while both its neighbours sit under it. Taking the contiguous run instead
 * would drop genuinely good speeds beyond such a spike; widening the claim and
 * quoting the real worst case keeps the statement true either way.
 */
export function nearOptimalBand(
  speeds: SpeedResult[],
  toleranceMin = 5
): Band | null {
  const feasible = speeds
    .filter((s) => s.feasible && s.total_min != null)
    .sort((a, b) => a.speed_kph - b.speed_kph)
  if (feasible.length === 0) return null

  const best = feasible.reduce((a, b) =>
    (b.total_min ?? 0) < (a.total_min ?? 0) ? b : a
  )
  const bestMin = best.total_min ?? 0

  const within = feasible.filter((s) => (s.total_min ?? 0) - bestMin <= toleranceMin)
  const lo = within[0]
  const hi = within[within.length - 1]

  // The strongest claim we can make about the whole closed range.
  const worstMin = feasible
    .filter((s) => s.speed_kph >= lo.speed_kph && s.speed_kph <= hi.speed_kph)
    .reduce((w, s) => Math.max(w, (s.total_min ?? 0) - bestMin), 0)

  return {
    lo: lo.speed_kph,
    hi: hi.speed_kph,
    best: best.speed_kph,
    loCostMin: Math.round((lo.total_min ?? 0) - bestMin),
    loSavesEur: Math.max(0, (best.cost_eur ?? 0) - (lo.cost_eur ?? 0)),
    sharp: within.length <= 1,
    toleranceMin: Math.max(1, Math.ceil(worstMin)),
  }
}
