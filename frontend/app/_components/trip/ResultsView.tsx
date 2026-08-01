"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { Check, Link2, RotateCcw } from "lucide-react"
import { toast } from "sonner"
import { SpeedResult, Trip } from "@/lib/client"
import { clockAt, fmtDuration, fmtHm, fmtKm } from "@/lib/format"
import BatteryChart from "./BatteryChart"
import Itinerary from "./Itinerary"
import JourneyHero from "./JourneyHero"
import SpeedChart from "./SpeedChart"

/**
 * Results page: the journey band you scroll through sits on top, and the
 * analysis that backs it up reads normally underneath.
 */
export default function ResultsView({ trip }: { trip: Trip }) {
  const { result } = trip
  const [selectedSpeed, setSelectedSpeed] = useState<number>(
    result.optimum_speed ?? result.speeds.find((s) => s.feasible)?.speed_kph ?? 0
  )
  const [hoverStop, setHoverStop] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [raceSpeed, setRaceSpeed] = useState<number | null>(null)

  const selected = useMemo(
    () => result.speeds.find((s) => s.speed_kph === selectedSpeed) ?? null,
    [result.speeds, selectedSpeed]
  )
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

  const vsBaseline =
    slowBaseline && slowBaseline.total_min != null && selected.total_min != null
      ? slowBaseline.total_min - selected.total_min
      : null

  return (
    <div>
      <JourneyHero
        trip={trip}
        result={selected}
        race={raceRun}
        raceSpeed={raceSpeed}
        feasibleSpeeds={feasibleSpeeds}
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
          </div>
        </div>

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
            sub={`${selected.n_stops} stop${selected.n_stops === 1 ? "" : "s"}`}
          />
          <Stat
            label={vsBaseline != null && slowBaseline ? `vs ${slowBaseline.speed_kph} km/h` : "vs plan"}
            value={
              vsBaseline != null
                ? `${vsBaseline > 0 ? "−" : "+"}${fmtDuration(Math.abs(vsBaseline))}`
                : "—"
            }
            accent={vsBaseline != null && vsBaseline > 0}
            sub={vsBaseline != null && vsBaseline > 0 ? "earlier at the chalet" : "slower overall"}
          />
        </div>

        {/* Charts + itinerary */}
        <div className="mt-4 grid gap-4 lg:grid-cols-[1fr_340px]">
          <div className="space-y-4">
            <SpeedChart
              speeds={result.speeds}
              optimumSpeed={result.optimum_speed}
              selectedSpeed={selectedSpeed}
              onSelect={setSelectedSpeed}
            />
            <SpeedPills
              speeds={result.speeds}
              optimumSpeed={result.optimum_speed}
              selectedSpeed={selectedSpeed}
              onSelect={setSelectedSpeed}
            />
            <BatteryChart
              result={selected}
              departureIso={trip.request.departure_iso}
              targetSoc={trip.request.target_soc}
            />
            {best && selected.speed_kph !== best.speed_kph && (
              <button
                onClick={() => setSelectedSpeed(best.speed_kph)}
                className="w-full rounded-xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm font-medium text-brand-800 transition-colors hover:bg-brand-100"
              >
                Jump to the fastest plan — {best.speed_kph} km/h, arrives{" "}
                {clockAt(trip.request.departure_iso, best.total_min ?? 0)}
              </button>
            )}
          </div>
          <aside aria-label="Itinerary">
            <h2 className="mb-2 px-1 font-display text-base font-semibold text-ink-900">
              Itinerary at {selected.speed_kph} km/h
            </h2>
            <Itinerary
              trip={trip}
              result={selected}
              highlightStop={hoverStop}
              onHoverStop={setHoverStop}
            />
          </aside>
        </div>

        <details className="mt-6 rounded-2xl border border-ink-100 bg-white px-5 py-4 text-sm text-ink-500 open:pb-5">
          <summary className="cursor-pointer select-none font-semibold text-ink-700">
            What this simulation assumes
          </summary>
          <ul className="mt-3 list-disc space-y-1.5 pl-5 leading-relaxed">
            <li>
              Your cruise speed applies on motorway stretches up to each country&apos;s legal cap
              (130 in AT/NL, derestricted German autobahn where the map data suggests it); everywhere
              else the road&apos;s normal driving speed governs. No live traffic — planned for quiet
              roads and overnight departures.
            </li>
            <li>
              Consumption is a per-car fit from public range tests (with your conditions multiplier);
              charging follows the car&apos;s measured fast-charge curve, capped by each site&apos;s
              power. Real cars vary a few percent either way.
            </li>
            <li>
              Every stop includes 5 minutes of plug-in overhead plus an estimated detour off the
              route, so arrival times at chargers are ±5 minutes.
            </li>
            <li>
              Chargers come from OpenChargeMap (community data) filtered to operational CCS sites of
              100 kW and up — glance at the operator and Maps link before counting on a specific one.
            </li>
            <li>Departure time shifts the clock, not the plan — there is no traffic model yet.</li>
          </ul>
        </details>
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
