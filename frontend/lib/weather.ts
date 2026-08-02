/**
 * Client-side mirror of the temperature model, used only to show the reader
 * what their number means as they type it.
 *
 * The authoritative versions are `consumption_factor_for_temp`,
 * `aux_kw_for_temp` and `charge_power_factor_for_temp` in
 * `src/app/services/simulator.py` — the plan itself is always computed there.
 *
 * Cold hits in three separate ways and they must stay separate: denser air and
 * stiffer tyres scale the per-kilometre figure, cabin heating is a roughly
 * constant power draw whose cost per kilometre FALLS as you speed up, and a
 * cold pack accepts charge more slowly. Rolling the heater into the per-km
 * multiplier would claim it pulls harder at 160 than at 90, and would bias the
 * whole answer towards driving slowly in exactly the weather this app is for.
 */

export function consumptionFactorForTemp(tempC: number): number {
  if (tempC < 15) return Math.min(1.2, 1 + (15 - tempC) * 0.005)
  return 1.0
}

/** Cabin and battery conditioning draw in kW — only the *extra* load weather
 *  imposes, over the hotel load already inside each car's fitted a_wh_km. */
export function auxKwForTemp(tempC: number): number {
  if (tempC < 15) return Math.min(4.5, (15 - tempC) * 0.13)
  if (tempC > 28) return Math.min(1.5, (tempC - 28) * 0.12)
  return 0
}

export function chargePowerFactorForTemp(tempC: number): number {
  if (tempC >= 15) return 1.0
  if (tempC >= 0) return 1.0 - (15 - tempC) * (0.2 / 15)
  if (tempC >= -10) return 0.8 - -tempC * (0.25 / 10)
  return 0.45
}

/** Puts the three effects into words, including the "no penalty" case. */
export function describeTemp(tempC: number): string {
  const extra = Math.round((consumptionFactorForTemp(tempC) - 1) * 100)
  const aux = auxKwForTemp(tempC)
  const charge = Math.round(chargePowerFactorForTemp(tempC) * 100)
  if (extra === 0 && aux === 0 && charge === 100) {
    return "no weather penalty — full range and full charging speed"
  }
  const parts: string[] = []
  if (extra > 0) parts.push(`${extra}% more energy per km`)
  if (aux > 0) parts.push(`${aux.toFixed(1)} kW of heating or cooling`)
  if (charge < 100) parts.push(`charging at ${charge}% of full speed`)
  return parts.join(" · ")
}
