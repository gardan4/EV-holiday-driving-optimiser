"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { Minus, Plus } from "lucide-react"
import {
  MONTHS_SHORT,
  WEEKDAYS_SHORT,
  fmtDeparture,
  parseLocalIso,
  toLocalIso,
} from "@/lib/format"
import { InfoTip } from "./fields"

/**
 * Departure day + time.
 *
 * Replaces `<input type="datetime-local">`, which is the worst control on the
 * form: on the desktop it is six typed segments in an order that changes with
 * the browser's locale, and the thing people actually want — "Friday night" —
 * takes all six. Here the common answers are one tap, the next fortnight is a
 * strip (a holiday drive is nearly always in it), the time is a drag, and a
 * plain date box still covers a trip planned months out.
 *
 * The value is unchanged: the naive local `YYYY-MM-DDTHH:MM` string the API
 * stores as `departure_iso`.
 */

const STRIP_DAYS = 14
const STEP_MIN = 15
const LAST_SLOT_MIN = 24 * 60 - STEP_MIN

function startOfDay(d: Date): Date {
  const x = new Date(d)
  x.setHours(0, 0, 0, 0)
  return x
}

function addDays(d: Date, n: number): Date {
  const x = new Date(d)
  x.setDate(x.getDate() + n)
  return x
}

/** Identity of a calendar day, so two Dates can be compared without the time. */
function dayKey(d: Date): string {
  return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

function clock(minutes: number): string {
  const h = Math.floor(minutes / 60)
  return `${String(h).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`
}

interface Preset {
  label: string
  iso: string
}

/**
 * The departures people actually pick, in the order they think of them.
 * "Tonight" is dropped once it is tonight — an offer to leave in the past is
 * worse than one option fewer.
 */
function buildPresets(now: Date): Preset[] {
  const midnight = startOfDay(now)
  const at = (offset: number, hour: number) => {
    const d = addDays(midnight, offset)
    d.setHours(hour, 0, 0, 0)
    return d
  }

  const candidates: Date[] = []
  const labels: string[] = []
  if (now.getHours() < 20) {
    candidates.push(at(0, 22))
    labels.push("Tonight")
  }
  candidates.push(at(1, 8))
  labels.push("Tomorrow")
  candidates.push(at((5 - now.getDay() + 7) % 7 || 7, 22))
  labels.push("Friday night")
  candidates.push(at((6 - now.getDay() + 7) % 7 || 7, 7))
  labels.push("Saturday early")

  const seen = new Set<string>()
  const out: Preset[] = []
  candidates.forEach((when, i) => {
    const iso = toLocalIso(when)
    if (seen.has(iso)) return
    seen.add(iso)
    out.push({ label: labels[i], iso })
  })
  return out
}

/** "in 3 days" — the fact the date alone doesn't give you. */
function relativeDay(selected: Date, now: Date): string {
  if (selected.getTime() < now.getTime()) return "in the past"
  const days = Math.round(
    (startOfDay(selected).getTime() - startOfDay(now).getTime()) / 86_400_000,
  )
  if (days === 0) return "today"
  if (days === 1) return "tomorrow"
  if (days < 14) return `in ${days} days`
  if (days < 60) return `in ${Math.round(days / 7)} weeks`
  return `in ${Math.round(days / 30)} months`
}

interface DepartureFieldProps {
  /** `YYYY-MM-DDTHH:MM`, local and naive. */
  value: string
  onChange: (iso: string) => void
}

export default function DepartureField({ value, onChange }: DepartureFieldProps) {
  const selected = useMemo(() => parseLocalIso(value), [value])
  const selectedMin = selected.getHours() * 60 + selected.getMinutes()
  const selectedKey = dayKey(selected)

  /* "Today" is a client fact. Deriving it while rendering on the server uses
     the server's clock and timezone, so the strip would hydrate onto a
     different fortnight for anyone whose date differs from UTC's — which,
     around midnight, is most of the world. Resolved after mount; until then
     the strip is a placeholder of the same height. */
  const [now, setNow] = useState<Date | null>(null)
  useEffect(() => setNow(new Date()), [])

  const presets = useMemo(() => (now ? buildPresets(now) : []), [now])

  const days = useMemo(() => {
    if (!now) return []
    const strip = Array.from({ length: STRIP_DAYS }, (_, i) => addDays(startOfDay(now), i))
    // A date picked from the date box can sit outside the fortnight. Keep it in
    // the strip anyway, or the selection has nowhere visible to be.
    if (strip.some((d) => dayKey(d) === selectedKey)) return strip
    return [startOfDay(selected), ...strip].sort((a, b) => a.getTime() - b.getTime())
  }, [now, selected, selectedKey])

  // Bring the chosen day into view. On a phone the strip is a fortnight wide in
  // a viewport that holds five days, so a date picked from the date box can
  // land off screen and the control looks like it has no selection at all.
  // "nearest", not "center": scrolling a day that is already visible moves the
  // strip under the reader for no reason, and it hides today behind the edge.
  const stripRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    stripRef.current
      ?.querySelector('[data-selected="true"]')
      ?.scrollIntoView({ block: "nearest", inline: "nearest" })
  }, [selectedKey, days.length])

  function pickDay(day: Date) {
    const next = new Date(day)
    next.setHours(selected.getHours(), selected.getMinutes(), 0, 0)
    onChange(toLocalIso(next))
  }

  function pickMinutes(total: number) {
    const clamped = Math.max(0, Math.min(LAST_SLOT_MIN, total))
    const next = new Date(selected)
    next.setHours(Math.floor(clamped / 60), clamped % 60, 0, 0)
    onChange(toLocalIso(next))
  }

  const relative = now ? relativeDay(selected, now) : null
  const isPast = relative === "in the past"

  return (
    <div>
      {/* The chosen departure reads out on the label's own line, where there
          was nothing but air. Under the panel it was one more grey sentence in
          a column of them. */}
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <span className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500">
          Departure
          <InfoTip>
            When you set off. It doesn&apos;t change how long the drive takes — the model
            has no traffic and no rush hour — but every clock time in the answer is
            counted from it, so it decides what time you actually walk in the door.
          </InfoTip>
        </span>
        <span
          role="status"
          className={`truncate text-xs font-medium ${isPast ? "text-amber-700" : "text-ink-500"}`}
        >
          {fmtDeparture(value)}
          {relative && ` · ${relative}`}
        </span>
      </div>

      <div className="rounded-xl border border-ink-200 bg-white p-3">
        {/* The one-tap answers. Nearly every holiday departure is one of these.
            A grid, not a wrapped row: equal tiles reach both edges, and on a
            phone two columns is a thumb-sized target rather than a chip. */}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {presets.length === 0
            ? Array.from({ length: 4 }, (_, i) => (
                <div key={i} className="h-[3.5rem] rounded-xl bg-ink-100" />
              ))
            : presets.map((p) => {
                const on = p.iso === value
                const d = parseLocalIso(p.iso)
                return (
                  <button
                    key={p.iso}
                    type="button"
                    // The visible label is two lines of fragments; spelled out
                    // here so it is one sentence when it is read aloud.
                    aria-label={`${p.label}, ${fmtDeparture(p.iso)}`}
                    aria-pressed={on}
                    onClick={() => onChange(p.iso)}
                    className={`flex h-[3.5rem] flex-col items-center justify-center rounded-xl border px-2 transition-all ${
                      on
                        ? "border-brand-400 bg-brand-50 ring-2 ring-brand-200"
                        : "border-transparent bg-ink-100 hover:bg-ink-200"
                    }`}
                  >
                    <span className="text-sm font-semibold leading-tight text-ink-900">
                      {p.label}
                    </span>
                    <span className="font-mono text-[11px] leading-tight text-ink-500">
                      {d.getDate()} {MONTHS_SHORT[d.getMonth()]} · {clock(d.getHours() * 60 + d.getMinutes())}
                    </span>
                  </button>
                )
              })}
        </div>

        <div className="mt-4 flex items-center justify-between gap-2">
          <span className="text-[11px] font-semibold uppercase tracking-wider text-ink-400">
            Day
          </span>
          {/* The long tail: a holiday booked for next spring is outside any
              strip worth scrolling, and a plain date box is the one native
              picker that is genuinely good at that. */}
          <input
            type="date"
            aria-label="Pick another date"
            value={value.slice(0, 10)}
            onChange={(e) => {
              if (!e.target.value) return
              const [y, mo, d] = e.target.value.split("-").map(Number)
              pickDay(new Date(y, mo - 1, d))
            }}
            className="rounded-lg border border-ink-200 bg-white px-2.5 py-1.5 text-xs text-ink-700 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-200"
          />
        </div>

        <div
          ref={stripRef}
          role="radiogroup"
          aria-label="Departure day"
          // Scrollbar hidden: the app styles it brand-green and 6px tall, which
          // under a horizontal strip reads as an underline on the control
          // rather than as chrome. The clipped cell at the edge is the cue.
          className="mt-1.5 flex snap-x gap-2 overflow-x-auto [-ms-overflow-style:none] [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
        >
          {days.length === 0 ? (
            <div className="h-[3.5rem] w-full rounded-xl bg-ink-100" />
          ) : (
            days.map((d) => {
              const on = dayKey(d) === selectedKey
              const weekend = d.getDay() === 0 || d.getDay() === 6
              return (
                <button
                  key={dayKey(d)}
                  type="button"
                  role="radio"
                  aria-label={`${WEEKDAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}`}
                  aria-checked={on}
                  data-selected={on}
                  onClick={() => pickDay(d)}
                  // grow + a touch-sized floor: the fortnight overflows and
                  // scrolls at 3.5rem a cell, but a shorter strip (a picked
                  // date sitting on its own week) spreads across the full row
                  // instead of huddling at the left.
                  className={`flex h-[3.5rem] min-w-[3.5rem] flex-1 shrink-0 snap-start flex-col items-center justify-center rounded-xl border transition-all ${
                    on
                      ? "border-brand-400 bg-brand-50 ring-2 ring-brand-200"
                      : weekend
                        ? "border-transparent bg-brand-50 hover:bg-brand-100"
                        : "border-transparent bg-ink-100 hover:bg-ink-200"
                  }`}
                >
                  <span
                    className={`text-[10px] font-medium uppercase leading-none ${
                      weekend ? "text-brand-700" : "text-ink-400"
                    }`}
                  >
                    {WEEKDAYS_SHORT[d.getDay()]}
                  </span>
                  {/* No month on the cell: it has nowhere to sit that doesn't
                      leave every other cell reserving a blank line for the one
                      that needs it, and the readout underneath already names
                      the month of whatever is selected. */}
                  <span className="mt-1 text-lg font-semibold leading-none text-ink-900">
                    {d.getDate()}
                  </span>
                </button>
              )
            })
          )}
        </div>

        {/* One row on a wide screen — clock and slider side by side, so neither
            leaves a dead strip across the panel. Stacked on a phone, where the
            steppers push out to the edges and become thumb-sized targets. */}
        <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-4">
          <div className="flex items-center justify-between gap-2 sm:justify-start">
            <StepButton
              label="15 minutes earlier"
              onClick={() => pickMinutes(selectedMin - STEP_MIN)}
              disabled={selectedMin <= 0}
            >
              <Minus className="h-5 w-5" />
            </StepButton>
            <span className="w-[5ch] text-center font-display text-2xl font-bold tabular-nums text-ink-900">
              {clock(selectedMin)}
            </span>
            <StepButton
              label="15 minutes later"
              onClick={() => pickMinutes(selectedMin + STEP_MIN)}
              disabled={selectedMin >= LAST_SLOT_MIN}
            >
              <Plus className="h-5 w-5" />
            </StepButton>
          </div>

          <div className="min-w-0 flex-1">
            <input
              type="range"
              aria-label="Departure time"
              aria-valuetext={clock(selectedMin)}
              min={0}
              max={LAST_SLOT_MIN}
              step={STEP_MIN}
              value={selectedMin}
              onChange={(e) => pickMinutes(Number(e.target.value))}
              // Taller than the track it draws: the extra height is hit area,
              // which is the difference between dragging the thumb and missing
              // it with a thumb.
              className="h-6 w-full accent-brand-500"
            />
            <div className="flex justify-between font-mono text-[10px] text-ink-400">
              {["00", "06", "12", "18", "24"].map((h) => (
                <span key={h}>{h}</span>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function StepButton({
  label,
  onClick,
  disabled,
  children,
}: {
  label: string
  onClick: () => void
  disabled: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      disabled={disabled}
      className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-ink-100 text-ink-700 transition-colors hover:bg-ink-200 disabled:cursor-not-allowed disabled:bg-ink-100 disabled:text-ink-300"
    >
      {children}
    </button>
  )
}
