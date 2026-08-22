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
 * **One option at a time, side by side.** Stacked, four candidates and their
 * buttons were a screen and a half of near-identical cards inside a plan row —
 * you scrolled past the plan to read them and lost the stop they belonged to
 * on the way. They are a pager now: swipe, or the arrows, with the count in
 * the header. It is a real scroll container rather than an index that swaps
 * one card for another, so the gesture a phone user tries first is the one
 * that works, and the arrows drive the same scroll rather than a second idea
 * of where the list is.
 *
 * **It hangs off the stop it is about.** The panel used to be a card of its
 * own below the plan, titled "Somewhere else to charge" and naming no stop —
 * so on a six-stop plan, where every row has its own swap button, the one
 * thing it did not say was which row it had come from. It renders inside the
 * plan list now, directly under that row, and names it in the heading: the
 * question ("instead of THIS one") and the answer are one block on the screen.
 *
 * And **bays are capacity, never availability**. OpenChargeMap lists how many
 * DC points a site has; no operator publishes free-stall counts to us, and
 * there is no aggregator behind this app. Twelve bays is a better bet than one
 * on a Friday evening and that is the whole of what the number means — so it
 * is labelled as what it is, in the one place a driver would otherwise read it
 * as "twelve free".
 */

import { useCallback, useEffect, useRef, useState } from "react"
import {
  AlertTriangle,
  BatteryCharging,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Info,
  Loader2,
  MapPin,
  RefreshCw,
  UtensilsCrossed,
  X,
} from "lucide-react"
import { Alternative, Alternatives } from "@/lib/client"
import { fmtBays, fmtDuration, fmtKm, mapsLink } from "@/lib/format"

/** The planner's own floor, for the sentence that explains why an option is
 *  amber. `simulator.SimParams.reserve_soc`, which is not configurable — if it
 *  ever becomes so, this has to come down the wire with the alternatives. */
const RESERVE_PCT = 10

/** How far the car can move before the arrival percentages and the minute
 *  costs in this panel are describing a different journey. Distances re-render
 *  from the live position and never go stale; these cannot, because they came
 *  out of a DP run from where the car was standing when it was asked. */
const STALE_AFTER_M = 5_000

export default function AlternativeStops({
  data,
  stopName,
  stopIndex,
  stopTotal,
  anchored = false,
  distM,
  fetchedAtM,
  busy,
  onTake,
  onRefresh,
  onClose,
}: {
  data: Alternatives
  /** The plan stop these replace. Named in the heading, because a plan has
   *  several stops and each of them has its own swap button. */
  stopName: string
  /** Its place in the list above, 1-based — "stop 2 of 4". The name alone is
   *  not enough on a corridor that repeats one: two of the four candidates on
   *  the drive this came from were both called "Comfortcharge Ladestation". */
  stopIndex?: number
  stopTotal?: number
  /** True when this is rendered inside the plan list, under its own row. Then
   *  the row above is the frame and this is a nested block; standalone (the
   *  stop has already been passed, the drive has finished) it has to be a card
   *  in its own right. */
  anchored?: boolean
  /** How far the car has come, NOW. Distances are rendered against this rather
   *  than against `dist_from_here_m`, which was measured from wherever the car
   *  stood when the panel was fetched — a panel left open for an hour said a
   *  charger was 136 km ahead while the stop card above it said 261 km, about
   *  the same charger, on the same screen. */
  distM: number
  /** `distM` at the moment this answer was fetched. */
  fetchedAtM: number
  busy: boolean
  onTake: (alt: Alternative) => void
  /** Ask the server again, for the same stop, from where the car is now. */
  onRefresh?: () => void
  onClose: () => void
}) {
  const [taking, setTaking] = useState<string | null>(null)
  // Which option is in view. Derived from the scroll position rather than
  // owned: the track is scrollable, so a swipe is a first-class way of moving
  // it and a React index would immediately be a second, disagreeing answer.
  const track = useRef<HTMLUListElement>(null)
  const [idx, setIdx] = useState(0)
  const n = data.alternatives.length
  const onScroll = useCallback(() => {
    const el = track.current
    if (!el || el.clientWidth === 0) return
    setIdx(Math.round(el.scrollLeft / el.clientWidth))
  }, [])
  // A fresh answer starts at the first option — including "Redo from here",
  // which is a new list and may not even hold the charger being looked at.
  useEffect(() => {
    setIdx(0)
    track.current?.scrollTo({ left: 0 })
  }, [data])
  const go = useCallback(
    (delta: 1 | -1) => {
      const el = track.current
      if (!el) return
      const to = Math.min(Math.max(idx + delta, 0), n - 1)
      el.scrollTo({ left: to * el.clientWidth, behavior: "smooth" })
      setIdx(to)
    },
    [idx, n]
  )
  // How far the car has come since this answer was computed. The distances
  // below re-render against the live position, so they stay honest; the arrival
  // percentages and the minute costs cannot, because they came out of a DP run
  // from a standing start back there.
  const moved = Math.max(distM - fetchedAtM, 0)
  // Whether the food question got an answer at all. On a German motorway the
  // fast chargers are called "Tesla Supercharger Geiselwind" and "IONITY
  // Holzkirchen Süd" — network plus place — so the reading comes back empty for
  // the whole list however hard the server looks. Saying nothing there leaves a
  // panel that promises to mention food and never does, which reads as "nowhere
  // to eat at any of these": the exact absence claim the hint is not allowed to
  // make. So the silence is stated, and stated as a fact about the NAMES.
  // Three states, not two, and they are genuinely different answers.
  //  - somebody has amenities → chips, no footer
  //  - we asked OpenStreetMap and it maps nothing → say that, about the MAP
  //  - we never got an answer → fall back to the old sentence, about the NAMES
  const shown = [data.current, ...data.alternatives].filter(
    (a): a is Alternative => a != null
  )
  const anyFood = shown.some((a) => (a.nearby?.length ?? 0) > 0 || a.food_hint)
  const asked = shown.some((a) => Array.isArray(a.nearby))
  const nothingSaidAboutFood = shown.length > 0 && !anyFood

  return (
    <div
      className={
        anchored
          ? "mt-2 rounded-xl border border-brand-200 bg-brand-50/40 p-2.5 sm:p-3"
          : "mt-3 rounded-2xl border border-ink-200 bg-white p-3 sm:p-4"
      }
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2
            className={`font-display font-semibold text-ink-900 ${
              anchored ? "text-sm" : "text-base"
            }`}
          >
            {/* The stop, in the heading. "Somewhere else to charge" is what
                the driver asked for and says nothing about WHICH stop they
                asked it about — and the answer is a list of chargers that all
                look like plausible next stops. */}
            Instead of{" "}
            <span className="text-brand-800">{stopName}</span>
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            {stopIndex != null && stopTotal != null && (
              <>
                Stop {stopIndex} of {stopTotal} ·{" "}
              </>
            )}
            cost is to your arrival, at {Math.round(data.speed_kph)} km/h.
          </p>
          {moved >= STALE_AFTER_M && (
            <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] font-medium text-amber-800">
              <span>
                Worked out {fmtKm(moved)} back — the percentages and minutes are
                from there.
              </span>
              {onRefresh && (
                <button
                  onClick={onRefresh}
                  disabled={busy}
                  className="inline-flex items-center gap-1 rounded-lg border border-amber-300 px-2 py-1 font-semibold text-amber-900 disabled:opacity-50"
                >
                  {busy ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3 w-3" />
                  )}
                  Redo from here
                </button>
              )}
            </p>
          )}
        </div>
        {/* Anchored, the row's own swap button has become the close — two X's
            an inch apart doing the same thing is not a choice anybody makes.
            Standalone there is no row, so this is the only way out. */}
        {!anchored && (
          <button
            onClick={onClose}
            aria-label={`Close the alternatives to ${stopName}`}
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-400 hover:bg-ink-100"
          >
            <X className="h-4 w-4" />
          </button>
        )}
      </div>

      {data.current && (
        <div className="mt-3 rounded-xl border border-ink-100 bg-white p-2.5">
          {/* "Planned now" read as a claim about the plan sitting one row
              under the plan itself, and the two can differ for an ordinary
              reason: this is the same stop RE-PRICED from where the car is,
              so a battery corrected since the plan was made moves it. Saying
              which of the two it is stops that reading as a contradiction. */}
          <div className="text-[10px] font-semibold uppercase tracking-wider text-ink-400">
            Keeping it · from here
          </div>
          {/* Anchored, the row above already carries the name, the power, the
              bays and the food — repeating all of it an inch lower is the
              same stop twice, and the only part that is NOT in the row above
              is the one thing this block exists for. */}
          <Row alt={data.current} distM={distM} compact={anchored} />
        </div>
      )}

      {n === 0 ? (
        <p className="mt-3 rounded-xl border border-ink-100 px-3 py-2 text-sm text-ink-500">
          Nothing else on this stretch reaches the destination. On a thin
          corridor the plan really does depend on this one.
        </p>
      ) : (
        <>
          {/* Where you are in the options, above the card rather than under
              it: it is the label for what follows, and on a phone the bottom
              of this panel is already past the fold. */}
          <div className="mt-3 flex items-center justify-between gap-2">
            <p
              className="text-[11px] font-semibold uppercase tracking-wider text-ink-400"
              aria-live="polite"
            >
              Option{" "}
              <span className="font-mono text-ink-600">
                {Math.min(idx + 1, n)}/{n}
              </span>
            </p>
            {n > 1 && (
              <div className="flex shrink-0 items-center gap-1">
                <Step dir="prev" disabled={idx === 0} onClick={() => go(-1)} />
                <Step dir="next" disabled={idx >= n - 1} onClick={() => go(1)} />
              </div>
            )}
          </div>

          {/* A real scroll container: swipe works, the arrows drive the same
              scroll, and `snap-mandatory` means a half-swipe still lands on an
              option. `items-stretch` keeps every card the height of the
              tallest, so paging does not make the panel jump. */}
          <ul
            ref={track}
            onScroll={onScroll}
            className="mt-1.5 flex snap-x snap-mandatory items-stretch gap-2 overflow-x-auto overscroll-x-contain pb-1 [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {data.alternatives.map((alt, i) => (
              <li
                key={alt.charger_id}
                aria-label={`Option ${i + 1} of ${n}`}
                className="w-full shrink-0 snap-start"
              >
                <div className="h-full rounded-xl border border-ink-100 bg-white p-2.5">
                  <Row alt={alt} distM={distM} />
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
                      {alt.below_reserve
                        ? "Push on to here"
                        : "Stop here instead"}
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
                </div>
              </li>
            ))}
          </ul>
        </>
      )}

      {nothingSaidAboutFood && (
        <p className="mt-2 flex items-start gap-1.5 rounded-xl bg-ink-50 px-2.5 py-2 text-[11px] leading-relaxed text-ink-500">
          <UtensilsCrossed className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            {asked ? (
              <>
                OpenStreetMap maps nothing to eat within a few hundred metres of
                any of these. Coverage is uneven, so that is the map rather than
                the ground — <b className="font-semibold text-ink-600">Look</b>{" "}
                is the way to be sure.
              </>
            ) : (
              <>
                Nothing known about food at any of these — the names are just a
                network and a place, and the map had nothing to add.{" "}
                <b className="font-semibold text-ink-600">Look</b> shows what is
                actually there.
              </>
            )}
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
            <b className="font-semibold text-ink-600">Food</b> comes from
            OpenStreetMap: what its contributors have mapped within a few
            hundred metres of the plug. Where the map is silent we fall back to
            reading the site&apos;s name, and say &quot;reads like&quot; when we
            do. Neither is a guarantee of an open kitchen at nine in the
            evening — check in Maps before counting on dinner.
          </p>
          <p>
            <b className="font-semibold text-ink-600">Keeping it</b> is the
            stop you are already heading for, re-priced from where the car is
            now — so it can read differently from the same stop in the plan
            above, which is what the plan said when it was made.
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

/** Pager arrow. 36 px, the floor for something pressed in a moving car, and
 *  the same control the stop card pages its stops with. */
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
      aria-label={dir === "prev" ? "Previous option" : "Next option"}
      className="grid h-9 w-9 place-items-center rounded-lg border border-ink-200 bg-white text-ink-600 disabled:opacity-30"
    >
      {dir === "prev" ? (
        <ChevronLeft className="h-4 w-4" />
      ) : (
        <ChevronRight className="h-4 w-4" />
      )}
    </button>
  )
}


/** One charger: what it is, and what taking it costs.
 *
 *  The cost can be NEGATIVE. A stretch option is planned under a lower floor
 *  for the leg being driven, and going further before stopping is exactly what
 *  that buys — so "4 min sooner, arriving on 6%" is a real row, and rendering
 *  it as "same" would hide half of the trade. */
function Row({
  alt,
  distM,
  compact = false,
}: {
  alt: Alternative
  distM: number
  /** Drop everything the plan row directly above already says, leaving the
   *  figures it does not have. Only ever the baseline, whose delta is 0 by
   *  construction — hence no pill either. */
  compact?: boolean
}) {
  const later = alt.delta_min >= 0.5
  const sooner = alt.delta_min <= -0.5
  return (
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0">
        {!compact && (
        <div className="truncate text-sm font-semibold text-ink-900">
          {alt.name}
        </div>
        )}
        {!compact && (
        <div className="mt-0.5 text-xs text-ink-500">
          {alt.operator && <span>{alt.operator} · </span>}
          {Math.round(alt.power_kw)} kW
          {fmtBays(alt.n_points) && <span> · {fmtBays(alt.n_points)}</span>} · in{" "}
          {/* From where the car is NOW, on the same axis as the stop card and
              the plan list — never `dist_from_here_m`, which is frozen at the
              moment of the fetch. */}
          {fmtKm(Math.max(alt.offset_m - distM, 0))}
        </div>
        )}
        {/* Two different claims, said differently. `nearby` is a place
            OpenStreetMap maps within a few hundred metres — it exists, so the
            chip states it. `food_hint` is a word in a title, so the chip
            hedges. When there is real data the guess is redundant and would
            only muddle which of the two the reader is looking at. */}
        {!compact &&
          (alt.nearby && alt.nearby.length > 0 ? (
          <div className="mt-1 inline-flex items-center gap-1 rounded-md bg-brand-100 px-1.5 py-0.5 text-[11px] font-semibold text-brand-900">
            <UtensilsCrossed className="h-3 w-3 shrink-0" />
            {alt.nearby.slice(0, 2).join(" · ")} here
          </div>
        ) : (
          alt.food_hint && (
            <div className="mt-1 inline-flex items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium text-brand-800">
              <UtensilsCrossed className="h-3 w-3 shrink-0" />
              {/* "reads like" and not "has": this is a guess from the site's
                  name, and a chip that asserted amenities we do not have would
                  be the exact invention the panel above disclaims. */}
              reads like {alt.food_hint}
            </div>
          )
          ))}
        <div
          className={`mt-0.5 flex items-start gap-1 text-xs ${
            alt.below_reserve ? "font-semibold text-amber-800" : "text-ink-500"
          }`}
        >
          {alt.below_reserve ? (
            <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
          ) : (
            <BatteryCharging className="mt-0.5 h-3 w-3 shrink-0 text-brand-600" />
          )}
          arrive {Math.round(alt.arrive_soc)}% · charge to{" "}
          {Math.round(alt.depart_soc)}% · {Math.round(alt.charge_min)} min
        </div>
      </div>
      {!compact && (
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
      )}
    </div>
  )
}
