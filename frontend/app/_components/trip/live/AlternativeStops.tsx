"use client"

/**
 * "Not this one." — as one axis you swipe along, not a list you read down.
 *
 * The planner optimises minutes, and minutes are not the only reason a stop is
 * a bad stop. The quickest charger on the corridor can be a lay-by behind a
 * warehouse with nowhere to eat, at nine in the evening, with everyone in the
 * car hungry. The model cannot see any of that — OpenChargeMap has no reliable
 * amenities and we store none — so this does not try to judge. It hands over
 * the candidates with the one thing the model CAN say about each, which is
 * what choosing it costs in arrival time, and lets the human do the judging
 * they were always better at.
 *
 * **The order is the road, and the road is a risk axis.** The cards are laid
 * out by where they sit on the route, so swiping is the same gesture as
 * moving the stop: to the RIGHT are chargers BEFORE the planned one — you
 * arrive on more, it costs a few minutes, it is the safe end — and to the LEFT
 * are chargers FURTHER on, arriving lower, ending at the ones the planner
 * would refuse on its own. That mapping is the reason this is a rail and not a
 * list: a vertical list of five cards has an order too, and it is an order the
 * reader has to decode from five arrival percentages while driving. Here the
 * question "is there somewhere safer?" is a thumb movement in one direction
 * and nothing else.
 *
 * **The far left is the wall**: the first charger the battery cannot reach at
 * all. It is not an option and cannot be taken — it is where the axis stops
 * being a choice, and without it the list simply ends at the last reachable
 * charger and says nothing about why it ends there.
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

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import {
  AlertTriangle,
  BatteryCharging,
  BatteryWarning,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Info,
  Loader2,
  MapPin,
  RefreshCw,
  ShieldCheck,
  UtensilsCrossed,
  X,
} from "lucide-react"
import { Alternative, Alternatives, Unreachable } from "@/lib/client"
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

/** Where a card sits on the axis. Derived from the ROUTE — an option is safer
 *  because it is earlier, not because we labelled it so — with the wall as the
 *  one kind that is not a plan at all. */
type Side = "further" | "planned" | "earlier"

type Card =
  | { kind: Side; key: string; alt: Alternative }
  | { kind: "wall"; key: string; wall: Unreachable }

export default function AlternativeStops({
  data,
  distM,
  fetchedAtM,
  busy,
  onTake,
  onRefresh,
  onClose,
}: {
  data: Alternatives
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
  // How far the car has come since this answer was computed. The distances
  // below re-render against the live position, so they stay honest; the arrival
  // percentages and the minute costs cannot, because they came out of a DP run
  // from a standing start back there.
  const moved = Math.max(distM - fetchedAtM, 0)

  // The axis. Sorted by position on the road, furthest first, so the DOM order
  // IS the left-to-right order the swipe promises: riskier to the left,
  // earlier and safer to the right. The wall sorts into place by itself — it
  // is by construction past everything a plan can reach.
  const here = data.current?.offset_m ?? 0
  const cards: Card[] = [
    ...(data.unreachable
      ? [
          {
            kind: "wall" as const,
            key: `wall:${data.unreachable.charger_id}`,
            wall: data.unreachable,
          },
        ]
      : []),
    ...(data.current
      ? [{ kind: "planned" as const, key: data.current.charger_id, alt: data.current }]
      : []),
    ...data.alternatives.map((alt) => ({
      kind: (alt.offset_m < here ? "earlier" : "further") as Side,
      key: alt.charger_id,
      alt,
    })),
  ].sort((a, b) => offsetOf(b) - offsetOf(a))

  const plannedIdx = Math.max(
    0,
    cards.findIndex((c) => c.kind === "planned")
  )

  const trackRef = useRef<HTMLUListElement>(null)
  const cardRefs = useRef<(HTMLLIElement | null)[]>([])
  const [active, setActive] = useState(plannedIdx)

  /** Scroll so a card sits in the middle of the rail.
   *
   *  Deliberately `scrollTo` on the track rather than `scrollIntoView` on the
   *  card: this panel lives below a full-bleed 3D scene, and asking the
   *  browser to bring an element into view scrolls the PAGE as well — the
   *  drive screen would jump every time the rail settled. */
  const centerOn = useCallback((i: number, smooth = true) => {
    const track = trackRef.current
    const card = cardRefs.current[i]
    if (!track || !card) return
    track.scrollTo({
      left: card.offsetLeft - (track.clientWidth - card.clientWidth) / 2,
      behavior: smooth ? "smooth" : "auto",
    })
  }, [])

  // Open on the plan in force, because that is the thing every other card is
  // priced against — starting at one end would make the axis read as a
  // ranking, and the answer to "is there somewhere earlier?" would be off
  // screen in the direction nobody had a reason to look.
  const shape = cards.map((c) => c.key).join("|")
  useLayoutEffect(() => {
    setActive(plannedIdx)
    centerOn(plannedIdx, false)
    // Only when the ANSWER changes. Re-centring on every render would fight
    // the thumb that is mid-swipe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [shape])

  // Which card the rail has settled on, for the dots and the arrows. Read from
  // the scroll position rather than tracked as state the buttons also write,
  // so a swipe, an arrow key and a button can never disagree about where the
  // rail is.
  const raf = useRef(0)
  useEffect(() => {
    const track = trackRef.current
    if (!track) return
    const onScroll = () => {
      cancelAnimationFrame(raf.current)
      raf.current = requestAnimationFrame(() => {
        const mid = track.scrollLeft + track.clientWidth / 2
        let best = 0
        let bestD = Infinity
        cardRefs.current.forEach((c, i) => {
          if (!c) return
          const d = Math.abs(c.offsetLeft + c.clientWidth / 2 - mid)
          if (d < bestD) {
            bestD = d
            best = i
          }
        })
        setActive(best)
      })
    }
    track.addEventListener("scroll", onScroll, { passive: true })
    return () => {
      track.removeEventListener("scroll", onScroll)
      cancelAnimationFrame(raf.current)
    }
  }, [])

  function step(delta: 1 | -1) {
    centerOn(Math.min(Math.max(active + delta, 0), cards.length - 1))
  }

  const activeCard = cards[active]

  return (
    <div className="mt-3 rounded-2xl border border-ink-200 bg-white p-3 sm:p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="font-display text-base font-semibold text-ink-900">
            Somewhere else to charge
          </h2>
          <p className="mt-0.5 text-xs text-ink-500">
            Swipe: right for earlier and safer, left to push on. Cost is to your
            arrival, at {Math.round(data.speed_kph)} km/h.
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
        <button
          onClick={onClose}
          aria-label="Close"
          className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-ink-400 hover:bg-ink-100"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {data.alternatives.length === 0 && (
        <p className="mt-3 rounded-xl border border-ink-100 px-3 py-2 text-sm text-ink-500">
          Nothing else on this stretch reaches the destination. On a thin
          corridor the plan really does depend on this one.
        </p>
      )}

      {cards.length > 0 && (
        <>
          {/* The axis, named. Two words at each end and a rule between them:
              without it the rail is just cards that move, and which direction
              means "safer" is something the reader has to infer from arrival
              percentages while driving. */}
          <div className="mt-3 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider">
            <button
              onClick={() => step(-1)}
              disabled={active === 0}
              aria-label="Further on"
              className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-ink-200 text-ink-500 disabled:opacity-30"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="shrink-0 text-amber-700">further · riskier</span>
            <span className="h-px flex-1 bg-gradient-to-r from-amber-300 via-ink-200 to-brand-300" />
            <span className="shrink-0 text-brand-700">earlier · safer</span>
            <button
              onClick={() => step(1)}
              disabled={active >= cards.length - 1}
              aria-label="Earlier stop"
              className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-ink-200 text-ink-500 disabled:opacity-30"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          </div>

          {/* One rail, native scroll-snap. A phone swipes it, a trackpad
              scrolls it, arrow keys step it — all three land on the same card
              because the position is read back off the element rather than
              held in three places. `-mx-*` + padding so a snapped card can sit
              centred without the panel's own padding cropping its neighbours. */}
          <ul
            ref={trackRef}
            aria-label="Charging options, riskiest first"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight") {
                e.preventDefault()
                step(1)
              } else if (e.key === "ArrowLeft") {
                e.preventDefault()
                step(-1)
              }
            }}
            className="relative -mx-3 mt-2 flex snap-x snap-mandatory gap-2 overflow-x-auto overscroll-x-contain rounded-xl px-3 pb-2 outline-none focus-visible:ring-2 focus-visible:ring-brand-400 sm:-mx-4 sm:px-4 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
          >
            {cards.map((card, i) => (
              <li
                key={card.key}
                ref={(el) => {
                  cardRefs.current[i] = el
                }}
                aria-current={i === active}
                className="w-[85%] max-w-sm shrink-0 list-none snap-center"
              >
                {card.kind === "wall" ? (
                  <WallCard wall={card.wall} distM={distM} speedKph={data.speed_kph} />
                ) : (
                  <OptionCard
                    alt={card.alt}
                    side={card.kind}
                    distM={distM}
                    busy={busy}
                    taking={taking === card.alt.charger_id}
                    onTake={() => {
                      setTaking(card.alt.charger_id)
                      onTake(card.alt)
                    }}
                  />
                )}
              </li>
            ))}
          </ul>

          {/* Where you are on the axis. Dots and not a scrollbar: five of them
              is a shape you read at a glance, which is the whole budget a
              driver has for this. */}
          <div className="mt-1 flex items-center justify-center gap-1.5">
            {cards.map((c, i) => (
              <button
                key={c.key}
                onClick={() => centerOn(i)}
                aria-label={labelOf(c)}
                className={`h-1.5 rounded-full transition-all ${
                  i === active ? "w-4" : "w-1.5"
                } ${
                  c.kind === "wall"
                    ? "bg-ink-300"
                    : c.kind === "planned"
                      ? "bg-ink-900"
                      : c.kind === "earlier"
                        ? "bg-brand-500"
                        : "bg-amber-500"
                } ${i === active ? "" : "opacity-40"}`}
              />
            ))}
          </div>
          <p className="mt-1 text-center text-[11px] text-ink-400">
            {activeCard ? labelOf(activeCard) : ""}
          </p>
        </>
      )}

      {/* The caveats, one tap away instead of five lines above the rail.
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
            <b className="font-semibold text-ink-600">The order is the road.</b>{" "}
            Cards to the right are chargers before the planned one: you arrive
            with more in the battery and it usually costs a few minutes. To the
            left they are further on, arriving lower.
          </p>
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
            <b className="font-semibold text-ink-600">Under the reserve</b>{" "}
            means arriving below the {RESERVE_PCT}% the planner keeps in hand.
            Every stop after it is still planned on the full reserve.
          </p>
        </div>
      </details>
    </div>
  )
}

function offsetOf(c: Card): number {
  return c.kind === "wall" ? c.wall.offset_m : c.alt.offset_m
}

/** What the card under the thumb is, in words. The dots carry the shape and
 *  this carries the meaning — a colour is not a label, and on this screen it
 *  is the difference between "safer" and "the planner refuses this". */
function labelOf(c: Card): string {
  if (c.kind === "wall") return "Out of reach — as far as this leg goes"
  if (c.kind === "planned") return "The stop you're heading for"
  if (c.kind === "earlier") return "Earlier — you arrive with more"
  return c.alt.below_reserve
    ? "Further — under the reserve"
    : "Further — you arrive with less"
}

/** One charger: what it is, and what taking it costs.
 *
 *  The cost can be NEGATIVE. A stretch option is planned under a lower floor
 *  for the leg being driven, and going further before stopping is exactly what
 *  that buys — so "4 min sooner, arriving on 6%" is a real row, and rendering
 *  it as "same" would hide half of the trade. */
function OptionCard({
  alt,
  side,
  distM,
  busy,
  taking,
  onTake,
}: {
  alt: Alternative
  side: Side
  distM: number
  busy: boolean
  taking: boolean
  onTake: () => void
}) {
  const later = alt.delta_min >= 0.5
  const sooner = alt.delta_min <= -0.5
  const planned = side === "planned"
  const tone = planned
    ? "border-ink-300 bg-ink-50/60"
    : side === "earlier"
      ? "border-brand-200 bg-brand-50/40"
      : alt.below_reserve
        ? "border-amber-300 bg-amber-50/50"
        : "border-amber-200 bg-white"

  return (
    <div className={`flex h-full flex-col rounded-xl border p-2.5 ${tone}`}>
      <div className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider">
        {planned ? (
          <span className="text-ink-500">Planned now</span>
        ) : side === "earlier" ? (
          <span className="inline-flex items-center gap-1 text-brand-700">
            <ShieldCheck className="h-3 w-3" /> Earlier · safer
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-amber-700">
            <AlertTriangle className="h-3 w-3" /> Further · riskier
          </span>
        )}
        <span
          className={`ml-auto rounded-md px-1.5 py-0.5 font-mono text-xs font-bold normal-case tracking-normal ${
            later ? "bg-amber-100 text-amber-800" : "bg-brand-50 text-brand-700"
          }`}
          title="Change to your arrival time"
        >
          {planned
            ? "the plan"
            : later
              ? `+${fmtDuration(alt.delta_min)}`
              : sooner
                ? `−${fmtDuration(Math.abs(alt.delta_min))}`
                : "same"}
        </span>
      </div>

      <div className="mt-1 truncate text-sm font-semibold text-ink-900">
        {alt.name}
      </div>
      <div className="mt-0.5 truncate text-xs text-ink-500">
        {alt.operator && <span>{alt.operator} · </span>}
        {Math.round(alt.power_kw)} kW
        {fmtBays(alt.n_points) && <span> · {fmtBays(alt.n_points)}</span>} · in{" "}
        {/* From where the car is NOW, on the same axis as the stop card and
            the plan list — never `dist_from_here_m`, which is frozen at the
            moment of the fetch. */}
        {fmtKm(Math.max(alt.offset_m - distM, 0))}
      </div>

      {/* Two different claims, said differently. `nearby` is a place
          OpenStreetMap maps within a few hundred metres — it exists, so the
          chip states it. `food_hint` is a word in a title, so the chip
          hedges. When there is real data the guess is redundant and would
          only muddle which of the two the reader is looking at. */}
      {alt.nearby && alt.nearby.length > 0 ? (
        <div className="mt-1 inline-flex w-fit items-center gap-1 rounded-md bg-brand-100 px-1.5 py-0.5 text-[11px] font-semibold text-brand-900">
          <UtensilsCrossed className="h-3 w-3 shrink-0" />
          {alt.nearby.slice(0, 2).join(" · ")} here
        </div>
      ) : (
        alt.food_hint && (
          <div className="mt-1 inline-flex w-fit items-center gap-1 rounded-md bg-brand-50 px-1.5 py-0.5 text-[11px] font-medium text-brand-800">
            <UtensilsCrossed className="h-3 w-3 shrink-0" />
            {/* "reads like" and not "has": this is a guess from the site's
                name, and a chip that asserted amenities we do not have would
                be the exact invention the panel above disclaims. */}
            reads like {alt.food_hint}
          </div>
        )
      )}

      <div
        className={`mt-1 flex items-center gap-1 text-xs ${
          alt.below_reserve ? "font-semibold text-amber-800" : "text-ink-600"
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

      {alt.below_reserve && (
        <p className="mt-1 text-[11px] font-medium text-amber-800">
          Under the {RESERVE_PCT}% reserve — the planner won&apos;t choose this
          on its own.
        </p>
      )}

      <div className="mt-2 flex items-center gap-2 pt-1">
        {planned ? (
          <span className="text-[11px] text-ink-500">
            Where you&apos;re heading. Swipe for somewhere else.
          </span>
        ) : (
          <button
            onClick={onTake}
            disabled={busy}
            className={`inline-flex items-center gap-1.5 rounded-xl px-3 py-2 text-sm font-semibold text-white disabled:opacity-50 ${
              alt.below_reserve
                ? "bg-amber-700"
                : side === "earlier"
                  ? "bg-brand-600"
                  : "bg-brand-600"
            }`}
          >
            {busy && taking ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Check className="h-4 w-4" />
            )}
            {alt.below_reserve
              ? "Push on to here"
              : side === "earlier"
                ? "Stop here first"
                : "Stop here instead"}
          </button>
        )}
        <a
          href={mapsLink(alt.lat, alt.lon)}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto inline-flex items-center gap-1.5 rounded-xl border border-ink-200 bg-white px-3 py-2 text-xs font-semibold text-ink-700"
        >
          <MapPin className="h-3.5 w-3.5" />
          Look
        </a>
      </div>
    </div>
  )
}

/** The end of the axis: the first charger the battery cannot reach.
 *
 *  Not an option, and it must not look like one — no cost in minutes, no
 *  arrival percentage, no button. It is here because every other card answers
 *  "where else could I stop?" and none of them answers "how much further does
 *  this leg go?", which is the question underneath all of them. The list
 *  otherwise just runs out, and a driver reading a list that runs out has no
 *  way to tell a thin corridor from a flat battery.
 *
 *  The claim is narrow and the copy says so: driving past everything else, at
 *  the speed in force, on what is in the battery now. */
function WallCard({
  wall,
  distM,
  speedKph,
}: {
  wall: Unreachable
  distM: number
  speedKph: number
}) {
  return (
    <div className="flex h-full flex-col rounded-xl border border-dashed border-ink-300 bg-ink-50/70 p-2.5">
      <div className="flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-ink-500">
        <BatteryWarning className="h-3 w-3" /> Out of reach
      </div>
      <div className="mt-1 truncate text-sm font-semibold text-ink-500">
        {wall.name}
      </div>
      <div className="mt-0.5 truncate text-xs text-ink-400">
        {wall.operator && <span>{wall.operator} · </span>}
        {Math.round(wall.power_kw)} kW · in{" "}
        {fmtKm(Math.max(wall.offset_m - distM, 0))}
      </div>
      <p className="mt-1.5 text-[11px] leading-relaxed text-ink-500">
        Drive past everything else at {Math.round(speedKph)} km/h and you stop
        about <b className="font-semibold text-ink-700">{fmtKm(wall.shortfall_m)}</b>{" "}
        short of it — roughly {Math.round(wall.deficit_pct)}% more battery than
        you have. This is where the swipe ends, not something to take.
      </p>
      <a
        href={mapsLink(wall.lat, wall.lon)}
        target="_blank"
        rel="noopener noreferrer"
        className="mt-auto inline-flex w-fit items-center gap-1.5 pt-2 text-xs font-semibold text-ink-500 underline decoration-ink-300 underline-offset-2"
      >
        <MapPin className="h-3.5 w-3.5" />
        Look anyway
      </a>
    </div>
  )
}
