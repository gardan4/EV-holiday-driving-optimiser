"use client"

/**
 * The next stop, and what you are meant to do when you get there.
 *
 * The HUD names the charger and says how far it is, which is all a driver can
 * read at 120 km/h. It is not what anyone asks ten minutes out: *how long am I
 * plugged in here, and what am I leaving with?* That was on the results page,
 * on a laptop, at home — the itinerary the plan was chosen from — and nowhere
 * on the screen of the phone actually doing the drive.
 *
 * Every number here comes from the plan in force. After a re-plan that is the
 * revised list, so this cannot go on quoting a charge time the drive has
 * already abandoned. Two things it deliberately does NOT do:
 *
 * - **It does not invent a live arrival percentage.** The plan predicts what
 *   you arrive on; the drive knows how far the battery has drifted from the
 *   plan. Adding one to the other looks like a measurement and is a guess, so
 *   the plan's figure is labelled as the plan's, and the drift is stated
 *   beside it in the words the rest of the screen already uses.
 * - **It does not repeat the arrival clock.** There is one ETA on this screen
 *   and it is in the HUD; a second time, computed a second way, is how a
 *   reader ends up trusting neither.
 */

import {
  BatteryCharging,
  ChevronLeft,
  ChevronRight,
  Flag,
  Loader2,
  Navigation,
  Search,
} from "lucide-react"
import { Stop, Vehicle } from "@/lib/client"
import { fmtBays, fmtDuration, fmtKm, mapsLink } from "@/lib/format"
import { chargeMinutes } from "@/lib/vehicles"

/** Beyond this the plan's arrival percentage is worth a caveat. Matches the
 *  band the HUD already colours the battery readout at. */
const DRIFT_BAND = 5

/** Paging arrow. 36px, which is the floor for something pressed in a car. */
function Step({
  dir,
  disabled,
  onClick,
}: {
  dir: "prev" | "next"
  disabled: boolean
  onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={dir === "prev" ? "Previous stop" : "Next stop"}
      className="grid h-9 w-9 place-items-center rounded-lg border border-ink-200 text-ink-600 disabled:opacity-30"
    >
      {dir === "prev" ? (
        <ChevronLeft className="h-4 w-4" />
      ) : (
        <ChevronRight className="h-4 w-4" />
      )}
    </button>
  )
}

export default function NextStopCard({
  stop,
  here = false,
  distM,
  destLabel,
  vehicle,
  socVsPlan,
  revised,
  onFindAlternatives,
  findingAlternatives = false,
  liveSoc = null,
  index = 0,
  total = 1,
  onStep,
  onArrive,
  onUndoArrive,
}: {
  /** The stop this card is about: the one under the car when plugged in,
   *  otherwise the next one ahead. Null when there are none left. */
  stop: Stop | null
  /** The car is AT this stop. Standing at the charger, "24 km to go" and a
   *  Navigate button are noise, and the charge figures are the whole card. */
  here?: boolean
  /** How far the car has come, to turn the stop's offset into "still to go". */
  distM: number
  destLabel: string
  vehicle: Vehicle
  /** Live battery against the plan, in points. */
  socVsPlan: number
  /** Whether these figures come from a revised plan rather than the original. */
  revised: boolean
  /** Driver only: look for somewhere else to stop. Absent for watchers, and
   *  once the car is standing at the charger — at that point the question is
   *  settled by the cable. */
  onFindAlternatives?: () => void
  /** A lookup is in flight. It takes seconds — several DP passes on the
   *  server — and a button that looks idle while it runs gets pressed again,
   *  which spends the per-run budget on the same question. */
  findingAlternatives?: boolean
  /** The battery now. Only used while plugged in, to answer the one question
   *  standing at a charger asks: how much longer. Null when unknown. */
  liveSoc?: number | null
  /** Which stop of the remaining plan this is, and how many there are, so the
   *  card can be paged rather than being permanently about the next one. */
  index?: number
  total?: number
  onStep?: (delta: 1 | -1) => void
  /** Driver only: "I'm standing at this one". The drive cannot see it — a
   *  locked phone reports no position, which is what a phone does while its
   *  car charges. */
  onArrive?: () => void
  /** Driver only, and only while there is one to take back: "I'm not there."
   *  The arrival is the single tap on this screen no later fix can correct —
   *  the offset is clamped forward, so a wrong one survives every ping — and
   *  the toast that offered Undo is long gone by the time most people notice.
   */
  onUndoArrive?: () => void
}) {
  if (!stop) {
    return (
      <div className="mt-4 flex items-start gap-3 rounded-2xl border border-ink-100 bg-white p-4">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-ink-100 text-ink-700">
          <Flag className="h-4 w-4" />
        </div>
        <div className="min-w-0">
          <p className="font-display text-sm font-semibold text-ink-900">
            No more charging stops
          </p>
          <p className="mt-0.5 text-sm text-ink-500">
            Straight through to {destLabel} from here.
          </p>
        </div>
      </div>
    )
  }

  const toGo = Math.max(stop.offset_m - distM, 0)
  const added = Math.max(stop.depart_soc - stop.arrive_soc, 0)
  // Usable capacity is what a percentage point of this car's battery is worth,
  // so this is the energy going IN. Not what the charger bills, which is
  // larger by the cable→battery loss the plan already prices separately.
  const kwh = (added / 100) * vehicle.usable_kwh
  const drifting = Math.abs(socVsPlan) >= DRIFT_BAND

  // How much longer at this plug, from the battery NOW.
  //
  // Estimated with the SITE's power, then scaled by whatever the plan's own
  // charge time implies. That ratio is how the server's cold-weather charge
  // factor gets in: it is not on the wire, but the plan's `charge_min` for a
  // known SoC window is enough to recover it, and a "12 min left" that
  // contradicts the plan it sits under is worse than no number.
  let leftMin: number | null = null
  if (here && liveSoc != null && liveSoc < stop.depart_soc) {
    const raw = chargeMinutes(vehicle, liveSoc, stop.depart_soc, stop.power_kw)
    const modelled = chargeMinutes(
      vehicle,
      stop.arrive_soc,
      stop.depart_soc,
      stop.power_kw
    )
    const scale =
      modelled > 0.5 && stop.charge_min > 0 ? stop.charge_min / modelled : 1
    leftMin = raw * (scale > 0.2 && scale < 5 ? scale : 1)
  }

  return (
    <div className="mt-3 rounded-2xl border border-brand-200 bg-white p-3 sm:p-4">
      {/* The name gets the whole width. It used to share the row with the
          Navigate button, which on a phone left "Tesla Supercharger Werth…" —
          a truncation that hides the one word telling you WHICH Wertheim. */}
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-600">
          <BatteryCharging className="h-4 w-4" />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-brand-700">
            {here ? "Charging here" : index === 0 ? "Next stop" : "Later stop"}
            {total > 1 && (
              <span className="font-mono text-ink-400"> · {index + 1}/{total}</span>
            )}
            {/* Which plan these figures are from. The revised panel says it
                too, but this card is the one people actually read. */}
            {revised && <span className="text-ink-400"> · revised</span>}
          </p>
          <h2 className="mt-0.5 font-display text-base font-semibold leading-tight text-ink-900">
            {stop.name}
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            {stop.operator && <span>{stop.operator} · </span>}
            {Math.round(stop.power_kw)} kW
            {fmtBays(stop.n_points) && <> · {fmtBays(stop.n_points)}</>}
            {!here && <> · {fmtKm(toGo)} to go</>}
          </p>
        </div>

        {onStep && total > 1 && (
          <div className="flex shrink-0 items-center gap-1">
            <Step
              dir="prev"
              disabled={index === 0}
              onClick={() => onStep(-1)}
            />
            <Step
              dir="next"
              disabled={index >= total - 1}
              onClick={() => onStep(1)}
            />
          </div>
        )}
      </div>

      {/* The answer to "how much do I need to charge", as one line of numbers
          and one bar. The bar is the same one the itinerary uses, so a driver
          who read the plan at home recognises it here. */}
      <div className="mt-3 rounded-xl bg-brand-50/70 p-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <p className="font-display text-lg font-semibold text-ink-900">
            Charge to {Math.round(stop.depart_soc)}%
          </p>
          {/* Standing at the plug, "23 min plugged in" is a fact about the
              past. What is left is the only number worth the space, and it is
              estimated from the battery NOW rather than from the plan's
              prediction of what you arrived on. */}
          <p className="font-mono text-sm font-bold text-brand-700">
            {here && leftMin != null
              ? `~${fmtDuration(leftMin)} left`
              : `~${fmtDuration(stop.charge_min)} plugged in`}
          </p>
        </div>

        <div
          className="mt-2 flex items-center gap-2"
          aria-label={`Plan: arrive ${Math.round(stop.arrive_soc)} percent, leave ${Math.round(stop.depart_soc)} percent`}
        >
          <span className="font-mono text-xs font-semibold text-ink-700">
            {Math.round(stop.arrive_soc)}%
          </span>
          <div className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white">
            <div
              className="absolute inset-y-0 rounded-full bg-brand-400"
              style={{
                left: `${stop.arrive_soc}%`,
                width: `${Math.max(stop.depart_soc - stop.arrive_soc, 0)}%`,
              }}
            />
          </div>
          <span className="font-mono text-xs font-semibold text-brand-700">
            {Math.round(stop.depart_soc)}%
          </span>
        </div>

        {/* The bar above already says 9% → 65%; repeating it in prose spent
            two lines of a phone screen on a number just read. What is left is
            the part the bar cannot show: the energy, and whether reality has
            drifted far enough that these figures will not hold. */}
        <p className="mt-1.5 text-[11px] leading-relaxed text-ink-500">
          ≈{Math.round(kwh)} kWh into the battery
          {drifting &&
            ` · you're ${Math.abs(Math.round(socVsPlan))}% ${
              socVsPlan > 0 ? "above" : "below"
            } plan, so expect to need ${socVsPlan > 0 ? "less" : "more"}`}
        </p>
      </div>

      {/* Two peers, one row, thumb-sized. "Take me there" and "not here" are
          the same decision seen from either side, so they sit together under
          the figures that prompt it rather than one in the header and one
          buried in the middle of the panel. */}
      {!here && (
        <div className="mt-3 grid grid-cols-2 gap-2">
          <a
            href={mapsLink(stop.lat, stop.lon)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-11 items-center justify-center gap-1.5 rounded-xl border border-ink-200 text-sm font-semibold text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700"
          >
            <Navigation className="h-4 w-4" />
            Navigate
          </a>
          {onFindAlternatives && (
            <button
              onClick={onFindAlternatives}
              disabled={findingAlternatives}
              aria-busy={findingAlternatives}
              className="inline-flex h-11 items-center justify-center gap-1.5 rounded-xl border border-ink-200 text-sm font-semibold text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700 disabled:opacity-60 disabled:hover:border-ink-200 disabled:hover:text-ink-700"
            >
              {findingAlternatives ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Search className="h-4 w-4" />
              )}
              {findingAlternatives ? "Looking…" : "Somewhere else"}
            </button>
          )}
        </div>
      )}

      {/* Quiet, and only for the driver. The phone stops reporting the moment
          it locks, so arriving somewhere is the one thing the drive cannot
          notice on its own — and until it is told, every number above is about
          a stretch of road already driven. */}
      {!here && onArrive && (
        <button
          onClick={onArrive}
          className="mt-2 w-full rounded-xl py-2 text-xs font-semibold text-ink-500 underline underline-offset-2 hover:text-brand-700"
        >
          I&apos;m plugged in here now
        </button>
      )}

      {/* The way back. Offered whenever an arrival can still be taken back,
          including on the card for the stop it moved the car to — that is
          where somebody who mis-tapped is looking, and by then the toast has
          gone. Amber rather than quiet grey: it undoes a thing that already
          happened. */}
      {onUndoArrive && (
        <button
          onClick={onUndoArrive}
          className="mt-2 w-full rounded-xl py-2 text-xs font-semibold text-amber-800 underline underline-offset-2 hover:text-amber-900"
        >
          I&apos;m not here — undo that arrival
        </button>
      )}
    </div>
  )
}
