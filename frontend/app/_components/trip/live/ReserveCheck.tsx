"use client"

/**
 * "On that figure you'd arrive at IONITY Haidt Nord on 6%."
 *
 * Raised by correcting the battery downwards, which is the moment the plan's
 * next stop can stop fitting inside the reserve. The app deliberately does NOT
 * act on it: the driver may well want that stop anyway — they can see the road,
 * they know what is at the services, and the alternative is being sent to a
 * charger they never chose. So this asks, and both answers are one tap.
 *
 * Three states, and they are different sentences on purpose.
 *
 * **Tight but reachable** — the ordinary case. Keep it, or look at somewhere
 * closer. Accepting does not re-plan: the plan already goes there, so all that
 * changes is that later plans stop refusing it.
 *
 * **Out of range** — not a risk to accept, a plan that does not work. There is
 * no "keep it" button, because offering one would be the app agreeing to a
 * journey it knows ends on the hard shoulder.
 *
 * **Accepted** — a quiet line rather than a panel, with the way back. The
 * reserve is a number the driver set; having overridden it for one leg, they
 * are entitled to see that they did and to change their mind.
 */

import { AlertTriangle, BatteryLow, Check, Loader2, Search, Undo2 } from "lucide-react"
import { NextStopRisk } from "@/lib/client"

export default function ReserveCheck({
  risk,
  acceptedSoc,
  isDriver,
  busy,
  onKeep,
  onUndo,
  onFindCloser,
}: {
  /** The open question, if there is one. */
  risk: NextStopRisk | null | undefined
  /** The floor already accepted for this leg, if any. */
  acceptedSoc: number | null | undefined
  isDriver: boolean
  busy: boolean
  onKeep: () => void
  onUndo: () => void
  onFindCloser: () => void
}) {
  // Accepted: the panel collapses to a line. Shown to watchers too — the car
  // they are following is running the reserve down, and that is worth knowing
  // — but only the driver gets the way back.
  if (acceptedSoc != null && !risk) {
    return (
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-xl border border-ink-200 bg-ink-100 px-3 py-2.5 text-sm text-ink-700">
        <span className="flex items-center gap-2">
          <Check className="h-4 w-4 shrink-0 text-brand-600" />
          Going for the next stop on about {Math.round(acceptedSoc)}%, under the
          reserve.
        </span>
        {isDriver && (
          <button
            onClick={onUndo}
            disabled={busy}
            className="inline-flex min-h-11 items-center gap-1.5 px-2 font-semibold text-ink-900 underline decoration-ink-300 underline-offset-2 disabled:text-ink-400"
          >
            <Undo2 className="h-3.5 w-3.5" />
            Undo
          </button>
        )}
      </div>
    )
  }

  if (!risk) return null

  return (
    <div className="mt-3 rounded-2xl border border-amber-300 bg-amber-50 p-4">
      <p className="flex items-start gap-2 font-display text-sm font-semibold text-amber-900">
        {risk.reachable ? (
          <BatteryLow className="mt-0.5 h-4 w-4 shrink-0" />
        ) : (
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
        )}
        {risk.reachable
          ? `You'd reach ${risk.name} on about ${Math.round(risk.arrive_soc)}%`
          : `${risk.name} is out of range on that charge`}
      </p>
      <p className="mt-1 text-sm leading-relaxed text-amber-900/90">
        {risk.reachable ? (
          <>
            That is under the {Math.round(risk.reserve_soc)}% you asked to keep
            in reserve. Nothing has been changed — it is your call.
          </>
        ) : (
          <>
            On this figure the plan does not work. Something closer is the only
            option.
          </>
        )}
      </p>

      {isDriver ? (
        <div className="mt-3 flex flex-col gap-2 sm:flex-row">
          {risk.reachable && (
            <button
              onClick={onKeep}
              disabled={busy}
              className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-lg bg-amber-900 px-3 py-2 text-sm font-semibold text-white transition-colors active:scale-[0.99] hover:bg-amber-800 disabled:bg-amber-900/40"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Check className="h-4 w-4" />
              )}
              Go there anyway
            </button>
          )}
          <button
            onClick={onFindCloser}
            disabled={busy}
            className="flex min-h-11 flex-1 items-center justify-center gap-2 rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm font-semibold text-amber-900 transition-colors active:scale-[0.99] hover:bg-amber-100 disabled:text-amber-900/40"
          >
            <Search className="h-4 w-4" />
            Somewhere closer
          </button>
        </div>
      ) : (
        <p className="mt-2 text-xs text-amber-900/70">
          The driver has to decide this one.
        </p>
      )}
    </div>
  )
}
