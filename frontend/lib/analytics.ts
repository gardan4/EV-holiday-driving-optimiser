/**
 * First-party usage counting.
 *
 * There is no third-party script here and no CSP change behind it — these are
 * ordinary POSTs to our own API, already allowed by `connect-src`. The server
 * (src/app/api/events.py) is what decides what gets stored: it rewrites the
 * path to a route pattern and reduces the referrer to a bare host, so no trip
 * id and no full URL ever lands in the table.
 *
 * We normalise the path here as well. Belt and braces — the id should not be
 * on the wire in the first place, not merely discarded on arrival.
 *
 * Three rules this module keeps:
 *  - It never throws. Analytics failing must never break a page. Every call is
 *    fire-and-forget with the rejection swallowed.
 *  - It never blocks. No await on the way to anything the user is waiting for.
 *  - It honours Global Privacy Control and Do Not Track. Someone who has said
 *    "don't count me" is not counted, and that is worth more than the row.
 */

import { API_URL } from "./api"

export type EventName =
  | "page_view"
  | "plan_submitted"
  | "trip_planned"
  | "plan_failed"
  | "drive_started"

/** GPC is the one with legal weight in some jurisdictions; DNT is honoured on
 *  the same principle even though most sites stopped bothering. */
function optedOut(): boolean {
  if (typeof navigator === "undefined") return true
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean; doNotTrack?: string }
  return nav.globalPrivacyControl === true || nav.doNotTrack === "1"
}

/** Strip the query string and replace ids with `:id`, so a trip's share token
 *  never leaves the browser inside an analytics call. */
export function routePattern(pathname: string): string {
  const clean = pathname.split(/[?#]/)[0] || "/"
  const parts = clean.split("/").map((seg) => {
    const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(seg)
    const isOpaque = /^(?=.*\d)[A-Za-z0-9_-]{12,}$/.test(seg)
    return isUuid || isOpaque ? ":id" : seg
  })
  const joined = parts.join("/") || "/"
  return joined.length > 1 ? joined.replace(/\/$/, "") : joined
}

export function track(name: EventName, pathname?: string): void {
  if (typeof window === "undefined" || optedOut()) return

  const path = routePattern(pathname ?? window.location.pathname)
  // Only send the referrer on a page view, and only when it is external — an
  // internal one is just the previous page of the same visit, which we have
  // already counted.
  let referrer: string | undefined
  if (name === "page_view" && document.referrer) {
    try {
      const host = new URL(document.referrer).hostname
      if (host !== window.location.hostname) referrer = document.referrer
    } catch {
      /* an unparseable referrer is not worth a thought */
    }
  }

  // keepalive: a page_view fired as the user navigates away would otherwise be
  // cancelled with the document.
  fetch(`${API_URL}/api/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, path, referrer }),
    keepalive: true,
  }).catch(() => {
    /* counting is best-effort by design */
  })
}
