/**
 * Server-side vehicle catalog access + the small slice of the simulator's
 * physics needed to describe one car on its own page.
 *
 * The numbers on `/ev/[slug]` have to be the numbers the planner would use, so
 * `curvePowerKw` / `chargeMinutes` / `consumptionWhKm` are deliberate ports of
 * `simulator.curve_power_kw`, `simulator.charge_minutes` and
 * `simulator.consumption_wh_km` — same piecewise-linear interpolation, same
 * midpoint-rule integral, same quadratic. If you change one, change both; the
 * page claims these are what the app models, and that has to stay true.
 */

import { API_URL } from "./api"
import type { Vehicle } from "./client"

/** Catalog only changes on deploy, so an hour of staleness costs nothing and
 *  keeps the marketing pages off the database on every crawl. */
const CATALOG_TTL_SECONDS = 3600

/** Long enough to absorb an App Service cold start, short enough that three of
 *  them still finish inside a CI step. */
const FETCH_TIMEOUT_MS = 15_000
const ATTEMPTS = 3

/**
 * Fetch the whole catalog for server rendering.
 *
 * Retried, because `/ev` is prerendered at build time and this call therefore
 * sits on the deploy's critical path. The API idles on an App Service plan that
 * cold-starts, and one slow first byte should not fail a release. Three tries
 * with a short backoff turns "the container was asleep" into a two-second delay
 * instead of a red pipeline.
 *
 * Throws once they are exhausted rather than returning `[]`: an empty array
 * would render an empty `/ev` index and 404 every vehicle page, and Next would
 * then cache that outcome for the full revalidate window. Failing loudly is the
 * better error.
 */
export async function fetchVehicles(): Promise<Vehicle[]> {
  let lastError: unknown

  for (let attempt = 1; attempt <= ATTEMPTS; attempt++) {
    try {
      const res = await fetch(`${API_URL}/api/vehicles`, {
        next: { revalidate: CATALOG_TTL_SECONDS },
        signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
      })
      if (!res.ok) {
        throw new Error(`vehicle catalog fetch failed: ${res.status}`)
      }
      return (await res.json()) as Vehicle[]
    } catch (err) {
      lastError = err
      if (attempt < ATTEMPTS) {
        await new Promise((r) => setTimeout(r, attempt * 750))
      }
    }
  }

  throw new Error(`vehicle catalog unavailable after ${ATTEMPTS} attempts`, {
    cause: lastError,
  })
}

/** Same as `fetchVehicles`, but yields `[]` instead of throwing. Only for
 *  callers that must still produce something useful when the API is down —
 *  `sitemap.xml` would rather list the static routes than return a 500. */
export async function fetchVehiclesOrEmpty(): Promise<Vehicle[]> {
  try {
    return await fetchVehicles()
  } catch {
    return []
  }
}

export async function findVehicle(slug: string): Promise<Vehicle | null> {
  const all = await fetchVehicles()
  return all.find((v) => v.slug === slug) ?? null
}

// ---------------------------------------------------------------------------
// Naming
// ---------------------------------------------------------------------------

/** "Volkswagen ID.3 Pro 58 kWh" — make, model and variant, in that order. */
export function vehicleName(v: Vehicle): string {
  return [v.make, v.model, v.variant].filter(Boolean).join(" ")
}

/** "ID.3 Pro 58 kWh" — for lists already grouped under the make. */
export function vehicleShortName(v: Vehicle): string {
  return [v.model, v.variant].filter(Boolean).join(" ")
}

// ---------------------------------------------------------------------------
// Consumption — port of simulator.consumption_wh_km
// ---------------------------------------------------------------------------

/** Steady-state motorway consumption at `kph`, in Wh/km. Fitted for cruise
 *  speeds; it is not a city-driving figure and the page says so. */
export function consumptionWhKm(v: Vehicle, kph: number): number {
  return v.consumption.a_wh_km + v.consumption.b_wh_km_per_kph2 * kph * kph
}

/** Kilometres covered at `kph` using `socSpan` percent of the usable pack. */
export function rangeKm(v: Vehicle, kph: number, socSpan = 100): number {
  const kwh = v.usable_kwh * (socSpan / 100)
  return (kwh * 1000) / consumptionWhKm(v, kph)
}

/** The cruise speeds the planner sweeps, in the steps the page tabulates. */
export const CRUISE_SPEEDS = [90, 100, 110, 120, 130, 140, 150, 160] as const

/** Speeds this car can actually hold, so a 160 km/h row never appears for a
 *  car limited to 150 — a wrong number is worse than a missing one. */
export function cruiseSpeedsFor(v: Vehicle): number[] {
  return CRUISE_SPEEDS.filter((s) => s <= v.top_speed_kph)
}

// ---------------------------------------------------------------------------
// Charging — port of simulator.curve_power_kw / charge_minutes
// ---------------------------------------------------------------------------

/** Matches `_INTEGRATION_STEP` in the simulator's charge integral. */
const INTEGRATION_STEP = 0.5

/** Piecewise-linear interpolation of the charge curve at `soc` percent. */
export function curvePowerKw(v: Vehicle, soc: number): number {
  const curve = v.charge_curve
  if (!curve.length) return 0
  if (soc <= curve[0][0]) return curve[0][1]
  for (let i = 0; i < curve.length - 1; i++) {
    const [s0, p0] = curve[i]
    const [s1, p1] = curve[i + 1]
    if (soc <= s1) {
      if (s1 === s0) return p1
      return p0 + ((soc - s0) / (s1 - s0)) * (p1 - p0)
    }
  }
  return curve[curve.length - 1][1]
}

/**
 * Minutes to charge `socFrom` → `socTo` on a charger fast enough not to be the
 * limit. Midpoint-rule integral over the piecewise-linear curve, as in the
 * simulator — so this is the car's own ceiling, not what a specific site gives.
 */
export function chargeMinutes(v: Vehicle, socFrom: number, socTo: number): number {
  if (socTo <= socFrom) return 0
  let minutes = 0
  let soc = socFrom
  while (soc < socTo - 1e-9) {
    const step = Math.min(INTEGRATION_STEP, socTo - soc)
    const p = Math.max(curvePowerKw(v, soc + step / 2), 1)
    minutes += (60 * ((v.usable_kwh * step) / 100)) / p
    soc += step
  }
  return minutes
}

/** Average power actually delivered across a SoC window, in kW. The honest
 *  headline number: peak kW is marketing, this is what the stop costs you. */
export function averageKw(v: Vehicle, socFrom: number, socTo: number): number {
  const minutes = chargeMinutes(v, socFrom, socTo)
  if (minutes <= 0) return 0
  const kwh = (v.usable_kwh * (socTo - socFrom)) / 100
  return kwh / (minutes / 60)
}

/** SoC at which the curve has fallen to `fraction` of its own peak — the point
 *  where staying plugged in starts costing more time than it saves.
 *
 *  Searched forward from the peak, not from 0: every curve here starts low
 *  (a cold-ish pack at 0% accepts well under peak), so scanning from zero would
 *  report "tapers at 0%" for all 46 cars. */
export function taperSoc(v: Vehicle, fraction = 0.5): number | null {
  let peak = 0
  let peakSoc = 0
  for (const [soc, kw] of v.charge_curve) {
    if (kw > peak) {
      peak = kw
      peakSoc = soc
    }
  }
  const threshold = peak * fraction
  for (let soc = peakSoc; soc <= 100; soc += 1) {
    if (curvePowerKw(v, soc) < threshold) return soc
  }
  return null
}
