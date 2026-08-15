"use client"

/**
 * The speed you are going to try to hit, decided by you.
 *
 * The re-plan swept a band around the planned speed and took whichever was
 * fastest, which quietly decides something the driver is better placed to
 * decide. Six hours in, "I'm going to sit at 120" is a fact about the road,
 * the weather and whoever is asleep in the back — the plan's job is to say
 * what it costs, not to overrule it.
 *
 * The preview is free. A re-plan already returns every speed it swept, so
 * stepping through them re-reads a list that is already in the browser and
 * shows the arrival for each one instantly — no request per tap, no lag on a
 * phone with one bar. Only committing costs a round trip.
 *
 * Two things this is careful about:
 *
 * - **The number shown while stepping is the arrival, not a delta from the
 *   optimum.** The optimum is the app's opinion; the arrival is the thing the
 *   passengers are asking about, and the comparison sits under it in smaller
 *   type rather than framing the choice as a deviation from what we wanted.
 * - **"Fastest" is one of the options**, so the choice is reversible in the
 *   same control that made it. A preference you cannot hand back is a trap,
 *   and on the server that is an explicit null rather than an absent field.
 */

import { useState } from "react"
import { Check, Gauge, Loader2 } from "lucide-react"
import { RevisedPlan } from "@/lib/client"
import { clockAt, fmtDuration } from "@/lib/format"

export default function HoldSpeed({
  plan,
  startedAtIso,
  busy,
  onHold,
}: {
  /** The revision in force. Its `speeds` are the sweep this steps through. */
  plan: RevisedPlan
  startedAtIso: string
  busy: boolean
  /** null means "give the choice back to the optimiser". */
  onHold: (speedKph: number | null) => void
}) {
  const feasible = plan.speeds.filter((s) => s.feasible && s.total_min != null)
  const inForce = plan.optimum_speed ?? feasible[0]?.speed_kph ?? 0
  const [picked, setPicked] = useState<number>(inForce)
  const [seen, setSeen] = useState<number>(inForce)

  // Follow the server when the plan changes underneath — a re-plan, or another
  // decision that moved the speed — rather than stranding the stepper on a
  // number that is no longer anything. Adjusted during render against the last
  // value seen, which is React's own pattern for this: an effect would re-run
  // the component a second time for every plan poll just to correct a number
  // it could have had right the first time.
  if (seen !== inForce) {
    setSeen(inForce)
    setPicked(inForce)
  }

  if (feasible.length === 0) return null

  const current = feasible.find((s) => s.speed_kph === picked) ?? null
  const fastest = feasible.reduce((a, b) =>
    (a.total_min ?? Infinity) <= (b.total_min ?? Infinity) ? a : b
  )
  const costMin = current ? (current.total_min ?? 0) - (fastest.total_min ?? 0) : 0
  const dirty = picked !== inForce

  function step(dir: 1 | -1) {
    const i = feasible.findIndex((s) => s.speed_kph === picked)
    const next = feasible[Math.min(Math.max(i + dir, 0), feasible.length - 1)]
    if (next) setPicked(next.speed_kph)
  }

  return (
    <div className="mt-3 rounded-2xl border border-ink-100 bg-white p-3 sm:p-4">
      <div className="flex items-center gap-2">
        <Gauge className="h-4 w-4 shrink-0 text-ink-400" />
        <h2 className="font-display text-sm font-semibold text-ink-900">
          Speed you&apos;re holding
        </h2>
        {plan.held_speed_kph != null && (
          <span className="rounded-md bg-ink-100 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-ink-600">
            yours
          </span>
        )}
      </div>

      <div className="mt-2 flex items-center gap-2">
        <Big label="−" onClick={() => step(-1)} disabled={busy} />
        <div className="flex-1 text-center">
          <div className="font-display text-4xl font-bold tabular-nums tracking-tight text-ink-900">
            {picked}
            <span className="ml-1 text-lg font-semibold text-ink-400">km/h</span>
          </div>
          {current?.total_min != null && (
            <div className="mt-0.5 text-xs text-ink-500">
              arrive {clockAt(startedAtIso, plan.elapsed_min + current.total_min)}
              {" · "}
              {current.n_stops} {current.n_stops === 1 ? "stop" : "stops"} left
            </div>
          )}
          <div className="text-[11px] text-ink-400">
            {costMin >= 0.5
              ? `${fmtDuration(costMin)} slower than ${fastest.speed_kph} km/h`
              : "the fastest of these"}
          </div>
        </div>
        <Big label="+" onClick={() => step(1)} disabled={busy} />
      </div>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          onClick={() => onHold(picked)}
          disabled={busy || !dirty}
          className="inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-40"
        >
          {busy ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Check className="h-4 w-4" />
          )}
          {dirty ? `Hold ${picked}` : "Holding this"}
        </button>
        {plan.held_speed_kph != null && (
          <button
            onClick={() => onHold(null)}
            disabled={busy}
            className="px-2 py-2 text-sm text-ink-500 underline underline-offset-2 disabled:opacity-40"
          >
            Use the fastest instead
          </button>
        )}
      </div>
    </div>
  )
}

/** 44px minimum, because this is operated in a car. */
function Big({
  label,
  onClick,
  disabled,
}: {
  label: string
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      aria-label={label === "+" ? "Faster" : "Slower"}
      className="grid h-12 w-12 shrink-0 place-items-center rounded-2xl border border-ink-200 bg-white font-mono text-xl font-bold text-ink-700 active:bg-ink-100 disabled:opacity-40"
    >
      {label}
    </button>
  )
}