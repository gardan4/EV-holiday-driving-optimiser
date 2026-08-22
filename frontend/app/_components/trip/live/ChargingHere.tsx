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
 *
 * **Pulling in is not plugging in.** Arriving here used to start the charge by
 * itself, and the several minutes at the front of a real stop are exactly the
 * ones where that is false: the queue for a free stall, the walk over to read
 * the screen, the post that turns out to be dead. The battery climbed through
 * all of them. So the card asks, once, with a button — and until it is
 * pressed it says plainly that nothing is being counted, because a projection
 * that has quietly stopped is worse than one that never started.
 *
 * **A third target, which is the driver's own.** The floor is what the road
 * needs and the plan's figure is what is quickest; neither is "I'll sit for
 * another ten minutes, the next hundred kilometres are through the Alps".
 * Chips rather than a stepper, deliberately — this card already has one and
 * two of them is how a stop turns into a form — and coarse, because nobody
 * decides to leave on 73%.
 */

import { useEffect, useState } from "react"
import { BatteryCharging, Check, LogOut, MapPin, Plug, UtensilsCrossed, X } from "lucide-react"
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
  onPlugIn,
  onLeaveAt,
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
  /** "The cable is in." Nothing counts until this is pressed. */
  onPlugIn: () => void
  /** "Wake me at 80%." Null clears it, back to the plan's figure. */
  onLeaveAt: (soc: number | null) => void
}) {
  const min = session?.min_soc ?? null
  const target = session?.target_soc ?? null
  const leaveAt = session?.leave_at_soc ?? null
  // The bar runs to whichever goal is furthest out, so every marker fits on it
  // and the ones already passed read as passed.
  const top = Math.max(min ?? 0, target ?? 0, leaveAt ?? 0, soc)

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

      {/* Not plugged in. The battery is standing still and the card says so
          in words — an estimate that has quietly stopped moving is worse than
          one that never started, because it still looks live. */}
      {!session && (
        <div className="mt-3 rounded-xl border border-amber-300 bg-amber-50 p-3">
          <div className="flex flex-wrap items-baseline justify-between gap-x-3">
            <p className="font-display text-2xl font-semibold tabular-nums text-ink-900">
              {socLabel}
            </p>
            <p className="text-xs font-semibold text-amber-900">not charging</p>
          </div>
          <p className="mt-1 text-xs leading-relaxed text-amber-900/90">
            Nothing is being counted yet. Tell us when the cable is actually in
            — waiting for a free stall, or a post that won&apos;t start, is time
            the battery doesn&apos;t move.
          </p>
          {isDriver && (
            <button
              onClick={onPlugIn}
              disabled={busy}
              className="mt-2.5 flex min-h-11 w-full items-center justify-center gap-2 rounded-lg bg-brand-600 px-3 text-sm font-semibold text-white transition-transform active:scale-[0.99] disabled:opacity-50"
            >
              <Plug className="h-4 w-4 shrink-0" />
              I&apos;m plugged in now
            </button>
          )}
        </div>
      )}

      {session && (
      <div className="mt-3 rounded-xl bg-brand-50/70 p-3">
        <div className="flex flex-wrap items-baseline justify-between gap-x-3">
          <p className="font-display text-2xl font-semibold tabular-nums text-ink-900">
            {socLabel}
          </p>
          <p className="text-xs text-ink-500">
            at {Math.round(session.power_kw)} kW, from{" "}
            {Math.round(session.start_soc)}%
          </p>
        </div>

        {/* Where the two goals sit relative to the battery now. */}
        <div className="relative mt-2 h-2 overflow-hidden rounded-full bg-white">
          <div
            className="absolute inset-y-0 left-0 rounded-full bg-brand-400"
            style={{ width: `${pct(soc, top)}%` }}
          />
          {min != null && <Tick at={pct(min, top)} tone="ink" />}
          {target != null && <Tick at={pct(target, top)} tone="brand" />}
          {leaveAt != null && <Tick at={pct(leaveAt, top)} tone="brand" />}
        </div>

        <div className="mt-2.5 space-y-1.5">
          <Goal
            label={`Enough for ${nextName ?? "the next stop"}`}
            soc={min}
            left={session.to_min_min ?? null}
            now={soc}
            tone="ink"
          />
          <Goal
            label="The plan leaves on"
            soc={target}
            left={session.to_target_min ?? null}
            now={soc}
            tone="brand"
          />
          <Goal
            label="You said"
            soc={leaveAt}
            left={session.to_leave_min ?? null}
            now={soc}
            tone="brand"
          />
        </div>


        {/* Reached. Said in words rather than left to be read off the bar,
            because this is the one thing on the card somebody is waiting for
            and they are not looking at the screen while they wait. */}
        {session.leave_ready && (
          <p className="mt-2 flex items-center gap-1.5 rounded-lg bg-brand-600 px-2.5 py-2 text-sm font-semibold text-white">
            <Check className="h-4 w-4 shrink-0" />
            You&apos;re at {Math.round(leaveAt ?? soc)}% — good to go
          </p>
        )}

        {isDriver && (
          <LeaveAt
            soc={soc}
            planTarget={target}
            value={leaveAt}
            busy={busy}
            onPick={onLeaveAt}
          />
        )}

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
      )}

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

/** "Wake me at 80%."
 *
 *  Chips, not a stepper. This card already has one and its own note says why a
 *  second would be wrong — two steppers is how a stop turns into a form — and
 *  the decision is coarse anyway: nobody sits at a plug deciding to leave on
 *  73%. One tap, and one tap to take it back.
 *
 *  Only offers figures ABOVE the battery now, because a target already passed
 *  is not a target, and it would render as "good to go" the instant it was
 *  set. The plan's own figure is offered too when it is not one of the round
 *  numbers — it is the one target with a reason behind it, and making the
 *  driver dial past it to reach 80 would be pretending it does not exist.
 */
function LeaveAt({
  soc,
  planTarget,
  value,
  busy,
  onPick,
}: {
  soc: number
  planTarget: number | null
  value: number | null
  busy: boolean
  onPick: (soc: number | null) => void
}) {
  if (value != null) {
    return (
      <div className="mt-2 flex items-center justify-between gap-2 rounded-lg border border-ink-200 bg-white px-3 py-1.5 text-xs text-ink-600">
        <span className="min-w-0 truncate">
          Charging to <strong className="text-ink-900">{Math.round(value)}%</strong>{" "}
          because you asked
        </span>
        <button
          onClick={() => onPick(null)}
          disabled={busy}
          aria-label="Drop that target and go back to the plan's"
          className="-mr-2 inline-flex min-h-11 shrink-0 items-center gap-1 px-2 font-semibold text-ink-500 disabled:opacity-40"
        >
          <X className="h-3.5 w-3.5" />
          Drop
        </button>
      </div>
    )
  }

  const rounds = [70, 80, 90, 100].filter((v) => v > soc + 1)
  const withPlan =
    planTarget != null && planTarget > soc + 1 && !rounds.some((v) => Math.abs(v - planTarget) < 3)
      ? [Math.round(planTarget), ...rounds]
      : rounds
  const options = withPlan.slice(0, 4)
  if (options.length === 0) return null

  return (
    <div className="mt-2">
      <p className="text-[11px] font-semibold uppercase tracking-wider text-ink-500">
        Or stay until
      </p>
      <div className="mt-1 flex flex-wrap gap-1.5">
        {options.map((v) => (
          <button
            key={v}
            onClick={() => onPick(v)}
            disabled={busy}
            // Real padding rather than an invisible hit area: these chips
            // WRAP, and an expanded box would overlap the row above and hand
            // the tap to a different percentage.
            className="min-h-11 rounded-lg border border-ink-200 bg-white px-3 py-2.5 text-xs font-semibold text-ink-700 transition-colors hover:border-ink-300 disabled:opacity-40"
          >
            {v}%
          </button>
        ))}
      </div>
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
