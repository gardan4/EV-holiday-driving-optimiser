"use client"

/**
 * "Take the whole thing to Google Maps."
 *
 * The itinerary's per-stop links are for reading a plan; this is for driving
 * one. It hands Maps the route with every charging stop already in it, so the
 * navigation the car follows is the journey this app planned rather than a
 * straight shot to the destination that ignores where the charging is.
 *
 * Two shapes, decided by the plan rather than by taste:
 *
 * - **One leg** — an ordinary link. The overwhelming majority of trips.
 * - **More than one** — a disclosure listing them. Google takes nine waypoints
 *   per route (`lib/maps.ts`), so a long plan is genuinely several
 *   navigations, and the alternative to saying so is a button that quietly
 *   drops the last fifteen stops. Opening them all at once is not on the table
 *   either: a browser blocks the second window, and a phone would end up with
 *   Maps holding whichever one won.
 */

import { ChevronDown, ExternalLink, Route } from "lucide-react"
import { track } from "@/lib/analytics"
import { MAX_WAYPOINTS, MapsLeg } from "@/lib/maps"

type Tone = "solid" | "outline"

const TONE: Record<Tone, string> = {
  solid:
    "bg-brand-600 text-white hover:bg-brand-700 border border-transparent",
  outline:
    "border border-ink-200 bg-white text-ink-700 hover:border-brand-300 hover:text-brand-700",
}

export default function MapsRouteButton({
  legs,
  label = "Route in Maps",
  tone = "outline",
  full = false,
}: {
  legs: MapsLeg[]
  label?: string
  tone?: Tone
  /** Fill the row — the phone screens, where this is a primary action. */
  full?: boolean
}) {
  if (legs.length === 0) return null

  const base = `inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-sm font-semibold transition-colors ${TONE[tone]} ${
    full ? "w-full justify-center" : ""
  }`

  if (legs.length === 1) {
    return (
      <a
        href={legs[0].url}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => track("maps_route_opened")}
        className={base}
      >
        <Route className="h-4 w-4" />
        {label}
        <ExternalLink className="h-3 w-3 opacity-60" />
      </a>
    )
  }

  return (
    <details className={`disclosure group relative ${full ? "w-full" : ""}`}>
      <summary className={`cursor-pointer list-none marker:content-[''] ${base}`}>
        <Route className="h-4 w-4" />
        {label}
        <span className="font-normal opacity-70">· {legs.length} parts</span>
        <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
      </summary>

      {/* Absolute on a wide screen so the header row doesn't grow under it;
          static on a phone, where a floating panel would hang off the edge. */}
      <div className="z-20 mt-2 w-full rounded-2xl border border-ink-100 bg-white p-3 shadow-lg sm:absolute sm:right-0 sm:w-80">
        <p className="text-xs leading-relaxed text-ink-500">
          Google Maps takes {MAX_WAYPOINTS} stops between a start and a finish,
          so this plan goes over in {legs.length} parts. Open the next one when
          you get there.
        </p>
        <ol className="mt-2 space-y-1.5">
          {legs.map((leg) => (
            <li key={leg.index}>
              <a
                href={leg.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={() => track("maps_route_opened")}
                className="flex items-baseline gap-2 rounded-xl border border-ink-100 px-3 py-2 text-sm text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700"
              >
                <span className="shrink-0 font-mono text-[11px] font-bold text-ink-400">
                  {leg.index}/{leg.total}
                </span>
                <span className="min-w-0 flex-1 truncate">
                  {leg.from} → {leg.to}
                </span>
                <span className="shrink-0 text-[11px] text-ink-400">
                  {leg.waypoints === 0
                    ? "direct"
                    : `+${leg.waypoints} ${leg.waypoints === 1 ? "stop" : "stops"}`}
                </span>
              </a>
            </li>
          ))}
        </ol>
      </div>
    </details>
  )
}
