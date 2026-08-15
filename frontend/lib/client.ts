/**
 * Typed API client — thin wrappers over apiFetch mirroring the FastAPI
 * schemas in src/app/api/schemas.py.
 */

import { apiFetch, getErrorMessage } from "./api"
import { ownerHeaders } from "./account"
import { clientIdHeaders } from "./analytics"

// ---------------------------------------------------------------------------
// Types (mirror src/app/api/schemas.py)
// ---------------------------------------------------------------------------

export interface Vehicle {
  id: string
  slug: string
  make: string
  model: string
  variant: string | null
  /** Which `/ev` page this variant belongs on; null means a page of its own. */
  nameplate_slug: string | null
  usable_kwh: number
  consumption: { model: string; a_wh_km: number; b_wh_km_per_kph2: number }
  charge_curve: [number, number][]
  max_dc_kw: number
  mass_kg: number
  top_speed_kph: number
  /** Cable → battery efficiency. The curve is measured at the cable, so charge
   *  times derive from it; see `chargeMinutes`. */
  charge_efficiency: number
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
  /** Winter tyres: a per-km rolling cost, not a weather setting. */
  winter_tyres?: boolean
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
  /** DC bays OpenChargeMap lists. NOT live availability — no operator
   *  publishes that to us — and absent on trips planned before it was
   *  carried through, hence optional. */
  n_points?: number
  /** What the site's NAME suggests you could eat there. Derived server-side on
   *  the way out, so old trips get it too; null means the name says nothing,
   *  not that there is nothing there. */
  food_hint?: string | null
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

/** Plan a trip and persist its permalink.
 *
 *  Carries both identifiers this app has, and is the ONLY read/write here that
 *  carries either. Not on `apiFetch`: `getTrip`, `geocode` and the run
 *  endpoints would then attach them to every open of a shared link, which
 *  would turn the share token into a record of who was sent it. Planning is the
 *  act of a person using the app; opening a link someone forwarded you is not.
 *
 *  The owner secret rides along so the trip is published under the planner's
 *  username — if they have claimed one. The server ignores it otherwise, so
 *  sending it unconditionally here is safe and keeps the rule simple: the
 *  planner is recorded, the reader is not.
 *
 *  Neither goes in the body. `PlanRequest` is persisted whole into
 *  `Trip.request`, exact coordinates and all. */
export async function planTrip(req: PlanRequest): Promise<Trip> {
  return unwrap<Trip>(
    await apiFetch("/api/trips", {
      method: "POST",
      body: JSON.stringify(req),
      headers: { ...clientIdHeaders(), ...ownerHeaders() },
    })
  )
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
// Usernames — a public handle over a secret this browser holds
//
// Writing takes the secret; reading takes only the name. That asymmetry is the
// feature: you can hand somebody your username and it works for them with no
// account, no secret and no state of their own.
// ---------------------------------------------------------------------------

export interface Profile {
  username: string
  created_at: string
}

/** One trip as it appears on a PUBLIC list. Deliberately not a `Trip`: no
 *  coordinates, no polyline, and the place labels are already reduced to their
 *  locality server-side. */
export interface TripSummary {
  id: string
  created_at: string
  origin_label: string
  dest_label: string
  distance_km: number
  vehicle_label: string
  departure_iso: string | null
  optimum_speed_kph: number | null
  n_stops: number | null
}

export interface UserTrips {
  username: string
  created_at: string
  trips: TripSummary[]
}

/** Bind a username to this browser's secret. The opt-in moment: from here on,
 *  trips planned by this browser are published under the name. */
export async function claimUsername(username: string): Promise<Profile> {
  return unwrap<Profile>(
    await apiFetch("/api/users", {
      method: "POST",
      body: JSON.stringify({ username }),
      headers: ownerHeaders(),
    })
  )
}

/** Which username a secret holds — this browser's, or a candidate one.
 *
 *  The one read in this app that sends an identifier, and it is narrow on
 *  purpose: it exists for the second-device flow, where the owner has just
 *  pasted their own code and is asking about their own name. It records
 *  nothing.
 *
 *  `candidate` is what makes that flow safe to get wrong. Adopting a code
 *  REPLACES whatever secret this browser holds, so asking the question with the
 *  pasted code before storing it is the difference between "that code is no
 *  good" and "that code is no good and your own username is now unreachable". */
export async function whoAmI(candidate?: string): Promise<Profile | null> {
  const resp = await apiFetch("/api/users/me", {
    headers: candidate ? { "X-Owner-Secret": candidate } : ownerHeaders(),
  })
  if (resp.status === 401 || resp.status === 404) return null
  return unwrap<Profile>(resp)
}

/** Everything published under a name. Sends no secret — anyone can read this,
 *  which is the point of having a username at all. */
export async function getUserTrips(username: string): Promise<UserTrips> {
  return unwrap<UserTrips>(
    await apiFetch(`/api/users/${encodeURIComponent(username)}/trips`)
  )
}

/** Give up a name and unpublish everything under it. The trips themselves and
 *  their share links survive — deleting a journey is a separate control. */
export async function releaseUsername(username: string): Promise<void> {
  const resp = await apiFetch(`/api/users/${encodeURIComponent(username)}`, {
    method: "DELETE",
    headers: ownerHeaders(),
  })
  if (!resp.ok && resp.status !== 404) {
    const data = await resp.json().catch(() => ({}))
    throw new Error(getErrorMessage(data.detail, "Could not release this username"))
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

export interface AtCharger {
  charger_id: string
  name: string
  operator: string | null
  power_kw: number
  n_points?: number
  lat: number
  lon: number
  food_hint?: string | null
  /** Whether the plan in force chose this one. */
  planned: boolean
}

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
  /** Where the car is plugged in, when it is — including chargers the plan
   *  never chose, which is the whole reason it is here. */
  at_charger?: AtCharger | null
  /** Battery needed to reach the next planned stop, reserve included. The
   *  question a driver at an unplanned charger is asking. */
  need_soc_next?: number | null
  /** Whether "I'm plugged in here now" can still be taken back. It is the one
   *  tap on the drive screen the model cannot undo on its own — the offset is
   *  clamped forward, so a wrong arrival survives every later fix. */
  can_undo_arrive?: boolean
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
  /** The speed the driver asked to hold, if they asked. Null means the plan is
   *  simply the fastest — `optimum_speed` is the plan's own speed either way. */
  held_speed_kph?: number | null
  /** What holding it costs against the best speed in the sweep. */
  hold_costs_min?: number
  /** The tail's own offsets start at zero; add this to place them. */
  offset_base_m: number
  elapsed_min: number
  remaining: SpeedResult
  speeds: SpeedResult[]
  optimum_speed: number | null
  benchmark: Benchmark
}

export interface Alternative {
  charger_id: string
  name: string
  operator: string | null
  power_kw: number
  /** DC bays OpenChargeMap lists; see `Stop.n_points`. */
  n_points?: number
  /** What the site's NAME suggests you could eat there — "motorway services",
   *  "supermarket" — or null when it says nothing. A guess from a string, so
   *  render it as one. Null is NOT a claim that there is nothing there. */
  food_hint?: string | null
  lat: number
  lon: number
  /** On the whole route's axis, like every other stop. */
  offset_m: number
  dist_from_here_m: number
  arrive_soc: number
  depart_soc: number
  charge_min: number
  /** Extra minutes to the DESTINATION, not to this stop — swapping a charger
   *  moves everything after it. Zero for the stop currently planned. */
  delta_min: number
  /** Send these to `replanRun` to make this one the next stop. */
  exclude_charger_ids: string[]
  /** Reaching this one means arriving under the planner's reserve. `arrive_soc`
   *  is the number that matters when it is true. */
  below_reserve?: boolean
  /** The first-leg floor it was computed under. Must go back with the re-plan,
   *  or the full reserve applies again and refuses this very stop. */
  min_arrival_soc?: number | null
}

export interface Alternatives {
  speed_kph: number
  current: Alternative | null
  alternatives: Alternative[]
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

/** Re-optimise the rest of the drive.
 *
 *  `excludeChargerIds` are stops the driver has turned down; the server merges
 *  them into the set it already holds for this run and keeps them, so a
 *  rejection survives every later re-plan rather than lasting one request. */
export async function replanRun(
  runId: string,
  excludeChargerIds: string[] = [],
  minArrivalSoc: number | null = null,
  /** `undefined` leaves the held speed alone; `null` hands the choice back to
   *  the optimiser; a number pins it. The key is OMITTED when undefined,
   *  because the server tells "unchanged" from "clear it" by whether the field
   *  was sent at all. */
  holdSpeedKph: number | null | undefined = undefined
): Promise<RevisedPlan> {
  const body: Record<string, unknown> = {
    exclude_charger_ids: excludeChargerIds,
    min_arrival_soc: minArrivalSoc,
  }
  if (holdSpeedKph !== undefined) body.hold_speed_kph = holdSpeedKph
  return unwrap<RevisedPlan>(
    await apiFetch(`/api/runs/${runId}/replan`, {
      method: "POST",
      body: JSON.stringify(body),
    })
  )
}

/** Other chargers the drive could stop at instead of the next one. Read-only:
 *  taking one means calling `replanRun` with its `exclude_charger_ids`. */
export async function getAlternatives(
  runId: string,
  /** Which stop to replace. Omit for the next one. */
  rejectChargerId?: string
): Promise<Alternatives> {
  return unwrap<Alternatives>(
    await apiFetch(`/api/runs/${runId}/alternatives`, {
      method: "POST",
      body: JSON.stringify({ reject_charger_id: rejectChargerId ?? null }),
    })
  )
}

export interface ArriveResult {
  state: LiveState
  /** The charger the position matched, or null when none was near enough. */
  matched_name: string | null
}

/** "I'm standing here now." A locked phone reports no position, which is the
 *  state a phone is in while its car charges — so the drive has to be tellable
 *  by hand. `lat`/`lon` wins when given, because a driver charging somewhere
 *  the plan never chose must not be told they are at the stop that happened to
 *  be on screen; `chargerId` is the fallback when no fix arrives. The server
 *  bills the kilometres it skips. */
export async function arriveAt(
  runId: string,
  chargerId: string | null,
  here?: { lat: number; lon: number } | null
): Promise<ArriveResult> {
  return unwrap<ArriveResult>(
    await apiFetch(`/api/runs/${runId}/arrive`, {
      method: "POST",
      body: JSON.stringify({
        charger_id: chargerId,
        lat: here?.lat ?? null,
        lon: here?.lon ?? null,
      }),
    })
  )
}

/** "I'm not there." Puts the drive back exactly as it was before the last
 *  arrival — position, battery and clock together, because the arrival billed
 *  the skipped kilometres and returning the position alone would hand back a
 *  battery that paid for road it is about to drive again. */
export async function undoArrival(runId: string): Promise<LiveState> {
  return unwrap<LiveState>(
    await apiFetch(`/api/runs/${runId}/arrive/undo`, { method: "POST" })
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
