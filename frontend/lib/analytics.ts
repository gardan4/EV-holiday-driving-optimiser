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
 *
 * Counting runs on legitimate interest rather than a consent banner, which is
 * only defensible if saying no is easy and actually works. GPC and DNT were the
 * whole of that for a while, and they are settings most people have never heard
 * of. `setCounting` is the explicit control: it stops the events, deletes the
 * persistent id on the spot rather than merely leaving it unsent, and survives
 * a reload. See docs/LIA-CLIENT-ID.md.
 */

import { API_URL } from "./api"
import { uuidV4 } from "./uuid"

/** What the browser is allowed to report.
 *
 *  `plan_failed` is deliberately absent: the server records it, with the
 *  reason it actually failed for, and the events endpoint refuses it from a
 *  client. Firing it from here too would double every failure. */
export type EventName =
  | "page_view"
  | "plan_submitted"
  | "trip_planned"
  | "drive_started"

const CLIENT_ID_KEY = "evtrip.cid"
const CAMPAIGN_KEY = "evtrip.src"
const OPTOUT_KEY = "evtrip.optout"

/** GPC is the one with legal weight in some jurisdictions; DNT is honoured on
 *  the same principle even though most sites stopped bothering. */
function browserOptOut(): boolean {
  if (typeof navigator === "undefined") return true
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean; doNotTrack?: string }
  return nav.globalPrivacyControl === true || nav.doNotTrack === "1"
}

/** The choice made on this site, as opposed to the one made in browser settings.
 *  Storage throws in some private windows, and a browser that will not tell us
 *  what was chosen is one we should not be counting. */
function storedOptOut(): boolean {
  try {
    return window.localStorage.getItem(OPTOUT_KEY) === "1"
  } catch {
    return true
  }
}

function optedOut(): boolean {
  return browserOptOut() || storedOptOut()
}

/** Why counting is off, when it is off. The toggle needs the difference: a
 *  visitor whose browser sends GPC cannot be talked out of it by a switch on
 *  our page, so the control says so instead of pretending to work. */
export type CountingState = "on" | "off" | "browser"

export function countingState(): CountingState {
  if (typeof window === "undefined") return "off"
  if (browserOptOut()) return "browser"
  return storedOptOut() ? "off" : "on"
}

/**
 * Turn counting on or off for this browser.
 *
 * Switching off deletes the persistent id immediately. Leaving it in place and
 * merely declining to send it would mean the opt-out could be undone by a bug,
 * and it would make the privacy page's claim about clearing site data narrower
 * than it reads.
 */
export function setCounting(on: boolean): void {
  try {
    if (on) {
      window.localStorage.removeItem(OPTOUT_KEY)
      return
    }
    window.localStorage.setItem(OPTOUT_KEY, "1")
    window.localStorage.removeItem(CLIENT_ID_KEY)
    window.sessionStorage.removeItem(CAMPAIGN_KEY)
  } catch {
    /* nothing to do: a browser that cannot store the choice is already treated
       as opted out by `storedOptOut` */
  }
}

/**
 * The `?src=` tag the visit started with, remembered for the session.
 *
 * Put `?src=r-electricvehicles` on the link you post and this reports it back
 * on every event of that visit — not just the landing page. That is the whole
 * point: the interesting number is not how many people a subreddit sent, it is
 * how many of them went on to plan a trip, and that happens two pages later.
 *
 * Needed because the referrer cannot answer it. Reddit's mobile app, where
 * most of that traffic lives, sends no referrer at all, so an untagged launch
 * shows its best channel as "direct".
 *
 * `sessionStorage`, not `localStorage`: the tag describes how *this visit*
 * started. Kept forever it would attribute a trip planned next month to a link
 * clicked today, which is a different and much less true claim.
 *
 * `utm_source` is read as a fallback so a link pasted from anywhere else still
 * lands somewhere, but the server keeps only well-formed short slugs.
 */
function campaign(): string | undefined {
  try {
    const params = new URLSearchParams(window.location.search)
    const fresh = params.get("src") || params.get("utm_source")
    if (fresh) {
      window.sessionStorage.setItem(CAMPAIGN_KEY, fresh)
      return fresh
    }
    return window.sessionStorage.getItem(CAMPAIGN_KEY) || undefined
  } catch {
    return undefined
  }
}

/**
 * A random id for this browser, so "did anyone come back?" has an answer.
 *
 * The server used to be the only thing telling two visitors apart, by hashing
 * the IP with a salt that changed at midnight — which counts a day's visitors
 * honestly and makes retention structurally unanswerable. This is the other
 * half: a v4 UUID that the browser generates, keeps, and can throw away.
 *
 * It is generated HERE rather than derived from anything about you, which is
 * what makes it clearable: the privacy page can honestly say that clearing
 * site data ends it, and that is only true because nothing regenerates it from
 * your IP or your user agent. The server stores a hash of it, never this value.
 *
 * Never created for someone who has opted out — `track()` and
 * `clientIdHeaders()` both return before this is reached, so GPC, DNT and the
 * on-page toggle all mean no id is minted at all, not merely unsent.
 *
 * Returns null rather than throwing when storage is unavailable (private
 * windows, storage disabled, quota). Retention is a nice thing to know; it is
 * not worth a broken page.
 */
function clientId(): string | null {
  try {
    const existing = window.localStorage.getItem(CLIENT_ID_KEY)
    if (existing) return existing
    const fresh = uuidV4()
    window.localStorage.setItem(CLIENT_ID_KEY, fresh)
    return fresh
  } catch {
    return null
  }
}

/** The header every counted request carries, when there is one to carry.
 *  Exported so the trip planner sends the same id the events do — two
 *  different ids would split one person across both tables. */
export function clientIdHeaders(): Record<string, string> {
  if (typeof window === "undefined" || optedOut()) return {}
  const id = clientId()
  return id ? { "X-Client-Id": id } : {}
}

/** Strip the query string and replace ids with `:id`, so a trip's share token
 *  never leaves the browser inside an analytics call. */
export function routePattern(pathname: string): string {
  const clean = pathname.split(/[?#]/)[0] || "/"
  // A public username page. Collapsed before the id rules, exactly as the
  // server does it — a username is the one handle here that could be somebody's
  // real name, and it has no business travelling to the events endpoint.
  if (/^\/u\/[a-z0-9-]+\/?$/i.test(clean)) return "/u/:username"
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
  // Width only, and only on a page view — the server bands it to a breakpoint
  // before storing, so what lands in the table is "641–768" and not a number
  // narrow enough to recognise a window by. Height adds nothing: the question
  // is whether the layout has room across, not down.
  const viewport_w = name === "page_view" ? Math.round(window.innerWidth) : undefined

  fetch(`${API_URL}/api/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...clientIdHeaders() },
    body: JSON.stringify({ name, path, referrer, viewport_w, campaign: campaign() }),
    keepalive: true,
  }).catch(() => {
    /* counting is best-effort by design */
  })
}
