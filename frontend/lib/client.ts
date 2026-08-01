/**
 * Typed API client — thin wrappers over apiFetch mirroring the FastAPI
 * schemas in src/app/api/schemas.py.
 */

import { apiFetch, getErrorMessage } from "./api"

// ---------------------------------------------------------------------------
// Types (mirror src/app/api/schemas.py)
// ---------------------------------------------------------------------------

export interface Vehicle {
  id: string
  slug: string
  make: string
  model: string
  variant: string | null
  usable_kwh: number
  consumption: { model: string; a_wh_km: number; b_wh_km_per_kph2: number }
  charge_curve: [number, number][]
  max_dc_kw: number
  mass_kg: number
  top_speed_kph: number
  source_note: string | null
}

export interface GeocodeHit {
  label: string
  lat: number
  lon: number
  country: string | null
}

export interface PlacePoint {
  label: string
  lat: number
  lon: number
}

export interface PlanRequest {
  origin: PlacePoint
  dest: PlacePoint
  vehicle_id: string
  departure_iso: string
  depart_soc: number
  target_soc: number
  speed_min?: number
  speed_max?: number
  speed_step?: number
  /** Consumption multiplier: 1.0 mild / 1.15 cold / 1.3 freezing. */
  conditions_factor?: number
  /** Cold packs charge slower too: 1.0 / 0.8 / 0.55. */
  charge_power_factor?: number
  /** Share of derestriction-eligible autobahn that is really open. */
  autobahn_open_share?: number
  over_cap_kph?: number
  over_freeflow_factor?: number
  queue_min?: number
  stop_overhead_min?: number
  rest_interval_min?: number
  rest_min?: number
  price_per_kwh?: number
}

export interface Stop {
  charger_id: string
  name: string
  operator: string | null
  lat: number
  lon: number
  power_kw: number
  offset_m: number
  arrive_min: number
  arrive_soc: number
  depart_soc: number
  charge_min: number
}

export interface TimelinePoint {
  t_min: number
  dist_m: number
  soc: number
}

export interface SpeedResult {
  speed_kph: number
  feasible: boolean
  total_min: number | null
  drive_min: number | null
  charge_min: number | null
  n_stops: number | null
  /** Break time the charging stops did not already absorb. */
  rest_min: number
  energy_kwh: number
  cost_eur: number
  stops: Stop[]
  timeline: TimelinePoint[]
}

export interface PlanResult {
  polyline: string
  total_dist_m: number
  speeds: SpeedResult[]
  optimum_speed: number | null
  vehicle: Vehicle
  climb_m: number
  /** The curve was still falling at the car's top speed. */
  optimum_at_top_speed: boolean
}

export interface Trip {
  id: string
  request: PlanRequest
  result: PlanResult
  created_at: string
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

async function unwrap<T>(res: Response): Promise<T> {
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    const detail = (data as { detail?: unknown } | null)?.detail
    throw new Error(getErrorMessage(detail, `Request failed (${res.status})`))
  }
  return data as T
}

export async function getVehicles(): Promise<Vehicle[]> {
  return unwrap<Vehicle[]>(await apiFetch("/api/vehicles"))
}

export async function geocode(q: string): Promise<GeocodeHit[]> {
  return unwrap<GeocodeHit[]>(await apiFetch(`/api/geocode?q=${encodeURIComponent(q)}`))
}

export async function planTrip(req: PlanRequest): Promise<Trip> {
  return unwrap<Trip>(await apiFetch("/api/trips", { method: "POST", body: JSON.stringify(req) }))
}

export async function getTrip(id: string): Promise<Trip> {
  return unwrap<Trip>(await apiFetch(`/api/trips/${id}`))
}
