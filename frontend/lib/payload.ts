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
 */
export function describePayload(
  occupants: number,
  luggageKg: number,
  usableKwh: number,
  whPerKmAtCruise: number,
): string {
  const extra = extraWhPerKm(occupants, luggageKg)
  if (Math.abs(extra) < 0.35) {
    return "The reference load the catalog figures already assume."
  }
  const rangeKm = (usableKwh * 1000) / whPerKmAtCruise
  const rangeDelta = rangeKm - (usableKwh * 1000) / (whPerKmAtCruise + extra)
  const kg = Math.round(payloadExtraKg(occupants, luggageKg))
  return extra > 0
    ? `+${kg} kg over the reference load, about +${extra.toFixed(1)} Wh/km, ` +
        `costing roughly ${Math.round(rangeDelta)} km of range per charge.`
    : `${kg} kg under the reference load, about ${extra.toFixed(1)} Wh/km, ` +
        `worth roughly ${Math.round(-rangeDelta)} km of extra range per charge.`
}
