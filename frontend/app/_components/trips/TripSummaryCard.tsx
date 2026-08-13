/**
 * One planned trip, as it appears in a list.
 *
 * Shared by the drawer and the public `/u/[username]` page so the two cannot
 * drift into showing different things about the same trip. Deliberately a
 * server-safe component — no hooks, no browser APIs — because the public page
 * renders it on the server.
 *
 * The place labels arrive already reduced to a locality by the API. Nothing
 * here truncates anything: a summary that looked short because the CSS clipped
 * it would still have shipped the address to every reader.
 *
 * `action` is an escape hatch for the owner's own list, which carries a delete
 * control. It is a slot rather than a prop like `onDelete` so this component
 * stays hook-free and server-renderable — the interactive part is passed in
 * already built. The public page passes nothing, which is why a stranger's
 * profile has no controls on it.
 */

import type { ReactNode } from "react"
import Link from "next/link"
import { fmtDeparture } from "@/lib/format"
import type { TripSummary } from "@/lib/client"

export default function TripSummaryCard({
  trip,
  action,
}: {
  trip: TripSummary
  action?: ReactNode
}) {
  return (
    <li className="relative">
      <Link
        href={`/trip/${trip.id}`}
        className="block rounded-2xl border border-ink-100 bg-white p-4 shadow-sm transition hover:border-brand-300 hover:shadow-md"
      >
        {/* Padded so a long "Origin → Destination" cannot run under the action
            sitting in the corner above it. */}
        <p className={`font-display text-base font-semibold leading-snug text-ink-900 ${action ? "pr-8" : ""}`}>
          {trip.origin_label} <span className="text-brand-500">→</span>{" "}
          {trip.dest_label}
        </p>
        <p className="mt-1 text-xs text-ink-400">
          {trip.departure_iso
            ? fmtDeparture(trip.departure_iso)
            : "Departure not recorded"}
        </p>
        <dl className="mt-3 grid grid-cols-3 gap-2">
          <Stat label="Distance" value={`${trip.distance_km} km`} />
          <Stat
            label="Best speed"
            value={
              trip.optimum_speed_kph ? `${Math.round(trip.optimum_speed_kph)} km/h` : "—"
            }
          />
          <Stat
            label="Stops"
            value={trip.n_stops === null ? "—" : String(trip.n_stops)}
          />
        </dl>
        <p className="mt-2 truncate text-xs text-ink-400">{trip.vehicle_label}</p>
      </Link>

      {/* Outside the Link, not inside it: a button nested in an anchor still
          navigates, so deleting would open the trip it just deleted. */}
      {action && <div className="absolute right-2 top-2">{action}</div>}
    </li>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-[0.65rem] uppercase tracking-wide text-ink-400">{label}</dt>
      <dd className="font-mono text-sm text-ink-700">{value}</dd>
    </div>
  )
}
