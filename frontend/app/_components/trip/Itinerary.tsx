"use client"

import { BatteryCharging, Car, ExternalLink, Flag, MapPin } from "lucide-react"
import { SpeedResult, Trip } from "@/lib/client"
import { clockAt, fmtDuration, fmtKm, mapsLink } from "@/lib/format"

interface ItineraryProps {
  trip: Trip
  result: SpeedResult
  highlightStop?: string | null
  onHoverStop?: (chargerId: string | null) => void
}

/**
 * Cula-panel-style stacked leg cards: Depart → (Drive / Charge)* → Arrive.
 * Each charge stop links out to Google Maps — there is no in-app map in v1.
 */
export default function Itinerary({ trip, result, highlightStop, onHoverStop }: ItineraryProps) {
  const departIso = trip.request.departure_iso
  const stops = result.stops

  const legs: React.ReactNode[] = []

  legs.push(
    <LegCard key="depart" icon={<Car className="h-4 w-4" />} tint="ink">
      <LegHeader
        title={`Depart ${shortLabel(trip.request.origin.label)}`}
        badge={clockAt(departIso, 0)}
      />
      <p className="text-xs text-ink-500">
        Battery at {Math.round(trip.request.depart_soc)}% · cruise {result.speed_kph} km/h
      </p>
    </LegCard>
  )

  stops.forEach((stop, i) => {
    const prevOffset = i === 0 ? 0 : stops[i - 1].offset_m
    legs.push(
      <DriveLeg
        key={`drive-${i}`}
        km={stop.offset_m - prevOffset}
        from={i === 0 ? 0 : stops[i - 1].arrive_min + minutesAtStop(stops[i - 1])}
        to={stop.arrive_min}
      />
    )
    legs.push(
      <LegCard
        key={stop.charger_id + i}
        icon={<BatteryCharging className="h-4 w-4" />}
        tint="brand"
        highlighted={highlightStop === stop.charger_id}
        onMouseEnter={() => onHoverStop?.(stop.charger_id)}
        onMouseLeave={() => onHoverStop?.(null)}
      >
        <LegHeader
          title={stop.name}
          badge={`+${Math.round(stop.charge_min)} min`}
          badgeTint="brand"
        />
        <p className="text-xs text-ink-500">
          {stop.operator && <span>{stop.operator} · </span>}
          {Math.round(stop.power_kw)} kW · arrive {clockAt(departIso, stop.arrive_min)}
        </p>
        <div className="mt-2 flex items-center justify-between gap-2">
          <SocDelta from={stop.arrive_soc} to={stop.depart_soc} />
          <a
            href={mapsLink(stop.lat, stop.lon)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex shrink-0 items-center gap-1 rounded-lg border border-ink-200 px-2 py-1 text-[11px] font-medium text-ink-500 transition-colors hover:border-brand-300 hover:text-brand-700"
          >
            <MapPin className="h-3 w-3" />
            Maps
            <ExternalLink className="h-2.5 w-2.5" />
          </a>
        </div>
      </LegCard>
    )
  })

  const lastLeave =
    stops.length === 0 ? 0 : stops[stops.length - 1].arrive_min + minutesAtStop(stops[stops.length - 1])
  legs.push(
    <DriveLeg
      key="drive-last"
      km={result.timeline.length ? result.timeline[result.timeline.length - 1].dist_m - (stops[stops.length - 1]?.offset_m ?? 0) : 0}
      from={lastLeave}
      to={result.total_min ?? 0}
    />
  )
  legs.push(
    <LegCard key="arrive" icon={<Flag className="h-4 w-4" />} tint="ink">
      <LegHeader
        title={`Arrive ${shortLabel(trip.request.dest.label)}`}
        badge={clockAt(departIso, result.total_min ?? 0)}
      />
      <p className="text-xs text-ink-500">
        Battery at {Math.round(result.timeline[result.timeline.length - 1]?.soc ?? 0)}% · total{" "}
        {fmtDuration(result.total_min ?? 0)}
      </p>
    </LegCard>
  )

  // A 900 km European trip is 4 stops and reads fine as one column. A US
  // interstate run is 24, and an unbounded column pushes everything below the
  // itinerary — the charts, the assumptions — thousands of pixels down the
  // page. Past a handful of stops it gets its own scroll box instead.
  if (stops.length <= SCROLL_AFTER_STOPS) {
    return <div className="space-y-2">{legs}</div>
  }

  return (
    <div className="relative min-h-0 flex-1">
      <div
        // Two different jobs. On a phone the column is all there is, so a fixed
        // cap keeps the charts below it reachable. From `lg` the aside is a
        // flex column filling the grid row, so `h-full` takes the height the
        // charts beside it already set and the cap has to get out of the way.
        // `overscroll-contain` so reaching the end of the list doesn't hand the
        // scroll back to the page mid-flick, which on a phone reads as the
        // itinerary "jumping".
        className="h-full max-h-[34rem] space-y-2 overflow-y-auto overscroll-contain rounded-2xl pr-1 lg:max-h-none"
        tabIndex={0}
        role="group"
        aria-label={`${stops.length} charging stops, scrollable`}
      >
        {legs}
      </div>
      {/* Says "there is more below" without spending a row on saying it. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-x-0 bottom-0 h-10 rounded-b-2xl bg-gradient-to-t from-background to-transparent"
      />
    </div>
  )
}

/** Above this many charging stops the itinerary becomes its own scroll area. */
const SCROLL_AFTER_STOPS = 6

function minutesAtStop(stop: { charge_min: number }): number {
  // Matches the simulator: charge + 5 min plug-in overhead (+ detour, folded
  // into arrive_min of the next leg via total timings; close enough for display).
  return stop.charge_min + 5
}

function shortLabel(label: string): string {
  return label.split(",")[0]
}

function LegCard({
  icon,
  tint,
  highlighted,
  children,
  onMouseEnter,
  onMouseLeave,
}: {
  icon: React.ReactNode
  tint: "ink" | "brand"
  highlighted?: boolean
  children: React.ReactNode
  onMouseEnter?: () => void
  onMouseLeave?: () => void
}) {
  return (
    <div
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className={`rounded-2xl border bg-white p-3.5 transition-all ${
        highlighted
          ? "border-brand-400 shadow-md shadow-brand-500/10 ring-2 ring-brand-200"
          : "border-ink-100 shadow-sm"
      }`}
    >
      <div className="flex gap-3">
        <div
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${
            tint === "brand" ? "bg-brand-50 text-brand-600" : "bg-ink-100 text-ink-700"
          }`}
        >
          {icon}
        </div>
        <div className="min-w-0 flex-1">{children}</div>
      </div>
    </div>
  )
}

function LegHeader({
  title,
  badge,
  badgeTint = "ink",
}: {
  title: string
  badge: string
  badgeTint?: "ink" | "brand"
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <h3 className="truncate text-sm font-semibold text-ink-900">{title}</h3>
      <span
        className={`shrink-0 rounded-md px-1.5 py-0.5 font-mono text-xs font-bold ${
          badgeTint === "brand" ? "bg-brand-50 text-brand-700" : "bg-ink-100 text-ink-700"
        }`}
      >
        {badge}
      </span>
    </div>
  )
}

function DriveLeg({ km, from, to }: { km: number; from: number; to: number }) {
  return (
    <div className="flex items-center gap-2 py-0.5 pl-7 text-[11px] text-ink-400">
      <span className="h-4 w-px bg-ink-200" />
      drive {fmtKm(km)} · {fmtDuration(Math.max(to - from, 0))}
    </div>
  )
}

function SocDelta({ from, to }: { from: number; to: number }) {
  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5" aria-label={`Charge from ${Math.round(from)} to ${Math.round(to)} percent`}>
      <span className="font-mono text-xs font-semibold text-ink-700">{Math.round(from)}%</span>
      <div className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-ink-100">
        <div
          className="absolute inset-y-0 rounded-full bg-brand-400"
          style={{ left: `${from}%`, width: `${Math.max(to - from, 0)}%` }}
        />
      </div>
      <span className="font-mono text-xs font-semibold text-brand-700">{Math.round(to)}%</span>
    </div>
  )
}
