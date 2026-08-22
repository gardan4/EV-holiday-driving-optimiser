"use client"

/**
 * The stop, from pulling in to pulling out.
 *
 * This is the screen a driver looks at while standing at a plug, and it has
 * three jobs in the order the questions arrive: where am I, how much do I need,
 * and — the one that was missing entirely — what am I actually leaving on.
 *
 * It renders for **every** charger the car is at, planned or not. It used to
 * render only for unplanned ones, on the reasoning that a planned stop was
 * already described by the stop card above it. That was wrong in the only place
 * it mattered: parked at a stop the plan chose, the card above was already
 * counting down to the NEXT charger, so the screen described a site 77 km up the
 * road and said nothing at all about the one the car was plugged into. Reported
 * twice from the road, from two different chargers, in those words.
 *
 * **Two targets, and both of them.** `min_soc` is the floor — enough to reach
 * the next stop with the reserve intact — and it is what says "you could go
 * now". `target_soc` is what the plan chose to leave on, usually higher,
 * because leaving later at a fast charger beats arriving somewhere slower.
 * Showing only the target sits a driver through minutes they did not have to
 * spend; showing only the floor quietly throws away the optimisation the whole
 * app exists for. So both, with what each costs in minutes, and the driver
 * picks.
 *
 * **The estimate moves on the clock, not on pings.** Everything numeric here
 * comes from the server's `charging` block, which is projected from when the
 * cable went in — because a phone whose car is charging is in a pocket, and
 * anything derived from pings freezes the moment the screen locks. See
 * `live.charging_soc`.
 *
 * **And it is an estimate until the driver says otherwise.** The projection
 * assumes the cable is still in and the curve is the catalog's; the number on
 * the dashboard is the only ground truth this app ever gets. So the last thing
 * on the card is "I'm leaving on ___%", which ends the stop, anchors the
 * battery, and is what the rest of the drive is then planned from.
 */

import { useEffect, useState } from "react"
import { BatteryCharging, Check, LogOut, MapPin, UtensilsCrossed } from "lucide-react"
import { AtCharger, ChargingSession } from "@/lib/client"
import { fmtBays, fmtDuration, mapsLink } from "@/lib/format"

export default function ChargingHere({
  charger,
  soc,
  socLabel,
  session,
  nextName,
  isDriver,
  busy,
  onCorrect,
  onLeave,
}: {
  charger: AtCharger
  /** The battery estimate now — the projection while the cable is in. */
  soc: number
  /** …as text, with its range. */
  socLabel: string
  /** The live session, when the server has one running. */
  session: ChargingSession | null
  /** Where the floor gets you to. */
  nextName: string | null
  isDriver: boolean
  busy: boolean
  /** "It's actually this much." Re-anchors and keeps the charge running. */
  onCorrect: (soc: number) => void
  /** "I'm leaving on this much." Ends the stop. Driver only, both of them. */
  onLeave: (soc: number) => void
}) {
  const min = session?.min_soc ?? null
  const target = session?.target_soc ?? null
  // The bar runs to whichever goal is further out, so both markers fit on it
  // and the one already passed reads as passed.
  const top = Math.max(min ?? 0, target ?? 0, soc)

  return (
    <div className="mt-3 rounded-2xl border border-brand-300 bg-white p-3 sm:p-4">
      <div className="flex items-start gap-3">
        <div className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-brand-100 text-brand-700">
          <BatteryCharging className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-semibold uppercase tracking-wider text-brand-700">
            You&apos;re here{!charger.planned && " · not in the plan"}
            {session && ` · plugged in ${fmtDuration(session.since_min)}`}
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
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <p className="font-display text-2xl font-semibold tabular-nums text-ink-900">
            {socLabel}
          </p>
          {session && (
            <p className="text-xs text-ink-500">
              at {Math.round(session.power_kw)} kW, from{" "}
              {Math.round(session.start_soc)}%
            </p>
          )}
        </div>

        {/* Where the two goals sit relative to the battery now. */}
        <div className="relative mt-2 h-2 overflow-hidden rounded-full bg-white">
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-brand-400"
            style={{ width: `${pct(soc, top)}%` }}
          />
          {min != null && <Tick at={pct(min, top)} tone="ink" />}
          {target != null && <Tick at={pct(target, top)} tone="brand" />}
        </div>

        <div className="mt-2.5 space-y-1.5">
          <Goal
            label={`Enough for ${nextName ?? "the next stop"}`}
            soc={min}
            left={session?.to_min_min ?? null}
            now={soc}
            tone="ink"
          />
          <Goal
            label="The plan leaves on"
            soc={target}
            left={session?.to_target_min ?? null}
            now={soc}
            tone="brand"
          />
        </div>

        <p className="mt-2 text-[11px] leading-relaxed text-ink-500">
          {min != null && target != null && target > min ? (
            <>
              The floor gets you there with the reserve intact; the plan&apos;s
              figure is what makes the rest of the drive quickest. Anywhere
              between the two is a fair trade.{" "}
            </>
          ) : target == null ? (
            <>
              The plan didn&apos;t choose this charger, so there&apos;s no
              target for it — only the floor to reach the next one.{" "}
            </>
          ) : null}
          Counted from the clock, not from your phone, so it keeps going while
          the screen is off. It&apos;s an estimate until you tell it otherwise.
        </p>
      </div>

      {isDriver && (
        <ReadingControls
          soc={soc}
          busy={busy}
          onCorrect={onCorrect}
          onLeave={onLeave}
        />
      )}
    </div>
  )
}

/** ONE number, TWO things it can mean.
 *
 *  A driver at a plug types their battery for two different reasons, ten
 *  minutes apart: to correct the estimate the projection is running on, and to
 *  say what they are pulling out with. Both are the same gesture — read the
 *  dashboard, dial the number — so they share one stepper and differ only in
 *  which button ends it. Two separate steppers on one card is how a stop turns
 *  into a form.
 *
 *  Prefilled with the projection and stepped rather than typed, because it is
 *  operated in a car park, one-handed, sometimes in gloves. The prefill follows
 *  the projection until the driver touches it — the number climbs while the
 *  card sits open, and handing them a stale default is how "leaving on 41%"
 *  gets submitted for a car on 68%. */
function ReadingControls({
  soc,
  busy,
  onCorrect,
  onLeave,
}: {
  soc: number
  busy: boolean
  onCorrect: (soc: number) => void
  onLeave: (soc: number) => void
}) {
  const [draft, setDraft] = useState(Math.round(soc))
  const [touched, setTouched] = useState(false)
  useEffect(() => {
    if (!touched) setDraft(Math.round(soc))
  }, [soc, touched])

  function step(by: number) {
    setTouched(true)
    setDraft((d) => Math.max(0, Math.min(100, d + by)))
  }

  return (
    <div className="mt-3 border-t border-ink-100 pt-3">
      <p className="text-sm font-semibold text-ink-900">
        What does the car say?
      </p>
      <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
        Correcting it keeps the estimate honest while you charge. Leaving ends
        the stop and plans the rest from your real figure.
      </p>
      <div className="mt-2 flex items-center gap-1 rounded-xl border border-ink-200 bg-white px-1 py-1">
        <Step label="−5" onClick={() => step(-5)} />
        <Step label="−1" onClick={() => step(-1)} />
        <span className="min-w-14 flex-1 text-center font-mono text-lg font-bold tabular-nums text-ink-900">
          {draft}%
        </span>
        <Step label="+1" onClick={() => step(1)} />
        <Step label="+5" onClick={() => step(5)} />
      </div>
      <div className="mt-2 grid grid-cols-2 gap-2">
        <button
          onClick={() => {
            setTouched(false)
            onCorrect(draft)
          }}
          disabled={busy}
          className="inline-flex items-center justify-center gap-1.5 rounded-xl border border-ink-300 px-3 py-2.5 text-sm font-semibold text-ink-700 disabled:opacity-40"
        >
          <Check className="h-4 w-4 shrink-0" />
          It&apos;s {draft}%
        </button>
        <button
          onClick={() => onLeave(draft)}
          disabled={busy}
          className="inline-flex items-center justify-center gap-1.5 rounded-xl bg-brand-600 px-3 py-2.5 text-sm font-semibold text-white disabled:opacity-50"
        >
          <LogOut className="h-4 w-4 shrink-0" />
          Leaving on {draft}%
        </button>
      </div>
    </div>
  )
}

function Goal({
  label,
  soc,
  left,
  now,
  tone,
}: {
  label: string
  soc: number | null
  left: number | null
  now: number
  tone: "ink" | "brand"
}) {
  if (soc == null) return null
  const reached = now >= soc - 0.5
  return (
    <div className="flex items-baseline justify-between gap-2 text-sm">
      <span className="min-w-0 truncate text-ink-600">
        {label}{" "}
        <strong
          className={`font-mono tabular-nums ${
            tone === "brand" ? "text-brand-700" : "text-ink-900"
          }`}
        >
          {Math.round(soc)}%
        </strong>
      </span>
      <span
        className={`shrink-0 font-mono text-xs font-bold ${
          reached ? "text-brand-700" : "text-ink-500"
        }`}
      >
        {reached ? "reached" : left != null ? `~${fmtDuration(left)}` : "—"}
      </span>
    </div>
  )
}

function Tick({ at, tone }: { at: number; tone: "ink" | "brand" }) {
  return (
    <span
      aria-hidden
      className={`absolute inset-y-0 w-0.5 ${
        tone === "brand" ? "bg-brand-700" : "bg-ink-400"
      }`}
      style={{ left: `calc(${at}% - 1px)` }}
    />
  )
}

function Step({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      // 44px targets: this is operated in a car park, sometimes in gloves.
      className="grid h-11 min-w-[44px] place-items-center rounded-lg font-mono text-sm font-bold text-ink-600 transition-colors hover:bg-ink-100 active:bg-ink-200"
    >
      {label}
    </button>
  )
}

const pct = (v: number, top: number) =>
  Math.max(0, Math.min(100, (v / Math.max(top, 1)) * 100))
