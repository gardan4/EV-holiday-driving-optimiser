"use client"

/**
 * The charger the car is standing at, when the plan never chose it.
 *
 * Stopping somewhere the plan did not pick is an ordinary thing to do — the
 * plan's four stops are four sites out of the hundreds on the corridor, and a
 * driver stops where the queue is short, where the food is, or where they
 * simply decided to. Until now the screen could only describe stops it had
 * planned, so the one place the car actually WAS stayed invisible: the card
 * kept counting down to a charger 69 km up the road while its driver stood at
 * a different one, plugged in.
 *
 * The number this card exists for is **how much has to go in before you can
 * leave** — which the plan cannot answer, because it never expected the car to
 * be here. The server prices it through the same profile the simulator uses
 * (`need_soc_next`), so it agrees with the rest of the screen rather than being
 * a second opinion.
 *
 * It is a FLOOR, not a target: enough to reach the next planned stop with the
 * reserve intact. Charging past it is the driver's call and usually a good one
 * — this says where the choice starts, not where it ends.
 */

import { BatteryCharging, MapPin, UtensilsCrossed } from "lucide-react"
import { AtCharger, Vehicle } from "@/lib/client"
import { fmtBays, fmtDuration, mapsLink } from "@/lib/format"
import { chargeMinutes } from "@/lib/vehicles"

export default function ChargingHere({
  charger,
  soc,
  socLabel,
  needSoc,
  vehicle,
  nextName,
}: {
  charger: AtCharger
  /** The battery estimate now. */
  soc: number
  /** …as text, with its range when it is inferred. */
  socLabel: string
  /** Enough to reach the next planned stop, reserve included. Null when the
   *  server could not price it. */
  needSoc: number | null
  vehicle: Vehicle
  /** Where that floor gets you to. */
  nextName: string | null
}) {
  const short = needSoc != null ? Math.max(0, needSoc - soc) : 0
  const minsLeft =
    needSoc != null && short > 0
      ? chargeMinutes(vehicle, soc, needSoc, charger.power_kw)
      : 0
  const enough = needSoc != null && short <= 0.5

  return (
    <div className="mt-3 rounded-2xl border border-brand-300 bg-white p-3 sm:p-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-100 text-brand-700">
          <BatteryCharging className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-brand-700">
            Charging here{!charger.planned && " · not in the plan"}
          </p>
          <h2 className="mt-0.5 font-display text-base font-semibold leading-tight text-ink-900">
            {charger.name}
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            {charger.operator && <span>{charger.operator} · </span>}
            {Math.round(charger.power_kw)} kW
            {fmtBays(charger.n_points) && <> · {fmtBays(charger.n_points)}</>}
          </p>
          {charger.food_hint && (
            <div className="mt-1 inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium text-brand-800">
              <UtensilsCrossed className="h-3 w-3 shrink-0" />
              reads like {charger.food_hint}
            </div>
          )}
        </div>
        <a
          href={mapsLink(charger.lat, charger.lon)}
          target="_blank"
          rel="noopener noreferrer"
          aria-label="Look at this place in Maps"
          className="grid h-9 w-9 shrink-0 place-items-center rounded-xl border border-ink-200 text-ink-600"
        >
          <MapPin className="h-4 w-4" />
        </a>
      </div>

      <div className="mt-3 rounded-xl bg-brand-50/70 p-3">
        {needSoc == null ? (
          <p className="text-sm text-ink-600">
            Battery {socLabel}. Charge as much as you want, then re-plan from
            here.
          </p>
        ) : enough ? (
          <p className="text-sm font-semibold text-ink-900">
            You have enough to reach {nextName ?? "the next stop"} — battery{" "}
            {socLabel}.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <p className="font-display text-lg font-semibold text-ink-900">
                Charge to at least {Math.round(needSoc)}%
              </p>
              <p className="font-mono text-sm font-bold text-brand-700">
                ~{fmtDuration(minsLeft)} left
              </p>
            </div>
            <div className="mt-2 flex items-center gap-2">
              <span className="font-mono text-xs font-semibold text-ink-700">
                {socLabel}
              </span>
              <div className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-white">
                <div
                  className="absolute inset-y-0 rounded-full bg-brand-400"
                  style={{
                    left: `${Math.max(0, Math.min(soc, 100))}%`,
                    width: `${Math.max(0, Math.min(needSoc - soc, 100))}%`,
                  }}
                />
              </div>
              <span className="font-mono text-xs font-semibold text-brand-700">
                {Math.round(needSoc)}%
              </span>
            </div>
          </>
        )}
        <p className="mt-1.5 text-[11px] leading-relaxed text-ink-500">
          {needSoc != null && (
            <>
              Enough to reach {nextName ?? "the next stop"} with the reserve
              intact — a floor, not a target.{" "}
            </>
          )}
          Correct the battery below when you unplug, then re-plan.
        </p>
      </div>
    </div>
  )
}
