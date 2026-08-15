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

import { BatteryCharging, Flag, Navigation, Search } from "lucide-react"
import { Stop, Vehicle } from "@/lib/client"
import { fmtBays, fmtDuration, fmtKm, mapsLink } from "@/lib/format"

/** Beyond this the plan's arrival percentage is worth a caveat. Matches the
 *  band the HUD already colours the battery readout at. */
const DRIFT_BAND = 5

export default function NextStopCard({
  stop,
  here = false,
  distM,
  destLabel,
  vehicle,
  socVsPlan,
  revised,
  onFindAlternatives,
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

  return (
    <div className="mt-4 rounded-2xl border border-brand-200 bg-white p-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-50 text-brand-600">
          <BatteryCharging className="h-4 w-4" />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-brand-700">
            {here ? "Charging here" : "Next stop"}
            {revised && " · revised plan"}
          </p>
          <h2 className="mt-0.5 truncate font-display text-base font-semibold text-ink-900">
            {stop.name}
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            {stop.operator && <span>{stop.operator} · </span>}
            {Math.round(stop.power_kw)} kW
            {fmtBays(stop.n_points) && <> · {fmtBays(stop.n_points)}</>}
            {!here && <> · {fmtKm(toGo)} to go</>}
          </p>
        </div>

        {!here && (
          <a
            href={mapsLink(stop.lat, stop.lon)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-xl border border-ink-200 px-3 text-xs font-semibold text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700"
          >
            <Navigation className="h-3.5 w-3.5" />
            Navigate
          </a>
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
          <p className="font-mono text-sm font-bold text-brand-700">
            ~{fmtDuration(stop.charge_min)} plugged in
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

        {/* The stop is a decision, not just a number. Right under the figures
            that make it look settled, because "how long am I here" and "do I
            want to be here" are the same thought. */}
        {!here && onFindAlternatives && (
          <button
            onClick={onFindAlternatives}
            className="mt-2 inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3 py-2 text-xs font-semibold text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700"
          >
            <Search className="h-3.5 w-3.5" />
            Somewhere else?
          </button>
        )}

        <p className="mt-2 text-xs leading-relaxed text-ink-500">
          The plan {here ? "had" : "has"} you arriving on{" "}
          {Math.round(stop.arrive_soc)}% and taking on about {Math.round(kwh)}{" "}
          kWh.
          {drifting &&
            ` Your battery is currently ${Math.abs(Math.round(socVsPlan))}% ${
              socVsPlan > 0 ? "above" : "below"
            } that plan, so expect to ${
              socVsPlan > 0 ? "need less" : "need more"
            } than this.`}
        </p>
      </div>
    </div>
  )
}
