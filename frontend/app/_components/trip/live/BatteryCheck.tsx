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
 *
 * Watchers get that line too, without the button — reading the battery is not
 * the capability being withheld from them, writing it is.
 */

import { ReactNode, useEffect, useRef, useState } from "react"
import { BatteryMedium, Check, Pencil, PlugZap } from "lucide-react"
import { toast } from "sonner"
import { LiveState } from "@/lib/client"
import { fmtDuration, socLabel } from "@/lib/format"

/** Above this much uncertainty the estimate is worth confirming unprompted. */
const NUDGE_BAND = 4

/** How far off plan a freshly MEASURED battery has to be for the confirmation
 *  to offer a re-plan. Lower than the server's own alarm threshold (8 points,
 *  `live.SOC_SHORTFALL_TO_REPLAN`) and symmetric, because this is an offer the
 *  driver can ignore rather than a standing warning, and it is the only thing
 *  in the app that reacts to the battery being BETTER than planned. */
const OFFER_REPLAN_BAND = 5

export default function BatteryCheck({
  state,
  isDriver,
  busy,
  stopName,
  onSubmit,
  onReplan,
  openSignal = 0,
  footer,
}: {
  state: LiveState
  isDriver: boolean
  busy: boolean
  stopName: string | null
  /** Returns the state the reading produced — what the correction revealed is
   *  the whole reason to offer anything after it. */
  onSubmit: (soc: number) => Promise<LiveState | null>
  /** Re-plan the rest of the drive. Offered on the confirmation, because a
   *  reading that moves the battery moves the charging plan with it. */
  onReplan?: () => void
  /** Bumped when the driver asks for this from somewhere else on the screen —
   *  the battery readout in the HUD, which is where they are already looking. */
  openSignal?: number
  /** A line to hang off the bottom of this card, inside its border. The drive
   *  screen passes the location-sharing row: two facts about the same phone,
   *  both one quiet line, and as separate cards they cost two borders and two
   *  margins for it on the screen with the least room to spare. */
  footer?: ReactNode
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(Math.round(state.soc))
  // Standing at a plug, the STOP CARD owns this question — it asks it once,
  // with the two answers that mean different things there ("it's 40%" keeps
  // charging, "leaving on 40%" ends the stop). This card used to prompt as
  // well, so a charging driver got two steppers and two headings asking the
  // same thing, which is how a stop turns into a form. It stays reachable as
  // the quiet one-line row; it just stops competing.
  const atCharger = state.at_charger_id != null && state.at_charger == null
  const [asked, setAsked] = useState<string | null>(null)

  // Arriving at a charger the stop card cannot describe — one we hold no
  // snapshot row for — is still the moment to ask, and only once per stop, so a
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

  // A watcher sees the battery and cannot touch it.
  //
  // Only the driver can submit a reading, so this used to render nothing at
  // all for anyone else — which meant the person at the other end of the share
  // link, the one actually wondering whether the car will make it, had the
  // number in the HUD and no idea how old it was or how firm. It is the same
  // row, minus the button.
  //
  // Shown as a RANGE while it is inferred, unlike the driver's row: they have
  // a correction one tap away and a dashboard in front of them, and a watcher
  // has neither, so the width of the estimate is the honest thing to give
  // them. Same helper the HUD uses, so the two cannot disagree.
  if (!isDriver) {
    return (
      <Shell footer={footer}>
        <div className="flex items-center gap-1.5 px-3 py-2 text-xs text-ink-500">
          <BatteryMedium className="h-4 w-4 shrink-0 text-ink-400" />
          <span className="min-w-0 truncate">
            Battery{" "}
            <strong className="text-ink-700">
              {socLabel(
                state.soc,
                state.soc_is_measured,
                state.soc_uncertainty_pct
              )}
            </strong>
            {state.soc_is_measured && state.anchor_age_min < 1
              ? " · just confirmed"
              : state.soc_is_measured
                ? ` · confirmed ${fmtDuration(state.anchor_age_min)} ago`
                : " · estimated"}
          </span>
          <span className="ml-auto hidden shrink-0 text-ink-400 sm:inline">
            the driving phone confirms it
          </span>
        </div>
      </Shell>
    )
  }

  function openEditor() {
    setDraft(Math.round(state.soc))
    setEditing(true)
  }

  // Not being asked right now. The control still has to be reachable — one
  // line, no prompt, no nagging.
  if (!editing && !atCharger && !nudging) {
    return (
      <Shell footer={footer} footerBeside>
        {/* The WHOLE row is the button, which is what lets this sit in half a
            card next to the sharing line and still clear 44 px: a status line
            with a small button beside it needs the label's width twice over,
            once to read and once to press. The icon carries the word
            "Battery" — at this size the sentence was most of the row. */}
        <button
          onClick={openEditor}
          aria-label="Correct the battery reading"
          className="flex min-h-11 w-full items-center gap-1.5 px-3 text-left text-xs text-ink-500"
        >
          <BatteryMedium className="h-4 w-4 shrink-0 text-ink-400" />
          <span className="min-w-0 flex-1 truncate">
            <strong className="text-ink-700">{Math.round(state.soc)}%</strong>
            {state.soc_is_measured && state.anchor_age_min < 1
              ? " · just confirmed"
              : state.soc_is_measured
                ? ` · confirmed ${fmtDuration(state.anchor_age_min)} ago`
                : " · estimated"}
          </span>
          {/* The pencil is the affordance at every width; the word joins it
              when there is room. At 320 px the two together pushed the
              freshness suffix out of the row — and "65% · …" drops the one
              word saying whether that number was measured or guessed, which
              is worth more here than a label on a row that is itself the
              button. */}
          <span className="flex shrink-0 items-center gap-1 font-semibold text-brand-700">
            <Pencil className="h-3.5 w-3.5" />
            <span className="hidden underline underline-offset-2 min-[360px]:inline">
              Correct
            </span>
          </span>
        </button>
      </Shell>
    )
  }

  // Whether the driver has moved the stepper off the estimate. It is what the
  // one primary button says, and nothing is disabled by it.
  const untouched = draft === Math.round(state.soc)

  async function send(value: number) {
    try {
      const next = await onSubmit(value)
      setEditing(false)
      const off = state.soc - value
      const message =
        Math.abs(off) < 1
          ? "Thanks, the estimate was on the money."
          : `Thanks, we were ${Math.abs(off).toFixed(0)}% ${
              off > 0 ? "optimistic" : "pessimistic"
            }. The rest of the drive is tuned to your car now.`

      // A correction is the one moment the battery is a MEASUREMENT, so it is
      // also the moment re-planning is worth most — and the driver's hands are
      // already here. Offered in both directions, deliberately: a battery
      // worse than planned raises the amber panel by itself, while a battery
      // BETTER than planned might drop a stop entirely and nothing else in the
      // app would ever suggest it.
      const drift = next ? Math.abs(next.soc_vs_plan) : 0
      const worthReplanning = next != null && (next.needs_replan || drift >= OFFER_REPLAN_BAND)
      toast.success(message, {
        duration: worthReplanning ? 12_000 : 4_000,
        action:
          worthReplanning && onReplan
            ? { label: "Re-plan", onClick: onReplan }
            : undefined,
      })
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
    <Shell accent={atCharger} footer={footer}>
      <div className="flex items-start gap-2 p-3.5">
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

          {/* Stepper across the full width, then ONE button under it — the
              layout `ChargingHere` already uses for the same question, and the
              reason this one did not survive a phone. Three controls in a
              wrapping row need about 400 px; on a 390 px screen they broke
              into three lines — a green "Spot on" alone, a bare stepper, and a
              greyed-out "It's 26%" — which reads as a broken form rather than
              as one question with a number in it. */}
          <div className="mt-3 flex items-center gap-1 rounded-xl border border-ink-200 bg-white p-1">
            <Stepper label="−5" onClick={() => setDraft((d) => clamp(d - 5))} />
            <Stepper label="−1" onClick={() => setDraft((d) => clamp(d - 1))} />
            <span className="min-w-14 flex-1 text-center font-mono text-lg font-bold tabular-nums text-ink-900">
              {draft}%
            </span>
            <Stepper label="+1" onClick={() => setDraft((d) => clamp(d + 1))} />
            <Stepper label="+5" onClick={() => setDraft((d) => clamp(d + 5))} />
          </div>

          {/* One button, not two. "Spot on" and "It's 26%" sent the SAME
              reading whenever the stepper had not been touched — so the panel
              opened with its confirm button greyed out beside a green one that
              did the identical thing, which is the "always greyed out" shape
              this app has been bitten by before. Agreeing with the estimate
              still costs exactly one tap, which is the whole point of "Spot
              on"; it is now the same tap as correcting it. */}
          <div className="mt-2 flex items-center gap-2">
            <button
              onClick={() => void send(draft)}
              disabled={busy}
              className="inline-flex min-h-11 flex-1 items-center justify-center gap-1.5 rounded-xl bg-brand-600 px-3 text-sm font-semibold text-white transition-transform active:scale-[0.99] disabled:opacity-50"
            >
              <Check className="h-4 w-4 shrink-0" />
              {untouched ? "Spot on" : `It's ${draft}%`}
            </button>

            {!atCharger && (
              <button
                onClick={() => setEditing(false)}
                className="min-h-11 shrink-0 px-3 text-sm text-ink-500 underline underline-offset-2"
              >
                Later
              </button>
            )}
          </div>
        </div>
      </div>
    </Shell>
  )
}

/** The card, and anything that shares its chrome.
 *
 * `footer` is how the drive screen puts the location-sharing line INSIDE this
 * card rather than in a second container below it. Both are one quiet line
 * about the same phone, and as separate cards they spent two borders, two
 * margins and a gap saying so — on the screen with the least room to spare.
 */
function Shell({
  accent = false,
  footer,
  footerBeside = false,
  children,
}: {
  accent?: boolean
  footer?: ReactNode
  /** Side by side rather than stacked. Only the one-line state can do this,
   *  and it is where the height actually goes: two 44 px rows stacked is a
   *  band of a phone screen spent on two short sentences, and side by side it
   *  is one. The open editor is a panel, so it stacks. */
  footerBeside?: boolean
  children: ReactNode
}) {
  return (
    <div
      className={`mt-2 overflow-hidden rounded-2xl border ${
        accent ? "border-brand-300 bg-brand-50" : "border-ink-200 bg-white"
      }`}
    >
      {footer && footerBeside ? (
        <div className="flex items-stretch divide-x divide-ink-100">
          <div className="min-w-0 flex-1">{children}</div>
          {/* Sized to its content rather than to half, because the two halves
              do not carry the same amount: "Sharing · Stop" is fixed and short
              while the battery side has to fit a percentage AND how old it is,
              and an even split clipped that to "65% · …" on a 320 px phone —
              dropping the one word that says whether the number is a
              measurement or a guess. */}
          <div className="shrink-0">{footer}</div>
        </div>
      ) : (
        <>
          {children}
          {footer && <div className="border-t border-ink-100">{footer}</div>}
        </>
      )}
    </div>
  )
}

function Stepper({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      // 44px targets: this is operated in a car, sometimes in gloves.
      className="grid h-11 min-w-[44px] place-items-center rounded-lg font-mono text-sm font-bold text-ink-600 transition-colors hover:bg-ink-100 active:bg-ink-200"
    >
      {label}
    </button>
  )
}

const clamp = (n: number) => Math.max(0, Math.min(100, n))
