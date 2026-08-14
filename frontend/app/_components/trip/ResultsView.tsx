"use client"

import { useMemo, useState } from "react"
import Link from "next/link"
import { Check, Link2, RotateCcw } from "lucide-react"
import { toast } from "sonner"
import { LiveRun, Trip } from "@/lib/client"
import { nearOptimalBand } from "@/lib/verdict"
import { buildVerdict } from "@/lib/summary"
import { useAnchoredSelection } from "@/lib/useAnchoredSelection"
import { clockAt, fmtDuration, fmtKm } from "@/lib/format"
import AnswerBlock from "./AnswerBlock"
import Assumptions from "./Assumptions"
import BatteryChart from "./BatteryChart"
import CostChart from "./CostChart"
import Itinerary from "./Itinerary"
import JourneyHero from "./JourneyHero"
import SpeedChart from "./SpeedChart"
import DeleteTrip from "./DeleteTrip"
import { DriveThisButton, SendToPhonePanel } from "./live/SendToPhone"
import DriveBanner from "./live/DriveBanner"
import SaveTripNudge from "../trips/SaveTripNudge"

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

  // What "Drive this" hands to the phone. Null while the reader is on the
  // optimum, which is both the common case and the one whose drive link should
  // stay bare — a speed the plan already recommends does not need saying.
  const chosenSpeed =
    selectedSpeed === result.optimum_speed ? null : selectedSpeed

  // Picking a different speed can change the itinerary from 4 stops to 9,
  // which changes the page height by ~440px. If the reader is scrolled down,
  // the browser clamps their scroll position and the page appears to jump.
  // Keep whatever they clicked visually still instead.
  const { anchorRef, select } = useAnchoredSelection(setSelectedSpeed)

  // The compact answer the hero states before anyone scrolls. Built here
  // rather than in the hero because it is the same Verdict the AnswerBlock
  // below expands on — one source, two densities.
  const heroVerdict = useMemo(() => {
    const v = buildVerdict(result, selectedSpeed)
    if (!v || v.selected.total_min == null) return null
    const arriveClock = clockAt(trip.request.departure_iso, v.selected.total_min)
    const sub =
      `arrive ${arriveClock} · ` +
      `${v.selected.n_stops} ${v.selected.n_stops === 1 ? "stop" : "stops"} · ` +
      `${fmtDuration(v.selected.charge_min ?? 0)} charging`
    if (v.baseline == null || v.savedMin == null) {
      return { headline: <>Arrive {arriveClock}</>, sub }
    }
    if (v.isBaseline) {
      return { headline: <>The {v.baseline.speed_kph} km/h plan</>, sub }
    }
    if (v.tie) {
      return {
        headline: (
          <>
            Same arrival{" "}
            <span className="font-medium text-white/60">
              as cruising {v.baseline.speed_kph} km/h
            </span>
          </>
        ),
        sub,
      }
    }
    const faster = v.savedMin > 0
    return {
      headline: (
        <>
          {fmtDuration(Math.abs(v.savedMin))} {faster ? "sooner" : "slower"}{" "}
          <span className="font-medium text-white/60">
            than cruising {v.baseline.speed_kph} km/h
          </span>
        </>
      ),
      sub,
    }
  }, [result, selectedSpeed, trip.request.departure_iso])

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
      toast.success("Trip link copied, send it to your co-driver")
      setTimeout(() => setCopied(false), 2000)
    } catch {
      toast.error("Could not copy the link")
    }
  }

  if (!selected || !selected.feasible) {
    return <p className="mx-auto max-w-6xl px-4 py-10 text-ink-500">No feasible plan at this speed.</p>
  }

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
        verdict={heroVerdict}
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
          {/* Wraps, and the secondary labels collapse to icons on a phone.
              Four side-by-side controls in a non-wrapping row is how "Drive
              this" and "New trip" ended up broken across two lines each. */}
          <div className="flex flex-wrap items-center justify-end gap-2">
            {/* A drive already under way replaces the invitation to start one —
                the banner below is the way in. */}
            {live?.status !== "active" && (
              <DriveThisButton
                tripId={trip.id}
                speedKph={chosenSpeed}
                onShowQr={() => setSendOpen(true)}
              />
            )}
            <button
              onClick={copyLink}
              className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3.5 py-2 text-sm font-medium text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700"
            >
              {copied ? <Check className="h-4 w-4 text-brand-600" /> : <Link2 className="h-4 w-4" />}
              <span className="sr-only sm:not-sr-only">Share</span>
            </button>
            <Link
              href="/"
              title="Plan a new trip"
              aria-label="Plan a new trip"
              className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3 py-2 text-sm font-medium text-ink-700 transition-colors hover:border-ink-300 sm:px-3.5"
            >
              <RotateCcw className="h-4 w-4" />
              <span className="sr-only sm:not-sr-only">New trip</span>
            </Link>
            <DeleteTrip tripId={trip.id} />
          </div>
        </div>

        {sendOpen && (
          <SendToPhonePanel
            tripId={trip.id}
            speedKph={chosenSpeed}
            onClose={() => setSendOpen(false)}
          />
        )}

        <DriveBanner tripId={trip.id} live={live} />

        {/* The answer, before the evidence */}
        <AnswerBlock trip={trip} selected={selected} band={band} onSelectSpeed={select} />

        {/* Below the answer, deliberately. This page leads with what the reader
            came for; an invitation above it would be an interstitial. */}
        <SaveTripNudge />

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
          </div>
          {/* `h-0 min-h-full` is load-bearing, not decoration. A grid row is as
              tall as its tallest item, so an uncapped itinerary sets the row
              height and drags the charts column down to match — 24 stops made
              a 4,300 px row with the charts floating in dead space at the top.
              Height 0 keeps the aside out of the row calculation; min-h-full
              then fills whatever height the charts settled on. */}
          <aside
            aria-label="Itinerary"
            className="flex min-h-0 flex-col lg:h-0 lg:min-h-full"
          >
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


