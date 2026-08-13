/** Typed client for the dashboard role's own API.
 *
 * Same-origin throughout: the SPA is served by the process it talks to, so the
 * session cookie rides along without CORS, without `allow_credentials`, and
 * without the STATS_TOKEN ever existing in JavaScript. That is the whole
 * reason the dashboard is its own served app rather than a route in the public
 * Next frontend, where the token would have had to reach the browser or be
 * proxied by a second server.
 */

export type Filters = {
  days: number
  device?: string
  browser?: string
  os?: string
  country?: string
  campaign?: string
  viewport?: string
  path?: string
  referrer?: string
  referrer_path?: string
}

export type Row = { label: string; value: number }
export type Point = { bucket: string; value: number }

export type Delta = { value: number; previous: number; change: number | null }

export type Provider = {
  provider: string
  /** The service within that provider — "directions", "geocode", "chargers".
   *  Ceilings are enforced per service, so this is part of the identity of a
   *  row rather than a detail. */
  kind: string
  calls_today: number
  cache_hits_today: number
  failures_today: number
  avg_ms: number
  calls_window: number
  daily_quota: number
  hit_rate: number | null
  headroom_pct: number | null
}

export type Overview = {
  window: {
    days: number
    since: string
    until: string
    granularity: string
    filters: Record<string, string>
  }
  totals: Record<string, Delta>
  series: { page_views: Point[]; visitors: Point[] }
  funnel: { name: string; label: string; count: number; from_previous: number | null }[]
  plan_failures: number
  retention: { identified: number; returning: number; rate: number | null }
  providers: Provider[]
}

export type Segments = {
  countries: Row[]
  devices: Row[]
  browsers: Row[]
  operating_systems: Row[]
  viewports: Row[]
  paths: Row[]
  referrers: Row[]
  referrer_paths: Row[]
  campaigns: Row[]
  failure_reasons: Row[]
}

export type Corridor = {
  label: string
  trips: number
  distance_km: number
  avg_stops: number | null
  stops_per_100km: number | null
}

export type Journeys = {
  corridors: {
    top: Corridor[]
    hardest: Corridor[]
    infeasible: Corridor[]
    unmodelled_countries: Row[]
    unplaceable_trips: number
  }
  shape: {
    trips: number
    feasible: number
    feasible_rate: number | null
    mean_km: number
    mean_charge_share: number | null
    median_charge_share: number | null
    distances: Row[]
    charge_shares: Row[]
    months: Row[]
    transit_countries: Row[]
    speeds: Row[]
  }
  drives: {
    runs: number
    finished: number
    active: number
    abandoned: number
    completion_rate: number | null
    median_minutes: number | null
    replan_rate: number | null
    off_route_rate: number | null
    total_replans: number
    median_pings: number | null
    charge_stops: { minutes: number; soc_start: number | null; soc_end: number | null }[]
    plan_vs_actual: { planned_kph: number; achieved_kph: number }[]
  }
  planners: Row[]
  cars: (Row & { avg_optimum_kph: number | null })[]
}

export type DayHealth = {
  day: string
  calls: number
  cache_hits: number
  failures: number
  operations: number
  hit_rate: number | null
}

export type Upstream = {
  daily: Record<string, DayHealth[]>
  latency: {
    provider: string
    kind: string
    operations: number
    p50_ms: number | null
    p95_ms: number | null
    max_ms: number | null
  }[]
}

export type Meta = {
  dimensions: string[]
  filterable: string[]
  measures: string[]
  granularities: string[]
  max_days: number
  stream_poll_seconds: number
  /** ISO-2 codes the simulator has real motorway caps for. Derived from
   * `simulator._default_country_caps` server-side, so the map's shading cannot
   * drift from what the planner actually models. */
  modelled_countries: string[]
  /** What everywhere else is assumed to be. */
  default_cap_kph: number
}

export class Unauthenticated extends Error {}

async function get<T>(path: string, params?: Record<string, unknown>): Promise<T> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v))
  }
  const url = qs.toString() ? `${path}?${qs}` : path
  const res = await fetch(url, { credentials: "same-origin" })
  // 401 is "sign in", 404 is "there is no dashboard configured here" — the SPA
  // needs to tell those apart to know whether a password box would help.
  if (res.status === 401) throw new Unauthenticated("Sign in required.")
  if (!res.ok) throw new Error(`${res.status} ${await res.text().catch(() => "")}`)
  return (await res.json()) as T
}

export const api = {
  session: () => get<{ authenticated: boolean }>("/api/session"),

  async login(password: string): Promise<boolean> {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ password }),
    })
    if (res.status === 429) throw new Error("Too many attempts. Try again later.")
    return res.ok
  },

  async logout(): Promise<void> {
    await fetch("/api/logout", { method: "POST", credentials: "same-origin" })
  },

  meta: () => get<Meta>("/api/meta"),
  overview: (f: Filters, granularity: string) =>
    get<Overview>("/api/overview", { ...f, granularity }),
  segments: (f: Filters) => get<Segments>("/api/segments", f),
  journeys: (f: Filters) => get<Journeys>("/api/journeys", f),
  upstream: (days: number) => get<Upstream>("/api/upstream", { days }),
}
