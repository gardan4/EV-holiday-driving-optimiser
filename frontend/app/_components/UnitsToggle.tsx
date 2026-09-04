"use client"

import { useUnits } from "@/lib/useUnits"
import { Units } from "@/lib/units"

/**
 * km ⇄ mi, for the whole app.
 *
 * A two-segment switch rather than a dropdown or a settings page: there are
 * exactly two answers, both are one syllable, and the reader has to be able to
 * see which one is on without opening anything. It sits beside the trips chip
 * in the header, and floats in the opposite corner on the landing page, which
 * has no header — the same two entry points that button already uses.
 *
 * Nothing renders until the stored preference has been read. The choice lives
 * in localStorage, so a server-rendered "km" that flips to "mi" a frame later
 * is a control that lies about its own state on first paint.
 */
export default function UnitsToggle({
  /** The landing page has no header, so there it floats in the top-left. */
  floating = false,
}: {
  floating?: boolean
}) {
  const { units, setUnits, mounted } = useUnits()
  if (!mounted) return null

  return (
    <div
      role="group"
      aria-label="Distance units"
      className={`inline-flex shrink-0 items-center rounded-full border border-ink-200 bg-white p-0.5 text-xs font-semibold ${
        floating
          ? "fixed left-4 top-4 z-30 shadow-sm shadow-ink-900/5 backdrop-blur sm:left-6"
          : ""
      }`}
    >
      <Segment current={units} value="metric" label="km" onSelect={setUnits} />
      <Segment current={units} value="imperial" label="mi" onSelect={setUnits} />
    </div>
  )
}

function Segment({
  current,
  value,
  label,
  onSelect,
}: {
  current: Units
  value: Units
  label: string
  onSelect: (u: Units) => void
}) {
  const on = current === value
  return (
    <button
      type="button"
      onClick={() => onSelect(value)}
      aria-pressed={on}
      // The full unit is on the accessible name only: "km" and "mi" are the
      // labels people scan for, but a screen reader reading two-letter buttons
      // out of context says nothing about what they change.
      aria-label={value === "imperial" ? "Show miles and mph" : "Show kilometres and km/h"}
      className={`rounded-full px-2.5 py-1 tabular-nums transition-colors ${
        on
          ? "bg-brand-500 text-white"
          : "text-ink-500 hover:text-ink-900"
      }`}
    >
      {label}
    </button>
  )
}
