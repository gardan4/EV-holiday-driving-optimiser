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

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import Link from "next/link"
import { AlertTriangle, Flag, Loader2, RefreshCw } from "lucide-react"
import { toast } from "sonner"
import { Alternative, Alternatives, LiveRun, Stop, Trip } from "@/lib/client"
import { clockAt, fmtDuration, fmtHm, fmtKm, socLabel } from "@/lib/format"
import { projectedSoc } from "@/lib/liveSoc"
import { remainingRouteLegs } from "@/lib/maps"
import { buildRouteIndex } from "@/lib/route"
import { useLiveRun } from "@/lib/useLiveRun"
import JourneyHero, { LiveHeroState } from "../JourneyHero"
import MapsRouteButton from "../MapsRouteButton"
import AlternativeStops from "./AlternativeStops"
import BatteryCheck from "./BatteryCheck"
import ChargingHere from "./ChargingHere"
import HoldSpeed from "./HoldSpeed"
import PlanAhead from "./PlanAhead"
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
  const { run, isDriver } = live

  // Derived from the server, NOT held locally: a watcher polls `run`, and a
  // re-plan the driver made is the single most important thing to reach them.
  const revised = run?.plan ?? null

  const distM = live.localOffsetM ?? live.state?.offset_m ?? 0
  const finished = run?.status === "finished"

  // The battery, smoothed between server updates.
  //
  // The server owns the model and every figure here starts from its last
  // answer — this only carries that answer forward over the seconds since it
  // arrived, using the same maths (`lib/liveSoc.ts`). Without it the position
  // moves smoothly, because the GPS watch projects it locally, and the
  // percentage next to it jumps a whole point every ~25 s and reads as stale.
  // Reported from the road in those words.
  //
  // One tick, one derived state, and every consumer downstream reads that —
  // the HUD, the stop card, the battery row — so nothing on the screen can
  // show a different battery from anything else.
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (finished) return
    const h = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(h)
  }, [finished])

  const heldSpeed =
    revised?.optimum_speed ?? result?.speed_kph ?? trip.result.optimum_speed ?? 120
  const state = useMemo(() => {
    const st = live.state
    if (!st || finished || !run) return st
    const soc = projectedSoc({
      state: st,
      startedAt: run.started_at,
      vehicle: trip.result.vehicle,
      distM,
      speedKph: heldSpeed,
      now: Date.now(),
    })
    // Only the number. `soc_is_measured` is left exactly as the server sent
    // it: flipping it here would turn "confirmed just now" into "estimated" a
    // second after the driver typed their reading, which reads as the app
    // discarding it. The projection is tenths of a point over a ping's window
    // and the server flips the flag itself on the next one.
    return Math.abs(soc - st.soc) < 0.05 ? st : { ...st, soc }
    // `tick` is the clock: it has no other reader, and dropping it would
    // freeze the projection at whatever the last render happened to compute.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [live.state, run, finished, distM, heldSpeed, trip.result.vehicle, tick])

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

  // What the card can page through: the stop under the car first, when there
  // is one, then everything still in front. `ahead` drops the charger you are
  // plugged INTO — right for navigation, and wrong for the twenty minutes you
  // spend standing at it wondering how much longer.
  const focusable = useMemo(
    () => (atStop ? [atStop, ...ahead] : ahead),
    [atStop, ahead]
  )
  // Held as a charger id rather than an index: the list shortens as the car
  // passes stops, and an index would silently start pointing at a different
  // charger every time it did.
  const [focusId, setFocusId] = useState<string | null>(null)
  const focusIdx = Math.max(
    0,
    focusable.findIndex((s) => s.charger_id === focusId)
  )
  const focused = focusable[focusIdx] ?? null
  function stepFocus(delta: 1 | -1) {
    const next = focusable[Math.min(Math.max(focusIdx + delta, 0), focusable.length - 1)]
    if (next) setFocusId(next.charger_id)
  }

  // Correcting the battery from the HUD. The scene is full-bleed, so on a phone
  // the check itself is below the fold — asking for it has to bring it into
  // view, or the tap looks like it did nothing.
  const batteryRef = useRef<HTMLDivElement>(null)
  const [socPrompt, setSocPrompt] = useState(0)
  // Alternatives to the next stop, once the driver has asked for them. Held
  // here rather than fetched on render: it is several DP runs on the server,
  // so it happens when somebody wants it and not on every poll.
  const [alts, setAlts] = useState<Alternatives | null>(null)
  // Which stop the open panel is about, and how far the car had come when the
  // server answered. Both are needed because the answer is a SNAPSHOT: the
  // charger id so "Redo from here" asks the same question again, the distance
  // so the panel can say how far back its percentages come from. Without them
  // a panel left open kept reporting distances measured an hour ago — "in
  // 136 km" under a stop card saying 261 km, about the same charger.
  const [altsFor, setAltsFor] = useState<string | undefined>(undefined)
  const [altsAtM, setAltsAtM] = useState(0)
  // Which stop is being looked up, so the button that was pressed can say so.
  // The ref is the guard and the state is the pixels: two taps inside one
  // render both read the same stale state, and it is precisely the second tap
  // this exists to swallow.
  const altsPending = useRef<string | null>(null)
  const [pendingAltFor, setPendingAltFor] = useState<string | null>(null)
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
   *  move until the driver picks one.
   *
   *  One at a time. The answer is a handful of DP passes on the server and the
   *  panel shows nothing until it lands, so a button that stayed idle-looking
   *  got pressed again — and again — until the per-run limit refused one and
   *  the drive screen reported a bare status code. The button now spins; this
   *  is the other half, because a stop swapped from the plan list and one from
   *  the card are the same request. */
  async function loadAlternatives(rejectChargerId?: string) {
    if (altsPending.current !== null) return
    altsPending.current = rejectChargerId ?? ""
    setPendingAltFor(altsPending.current)
    try {
      const found = await live.findAlternatives(rejectChargerId)
      if (found) {
        setAlts(found)
        setAltsFor(rejectChargerId)
        setAltsAtM(distM)
      }
      if (found && found.alternatives.length === 0) {
        toast.message("Nothing else on this stretch reaches the destination.")
      }
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Could not look for alternatives"
      )
    } finally {
      altsPending.current = null
      setPendingAltFor(null)
    }
  }

  /** Take one. The exclusions the server handed back are what make this
   *  charger the next stop, and it keeps them, so the one turned down does not
   *  come back on the next re-plan. */
  async function takeAlternative(alt: Alternative) {
    try {
      // The floor travels with the choice: without it the re-plan applies the
      // full reserve again and refuses the stop just picked.
      await live.requestReplan(
        alt.exclude_charger_ids,
        alt.min_arrival_soc ?? null
      )
      setAlts(null)
      toast.success(
        alt.below_reserve
          ? `Pushing on to ${alt.name} — arriving on about ${Math.round(alt.arrive_soc)}%.`
          : `Stopping at ${alt.name} instead.`
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not swap the stop")
    }
  }

  /** Hold a speed from here — or hand the choice back with null. The server
   *  keeps it, so the standing "Re-plan" cannot overrule it later. */
  async function holdSpeed(speedKph: number | null) {
    try {
      const plan = await live.requestReplan([], null, speedKph)
      // The panel is an alternative to a plan that no longer exists.
      if (plan) setAlts(null)
      if (plan) {
        toast.success(
          speedKph == null
            ? `Back to the fastest — hold ${plan.optimum_speed?.toFixed(0)} km/h.`
            : `Holding ${speedKph} km/h, arriving ${clockAt(
                run!.started_at,
                plan.benchmark.live_total_min
              )}.`
        )
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not set the speed")
    }
  }

  /** "I'm standing at this one." Moves the drive to the stop and marks it, so
   *  the screen stops being about a stretch of road already driven. */
  async function markArrived(stop: Stop) {
    try {
      const res = await live.markArrived(stop.charger_id)
      if (!res) return
      // What it matched, not what the card was showing: the position wins, so
      // saying "charging at Geiselwind" when the phone put you at a services
      // 40 km further would be the same wrong answer in a friendlier voice.
      setFocusId(stop.charger_id)
      toast.success(
        res.matched_name
          ? `Charging at ${res.matched_name}.`
          : "Moved to where you are — no charger recognised here.",
        // The way out, offered where the mistake is noticed. This tap is one
        // button away from "Somewhere else" on a phone in a moving car, and it
        // is the only one the drive cannot walk back on its own.
        { action: { label: "Undo", onClick: () => void undoArrival() } }
      )
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not move the drive")
    }
  }

  /** "I'm not there." Puts the drive back exactly as it stood before the last
   *  arrival — the toast's Undo and the card's own link are the same call,
   *  because a toast is gone in five seconds and a misclick is often noticed
   *  after it. */
  async function undoArrival() {
    try {
      const st = await live.undoArrive()
      if (st) toast.success("Put back — you're on the road again.")
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Could not undo that arrival"
      )
    }
  }

  async function doReplan() {
    try {
      const plan = await live.requestReplan()
      // Same as above: whatever was on offer was on offer against the old plan.
      if (plan) setAlts(null)
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

  /** "It's actually this much", typed at the plug. Re-anchors the projection
   *  on the driver's figure and leaves the cable in. No re-plan offer: nothing
   *  about the journey has changed yet, and a toast with a button in it while
   *  somebody is mid-charge is noise. */
  async function doCorrect(soc: number) {
    try {
      await live.submitSoc(soc)
      toast.success(`Noted — ${Math.round(soc)}%. Counting up from there.`)
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Couldn't save that reading"
      )
    }
  }

  /** "I'm leaving on this much." Ends the stop, anchors the battery on the
   *  driver's own figure, and offers the re-plan — because the number they
   *  actually left on is exactly what the rest of the drive should be computed
   *  from, and it is never the number the projection guessed. */
  async function doLeave(soc: number) {
    try {
      const next = await live.submitSoc(soc, true)
      const worthReplanning =
        next != null && (next.needs_replan || Math.abs(next.soc_vs_plan) >= 5)
      toast.success(`On the road again on ${Math.round(soc)}%.`, {
        duration: worthReplanning ? 12_000 : 4_000,
        action: worthReplanning
          ? { label: "Re-plan", onClick: () => void doReplan() }
          : undefined,
      })
    } catch (err) {
      toast.error(
        err instanceof Error ? err.message : "Couldn't save that reading"
      )
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
        {/* Title and actions share ONE row, with the long subtitle underneath.
            Two full-size buttons of their own used to take a whole band of a
            phone screen before the plan started — and neither is what the
            driver came to this page to read. */}
        <div className="mt-4 flex items-center justify-between gap-3 sm:mt-6">
          <h1 className="min-w-0 truncate font-display text-xl font-semibold text-ink-900">
            {finished ? "That's the drive done" : "On the road"}
          </h1>
          {!finished && isDriver && (
            <div className="flex shrink-0 items-center gap-1.5">
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
                className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-ink-200 px-2.5 text-xs font-semibold text-ink-700 hover:border-ink-300 disabled:opacity-50"
              >
                {live.busy ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
                Re-plan
              </button>
              {/* Quieter than Re-plan on purpose: it is pressed once, at the
                  end, and it ENDS the drive. Same row, less weight. */}
              <button
                onClick={() => void live.end().catch(() => toast.error("Could not finish"))}
                disabled={live.busy}
                aria-label="Arrived — finish this drive"
                className="inline-flex h-9 items-center gap-1.5 rounded-xl px-2.5 text-xs font-medium text-ink-500 hover:bg-ink-100 disabled:opacity-50"
              >
                <Flag className="h-3.5 w-3.5" /> Arrived
              </button>
            </div>
          )}
        </div>
        <p className="mt-0.5 text-sm text-ink-500">
          {trip.request.origin.label.split(",")[0]} →{" "}
          {trip.request.dest.label.split(",")[0]} · {fmtKm(distM)} of{" "}
          {fmtKm(trip.result.total_dist_m)}
          {isDriver ? " · you're driving" : " · you're watching"}
        </p>

        {/* Where the car actually IS. Above the next stop, because a driver
            standing at a plug is not asking about a charger 69 km up the road
            — and for EVERY charger, planned or not: at a planned stop the card
            below is already counting down to the one after it, so restricting
            this to unplanned chargers left the screen silent about the site
            the car was plugged into. */}
        {!finished && state.at_charger && (
          <ChargingHere
            charger={state.at_charger}
            soc={state.soc}
            socLabel={socLabel(
              state.soc,
              state.soc_is_measured,
              state.soc_uncertainty_pct
            )}
            session={state.charging ?? null}
            nextName={ahead[0]?.name ?? null}
            isDriver={isDriver}
            busy={live.busy}
            onCorrect={(soc) => void doCorrect(soc)}
            onLeave={(soc) => void doLeave(soc)}
          />
        )}

        {/* What happens next, before anything about the drive as a whole. The
            HUD can only carry the charger's name and its distance; the two
            numbers a driver plans a stop around — how long, and what they
            leave on — were only ever on the results page. */}
        {!finished && (
          <NextStopCard
            stop={focused}
            here={focused != null && focused.charger_id === atChargerId}
            distM={distM}
            destLabel={trip.request.dest.label.split(",")[0]}
            vehicle={trip.result.vehicle}
            socVsPlan={state.soc_vs_plan}
            revised={revised != null}
            liveSoc={state.soc}
            index={focusIdx}
            total={focusable.length}
            onStep={stepFocus}
            onFindAlternatives={
              isDriver && focused
                ? () => void loadAlternatives(focused.charger_id)
                : undefined
            }
            findingAlternatives={pendingAltFor !== null}
            onArrive={
              isDriver && focused
                ? () => void markArrived(focused)
                : undefined
            }
            onUndoArrive={
              isDriver && state.can_undo_arrive
                ? () => void undoArrival()
                : undefined
            }
          />
        )}

        {/* The plan itself, always — not only after a re-plan. Every stop is
            swappable, because by the time one is the NEXT stop the choice
            about it has been made for you. */}
        {!finished && (
          <PlanAhead
            stops={focusable}
            distM={distM}
            atChargerId={atChargerId}
            focusedId={focused?.charger_id ?? null}
            onFocus={setFocusId}
            destLabel={trip.request.dest.label.split(",")[0]}
            destArrivalClock={clockAt(run.started_at, etaMin)}
            onSwap={
              isDriver ? (id) => void loadAlternatives(id) : undefined
            }
            swappingId={pendingAltFor}
          />
        )}

        {alts && (
          <AlternativeStops
            data={alts}
            distM={distM}
            fetchedAtM={altsAtM}
            busy={live.busy}
            onTake={(alt) => void takeAlternative(alt)}
            onRefresh={
              isDriver ? () => void loadAlternatives(altsFor) : undefined
            }
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

        {/* Driver only, like every other control on this screen. A watcher has
            no run id, so the stepper's "Hold 135" reached `requestReplan`,
            found no token and returned null — a button that silently does
            nothing, offered to a passenger who has every reason to think they
            just changed the driver's plan. Nothing is hidden by this: the
            speed in force is on the revised panel as "Now hold", for everyone,
            because `optimum_speed` is the plan's speed whether it was chosen
            or computed. */}
        {revised && !finished && isDriver && (
          <HoldSpeed
            plan={revised}
            startedAtIso={run.started_at}
            busy={live.busy}
            onHold={(kph) => void holdSpeed(kph)}
          />
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
 * Three headline numbers and nothing else: what to hold, what is left, when
 * you land. The stop list it used to carry moved to `PlanAhead`, which shows
 * the tail of whichever plan is in force whether or not anyone has re-planned
 * — two lists of the same stops is one list too many on a phone.
 *
 * They stay three across at every width. Stacked they were a 270-pixel column
 * of one number each, which pushed everything below off the bottom of the
 * screen.
 */
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

      {/* No stop list here any more: `PlanAhead` shows the tail of whichever
          plan is in force, always, and two lists of the same stops is one
          list too many on a phone. */}
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
