/**
 * Distance and speed units, for readers who think in miles.
 *
 * The app is metric all the way down — the simulator plans in km/h and Wh/km,
 * `PlanRequest` carries `motorway_cap_kph`, and every number the API returns is
 * SI. This module is a DISPLAY layer only: nothing here ever reaches the wire.
 * A speed the reader types in mph is converted back to km/h by
 * `speedToKph` at the edge of the form, so the request is identical whichever
 * units were on screen.
 *
 * Rounding is done at the display boundary and nowhere else. mph is coarser
 * than km/h (1 mph = 1.609 km/h), so `speedToKph(speedIn(kph))` is stable for
 * every integer mph — which is what lets a number field hold an mph value
 * without the box rewriting itself under the cursor on every keystroke.
 *
 * Deliberately NOT applied to `/ev`: those tables are keyed on round metric
 * speeds (100 · 130 · 160) and are the indexable document. Rows an mph reader
 * wants are 60 · 70 · 80, which is a content decision rather than a format one.
 */

export type Units = "metric" | "imperial"

export const KM_PER_MI = 1.609344
const FT_PER_M = 3.280839895

/** "km/h" or "mph" — the bare unit, for axis labels and input suffixes. */
export function speedUnit(u: Units): string {
  return u === "imperial" ? "mph" : "km/h"
}

/** "km" or "mi". */
export function distUnit(u: Units): string {
  return u === "imperial" ? "mi" : "km"
}

/** "Wh/km" or "Wh/mi". */
export function consumptionUnit(u: Units): string {
  return u === "imperial" ? "Wh/mi" : "Wh/km"
}

/** A speed in km/h → the number to show, rounded. */
export function speedIn(kph: number, u: Units): number {
  return Math.round(u === "imperial" ? kph / KM_PER_MI : kph)
}

/** A speed the reader typed → km/h for the API. Inverse of `speedIn`. */
export function speedToKph(value: number, u: Units): number {
  return Math.round(u === "imperial" ? value * KM_PER_MI : value)
}

/** "130 km/h" / "81 mph". */
export function fmtSpeed(kph: number, u: Units): string {
  return `${speedIn(kph, u)} ${speedUnit(u)}`
}

/** Metres → the distance number to show, rounded. */
export function distIn(meters: number, u: Units): number {
  const km = meters / 1000
  return Math.round(u === "imperial" ? km / KM_PER_MI : km)
}

/** Metres → "912 km" / "567 mi". Replaces `fmtKm` on every units-aware surface. */
export function fmtDist(meters: number, u: Units): string {
  return `${distIn(meters, u)} ${distUnit(u)}`
}

/** Kilometres → "45 km" / "28 mi", for the range figures written as km. */
export function fmtRangeKm(km: number, u: Units): string {
  return fmtDist(km * 1000, u)
}

/** Wh per km → "182 Wh/km" / "293 Wh/mi". Energy per unit distance, so the
 *  imperial figure is LARGER: a mile is further than a kilometre. */
export function consumptionIn(whPerKm: number, u: Units): number {
  return Math.round(u === "imperial" ? whPerKm * KM_PER_MI : whPerKm)
}

export function fmtConsumption(whPerKm: number, u: Units): string {
  return `${consumptionIn(whPerKm, u)} ${consumptionUnit(u)}`
}

/** Cumulative climb: "1.2 km" reads badly in miles, so imperial gets feet —
 *  which is how elevation is quoted there anyway. */
export function fmtClimb(meters: number, u: Units): string {
  if (u === "imperial") {
    return `${Math.round(meters * FT_PER_M).toLocaleString("en-US")} ft`
  }
  return `${(meters / 1000).toFixed(1)} km`
}

/** "per km" / "per mile", for prose that quotes a rate. */
export function perDistance(u: Units): string {
  return u === "imperial" ? "per mile" : "per km"
}

/**
 * The units a locale drives in, used only when nobody has chosen yet.
 *
 * The mph countries are the US, the UK and a short tail; everywhere else is
 * metric. Read from the browser's language tag, which carries a region only
 * sometimes — a bare "en" is ambiguous and stays metric, because guessing
 * imperial for every English speaker would flip the units for most of Europe.
 */
const IMPERIAL_REGIONS = new Set(["US", "GB", "LR", "MM", "PR", "GU", "VI", "AS", "MP"])

export function unitsForLocale(locale: string | undefined): Units {
  if (!locale) return "metric"
  const region = locale.split("-")[1]
  if (!region) return "metric"
  return IMPERIAL_REGIONS.has(region.toUpperCase()) ? "imperial" : "metric"
}
