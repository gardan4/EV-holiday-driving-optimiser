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
  /** Ambient temperature; when set the server derives the two factors below. */
  temperature_c?: number
  conditions_factor?: number
  /** Cold packs charge slower too: 1.0 / 0.8 / 0.55. */
  charge_power_factor?: number
  /** Share of derestriction-eligible autobahn that is really open. */
  autobahn_open_share?: number
  /** Motorway limit where the country's own isn't modelled. */
  motorway_cap_kph?: number
  /** What-if: treat every motorway as derestricted, limits ignored. */
  ignore_speed_limits?: boolean
  over_cap_kph?: number
  over_freeflow_factor?: number
  /** Fraction of a charger's rated power you actually get (0.3–1). */
  site_power_factor?: number
  queue_min?: number
  stop_overhead_min?: number
  rest_interval_min?: number
  rest_min?: number
  price_per_kwh?: number
  /** People aboard, driver included. Defaults to 2. */
  occupants?: number
  /** Luggage in the boot. Defaults to 30 kg. */
  luggage_kg?: number
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
  /** ISO-2 codes the route crosses, in driving order. Empty on older trips. */
  countries?: string[]
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

/** Erase a trip, every drive recorded on it, and their location trails.
 *  The link is the only key this app has, so holding the link is the
 *  authorisation — the same capability that reads the trip destroys it. */
export async function deleteTrip(id: string): Promise<void> {
  const resp = await apiFetch(`/api/trips/${id}`, { method: "DELETE" })
  if (!resp.ok && resp.status !== 404) {
    const data = await resp.json().catch(() => ({}))
    throw new Error(getErrorMessage(data.detail, "Could not delete this trip"))
  }
}

// ---------------------------------------------------------------------------
// Live runs
//
// Two capabilities: the trip id is a public read-only share link, the run id
// is the driver's write token. Only `startRun` ever returns the run id, and it
// is kept in localStorage on the driving device — a watcher opening the same
// link simply has no token and gets the read-only view.
// ---------------------------------------------------------------------------

export interface LiveState {
  at_min: number
  offset_m: number
  lat: number
  lon: number
  soc: number
  soc_is_measured: boolean
  /** Widens with the age of the last reading — never show `soc` without it. */
  soc_uncertainty_pct: number
  anchor_age_min: number
  off_route_m: number
  /** Off the planned route: position and battery are unknown, not zero. */
  stale: boolean
  at_charger_id: string | null
  run_factor: number
  /** Against the original plan. Positive = later than promised. */
  ahead_behind_min: number
  soc_vs_plan: number
  needs_replan: boolean
  replan_reasons: string[]
  status: string
}

export interface Benchmark {
  original_speed_kph: number
  original_total_min: number
  live_total_min: number
  delta_min: number
  original_stops_remaining: number
  live_stops_remaining: number
}

export interface RevisedPlan {
  plan_version: number
  /** The tail's own offsets start at zero; add this to place them. */
  offset_base_m: number
  elapsed_min: number
  remaining: SpeedResult
  speeds: SpeedResult[]
  optimum_speed: number | null
  benchmark: Benchmark
}

export interface StartRunResult {
  run_id: string
  run_ref: string
  trip_id: string
  state: LiveState
}

export interface LiveRun {
  run_ref: string
  status: string
  started_at: string
  finished_at: string | null
  planned_speed_kph: number
  state: LiveState
  plan: RevisedPlan | null
  trail: TimelinePoint[]
  /** How long since the driving phone last reported. A page whose driver lost
   *  signal must say so rather than show the same numbers under a live badge. */
  seconds_since_ping: number
}

export interface RunSummary {
  run_ref: string
  status: string
  started_at: string
  finished_at: string | null
  planned_speed_kph: number
  distance_m: number
  n_replans: number
}

export async function listRuns(tripId: string): Promise<RunSummary[]> {
  return unwrap<RunSummary[]>(await apiFetch(`/api/trips/${tripId}/runs`))
}

export interface ReviewStop {
  charger_id: string
  name: string
  planned_arrive_min: number | null
  actual_arrive_min: number | null
  planned_charge_min: number | null
  actual_charge_min: number | null
  planned_soc: number | null
  actual_soc: number | null
}

export interface RunReview {
  run_ref: string
  status: string
  started_at: string
  finished_at: string | null
  planned_speed_kph: number
  planned_total_min: number
  actual_total_min: number | null
  delta_min: number | null
  n_replans: number
  n_soc_readings: number
  final_run_factor: number
  stops: ReviewStop[]
  trail: TimelinePoint[]
}

export async function startRun(
  tripId: string,
  body: {
    planned_speed_kph: number
    depart_soc: number
    lat: number
    lon: number
    /** Replace a drive already under way. Only offered after a 409. */
    supersede?: boolean
  }
): Promise<StartRunResult> {
  return unwrap<StartRunResult>(
    await apiFetch(`/api/trips/${tripId}/runs`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  )
}

export async function pingRun(
  runId: string,
  body: {
    lat: number
    lon: number
    moving_s: number
    stationary_s: number
    accuracy_m?: number
  }
): Promise<LiveState> {
  return unwrap<LiveState>(
    await apiFetch(`/api/runs/${runId}/ping`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  )
}

export async function recordSoc(runId: string, soc: number): Promise<LiveState> {
  return unwrap<LiveState>(
    await apiFetch(`/api/runs/${runId}/soc`, {
      method: "POST",
      body: JSON.stringify({ soc }),
    })
  )
}

export async function replanRun(runId: string): Promise<RevisedPlan> {
  return unwrap<RevisedPlan>(
    await apiFetch(`/api/runs/${runId}/replan`, { method: "POST", body: "{}" })
  )
}

export async function finishRun(runId: string): Promise<LiveState> {
  return unwrap<LiveState>(
    await apiFetch(`/api/runs/${runId}/finish`, { method: "POST" })
  )
}

export async function getLiveRun(tripId: string): Promise<LiveRun> {
  return unwrap<LiveRun>(await apiFetch(`/api/trips/${tripId}/live`))
}

export async function getRunReview(
  tripId: string,
  runRef: string
): Promise<RunReview> {
  return unwrap<RunReview>(
    await apiFetch(`/api/trips/${tripId}/runs/${runRef}/review`)
  )
}

export interface FeedbackRequest {
  message: string
  contact?: string | null
  path?: string | null
}

/** Feedback goes to our own API, not to a third-party form — see
 *  `app/_components/FeedbackLink.tsx` for why. */
export async function sendFeedback(req: FeedbackRequest): Promise<void> {
  const resp = await apiFetch("/api/feedback", {
    method: "POST",
    body: JSON.stringify(req),
  })
  if (!resp.ok) {
    const data = await resp.json().catch(() => ({}))
    throw new Error(
      getErrorMessage(
        data.detail,
        resp.status === 429
          ? "That's a few messages already. Try again a bit later."
          : "Could not send that"
      )
    )
  }
}
