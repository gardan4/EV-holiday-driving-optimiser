/**
 * Client-side mirror of the temperature model, used only to show the reader
 * what their number means as they type it.
 *
 * The authoritative version is `consumption_factor_for_temp` /
 * `charge_power_factor_for_temp` in `src/app/services/simulator.py` — the plan
 * itself is always computed there. Keep the anchors in step if either changes:
 * mild 1.00/1.00, 0 °C 1.15/0.80, −10 °C 1.30/0.55.
 */

export function consumptionFactorForTemp(tempC: number): number {
  if (tempC < 10) return Math.min(1.55, 1 + (10 - tempC) * 0.015)
  if (tempC > 28) return Math.min(1.15, 1 + (tempC - 28) * 0.005)
  return 1.0
}

export function chargePowerFactorForTemp(tempC: number): number {
  if (tempC >= 15) return 1.0
  if (tempC >= 0) return 1.0 - (15 - tempC) * (0.2 / 15)
  if (tempC >= -10) return 0.8 - -tempC * (0.25 / 10)
  return 0.45
}
