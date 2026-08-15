"use client"

/**
 * The drive, as it happens.
 *
 * Two audiences on one page. The driver's device holds a write token and is
 * posting GPS; anyone else with the link is watching. Everything below reads
 * the same state — the only difference is who can act on it.
 *
 * Two rules this screen keeps everywhere:
 *
 * - **The battery is an estimate, and never shown as a bare number.** It
 *   carries a range, it says how long since anyone confirmed it, and
 *   confirming it is one tap.
 * - **There is one arrival time on screen.** Once the journey has been
 *   re-planned, the revision is the answer and the original is history; two
 *   live ETAs that disagree make the reader distrust both.
 */

import { useCallback, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { AlertTriangle, Flag, Loader2, PlugZap } from "lucide-react"
import { toast } from "sonner"
import { LiveRun, Stop, Trip } from "@/lib/client"
import { clockAt, fmtDuration, fmtKm } from "@/lib/format"
import { remainingRouteLegs } from "@/lib/maps"
import { buildRouteIndex } from "@/lib/route"
import { useLiveRun } from "@/lib/useLiveRun"
import JourneyHero, { LiveHeroState } from "../JourneyHero"
import MapsRouteButton from "../MapsRouteButton"
import BatteryCheck from "./BatteryCheck"
import NextStopCard from "./NextStopCard"

export default function LiveView({
  trip,
  initial,
}: {
  trip: Trip
  initial: LiveRun | null
}) {
  const result = useMemo(() => {
    const speed = initial?.planned_speed_kph ?? trip.result.optimum_speed
    return (
      trip.result.speeds.find((s) => s.speed_kph === speed) ??
      trip.result.speeds.find((s) => s.feasible) ??
      trip.result.speeds[0]
    )
  }, [trip, initial])

  const routeIndex = useMemo(
    () => buildRouteIndex(trip.result.polyline, trip.result.total_dist_m),
    [trip.result.polyline, trip.result.total_dist_m]
  )

  const live = useLiveRun(trip.id, routeIndex, initial)
  const { state, run, isDriver } = live

  // Derived from the server, NOT held locally: a watcher polls `run`, and a
  // re-plan the driver made is the single most important thing to reach them.
  const revised = run?.plan ?? null

  const distM = live.localOffsetM ?? state?.offset_m ?? 0
  const finished = run?.status === "finished"

  // Which plan is in force. After a re-plan the original is a benchmark, not
  // an instruction.
  const plannedTotal = result?.total_min ?? 0
  const etaMin = revised
    ? revised.benchmark.live_total_min
    : plannedTotal + (state?.ahead_behind_min ?? 0)

  // The stops of whichever plan is in force, placed on the route. Everything
  // that names a charger reads this one list — the HUD, the Maps hand-off, the
  // next-stop card — so a re-plan moves all of them together or none.
  const planStops = useMemo(
    () => placedStops(result?.stops ?? [], revised),
    [result, revised]
  )
  const ahead = useMemo(
    () => planStops.filter((s) => s.offset_m > distM + STOP_PASSED_M),
    [planStops, distM]
  )
  // Standing at a charger, the stop that matters is the one under the car, not
  // the one after it — `ahead` has already dropped this one.
  const atChargerId = state?.at_charger_id ?? null
  const atStop = useMemo(
    () =>
      atChargerId
        ? (planStops.find((s) => s.charger_id === atChargerId) ?? null)
        : null,
    [planStops, atChargerId]
  )
  const nextStop = useMemo(() => heroStop(ahead[0] ?? null, distM), [ahead, distM])

  // Correcting the battery from the HUD. The scene is full-bleed, so on a phone
  // the check itself is below the fold — asking for it has to bring it into
  // view, or the tap looks like it did nothing.
  const batteryRef = useRef<HTMLDivElement>(null)
  const [socPrompt, setSocPrompt] = useState(0)
  const askForBattery = useCallback(() => {
    setSocPrompt((n) => n + 1)
    batteryRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })
  }, [])

  const heroLive: LiveHeroState | null = useMemo(() => {
    if (!state || !run) return null
    return {
      distM,
      soc: state.soc,
      socLabel: socLabel(state.soc, state.soc_is_measured, state.soc_uncertainty_pct),
      startedAtIso: run.started_at,
      etaMin,
      aheadBehindMin: revised ? revised.benchmark.delta_min : state.ahead_behind_min,
      socVsPlan: state.soc_vs_plan,
      stale: state.stale,
      secondsSincePing: run.seconds_since_ping ?? 0,
      nextStop,
    }
  }, [state, run, distM, etaMin, revised, nextStop])

  if (!state || !run || !heroLive) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16 text-center">
        <h1 className="font-display text-2xl font-semibold text-ink-900">
          Nobody is driving this trip yet
        </h1>
        <p className="mt-2 text-sm text-ink-500">
          Open the trip and send it to the phone that&apos;s coming along.
        </p>
        <Link
          href={`/trip/${trip.id}`}
          className="mt-6 inline-block rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-semibold text-white"
        >
          Back to the plan
        </Link>
      </div>
    )
  }

  async function doReplan() {
    try {
      const plan = await live.requestReplan()
      if (plan) {
        toast.success(
          plan.benchmark.delta_min > 1
            ? `Revised: ${fmtDuration(Math.abs(plan.benchmark.delta_min))} later than promised`
            : "Revised, you're still on for the original arrival"
        )
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not re-plan")
    }
  }

  return (
    <div className="pb-16">
      {result && (
        <JourneyHero
          trip={trip}
          result={result}
          race={null}
          raceSpeed={null}
          feasibleSpeeds={[]}
          onSpeedChange={() => {}}
          onRaceSpeedChange={() => {}}
          hoverStop={null}
          onHoverStop={() => {}}
          live={heroLive}
          onCorrectBattery={isDriver && !finished ? askForBattery : undefined}
        />
      )}

      <div className="mx-auto max-w-4xl px-4">
        <div className="mt-6 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="font-display text-xl font-semibold text-ink-900">
              {finished ? "That's the drive done" : "On the road"}
            </h1>
            <p className="text-sm text-ink-500">
              {trip.request.origin.label.split(",")[0]} →{" "}
              {trip.request.dest.label.split(",")[0]} ·{" "}
              {fmtKm(distM)} of {fmtKm(trip.result.total_dist_m)}
              {isDriver ? " · you're driving" : " · you're watching"}
            </p>
          </div>
          {!finished && isDriver && (
            <button
              onClick={() => void live.end().catch(() => toast.error("Could not finish"))}
              disabled={live.busy}
              className="inline-flex items-center gap-2 rounded-xl border border-ink-200 px-3 py-2 text-sm font-semibold text-ink-700 hover:border-ink-300 disabled:opacity-50"
            >
              <Flag className="h-4 w-4" /> Arrived
            </button>
          )}
        </div>

        {/* What happens next, before anything about the drive as a whole. The
            HUD can only carry the charger's name and its distance; the two
            numbers a driver plans a stop around — how long, and what they
            leave on — were only ever on the results page. */}
        {!finished && (
          <NextStopCard
            stop={atStop ?? ahead[0] ?? null}
            here={atStop != null}
            distM={distM}
            destLabel={trip.request.dest.label.split(",")[0]}
            vehicle={trip.result.vehicle}
            socVsPlan={state.soc_vs_plan}
            revised={revised != null}
          />
        )}

        {/* The plan, in the navigation the car is actually following. The HUD's
            "Navigate" is the next charger and nothing else, which is the right
            thing at 120 km/h; this is the one to take at a stop, and it starts
            from where the phone is rather than from this morning's origin. */}
        {!finished && (
          <div className="mt-4 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-ink-100 bg-white p-3">
            <div className="min-w-0">
              <p className="text-sm font-semibold text-ink-900">
                The rest of the way, in Google Maps
              </p>
              <p className="mt-0.5 text-xs text-ink-500">
                {ahead.length === 0
                  ? `Straight to ${trip.request.dest.label.split(",")[0]}, no stops left`
                  : `${ahead.length} ${ahead.length === 1 ? "stop" : "stops"} left, then ${trip.request.dest.label.split(",")[0]}`}{" "}
                · from where you are now
              </p>
            </div>
            <MapsRouteButton
              legs={remainingRouteLegs(trip, ahead)}
              label="Navigate the rest"
              tone="solid"
            />
          </div>
        )}

        {isDriver && !finished && (
          <p className="mt-3 flex items-center justify-between gap-3 rounded-xl border border-ink-200 bg-white px-3 py-2 text-xs text-ink-500">
            <span>This phone is sharing its location to follow the drive.</span>
            <button
              onClick={live.toggleSharing}
              className="shrink-0 font-semibold text-brand-700 underline underline-offset-2"
            >
              {live.sharing ? "Stop sharing" : "Resume sharing"}
            </button>
          </p>
        )}

        {live.gpsError && (
          <p className="mt-3 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            {live.gpsError}
          </p>
        )}

        {state.stale && (
          <p className="mt-3 flex items-start gap-2 rounded-xl border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              You&apos;re {fmtKm(state.off_route_m)} off the planned route, so the
              figures are frozen at their last good values rather than guessed.
              Rejoin the route and they&apos;ll pick up again.
            </span>
          </p>
        )}

        {!finished && (
          <div ref={batteryRef} className="scroll-mt-4">
            <BatteryCheck
              state={state}
              isDriver={isDriver}
              busy={live.busy}
              stopName={nextStop?.name ?? null}
              onSubmit={live.submitSoc}
              openSignal={socPrompt}
            />
          </div>
        )}

        {/* --- diverged? offer to re-plan --------------------------------- */}
        {state.needs_replan && !finished && (
          <div className="mt-4 rounded-2xl border border-amber-300 bg-amber-50 p-4">
            <p className="font-display text-sm font-semibold text-amber-900">
              Reality has drifted from the plan
            </p>
            <ul className="mt-1 list-inside list-disc text-sm text-amber-900/90">
              {state.replan_reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
            {isDriver && (
              <button
                onClick={() => void doReplan()}
                disabled={live.busy}
                className="mt-3 inline-flex items-center gap-2 rounded-xl bg-amber-900 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                {live.busy && <Loader2 className="h-4 w-4 animate-spin" />}
                Re-plan the rest from here
              </button>
            )}
          </div>
        )}

        {revised && (
          <RevisedPanel plan={revised} startedAtIso={run.started_at} />
        )}

        {finished && (
          <Link
            href={`/trip/${trip.id}/runs/${run.run_ref}`}
            className="mt-6 inline-block rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-semibold text-white"
          >
            See how it went against the plan
          </Link>
        )}
      </div>
    </div>
  )
}

/**
 * "46%" once a human has confirmed it; a RANGE while it's inferred.
 *
 * A range reads as an estimate in a way "46% ± 2" doesn't — the latter looks
 * like a measurement with a footnote, which is exactly the impression this
 * number has not earned.
 */
function socLabel(soc: number, measured: boolean, band: number): string {
  if (measured || band < 1) return `${Math.round(soc)}%`
  return `${Math.max(0, Math.round(soc - band))}-${Math.min(100, Math.round(soc + band))}%`
}

/**
 * The charging stops of whichever plan is in force, placed on the route.
 *
 * A re-plan replaces the list outright — its stops are offset from where the
 * re-plan happened, so they are put back on the route here, once, rather than
 * by each caller that compares one with how far the car has come.
 */
function placedStops(originalStops: Stop[], revised: LiveRun["plan"]): Stop[] {
  if (!revised) return originalStops
  return revised.remaining.stops.map((s) => ({
    ...s,
    offset_m: s.offset_m + revised.offset_base_m,
  }))
}

/** A stop within this distance behind the car counts as passed. GPS noise at
 *  a services is comfortably inside it, so arriving doesn't flip the next
 *  stop back and forth. */
const STOP_PASSED_M = 200

/** The next stop as the HUD wants it: a distance from here, not an offset. */
function heroStop(next: Stop | null, distM: number): LiveHeroState["nextStop"] {
  if (!next) return null
  return {
    name: next.name,
    distanceM: next.offset_m - distM,
    arriveSoc: next.arrive_soc,
    lat: next.lat,
    lon: next.lon,
  }
}

/** The revised journey, always shown next to the promise it replaces. */
function RevisedPanel({
  plan,
  startedAtIso,
}: {
  plan: NonNullable<LiveRun["plan"]>
  startedAtIso: string
}) {
  const b = plan.benchmark
  const worse = b.delta_min > 1
  return (
    <div className="mt-4 rounded-2xl border border-ink-100 bg-white p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-display text-base font-semibold text-ink-900">
          Revised plan for the rest of the way
        </h2>
        <span className="text-xs text-ink-400">
          re-planned {fmtKm(plan.offset_base_m)} in · version {plan.plan_version}
        </span>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-3">
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-400">Now hold</div>
          <div className="font-display text-2xl font-semibold text-brand-700">
            {plan.optimum_speed?.toFixed(0)} <span className="text-base">km/h</span>
          </div>
          <div className="text-xs text-ink-400">
            was {b.original_speed_kph.toFixed(0)} km/h
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-400">Still to go</div>
          <div className="font-display text-2xl font-semibold text-ink-900">
            {fmtDuration(plan.remaining.total_min ?? 0)}
          </div>
          <div className="text-xs text-ink-400">
            {b.live_stops_remaining} stops (plan said {b.original_stops_remaining})
          </div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wider text-ink-400">Arriving</div>
          <div
            className={`font-display text-2xl font-semibold ${
              worse ? "text-amber-700" : "text-brand-700"
            }`}
          >
            {clockAt(startedAtIso, b.live_total_min)}
          </div>
          <div className="text-xs text-ink-400">
            {Math.abs(b.delta_min) < 1 ? (
              "as promised"
            ) : (
              <>
                <span className="line-through">
                  {clockAt(startedAtIso, b.original_total_min)}
                </span>{" "}
                promised
              </>
            )}
          </div>
        </div>
      </div>

      <ol className="mt-4 space-y-1.5 text-sm">
        {plan.remaining.stops.map((s) => (
          <li key={s.charger_id} className="flex items-baseline justify-between gap-3">
            <span className="min-w-0 flex-1 truncate text-ink-700">{s.name}</span>
            <span className="shrink-0 font-mono text-xs text-ink-400">
              {fmtKm(s.offset_m + plan.offset_base_m)} · arrive {s.arrive_soc.toFixed(0)}% ·{" "}
              {s.charge_min.toFixed(0)} min
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}
