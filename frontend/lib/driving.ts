/**
 * An ordinary road's flow speed lifts more gently than a motorway's legal cap
 * when you push: +20 km/h on the autobahn is not +20 through a village.
 * Anchored to the presets this replaced (0 → 1.00, 10 → 1.08, 20 → 1.15).
 */
export function freeflowFactorFor(overCapKph: number): number {
  return Math.min(1.15, 1 + overCapKph * 0.0075)
}
