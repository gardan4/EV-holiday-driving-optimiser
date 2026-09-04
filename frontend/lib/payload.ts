import { KM_PER_MI, Units, consumptionUnit, fmtRangeKm } from "./units"

/**
 * What the car is carrying, and what it costs.
 *
 * Mirrors the constants in `src/app/services/simulator.py` — keep the two in
 * sync or the figure shown next to the input stops matching the plan it
 * produces.
 */

export const OCCUPANT_KG = 75
export const NOMINAL_LOAD_KG = 180
const ROLLING_CRR = 0.01
const GRAVITY = 9.81
const DRIVETRAIN_EFFICIENCY = 0.9

/** Extra Wh/km per kilogram carried: Crr·m·g over 1000 m, J → Wh, then
 *  battery→wheels. ~3.0 Wh/km per 100 kg. */
const ROLLING_WH_KM_PER_KG =
  (ROLLING_CRR * GRAVITY * 1000) / 3600 / DRIVETRAIN_EFFICIENCY

/** Payload beyond the nominal load already inside the catalog's `mass_kg`.
 *  Negative for a lone driver with an empty boot. */
export function payloadExtraKg(occupants: number, luggageKg: number): number {
  return occupants * OCCUPANT_KG + luggageKg - NOMINAL_LOAD_KG
}

/** The per-kilometre rolling penalty. Speed-independent by design — the
 *  elevation term is separate and only bites on climbs. */
export function extraWhPerKm(occupants: number, luggageKg: number): number {
  return ROLLING_WH_KM_PER_KG * payloadExtraKg(occupants, luggageKg)
}

/**
 * One line explaining what this load does, measured against the two-people-
 * plus-a-bag the catalog figures already assume.
 *
 * The maths stays metric — `units` only decides how the two figures at the end
 * are written, so the sentence matches the units the rest of the page is in.
 */
export function describePayload(
  occupants: number,
  luggageKg: number,
  usableKwh: number,
  whPerKmAtCruise: number,
  units: Units = "metric",
): string {
  const extra = extraWhPerKm(occupants, luggageKg)
  if (Math.abs(extra) < 0.35) {
    return "The reference load the catalog figures already assume."
  }
  const rangeKm = (usableKwh * 1000) / whPerKmAtCruise
  const rangeDelta = rangeKm - (usableKwh * 1000) / (whPerKmAtCruise + extra)
  const kg = Math.round(payloadExtraKg(occupants, luggageKg))
  const per = consumptionUnit(units)
  const rate = (wh: number) => (units === "imperial" ? wh * KM_PER_MI : wh)
  return extra > 0
    ? `+${kg} kg over the reference load, about +${rate(extra).toFixed(1)} ${per}, ` +
        `costing roughly ${fmtRangeKm(rangeDelta, units)} of range per charge.`
    : `${kg} kg under the reference load, about ${rate(extra).toFixed(1)} ${per}, ` +
        `worth roughly ${fmtRangeKm(-rangeDelta, units)} of extra range per charge.`
}
