"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { Check, Link2, RotateCcw } from "lucide-react"
import { toast } from "sonner"
import { LiveRun, SpeedResult, Trip } from "@/lib/client"
import { nearOptimalBand } from "@/lib/verdict"
import { useAnchoredSelection } from "@/lib/useAnchoredSelection"
import { clockAt, fmtDuration, fmtHm, fmtKm } from "@/lib/format"
import Assumptions from "./Assumptions"
import BatteryChart from "./BatteryChart"
import CostChart from "./CostChart"
import Itinerary from "./Itinerary"
import JourneyHero from "./JourneyHero"
import SpeedChart from "./SpeedChart"
import DeleteTrip from "./DeleteTrip"
import { DriveThisButton, SendToPhonePanel } from "./live/SendToPhone"
import DriveBanner from "./live/DriveBanner"

/**
 * Results page: the journey band you scroll through sits on top, and the
 * analysis that backs it up reads normally underneath.
 */
export default function ResultsView({
  trip,
  live = null,
}: {
  trip: Trip
  /** The most recent drive of this trip, if any. Fetched server-side so a
   *  passenger opening the share link mid-journey sees it immediately. */
  live?: LiveRun | null
}) {
  const { result } = trip
  const [selectedSpeed, setSelectedSpeed] = useState<number>(
    result.optimum_speed ?? result.speeds.find((s) => s.feasible)?.speed_kph ?? 0
  )
  const [hoverStop, setHoverStop] = useState<string | null>(null)
  // Lifted out of the button so the panel can render full-width below the
  // header instead of as a flex sibling of Share and New trip.
  const [sendOpen, setSendOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [raceSpeed, setRaceSpeed] = useState<number | null>(null)

  const selected = useMemo(
    () => result.speeds.find((s) => s.speed_kph === selectedSpeed) ?? null,
    [result.speeds, selectedSpeed]
  )
  const band = useMemo(() => nearOptimalBand(result.speeds), [result.speeds])

  // Picking a different speed can change the itinerary from 4 stops to 9,
  // which changes the page height by ~440px. If the reader is scrolled down,
  // the browser clamps their scroll position and the page appears to jump.
  // Keep whatever they clicked visually still instead.
  const { anchorRef, select } = useAnchoredSelection(setSelectedSpeed)

  const best = useMemo(
    () => result.speeds.find((s) => s.speed_kph === result.optimum_speed) ?? null,
    [result.speeds, result.optimum_speed]
  )
  const slowBaseline = useMemo(() => {
    // The friend's plan: 100 km/h if it was simulated, else the slowest that
    // was — the comparison this whole app exists to settle.
    const feasible = result.speeds.filter((s) => s.feasible)
    return feasible.find((s) => s.speed_kph === 100) ?? feasible[0] ?? null
  }, [result.speeds])

  const raceRun = useMemo(
    () =>
      raceSpeed == null
        ? null
        : (result.speeds.find((s) => s.feasible && s.speed_kph === raceSpeed) ?? null),
    [result.speeds, raceSpeed]
  )
  const feasibleSpeeds = useMemo(
    () => result.speeds.filter((s) => s.feasible).map((s) => s.speed_kph),
    [result.speeds]
  )

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href)
      setCopied(true)
      toast.success("Trip link copied — send it to your co-driver")
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("Could not copy the link")
    }
  }

  if (!selected || !selected.feasible) {
    return <p className="mx-auto max-w-6xl px-4 py-10 text-ink-500">No feasible plan at this speed.</p>
  }

  // Minutes this plan saves against the friend's 100 km/h plan. Positive =
  // arrives earlier. Anything under a minute is a tie, not a win — the DP plans
  // on a 2.5% SoC grid, so differences that small are quantisation, not driving.
  const vsBaseline =
    slowBaseline && slowBaseline.total_min != null && selected.total_min != null
      ? slowBaseline.total_min - selected.total_min
      : null
  const isBaseline = slowBaseline?.speed_kph === selected.speed_kph
  const vsTie = vsBaseline != null && Math.abs(vsBaseline) < 1

  return (
    <div>
      <JourneyHero
        trip={trip}
        result={selected}
        race={raceRun}
        raceSpeed={raceSpeed}
        feasibleSpeeds={feasibleSpeeds}
        onSpeedChange={select}
        onRaceSpeedChange={setRaceSpeed}
        hoverStop={hoverStop}
        onHoverStop={setHoverStop}
      />

      <div className="mx-auto max-w-6xl px-4 pb-16 sm:px-6">
        {/* Header + share */}
        <div className="flex flex-wrap items-center justify-between gap-3 py-6">
          <div className="min-w-0">
            <h1 className="truncate font-display text-xl font-bold text-ink-900 sm:text-2xl">
              {short(trip.request.origin.label)} → {short(trip.request.dest.label)}
            </h1>
            <p className="mt-0.5 text-sm text-ink-500">
              {fmtKm(result.total_dist_m)} · {result.vehicle.make} {result.vehicle.model}{" "}
              {result.vehicle.variant} · leaves {clockAt(trip.request.departure_iso, 0)}
            </p>
          </div>
          <div className="flex items-center gap-2">
            {/* A drive already under way replaces the invitation to start one —
                the banner below is the way in. */}
            {live?.status !== "active" && (
              <DriveThisButton
                tripId={trip.id}
                onShowQr={() => setSendOpen(true)}
              />
            )}
            <button
              onClick={copyLink}
              className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3.5 py-2 text-sm font-medium text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700"
            >
              {copied ? <Check className="h-4 w-4 text-brand-600" /> : <Link2 className="h-4 w-4" />}
              Share
            </button>
            <Link
              href="/"
              className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3.5 py-2 text-sm font-medium text-ink-700 transition-colors hover:border-ink-300"
            >
              <RotateCcw className="h-4 w-4" />
              New trip
            </Link>
            <DeleteTrip tripId={trip.id} />
          </div>
        </div>

        {sendOpen && (
          <SendToPhonePanel tripId={trip.id} onClose={() => setSendOpen(false)} />
        )}

        <DriveBanner tripId={trip.id} live={live} />

        {/* Hero stats for the selected speed */}
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat
            label="cruise at"
            value={`${selected.speed_kph}`}
            unit="km/h"
            accent={selected.speed_kph === result.optimum_speed}
            sub={selected.speed_kph === result.optimum_speed ? "the fastest plan" : "your pick"}
          />
          <Stat
            label="arrive at"
            value={clockAt(trip.request.departure_iso, selected.total_min ?? 0)}
            sub={`door to door ${fmtHm(selected.total_min ?? 0)}`}
          />
          <Stat
            label="charging"
            value={fmtDuration(selected.charge_min ?? 0)}
            sub={
              `${selected.n_stops} stop${selected.n_stops === 1 ? "" : "s"}` +
              (selected.rest_min > 0 ? ` · +${Math.round(selected.rest_min)} min break` : "") +
              (selected.cost_eur > 0 ? ` · €${selected.cost_eur.toFixed(2)}` : "")
            }
          />
          <Stat
            label={slowBaseline ? `vs ${slowBaseline.speed_kph} km/h` : "vs plan"}
            value={
              vsBaseline == null
                ? "—"
                : isBaseline || vsTie
                  ? "±0"
                  : `${vsBaseline > 0 ? "−" : "+"}${fmtDuration(Math.abs(vsBaseline))}`
            }
            accent={vsBaseline != null && !isBaseline && !vsTie && vsBaseline > 0}
            sub={
              vsBaseline == null
                ? undefined
                : isBaseline
                  ? "this is that plan"
                  : vsTie
                    ? "the same, to the minute"
                    : vsBaseline > 0
                      ? "earlier at the chalet"
                      : "slower overall"
            }
          />
        </div>

        {band && !band.sharp && (
          <p className="mt-4 rounded-2xl border border-brand-200 bg-brand-50 px-4 py-3 text-sm leading-relaxed text-ink-800">
            <span className="font-semibold">
              Anything from {band.lo} to {band.hi} km/h arrives within{" "}
              {band.toleranceMin} minutes of the best.
            </span>{" "}
            The curve is flat across that range, so hold whatever is comfortable —
            sitting at {band.lo} instead of {band.best} costs you{" "}
            {band.loCostMin <= 0 ? "nothing" : `${band.loCostMin} min`}
            {band.loSavesEur > 0.5 && (
              <> and saves €{band.loSavesEur.toFixed(0)} in charging</>
            )}
            .
          </p>
        )}

        {band?.sharp && (
          <p className="mt-4 rounded-2xl border border-brand-200 bg-brand-50 px-4 py-3 text-sm leading-relaxed text-ink-800">
            <span className="font-semibold">Speed genuinely matters here.</span> No
            other cruise speed comes within {band.toleranceMin} minutes of{" "}
            {band.best} km/h.
          </p>
        )}

        {result.optimum_at_top_speed && (
          <p className="mt-4 rounded-2xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-relaxed text-amber-900">
            <span className="font-semibold">The curve never turned.</span> Under these
            assumptions the trip keeps getting shorter right up to{" "}
            {Math.round(result.vehicle.top_speed_kph)} km/h — the {result.vehicle.make}{" "}
            {result.vehicle.model}&apos;s limiter — so the theoretical optimum is faster
            than the car goes. Charging is quick enough here that speed simply wins;
            try colder conditions or a lower open-autobahn share to see it turn.
          </p>
        )}

        {/* Charts + itinerary */}
        <div
          ref={anchorRef as React.RefObject<HTMLDivElement>}
          className="mt-4 grid gap-4 lg:grid-cols-[1fr_340px]"
        >
          <div className="space-y-4">
            <SpeedChart
              speeds={result.speeds}
              optimumSpeed={result.optimum_speed}
              selectedSpeed={selectedSpeed}
              onSelect={select}
              restIntervalMin={trip.request.rest_interval_min ?? 0}
            />
            <SpeedPills
              speeds={result.speeds}
              optimumSpeed={result.optimum_speed}
              selectedSpeed={selectedSpeed}
              onSelect={select}
            />
            <CostChart
              speeds={result.speeds}
              optimumSpeed={result.optimum_speed}
              selectedSpeed={selectedSpeed}
              onSelect={select}
            />
            <BatteryChart
              result={selected}
              departureIso={trip.request.departure_iso}
              targetSoc={trip.request.target_soc}
            />
            {best && selected.speed_kph !== best.speed_kph && (
              <button
                onClick={() => select(best.speed_kph)}
                className="w-full rounded-xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm font-medium text-brand-800 transition-colors hover:bg-brand-100"
              >
                Jump to the fastest plan — {best.speed_kph} km/h, arrives{" "}
                {clockAt(trip.request.departure_iso, best.total_min ?? 0)}
              </button>
            )}
          </div>
          {/* Stretches to the row height, which the charts column sets —
              so the itinerary ends level with them instead of stopping
              short and leaving a column of dead space beside them. */}
          <aside aria-label="Itinerary" className="flex min-h-0 flex-col">
            <h2 className="mb-2 flex items-baseline justify-between gap-2 px-1 font-display text-base font-semibold text-ink-900">
              <span>Itinerary at {selected.speed_kph} km/h</span>
              {/* On a long route the list scrolls, so the count is the only
                  place the size of the plan is still visible at a glance. */}
              {selected.stops.length > 0 && (
                <span className="shrink-0 font-sans text-xs font-medium text-ink-400">
                  {selected.stops.length} {selected.stops.length === 1 ? "stop" : "stops"}
                </span>
              )}
            </h2>
            <Itinerary
              trip={trip}
              result={selected}
              highlightStop={hoverStop}
              onHoverStop={setHoverStop}
            />
          </aside>
        </div>

        <Assumptions trip={trip} />
      </div>
    </div>
  )
}

function short(label: string): string {
  return label.split(",")[0]
}

function SpeedPills({
  speeds,
  optimumSpeed,
  selectedSpeed,
  onSelect,
}: {
  speeds: SpeedResult[]
  optimumSpeed: number | null
  selectedSpeed: number
  onSelect: (speed: number) => void
}) {
  return (
    <div>
      {/* The pills, the two charts and the journey band at the top of the page
          are all one selection. Say so once, next to the plainest control. */}
      <p className="mb-1.5 px-0.5 text-xs text-ink-400">
        <span className="font-semibold text-ink-500">Cruise speed</span> — sets the
        itinerary, both charts, and the journey you scroll through at the top.
      </p>
      <div className="flex flex-wrap gap-1.5" role="radiogroup" aria-label="Cruise speed">
        {speeds.map((s) => {
          const isSelected = s.speed_kph === selectedSpeed
          const isBest = s.speed_kph === optimumSpeed
          return (
            <button
              key={s.speed_kph}
              role="radio"
              aria-checked={isSelected}
              disabled={!s.feasible}
              onClick={() => onSelect(s.speed_kph)}
              title={!s.feasible ? "Not feasible — charger gaps too large at this speed" : undefined}
              className={`rounded-lg border px-2.5 py-1.5 font-mono text-xs font-semibold transition-colors ${
                isSelected
                  ? "border-ink-900 bg-ink-900 text-white"
                  : isBest
                    ? "border-brand-400 bg-brand-50 text-brand-800 hover:bg-brand-100"
                    : s.feasible
                      ? "border-ink-200 bg-white text-ink-700 hover:border-ink-300"
                      : "cursor-not-allowed border-ink-100 bg-ink-100/50 text-ink-300 line-through"
              }`}
            >
              {s.speed_kph}
            </button>
          )
        })}
      </div>
    </div>
  )
}

function Stat({
  label,
  value,
  unit,
  sub,
  accent,
}: {
  label: string
  value: string
  unit?: string
  sub?: string
  accent?: boolean
}) {
  return (
    <div
      className={`rounded-2xl border p-4 ${
        accent ? "border-brand-200 bg-brand-50" : "border-ink-100 bg-white"
      }`}
    >
      <div className="text-[11px] font-semibold uppercase tracking-wider text-ink-400">{label}</div>
      <div className="mt-1 font-display text-2xl font-bold text-ink-900">
        {value}
        {unit && <span className="ml-1 text-sm font-semibold text-ink-500">{unit}</span>}
      </div>
      {sub && (
        <div className={`mt-0.5 text-xs ${accent ? "text-brand-700" : "text-ink-500"}`}>{sub}</div>
      )}
    </div>
  )
}
