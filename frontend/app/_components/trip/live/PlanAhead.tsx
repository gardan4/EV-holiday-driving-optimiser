"use client"

/**
 * The rest of the journey, as a list, always.
 *
 * The drive screen used to show the plan only after a re-plan — the revised
 * panel carried the stop list, and until you pressed the button there was
 * nothing on the page but the next stop and a diorama. So the question every
 * passenger asks ("where are we stopping, and when do we eat?") had no answer
 * on the screen built for the drive, while the results page they left at home
 * had it in full.
 *
 * Three things it does that the next-stop card cannot:
 *
 * - **It shows the whole tail**, ending at the destination, so the shape of
 *   the evening is visible: three stops or six, and which of them is dinner.
 * - **It says where you could eat**, from `services/amenities.py`. That is a
 *   reading of the site's NAME and it says so — the value is highest here,
 *   because a food stop four hours out is a decision you want to make now
 *   rather than when you arrive hungry at a lay-by.
 * - **Every stop is swappable**, not just the next one. By the time a stop is
 *   the next one, the choice about it has been made for you.
 *
 * Distances are from the CAR, not from this morning: this is a list about the
 * road ahead and it is written in the road ahead's units.
 */

import {
  BatteryCharging,
  Flag,
  Loader2,
  Replace,
  UtensilsCrossed,
} from "lucide-react"
import { Stop } from "@/lib/client"
import { fmtBays, fmtKm } from "@/lib/format"

export default function PlanAhead({
  stops,
  distM,
  atChargerId = null,
  focusedId = null,
  onFocus,
  destLabel,
  destArrivalClock,
  onSwap,
  swappingId = null,
}: {
  /** The stop under the car, if any, then everything still ahead. */
  stops: Stop[]
  distM: number
  /** The charger the car is plugged into. Marked, and never "in 0 km". */
  atChargerId?: string | null
  /** The one the card above is showing. */
  focusedId?: string | null
  /** Tap a row to bring it into the card. */
  onFocus?: (chargerId: string) => void
  destLabel: string
  /** Clock time at the destination, on the plan in force. */
  destArrivalClock: string
  /** Driver only: replace this stop with something else. */
  onSwap?: (chargerId: string) => void
  /** The stop whose alternatives are being fetched, if any. The lookup takes
   *  seconds, and a row that gave no sign of having heard the tap got tapped
   *  again — one question, several requests, and eventually a refusal. Every
   *  swap is held while one is in flight, because they all go to the same
   *  per-run budget. */
  swappingId?: string | null
}) {
  return (
    <div className="mt-3 rounded-2xl border border-ink-100 bg-white p-3 sm:p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="font-display text-sm font-semibold text-ink-900">
          The rest of the plan
        </h2>
        <span className="text-xs text-ink-400">
          {stops.length === 0
            ? "no stops left"
            : `${stops.length} ${stops.length === 1 ? "stop" : "stops"}`}
        </span>
      </div>

      <ol className="mt-2 space-y-1.5">
        {stops.map((s) => {
          const atThis = s.charger_id === atChargerId
          const focused = s.charger_id === focusedId
          return (
          <li
            key={s.charger_id}
            className={`flex items-start gap-2.5 rounded-xl border p-2.5 transition-colors ${
              focused
                ? "border-brand-300 bg-brand-50/50"
                : "border-ink-100"
            }`}
          >
            <BatteryCharging
              className={`mt-0.5 h-4 w-4 shrink-0 ${
                atThis ? "text-brand-700" : "text-brand-600"
              }`}
            />
            {/* The whole row focuses the card. A stop is a thing you look at
                before it is a thing you act on. */}
            <button
              onClick={() => onFocus?.(s.charger_id)}
              disabled={!onFocus}
              className="min-w-0 flex-1 text-left disabled:cursor-default"
            >
              <div className="truncate text-sm font-medium text-ink-900">
                {s.name}
                {atThis && (
                  <span className="ml-1.5 rounded bg-brand-100 px-1 py-0.5 align-middle text-[10px] font-semibold uppercase tracking-wide text-brand-800">
                    charging
                  </span>
                )}
              </div>
              <div className="mt-0.5 text-[11px] text-ink-500">
                {atThis ? "here now" : `in ${fmtKm(Math.max(s.offset_m - distM, 0))}`} ·{" "}
                {Math.round(s.arrive_soc)}→{Math.round(s.depart_soc)}% ·{" "}
                {Math.round(s.charge_min)} min
                {fmtBays(s.n_points) && <> · {fmtBays(s.n_points)}</>}
              </div>
              {/* Two different claims, said differently — the same rule
                  `AlternativeStops` follows, because the two lists describe
                  the same chargers and disagreeing about one on a single
                  screen is what made this worth fixing. `nearby` is a place
                  OpenStreetMap maps within a few hundred metres, so the chip
                  states it; `food_hint` is a word in a title, so it hedges.
                  With real data the guess is redundant and would only muddle
                  which of the two the reader is looking at. */}
              {s.nearby && s.nearby.length > 0 ? (
                <div className="mt-1 inline-flex items-center gap-1 rounded-md bg-brand-100 px-1.5 py-0.5 text-[11px] font-semibold text-brand-900">
                  <UtensilsCrossed className="h-3 w-3 shrink-0" />
                  {s.nearby.slice(0, 2).join(" · ")} here
                </div>
              ) : (
                s.food_hint && (
                  <div className="mt-1 inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium text-brand-800">
                    <UtensilsCrossed className="h-3 w-3 shrink-0" />
                    reads like {s.food_hint}
                  </div>
                )
              )}
            </button>
            {onSwap && (
              <button
                onClick={() => onSwap(s.charger_id)}
                disabled={swappingId !== null}
                aria-busy={swappingId === s.charger_id}
                aria-label={`Swap ${s.name} for somewhere else`}
                className="grid h-9 w-9 shrink-0 place-items-center rounded-lg text-ink-400 transition-colors hover:bg-ink-100 hover:text-brand-700 disabled:hover:bg-transparent disabled:hover:text-ink-400"
              >
                {swappingId === s.charger_id ? (
                  <Loader2 className="h-4 w-4 animate-spin text-brand-600" />
                ) : (
                  <Replace
                    className={`h-4 w-4 ${swappingId !== null ? "opacity-40" : ""}`}
                  />
                )}
              </button>
            )}
          </li>
          )
        })}

        {/* The destination closes the list, so the tail reads as a journey
            rather than as a set of chargers that stops in mid-air. */}
        <li className="flex items-center gap-2.5 px-2.5 pt-0.5 text-xs text-ink-500">
          <Flag className="h-4 w-4 shrink-0 text-ink-400" />
          <span className="min-w-0 truncate">
            {destLabel} · arrive {destArrivalClock}
          </span>
        </li>
      </ol>
    </div>
  )
}
