"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { createPortal } from "react-dom"
import { useOverlayPresence } from "@/lib/overlay"

/**
 * Shared form controls. Used by the planner form and by the editable
 * assumptions panel on a trip, so the two can never drift apart.
 */

/**
 * The "i" beside a label, and the explanation behind it.
 *
 * Two behaviours, because a tooltip is a hover idiom and a phone has no hover.
 *
 * **Pointer devices** keep the hover tooltip: anchored under the icon, shown on
 * hover or keyboard focus, gone when you leave. Nothing to dismiss.
 *
 * **Touch** gets an explicit sheet at the bottom of the screen instead. The
 * anchored version was not merely awkward there, it was clipped: a fixed 256px
 * box centred on an icon sitting near a screen edge ran up to 27px off the left
 * of a 375px viewport and 66px off the right, so a third of the tips on the
 * planner form were partly unreadable. A sheet is full-width by construction,
 * lands under the thumb, and can be dismissed deliberately rather than by
 * hoping a tap lands somewhere that removes focus.
 *
 * The sheet is PORTALLED to the body. `animate-fade-in-up` finishes on
 * `transform: translateY(0)` with `fill-mode: both`, so the form card keeps a
 * transform forever — which makes it the containing block for any `fixed`
 * descendant. Rendered in place, the sheet would be positioned against the card
 * rather than the viewport, which is the same clipping problem wearing a hat.
 */
export function InfoTip({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const [peek, setPeek] = useState(false)
  const [mounted, setMounted] = useState(false)
  // 260ms: the sheet's own travel in `globals.css`. It outlasts the 200ms
  // opacity, so it is the one that decides when the element may go.
  const sheet = useOverlayPresence(open, 260)

  useEffect(() => setMounted(true), [])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [open])

  return (
    <span
      className="relative inline-flex align-middle"
      onMouseEnter={() => setPeek(true)}
      onMouseLeave={() => setPeek(false)}
    >
      <button
        type="button"
        aria-label="What does this mean?"
        aria-expanded={open}
        onFocus={() => setPeek(true)}
        onBlur={() => setPeek(false)}
        onClick={(e) => {
          // Inside a <label> on some fields, where a click would otherwise
          // focus the input and scroll the sheet out from under the thumb.
          e.preventDefault()
          setOpen((o) => !o)
        }}
        className="grid h-4 w-4 place-items-center rounded-full border border-ink-300 text-[9px] font-bold text-ink-400 transition-colors hover:border-brand-400 hover:text-brand-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand-400"
      >
        i
      </button>

      {/* Pointer devices only, hidden below `sm` where the sheet takes over.
          Visibility comes from state rather than a `group-hover/group-focus-
          within` pair: those stopped applying here and cost an hour to not
          explain, and one boolean is both simpler and testable. */}
      {/* Grows out of the "i", rather than materialising under it. A tooltip is
          anchored to the thing that explains it, so the default centre origin
          is wrong here: this one hangs BELOW the trigger, which makes its top
          edge the point it is attached by. Scaling from there — and from 0.97,
          never from 0, because nothing in the world appears out of nothing —
          is what makes the panel read as belonging to the button rather than
          arriving on top of it. The x-translation is the centring, and the two
          transforms compose. */}
      <span
        role="tooltip"
        className={`pointer-events-none absolute left-1/2 top-full z-40 mt-2 hidden w-64 origin-top -translate-x-1/2 rounded-xl border border-ink-200 bg-white p-3 text-[11px] font-normal normal-case leading-relaxed tracking-normal text-ink-600 shadow-xl shadow-ink-900/10 transition-[opacity,transform] duration-150 ease-out sm:block ${
          peek ? "scale-100 opacity-100" : "scale-[0.97] opacity-0"
        }`}
      >
        {children}
      </span>

      {mounted &&
        sheet.present &&
        createPortal(
          <div
            data-leaving={sheet.leaving}
            className="overlay-scrim fixed inset-0 z-[70] flex items-end bg-ink-900/30 backdrop-blur-[1px] sm:hidden"
            onClick={() => setOpen(false)}
            role="dialog"
            aria-modal="true"
            aria-label="What does this mean?"
          >
            <div
              data-leaving={sheet.leaving}
              className="overlay-sheet w-full rounded-t-2xl border-t border-ink-100 bg-white p-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] text-left text-sm font-normal normal-case leading-relaxed tracking-normal text-ink-600 shadow-2xl"
              onClick={(e) => e.stopPropagation()}
            >
              {children}
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="mt-4 w-full rounded-xl border border-ink-200 py-3 text-sm font-semibold text-ink-700 transition-transform duration-150 ease-out active:scale-[0.98]"
              >
                Got it
              </button>
            </div>
          </div>,
          document.body
        )}
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
  /** Told whenever this field starts or stops holding a usable number. */
  onValidity?: (id: string, valid: boolean) => void
}

/**
 * Collects the "this box is empty / out of range" flags from a set of
 * NumberFields so the surrounding form can refuse to submit. Pass `onValidity`
 * down to every field; read `allValid` next to the submit button.
 */
export function useNumberFieldValidity() {
  const [bad, setBad] = useState<string[]>([])
  const onValidity = useCallback((id: string, valid: boolean) => {
    setBad((prev) => {
      const has = prev.includes(id)
      if (valid === !has) return prev
      return valid ? prev.filter((x) => x !== id) : [...prev, id]
    })
  }, [])
  return { allValid: bad.length === 0, onValidity }
}

/**
 * A plain number with its unit. Preferred over preset chips wherever the reader
 * plausibly knows the real figure — "12 °C" or "10 min" says what it means,
 * where "Cold" or "typical" hides a multiplier they can't see or argue with.
 *
 * The text you type is held as a string, not as the clamped number: a field
 * bound straight to `value` re-writes itself on every keystroke, so clearing it
 * snaps back to 0 (or to `min`) and "0.59" is unreachable because "0." parses
 * to 0 mid-word. Here an empty or out-of-range box simply stays what you typed
 * and turns red, and only a usable number is handed upwards.
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
  onValidity,
}: NumberFieldProps) {
  const [draft, setDraft] = useState(() => String(value))
  const inputRef = useRef<HTMLInputElement>(null)

  // What we last handed up. Lets us tell an outside change (a reset, a preset)
  // — which should overwrite the box — from our own echo, which must not.
  const emitted = useRef(value)
  useEffect(() => {
    if (value !== emitted.current) {
      emitted.current = value
      setDraft(String(value))
    }
  }, [value])

  const trimmed = draft.trim()
  const parsed = trimmed === "" ? NaN : Number(trimmed)
  const problem = Number.isNaN(parsed)
    ? "Enter a number"
    : parsed < min || parsed > max
      ? `Must be between ${min} and ${max}`
      : null

  useEffect(() => {
    onValidity?.(id, problem === null)
    return () => onValidity?.(id, true)
  }, [id, problem, onValidity])

  function commit(next: string) {
    setDraft(next)
    const n = next.trim() === "" ? NaN : Number(next)
    if (!Number.isNaN(n) && n >= min && n <= max) {
      emitted.current = n
      onChange(n)
    }
  }

  // Arrow keys still step the value — type="text" is what keeps the typing sane
  // (no browser sanitising "0." or "-" away mid-word), but it drops the native
  // spinner behaviour along with the spinner arrows.
  function nudge(dir: 1 | -1) {
    const base = Number.isNaN(parsed) ? value : parsed
    const decimals = (String(step).split(".")[1] ?? "").length
    const next = Number((base + dir * step).toFixed(decimals))
    commit(String(Math.min(max, Math.max(min, next))))
  }

  return (
    <div>
      {/* The label is the box's own first line rather than a row above it.
          This is a Material "filled" field with the label permanently in its
          floated position — deliberately NOT a floating label, which animates
          from inside the box to the top as you type. The usability complaints
          about that pattern are about the movement and about an empty field
          reading as pre-filled (NN/g on placeholders); neither can happen here,
          because every one of these fields ships with a value, so a floating
          label would sit floated 100% of the time anyway. What is left is the
          part that pays: label and value are one object, and the pair costs one
          row instead of two.

          The readout lives in the tip, not under the box. It is the line that
          says what the number you typed actually does, so it has to stay — but
          nine of them stacked down a form is what made the page read as grey
          soup. It sits at the top of the tip because it is the live half. */}
      <div
        onClick={(e) => {
          // The padding around the input is part of the target — except over
          // the tip, where focusing the input would shut the tooltip that the
          // same tap just opened.
          if ((e.target as HTMLElement).closest("button")) return
          inputRef.current?.focus()
        }}
        className={`cursor-text rounded-xl border bg-white px-3.5 pb-2.5 pt-2 ${
          problem
            ? "border-red-400 focus-within:border-red-500 focus-within:ring-2 focus-within:ring-red-200"
            : "border-ink-200 focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-200"
        }`}
      >
        <div className="flex min-h-4 items-center gap-1.5">
          <label
            htmlFor={id}
            className="cursor-text text-[10px] font-semibold uppercase tracking-wider text-ink-400"
          >
            {label}
          </label>
          {(explain || readout) && (
            <InfoTip>
              {readout && (
                <span className="mb-1.5 block font-semibold text-ink-900">{readout}</span>
              )}
              {explain}
            </InfoTip>
          )}
        </div>
        {/* The unit sits beside the input, not over it: absolutely positioning
            it put it on top of the native spinner arrows, which clipped "min"
            and wrapped "€/kWh" out of the box. */}
        <div className="flex items-baseline gap-2">
        <input
          id={id}
          ref={inputRef}
          type="text"
          // A decimal pad has no minus key, so anything that can go negative
          // gets the full keyboard instead.
          inputMode={min < 0 ? "text" : Number.isInteger(step) ? "numeric" : "decimal"}
          autoComplete="off"
          value={draft}
          aria-invalid={problem !== null}
          aria-describedby={problem ? `${id}-error` : undefined}
          onChange={(e) => commit(e.target.value)}
          // Tidy "040" back to "40" once you've stopped typing — never during,
          // where rewriting the box under the cursor is what made this
          // infuriating in the first place.
          onBlur={() => {
            if (problem === null && String(parsed) !== trimmed) setDraft(String(parsed))
          }}
          onKeyDown={(e) => {
            if (e.key === "ArrowUp" || e.key === "ArrowDown") {
              e.preventDefault()
              nudge(e.key === "ArrowUp" ? 1 : -1)
            }
          }}
          // 16px on a phone: WebKit zooms the page when a field under 16px
          // takes focus, which on this form meant every tap on a number
          // jolted the layout.
          className="min-w-0 flex-1 bg-transparent text-base text-ink-900 outline-none sm:text-sm"
        />
        <span className="shrink-0 whitespace-nowrap font-mono text-xs text-ink-400">
          {unit}
        </span>
        </div>
      </div>
      {problem && (
        <p id={`${id}-error`} className="mt-1 text-xs leading-relaxed text-red-600">
          {problem}
        </p>
      )}
    </div>
  )
}
