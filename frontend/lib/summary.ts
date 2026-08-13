import { PlanResult, SpeedResult, Trip } from "./client"
import { fmtDuration, fmtHm } from "./format"

/**
 * The verdict math behind every summary surface: the hero plate, the answer
 * block, and the OG share description all read from here, so the page cannot
 * say one thing and the share card another.
 */

/** The friend's plan: 100 km/h if it was simulated, else the slowest that
 *  was — the comparison this whole app exists to settle. */
export function slowBaseline(speeds: SpeedResult[]): SpeedResult | null {
  const feasible = speeds.filter((s) => s.feasible)
  return feasible.find((s) => s.speed_kph === 100) ?? feasible[0] ?? null
}

export interface Verdict {
  selected: SpeedResult
  baseline: SpeedResult | null
  /** The optimum plan, when one exists. */
  best: SpeedResult | null
  /** Minutes this plan saves against the baseline. Positive = arrives earlier. */
  savedMin: number | null
  /** Anything under a minute is a tie, not a win — the DP plans on a 2.5% SoC
   *  grid, so differences that small are quantisation, not driving. */
  tie: boolean
  isBaseline: boolean
  /** Minutes the optimum would still save over this pick (0 when it IS the pick). */
  bestGainMin: number | null
}

export function buildVerdict(result: PlanResult, selectedSpeed: number): Verdict | null {
  const selected = result.speeds.find((s) => s.speed_kph === selectedSpeed)
  if (!selected || !selected.feasible) return null
  const baseline = slowBaseline(result.speeds)
  const best = result.speeds.find((s) => s.speed_kph === result.optimum_speed) ?? null
  const savedMin =
    baseline && baseline.total_min != null && selected.total_min != null
      ? baseline.total_min - selected.total_min
      : null
  const isBaseline = baseline?.speed_kph === selected.speed_kph
  const tie = savedMin != null && Math.abs(savedMin) < 1
  const bestGainMin =
    best && best.total_min != null && selected.total_min != null
      ? selected.total_min - best.total_min
      : null
  return { selected, baseline, best, savedMin, tie, isBaseline, bestGainMin }
}

/** The range-anxiety numbers: where the battery ends and where it bottoms out. */
export interface BatteryShape {
  arriveSoc: number
  minSoc: number
  /** Stop the low point rolls into, when it can be attributed to one. */
  tightestStop: string | null
  /** True when the lowest SoC of the whole trip is the final arrival. */
  lowestIsArrival: boolean
}

export function batteryShape(r: SpeedResult): BatteryShape | null {
  if (!r.timeline.length) return null
  const arriveSoc = r.timeline[r.timeline.length - 1].soc
  const minSoc = r.timeline.reduce((m, p) => Math.min(m, p.soc), Infinity)
  const lowestIsArrival = arriveSoc - minSoc < 0.75
  let tightestStop: string | null = null
  if (!lowestIsArrival && r.stops.length) {
    const s = r.stops.reduce((a, b) => (b.arrive_soc < a.arrive_soc ? b : a))
    if (Math.abs(s.arrive_soc - minSoc) < 0.75) tightestStop = s.name
  }
  return { arriveSoc, minSoc, tightestStop, lowestIsArrival }
}

/** The time-vs-money trade in numbers: what the fast plan buys and costs. */
export interface CostOfSpeed {
  extraEur: number
  savedMin: number
  eurPerHour: number
  fastestKph: number
  cheapestKph: number
}

export function costOfSpeed(
  speeds: SpeedResult[],
  optimumSpeed: number | null
): CostOfSpeed | null {
  const feasible = speeds.filter((s) => s.feasible)
  if (feasible.length === 0 || feasible.every((s) => s.cost_eur === 0)) return null
  const cheapest = feasible.reduce((a, b) => (b.cost_eur < a.cost_eur ? b : a))
  const fastest = feasible.find((s) => s.speed_kph === optimumSpeed) ?? null
  if (!fastest || fastest.speed_kph === cheapest.speed_kph) return null
  const extraEur = fastest.cost_eur - cheapest.cost_eur
  const savedMin = (cheapest.total_min ?? 0) - (fastest.total_min ?? 0)
  if (savedMin <= 0 || extraEur <= 0) return null
  return {
    extraEur,
    savedMin,
    eurPerHour: extraEur / (savedMin / 60),
    fastestKph: fastest.speed_kph,
    cheapestKph: cheapest.speed_kph,
  }
}

/** The share/OG sentence. Evaluated at the optimum — metadata is static and
 *  cannot follow the reader's speed selection. */
export function headlineDescription(trip: Trip): string {
  const { result, request } = trip
  const origin = request.origin.label.split(",")[0]
  const dest = request.dest.label.split(",")[0]
  const v =
    result.optimum_speed != null ? buildVerdict(result, result.optimum_speed) : null
  if (!v || v.selected.total_min == null) {
    return `${origin} → ${dest} in a ${result.vehicle.make} ${result.vehicle.model}.`
  }
  let description = `${origin} → ${dest}: cruise ${v.selected.speed_kph} km/h and you're there in ${fmtHm(v.selected.total_min)}`
  if (v.baseline && !v.isBaseline && v.savedMin != null && v.savedMin >= 1) {
    description += `, ${fmtDuration(v.savedMin)} sooner than at ${v.baseline.speed_kph} km/h`
  }
  return description + ", charging stops included."
}
