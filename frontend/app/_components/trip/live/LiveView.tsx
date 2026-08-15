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
import { AlertTriangle, Flag, Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { Alternative, Alternatives, LiveRun, Stop, Trip } from "@/lib/client"
import { clockAt, fmtDuration, fmtHm, fmtKm, socLabel } from "@/lib/format"
import { remainingRouteLegs } from "@/lib/maps"
import { buildRouteIndex } from "@/lib/route"
import { useLiveRun } from "@/lib/useLiveRun"
import JourneyHero, { LiveHeroState } from "../JourneyHero"
import MapsRouteButton from "../MapsRouteButton"
import AlternativeStops from "./AlternativeStops"
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
  // Alternatives to the next stop, once the driver has asked for them. Held
  // here rather than fetched on render: it is several DP runs on the server,
  // so it happens when somebody wants it and not on every poll.
  const [alts, setAlts] = useState<Alternatives | null>(null)
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

  /** Look for another charger for the next stop. Read-only — the plan does not
   *  move until the driver picks one. */
  async function loadAlternatives() {
    try {
      const found = await live.findAlternatives()
      if (found) setAlts(found)
      if (found && found.alternatives.length === 0) {
        toast.message("Nothing else on this stretch reaches the destination.")
      }
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Could not look for alternatives"
      )
    }
  }

  /** Take one. The exclusions the server handed back are what make this
   *  charger the next stop, and it keeps them, so the one turned down does not
   *  come back on the next re-plan. */
  async function takeAlternative(alt: Alternative) {
    try {
      await live.requestReplan(alt.exclude_charger_ids)
      setAlts(null)
      toast.success(`Stopping at ${alt.name} instead.`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not swap the stop")
    }
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

      {/* Phone first, and the whole page is read at arm's length in a moving
          car: tighter gutters and a tighter rhythm than the results page, so
          more than one card is on screen at a time. */}
      <div className="mx-auto max-w-4xl px-3 sm:px-4">
        <div className="mt-4 flex flex-wrap items-center justify-between gap-3 sm:mt-6">
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
            <div className="flex items-center gap-2">
              {/* Standing, not conditional. `needs_replan` fires on a shortfall
                  or a delay big enough to alarm about, which is a fine moment
                  to INTERRUPT somebody and a poor definition of when they are
                  allowed to ask. How you actually drive — the speed you settle
                  at, the heating, a headwind — moves what the charging plan
                  should be long before it trips a threshold, and the driver is
                  the one who knows that before any of our numbers do. The
                  amber panel keeps its own button: it carries the reasons, and
                  it may be the only thing read on a screen this busy. */}
              <button
                onClick={() => void doReplan()}
                disabled={live.busy}
                className="inline-flex items-center gap-2 rounded-xl border border-ink-200 px-3 py-2 text-sm font-semibold text-ink-700 hover:border-ink-300 disabled:opacity-50"
              >
                {live.busy ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RefreshCw className="h-4 w-4" />
                )}
                Re-plan
              </button>
              <button
                onClick={() => void live.end().catch(() => toast.error("Could not finish"))}
                disabled={live.busy}
                className="inline-flex items-center gap-2 rounded-xl border border-ink-200 px-3 py-2 text-sm font-semibold text-ink-700 hover:border-ink-300 disabled:opacity-50"
              >
                <Flag className="h-4 w-4" /> Arrived
              </button>
            </div>
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
            onFindAlternatives={
              isDriver ? () => void loadAlternatives() : undefined
            }
          />
        )}

        {alts && (
          <AlternativeStops
            data={alts}
            busy={live.busy}
            onTake={(alt) => void takeAlternative(alt)}
            onClose={() => setAlts(null)}
          />
        )}

        {/* The plan, in the navigation the car is actually following. The HUD's
            "Navigate" is the next charger and nothing else, which is the right
            thing at 120 km/h; this is the one to take at a stop, and it starts
            from where the phone is rather than from this morning's origin. */}
        {!finished && (
          <div className="mt-3 flex flex-col gap-2 rounded-2xl border border-ink-100 bg-white p-3 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
            {/* No heading: the button says what it does, and a phone screen
                cannot afford a line that only introduces the next one. */}
            <p className="min-w-0 text-xs text-ink-500">
              {ahead.length === 0
                ? `Straight to ${trip.request.dest.label.split(",")[0]}, no stops left`
                : `${ahead.length} ${ahead.length === 1 ? "stop" : "stops"} left, then ${trip.request.dest.label.split(",")[0]}`}{" "}
              · from where you are now
            </p>
            {/* Full width on a phone — a primary action operated in a car —
                and back to its own size once there is a row to sit in. */}
            <div className="w-full sm:w-auto">
              <MapsRouteButton
                legs={remainingRouteLegs(trip, ahead)}
                label="Navigate the rest"
                tone="solid"
                full
              />
            </div>
          </div>
        )}

        {isDriver && !finished && (
          <p className="mt-2 flex items-center justify-between gap-3 rounded-xl border border-ink-200 bg-white px-3 py-2 text-xs text-ink-500">
            <span className="min-w-0 truncate">
              Sharing this phone&apos;s location
            </span>
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
              onReplan={() => void doReplan()}
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
          <RevisedPanel
            plan={revised}
            startedAtIso={run.started_at}
            distM={distM}
          />
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

/**
 * The revised journey, always shown next to the promise it replaces.
 *
 * Everything here is measured FROM THE CAR, not from this morning's origin.
 * The stops used to be labelled with their distance along the whole route —
 * "227 km" while sitting at the 175 km mark — which is a fact about the trip
 * and no help at all to somebody deciding whether to stop now. A re-plan is a
 * plan for the road ahead, so it is written in the road ahead's units.
 *
 * The three headline numbers stay three across at every width. Stacked on a
 * phone they were a 270-pixel column of one number each, which pushed the list
 * of stops — the part you re-planned to see — off the bottom of the screen.
 */
function RevisedPanel({
  plan,
  startedAtIso,
  distM,
}: {
  plan: NonNullable<LiveRun["plan"]>
  startedAtIso: string
  /** How far the car has come, so every distance can be relative to it. */
  distM: number
}) {
  const b = plan.benchmark
  const worse = b.delta_min > 1
  return (
    <div className="mt-3 rounded-2xl border border-ink-100 bg-white p-3 sm:p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="font-display text-base font-semibold text-ink-900">
          Revised plan from here
        </h2>
        <span className="text-xs text-ink-400">
          re-planned {fmtKm(plan.offset_base_m)} in · version {plan.plan_version}
        </span>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 sm:gap-3">
        <Stat
          label="Now hold"
          value={`${plan.optimum_speed?.toFixed(0)} km/h`}
          sub={`was ${b.original_speed_kph.toFixed(0)}`}
          tone="brand"
        />
        <Stat
          label="Still to go"
          // "8h08", not "8 h 08 min": three columns on a 360px phone, and the
          // label above it already says what the number is.
          value={fmtHm(plan.remaining.total_min ?? 0)}
          sub={`${b.live_stops_remaining} stops, was ${b.original_stops_remaining}`}
        />
        <Stat
          label="Arriving"
          value={clockAt(startedAtIso, b.live_total_min)}
          sub={
            Math.abs(b.delta_min) < 1
              ? "as promised"
              : `${clockAt(startedAtIso, b.original_total_min)} promised`
          }
          tone={worse ? "warn" : "brand"}
        />
      </div>

      <ol className="mt-3 space-y-1.5 text-sm">
        {plan.remaining.stops.map((s) => (
          <li key={s.charger_id} className="flex items-baseline justify-between gap-2">
            <span className="min-w-0 flex-1 truncate text-ink-700">{s.name}</span>
            <span className="shrink-0 font-mono text-[11px] text-ink-400">
              in {fmtKm(Math.max(s.offset_m + plan.offset_base_m - distM, 0))} ·{" "}
              {s.arrive_soc.toFixed(0)}% · {s.charge_min.toFixed(0)} min
            </span>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** One headline number. Sized to survive three-across on a 360px phone. */
function Stat({
  label,
  value,
  sub,
  tone = "plain",
}: {
  label: string
  value: string
  sub: string
  tone?: "plain" | "brand" | "warn"
}) {
  const toneCls =
    tone === "brand"
      ? "text-brand-700"
      : tone === "warn"
        ? "text-amber-700"
        : "text-ink-900"
  return (
    <div className="min-w-0">
      <div className="truncate text-[10px] uppercase tracking-wider text-ink-400">
        {label}
      </div>
      <div className={`font-display text-lg font-semibold sm:text-2xl ${toneCls}`}>
        {value}
      </div>
      <div className="text-[11px] leading-tight text-ink-400">{sub}</div>
    </div>
  )
}
