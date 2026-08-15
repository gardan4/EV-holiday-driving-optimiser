"use client"

/**
 * Keeping the battery estimate honest.
 *
 * This is the one thing the app genuinely needs a human to do, repeatedly, and
 * its whole accuracy decays without it. So it asks rather than waits, and it
 * asks at the only moment that is actually safe and easy: standing at a
 * charger, engine off, dashboard lit.
 *
 * The most important control here is **"Spot on"**. Agreeing with the estimate
 * calibrates the model just as usefully as correcting it, and it costs one tap
 * — without it, the only way to tell the app it was right is to retype the
 * number it is already showing, which nobody will ever do.
 *
 * Asking is not the same as being available, and this used to be only the ask:
 * the card rendered when the car reached a charger or when the estimate had
 * drifted far enough to be worth querying, and the rest of the time it did not
 * exist. So a driver who simply wanted to correct the number — a passenger
 * reading the dash out on the motorway, a coffee stop that is not a charger,
 * the minutes right after setting off when the estimate is still confident —
 * had nothing to press, on the one screen whose accuracy depends on them
 * pressing it. It is now always there for the driver, as a single quiet line
 * that opens the full control. The prompting behaviour is unchanged; what
 * changed is that dismissing a prompt, or never getting one, no longer takes
 * the feature away.
 */

import { useEffect, useRef, useState } from "react"
import { BatteryMedium, Check, PlugZap } from "lucide-react"
import { toast } from "sonner"
import { LiveState } from "@/lib/client"
import { fmtDuration } from "@/lib/format"

/** Above this much uncertainty the estimate is worth confirming unprompted. */
const NUDGE_BAND = 4

export default function BatteryCheck({
  state,
  isDriver,
  busy,
  stopName,
  onSubmit,
  openSignal = 0,
}: {
  state: LiveState
  isDriver: boolean
  busy: boolean
  stopName: string | null
  onSubmit: (soc: number) => Promise<void>
  /** Bumped when the driver asks for this from somewhere else on the screen —
   *  the battery readout in the HUD, which is where they are already looking. */
  openSignal?: number
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(Math.round(state.soc))
  const atCharger = state.at_charger_id != null
  const [asked, setAsked] = useState<string | null>(null)

  // Arriving at a charger is the moment to ask — and only once per stop, so a
  // driver who dismisses it isn't nagged for the whole time they're plugged in.
  useEffect(() => {
    if (atCharger && state.at_charger_id !== asked) {
      setEditing(true)
      setDraft(Math.round(state.soc))
      setAsked(state.at_charger_id)
    }
  }, [atCharger, state.at_charger_id, asked, state.soc])

  // An open request from elsewhere on the screen. Guarded against the signal's
  // own value rather than only its identity, so the effect can depend on
  // `state.soc` — which changes on every ping — without re-opening the panel
  // under a driver who has just closed it.
  const lastOpen = useRef(openSignal)
  useEffect(() => {
    if (openSignal === lastOpen.current) return
    lastOpen.current = openSignal
    setDraft(Math.round(state.soc))
    setEditing(true)
  }, [openSignal, state.soc])

  const nudging = !state.soc_is_measured && state.soc_uncertainty_pct >= NUDGE_BAND
  // Only the driver can submit a reading, so only the driver is offered one.
  if (!isDriver) return null

  function openEditor() {
    setDraft(Math.round(state.soc))
    setEditing(true)
  }

  // Not being asked right now. The control still has to be reachable — one
  // line, no prompt, no nagging.
  if (!editing && !atCharger && !nudging) {
    return (
      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 rounded-2xl border border-ink-200 bg-white px-3 py-2">
        <p className="flex min-w-0 items-center gap-1.5 text-xs text-ink-500">
          <BatteryMedium className="h-4 w-4 shrink-0 text-ink-400" />
          <span className="truncate">
            Battery <strong className="text-ink-700">{Math.round(state.soc)}%</strong>
            {state.soc_is_measured && state.anchor_age_min < 1
              ? ", confirmed just now"
              : state.soc_is_measured
                ? `, confirmed ${fmtDuration(state.anchor_age_min)} ago`
                : ", estimated from distance driven"}
          </span>
        </p>
        <button
          onClick={openEditor}
          className="shrink-0 rounded-xl border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-700"
        >
          Correct it
        </button>
      </div>
    )
  }

  async function send(value: number) {
    try {
      await onSubmit(value)
      setEditing(false)
      const off = state.soc - value
      toast.success(
        Math.abs(off) < 1
          ? "Thanks, the estimate was on the money."
          : `Thanks, we were ${Math.abs(off).toFixed(0)}% ${
              off > 0 ? "optimistic" : "pessimistic"
            }. The rest of the drive is tuned to your car now.`
      )
    } catch (err) {
      // Silence here would be the worst outcome: the driver believes the app
      // has their real figure and it's still guessing.
      toast.error(
        err instanceof Error && err.message.includes("rate")
          ? "Give it a few seconds and try again."
          : err instanceof Error
            ? err.message
            : "Couldn't save that reading"
      )
    }
  }

  return (
    <div
      className={`mt-4 rounded-2xl border p-4 ${
        atCharger ? "border-brand-300 bg-brand-50" : "border-ink-200 bg-white"
      }`}
    >
      <div className="flex items-start gap-2">
        {atCharger ? (
          <PlugZap className="mt-0.5 h-5 w-5 shrink-0 text-brand-700" />
        ) : (
          <BatteryMedium className="mt-0.5 h-5 w-5 shrink-0 text-ink-500" />
        )}
        <div className="min-w-0 flex-1">
          <p className="font-display text-sm font-semibold text-ink-900">
            {atCharger && stopName
              ? `Plugged in at ${stopName}. What does the car say?`
              : atCharger
                ? "You're at a charger. What does the car say?"
                : "Battery check"}
          </p>
          <p className="mt-0.5 text-xs leading-relaxed text-ink-500">
            We reckon <strong>{Math.round(state.soc)}%</strong>
            {state.soc_is_measured
              ? ", confirmed just now."
              : `, estimated from distance driven, ${fmtDuration(
                  state.anchor_age_min
                )} since you last confirmed it. No car tells a web page its battery.`}
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              onClick={() => void send(Math.round(state.soc))}
              disabled={busy}
              className="inline-flex items-center gap-1.5 rounded-xl bg-brand-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
            >
              <Check className="h-4 w-4" />
              Spot on
            </button>

            <div className="flex items-center gap-1 rounded-xl border border-ink-200 bg-white px-1 py-1">
              <Stepper label="−5" onClick={() => setDraft((d) => clamp(d - 5))} />
              <Stepper label="−1" onClick={() => setDraft((d) => clamp(d - 1))} />
              <span className="w-14 text-center font-mono text-base font-bold tabular-nums text-ink-900">
                {draft}%
              </span>
              <Stepper label="+1" onClick={() => setDraft((d) => clamp(d + 1))} />
              <Stepper label="+5" onClick={() => setDraft((d) => clamp(d + 5))} />
            </div>

            <button
              onClick={() => void send(draft)}
              disabled={busy || draft === Math.round(state.soc)}
              className="rounded-xl border border-ink-300 px-3 py-2 text-sm font-semibold text-ink-700 disabled:opacity-40"
            >
              It&apos;s {draft}%
            </button>

            {!atCharger && (
              <button
                onClick={() => setEditing(false)}
                className="px-2 py-2 text-sm text-ink-500 underline underline-offset-2"
              >
                Later
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function Stepper({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      // 44px targets: this is operated in a car, sometimes in gloves.
      className="grid h-10 min-w-[44px] place-items-center rounded-lg font-mono text-sm font-bold text-ink-600 transition-colors hover:bg-ink-100 active:bg-ink-200"
    >
      {label}
    </button>
  )
}

const clamp = (n: number) => Math.max(0, Math.min(100, n))
