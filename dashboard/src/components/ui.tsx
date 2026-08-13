import { useEffect, useRef, useState, type ReactNode } from "react"
import { compact, delta, group } from "../lib/format"

export function Panel({
  title,
  hint,
  right,
  className = "",
  children,
}: {
  title?: string
  hint?: string
  right?: ReactNode
  className?: string
  children: ReactNode
}) {
  return (
    <section className={`panel rise ${className}`}>
      {(title || right) && (
        <header className="mb-4 flex items-start justify-between gap-4">
          <div>
            {title && <h2 className="panel-title">{title}</h2>}
            {hint && <p className="mt-1 text-[12px] text-ink-mute">{hint}</p>}
          </div>
          {right}
        </header>
      )}
      {children}
    </section>
  )
}

/** A counter that rolls when it changes.
 *
 * The roll is the point: on a live dashboard a number that simply swaps is
 * indistinguishable from one that was always that value, so you cannot tell a
 * working stream from a stalled one. Digits are tabular so the roll does not
 * reflow the tile. */
export function Odometer({ value, className = "" }: { value: number; className?: string }) {
  const [shown, setShown] = useState(value)
  const [bumped, setBumped] = useState(false)
  const previous = useRef(value)

  useEffect(() => {
    if (value === previous.current) return
    const from = previous.current
    previous.current = value
    setBumped(true)

    // Short ease rather than a per-digit reel: at these magnitudes a reel is
    // illegible, and what the eye needs is "this moved", not "this moved by".
    const started = performance.now()
    const ms = 420
    let raf = 0
    const step = (now: number) => {
      const t = Math.min(1, (now - started) / ms)
      const eased = 1 - Math.pow(1 - t, 3)
      setShown(Math.round(from + (value - from) * eased))
      if (t < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    const timer = window.setTimeout(() => setBumped(false), 900)
    return () => {
      cancelAnimationFrame(raf)
      window.clearTimeout(timer)
    }
  }, [value])

  return (
    <span
      className={`figure ${className}`}
      style={{ color: bumped ? "var(--color-s-mint)" : undefined, transition: "color 600ms ease" }}
    >
      {group(shown)}
    </span>
  )
}

export function Tile({
  label,
  value,
  change,
  suffix,
  live = false,
}: {
  label: string
  value: number
  change?: number | null
  suffix?: string
  live?: boolean
}) {
  const up = change !== null && change !== undefined && change > 0
  const down = change !== null && change !== undefined && change < 0
  return (
    <div className="panel !p-4">
      <div className="flex items-baseline justify-between gap-2">
        <span className="panel-title !text-[10px]">{label}</span>
        {live && <LivePip />}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <Odometer value={value} className="text-[26px] font-semibold leading-none" />
        {suffix && <span className="text-[12px] text-ink-mute">{suffix}</span>}
      </div>
      {change !== undefined && (
        <div
          className="figure mt-2 text-[11px]"
          style={{
            color: up ? "var(--color-s-mint)" : down ? "var(--color-s-rose)" : "var(--color-ink-mute)",
          }}
          // The baseline is half the information: "+40%" without "from 90" is
          // a number you cannot check.
          title="vs the previous window of the same length"
        >
          {delta(change)} <span className="text-ink-mute">vs previous</span>
        </div>
      )}
    </div>
  )
}

export function LivePip({ status = "live" }: { status?: "connecting" | "live" | "retrying" }) {
  const colour =
    status === "live"
      ? "var(--color-s-mint)"
      : status === "retrying"
        ? "var(--color-s-charge)"
        : "var(--color-ink-mute)"
  return (
    <span className="inline-flex items-center gap-1.5 text-[10px] uppercase tracking-[0.09em] text-ink-mute">
      <span
        className="inline-block h-1.5 w-1.5 rounded-full"
        style={{
          background: colour,
          boxShadow: status === "live" ? `0 0 0 3px color-mix(in oklab, ${colour} 22%, transparent)` : undefined,
        }}
      />
      {status}
    </span>
  )
}

/** Horizontal bars. Clicking one narrows every panel on the page — that is the
 * whole drilldown mechanic, so it costs no extra UI. */
export function Bars({
  rows,
  onPick,
  active,
  empty = "Nothing in this window.",
  format = compact,
}: {
  rows: { label: string; value: number }[]
  onPick?: (label: string) => void
  active?: string | null
  empty?: string
  format?: (n: number) => string
}) {
  if (!rows.length) return <p className="text-[13px] text-ink-mute">{empty}</p>
  const top = Math.max(...rows.map((r) => r.value), 1)

  return (
    <ul className="flex flex-col gap-1.5">
      {rows.map((r) => {
        const on = active === r.label
        return (
          <li key={r.label}>
            <button
              type="button"
              disabled={!onPick}
              onClick={() => onPick?.(r.label)}
              // The visible text is split across three spans, so without this
              // the control has no accessible name — a screen reader (and any
              // automated check) sees a row of unlabelled buttons.
              aria-label={`${r.label}: ${r.value}${onPick ? on ? " — remove filter" : " — filter to this" : ""}`}
              aria-pressed={onPick ? on : undefined}
              className="group grid w-full grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3 rounded-md py-0.5 text-left disabled:cursor-default"
            >
              <span
                className="truncate text-[12.5px]"
                style={{ color: on ? "var(--color-s-mint)" : "var(--color-ink-soft)" }}
                title={r.label}
              >
                {r.label}
              </span>
              <span className="h-2 rounded-full bg-deck-line-soft">
                <span
                  className="block h-2 rounded-full transition-[width] duration-500"
                  style={{
                    width: `${Math.max(2, (100 * r.value) / top)}%`,
                    background: on
                      ? "var(--color-s-mint)"
                      : "color-mix(in oklab, var(--color-s-drive) 88%, transparent)",
                  }}
                />
              </span>
              <span className="figure w-12 text-right text-[12px] text-ink-soft">
                {format(r.value)}
              </span>
            </button>
          </li>
        )
      })}
    </ul>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-6 text-center text-[13px] text-ink-mute">{children}</p>
}
