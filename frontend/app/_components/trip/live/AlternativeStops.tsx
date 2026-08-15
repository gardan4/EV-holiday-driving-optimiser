"use client"

/**
 * "Not this one."
 *
 * The planner optimises minutes, and minutes are not the only reason a stop is
 * a bad stop. The quickest charger on the corridor can be a lay-by behind a
 * warehouse with nowhere to eat, at nine in the evening, with everyone in the
 * car hungry. The model cannot see any of that — OpenChargeMap has no reliable
 * amenities and we store none — so this does not try to judge. It hands over
 * the next few candidates with the one thing the model CAN say about each,
 * which is what choosing it costs in arrival time, and lets the human do the
 * judging they were always better at.
 *
 * Three things that matter in the copy. **The food hint is a reading of the
 * site's NAME**, never amenity data we do not have — "reads like motorway
 * services", not "has a restaurant" — and every option still links out to
 * Maps, which is where a person can actually see whether there is a building
 * next to it. **The cost is to the destination**, not to the stop: swapping a
 * charger re-optimises every stop after it, and a per-leg number would flatter
 * the swap.
 *
 * And **bays are capacity, never availability**. OpenChargeMap lists how many
 * DC points a site has; no operator publishes free-stall counts to us, and
 * there is no aggregator behind this app. Twelve bays is a better bet than one
 * on a Friday evening and that is the whole of what the number means — so it
 * is labelled as what it is, in the one place a driver would otherwise read it
 * as "twelve free".
 */

import { useState } from "react"
import {
  AlertTriangle,
  BatteryCharging,
  Check,
  ChevronDown,
  Info,
  Loader2,
  MapPin,
  UtensilsCrossed,
  X,
} from "lucide-react"
import { Alternative, Alternatives } from "@/lib/client"
import { fmtBays, fmtDuration, fmtKm, mapsLink } from "@/lib/format"

/** The planner's own floor, for the sentence that explains why an option is
 *  amber. `simulator.SimParams.reserve_soc`, which is not configurable — if it
 *  ever becomes so, this has to come down the wire with the alternatives. */
const RESERVE_PCT = 10

export default function AlternativeStops({
  data,
  busy,
  onTake,
  onClose,
}: {
  data: Alternatives
  busy: boolean
  onTake: (alt: Alternative) => void
  onClose: () => void
}) {
  const [taking, setTaking] = useState<string | null>(null)
  // Whether the food question got an answer at all. On a German motorway the
  // fast chargers are called "Tesla Supercharger Geiselwind" and "IONITY
  // Holzkirchen Süd" — network plus place — so the reading comes back empty for
  // the whole list however hard the server looks. Saying nothing there leaves a
  // panel that promises to mention food and never does, which reads as "nowhere
  // to eat at any of these": the exact absence claim the hint is not allowed to
  // make. So the silence is stated, and stated as a fact about the NAMES.
  const nothingSaidAboutFood =
    data.alternatives.length > 0 &&
    !data.alternatives.some((a) => a.food_hint) &&
    !data.current?.food_hint

  return (
    <div className="mt-3 rounded-2xl border border-ink-200 bg-white p-3 sm:p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold text-ink-900">
            Somewhere else to charge
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            Cost is to your arrival, at {Math.round(data.speed_kph)} km/h.
          </p>
        </div>
        <button
          onClick={onClose}
          aria-label="Close"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-400 hover:bg-ink-100"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {data.current && (
        <div className="mt-3 rounded-xl border border-ink-100 bg-ink-50/60 p-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            Planned now
          </div>
          <Row alt={data.current} />
        </div>
      )}

      {data.alternatives.length === 0 ? (
        <p className="mt-3 rounded-xl border border-ink-100 px-3 py-2 text-sm text-ink-500">
          Nothing else on this stretch reaches the destination. On a thin
          corridor the plan really does depend on this one.
        </p>
      ) : (
        <ul className="mt-2 space-y-2">
          {data.alternatives.map((alt) => (
            <li
              key={alt.charger_id}
              className="rounded-xl border border-ink-100 p-2.5"
            >
              <Row alt={alt} />
              {alt.below_reserve && (
                <p className="mt-1 text-[11px] font-medium text-amber-800">
                  Under the {RESERVE_PCT}% reserve — the planner won&apos;t
                  choose this on its own.
                </p>
              )}
              <div className="mt-2 flex items-center gap-2">
                <button
                  onClick={() => {
                    setTaking(alt.charger_id)
                    onTake(alt)
                  }}
                  disabled={busy}
                  className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 ${
                    alt.below_reserve ? "bg-amber-700" : "bg-brand-600"
                  }`}
                >
                  {busy && taking === alt.charger_id ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Check className="h-4 w-4" />
                  )}
                  {alt.below_reserve ? "Push on to here" : "Stop here instead"}
                </button>
                <a
                  href={mapsLink(alt.lat, alt.lon)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-xl border border-ink-200 px-3 py-2 text-xs font-semibold text-ink-700"
                >
                  <MapPin className="h-3.5 w-3.5" />
                  Look
                </a>
              </div>
            </li>
          ))}
        </ul>
      )}

      {nothingSaidAboutFood && (
        <p className="mt-2 flex items-start gap-1.5 rounded-xl bg-ink-50 px-2.5 py-2 text-[11px] leading-relaxed text-ink-500">
          <UtensilsCrossed className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            None of these names say anything about food — most are just a
            network and a place. That&apos;s the names, not the places:{" "}
            <b className="font-semibold text-ink-600">Look</b> shows what is
            actually there.
          </span>
        </p>
      )}

      {/* The caveats, one tap away instead of five lines above the list.
          Every claim still has to be here — bays are not free stalls, a food
          hint is a reading of a name — but a wall of grey text at the top of a
          panel opened while driving is text nobody reads, which is a worse
          outcome for the same words. The planner form learned this already:
          helper text lives in the tip, not under the field. */}
      <details className="disclosure group mt-2">
        <summary className="flex cursor-pointer list-none items-center gap-1 text-[11px] font-medium text-ink-400 marker:content-['']">
          <Info className="h-3 w-3" />
          What these numbers mean
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-180" />
        </summary>
        <div className="mt-1.5 space-y-1.5 text-[11px] leading-relaxed text-ink-500">
          <p>
            <b className="font-semibold text-ink-600">Bays</b> are how many the
            site lists, not how many are free — nobody publishes live
            availability to us.
          </p>
          <p>
            <b className="font-semibold text-ink-600">Food</b> is read off the
            site&apos;s name, not from amenity data. Check it in Maps before
            counting on dinner; a charger with no hint may still sit next to a
            café we never hear about.
          </p>
          <p>
            <b className="font-semibold text-ink-600">Under the reserve</b>{" "}
            means arriving below the {RESERVE_PCT}% the planner keeps in hand.
            Every stop after it is still planned on the full reserve.
          </p>
        </div>
      </details>
    </div>
  )
}

/** One charger: what it is, and what taking it costs.
 *
 *  The cost can be NEGATIVE. A stretch option is planned under a lower floor
 *  for the leg being driven, and going further before stopping is exactly what
 *  that buys — so "4 min sooner, arriving on 6%" is a real row, and rendering
 *  it as "same" would hide half of the trade. */
function Row({ alt }: { alt: Alternative }) {
  const later = alt.delta_min >= 0.5
  const sooner = alt.delta_min <= -0.5
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-ink-900">
          {alt.name}
        </div>
        <div className="mt-0.5 truncate text-xs text-ink-500">
          {alt.operator && <span>{alt.operator} · </span>}
          {Math.round(alt.power_kw)} kW
          {fmtBays(alt.n_points) && <span> · {fmtBays(alt.n_points)}</span>} · in{" "}
          {fmtKm(alt.dist_from_here_m)}
        </div>
        {alt.food_hint && (
          <div className="mt-1 inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium text-brand-800">
            <UtensilsCrossed className="h-3 w-3 shrink-0" />
            {/* "reads like" and not "has": this is a guess from the site's
                name, and a chip that asserted amenities we do not have would
                be the exact invention the panel above disclaims. */}
            reads like {alt.food_hint}
          </div>
        )}
        <div
          className={`mt-0.5 flex items-center gap-1 text-xs ${
            alt.below_reserve ? "font-semibold text-amber-800" : "text-ink-500"
          }`}
        >
          {alt.below_reserve ? (
            <AlertTriangle className="h-3 w-3 shrink-0" />
          ) : (
            <BatteryCharging className="h-3 w-3 shrink-0 text-brand-600" />
          )}
          arrive {Math.round(alt.arrive_soc)}% · charge to{" "}
          {Math.round(alt.depart_soc)}% · {Math.round(alt.charge_min)} min
        </div>
      </div>
      <span
        className={`shrink-0 rounded-md px-1.5 py-0.5 font-mono text-xs font-bold ${
          later ? "bg-amber-100 text-amber-800" : "bg-brand-50 text-brand-700"
        }`}
        title="Change to your arrival time"
      >
        {later
          ? `+${fmtDuration(alt.delta_min)}`
          : sooner
            ? `−${fmtDuration(Math.abs(alt.delta_min))}`
            : "same"}
      </span>
    </div>
  )
}
