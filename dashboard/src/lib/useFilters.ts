import { useCallback, useEffect, useState } from "react"
import type { Filters } from "./api"

/** Filter state, kept in the URL.
 *
 * In the URL rather than in React state alone so a view is linkable: "mobile
 * traffic from Reddit over 30 days" is a thing worth sending to somebody, or
 * bookmarking before a launch, and a dashboard whose state dies on refresh
 * makes you rebuild the question every time.
 */

export const FACETS = [
  "device",
  "browser",
  "os",
  "country",
  "campaign",
  "viewport",
  "path",
  "referrer",
  "referrer_path",
] as const

export type Facet = (typeof FACETS)[number]

const DEFAULT_DAYS = 7

function read(): Filters {
  const q = new URLSearchParams(window.location.search)
  const days = Number(q.get("days"))
  const f: Filters = {
    days: Number.isFinite(days) && days >= 1 && days <= 90 ? days : DEFAULT_DAYS,
  }
  for (const key of FACETS) {
    const v = q.get(key)
    if (v) f[key] = v
  }
  return f
}

function write(f: Filters) {
  const q = new URLSearchParams()
  if (f.days !== DEFAULT_DAYS) q.set("days", String(f.days))
  for (const key of FACETS) {
    const v = f[key]
    if (v) q.set(key, v)
  }
  const qs = q.toString()
  // replaceState, not pushState: a filter chip is a change of view, not a
  // navigation, and stacking every toggle onto the back button makes Back
  // useless for actually leaving the page.
  window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname)
}

export function useFilters() {
  const [filters, setFilters] = useState<Filters>(read)

  useEffect(() => {
    write(filters)
  }, [filters])

  // Back/forward still work, because the URL is the source of truth.
  useEffect(() => {
    const onPop = () => setFilters(read())
    window.addEventListener("popstate", onPop)
    return () => window.removeEventListener("popstate", onPop)
  }, [])

  /** Toggle: picking the value that is already set clears it, so the same
   * click that drilled in is the one that comes back out. */
  const toggle = useCallback((facet: Facet, value: string) => {
    setFilters((f) => ({ ...f, [facet]: f[facet] === value ? undefined : value }))
  }, [])

  const setDays = useCallback((days: number) => {
    setFilters((f) => ({ ...f, days }))
  }, [])

  const clear = useCallback(() => {
    setFilters((f) => ({ days: f.days }))
  }, [])

  const active = FACETS.filter((k) => filters[k]).map((k) => ({
    facet: k,
    value: filters[k] as string,
  }))

  return { filters, toggle, setDays, clear, active }
}
