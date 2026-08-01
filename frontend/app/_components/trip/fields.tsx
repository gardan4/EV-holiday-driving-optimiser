"use client"

/**
 * Shared form controls. Used by the planner form and by the editable
 * assumptions panel on a trip, so the two can never drift apart.
 */

export function InfoTip({ children }: { children: React.ReactNode }) {
  return (
    <span className="group/tip relative inline-flex align-middle">
      <button
        type="button"
        aria-label="What does this mean?"
        onClick={(e) => e.preventDefault()}
        className="grid h-4 w-4 place-items-center rounded-full border border-ink-300 text-[9px] font-bold text-ink-400 transition-colors hover:border-brand-400 hover:text-brand-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
      >
        i
      </button>
      <span
        role="tooltip"
        className="pointer-events-none absolute left-1/2 top-full z-40 mt-2 w-64 -translate-x-1/2 rounded-xl border border-ink-200 bg-white p-3 text-[11px] font-normal normal-case leading-relaxed tracking-normal text-ink-600 opacity-0 shadow-xl shadow-ink-900/10 transition-opacity duration-150 group-hover/tip:opacity-100 group-focus-within/tip:opacity-100"
      >
        {children}
      </span>
    </span>
  )
}

interface NumberFieldProps {
  id: string
  label: string
  unit: string
  value: number
  min: number
  max: number
  step?: number
  onChange: (v: number) => void
  explain?: string
  /** Optional line showing what the entered number actually does. */
  readout?: string
}

/**
 * A plain number with its unit. Preferred over preset chips wherever the reader
 * plausibly knows the real figure — "12 °C" or "10 min" says what it means,
 * where "Cold" or "typical" hides a multiplier they can't see or argue with.
 */
export function NumberField({
  id,
  label,
  unit,
  value,
  min,
  max,
  step = 1,
  onChange,
  explain,
  readout,
}: NumberFieldProps) {
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500"
      >
        {label}
        {explain && <InfoTip>{explain}</InfoTip>}
      </label>
      {/* The unit sits beside the input, not over it: absolutely positioning it
          put it on top of the native spinner arrows, which clipped "min" and
          wrapped "€/kWh" out of the box. */}
      <div className="flex items-center rounded-xl border border-ink-200 bg-white focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-200">
        <input
          id={id}
          type="number"
          inputMode="numeric"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => {
            const n = Number(e.target.value)
            if (!Number.isNaN(n)) onChange(Math.min(max, Math.max(min, n)))
          }}
          className="min-w-0 flex-1 rounded-xl bg-transparent py-2.5 pl-3 pr-1 text-sm text-ink-900 outline-none"
        />
        <span className="shrink-0 whitespace-nowrap pl-1 pr-3 font-mono text-xs text-ink-400">
          {unit}
        </span>
      </div>
      {readout && <p className="mt-1 text-xs leading-relaxed text-ink-500">{readout}</p>}
    </div>
  )
}
