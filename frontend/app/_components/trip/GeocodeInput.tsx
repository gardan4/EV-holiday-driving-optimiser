"use client"

import { useEffect, useId, useRef, useState } from "react"
import { MapPin } from "lucide-react"
import { geocode, GeocodeHit, PlacePoint } from "@/lib/client"

interface GeocodeInputProps {
  label: string
  placeholder: string
  value: PlacePoint | null
  onChange: (place: PlacePoint | null) => void
}

/** Debounced place autocomplete backed by GET /api/geocode. */
export default function GeocodeInput({ label, placeholder, value, onChange }: GeocodeInputProps) {
  const [text, setText] = useState(value?.label ?? "")
  const [hits, setHits] = useState<GeocodeHit[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const id = useId()

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener("mousedown", onDocClick)
    return () => document.removeEventListener("mousedown", onDocClick)
  }, [])

  function handleInput(next: string) {
    setText(next)
    onChange(null) // typing invalidates the previous selection
    if (timer.current) clearTimeout(timer.current)
    // Three, matching the server's `min_length`. Autocomplete is by far this
    // app's largest consumer of the ORS free tier — it fires while somebody is
    // still typing, so unlike a route it cannot be cached before the fact — and
    // two-letter prefixes are the highest-volume, least-useful bucket: "ut"
    // matches half of Europe. Asking the server anyway would spend a request to
    // be told the query was too short.
    if (next.trim().length < 3) {
      setHits([])
      setOpen(false)
      return
    }
    timer.current = setTimeout(async () => {
      setLoading(true)
      try {
        const results = await geocode(next.trim())
        setHits(results)
        setError(results.length === 0 ? "No places found. Try a city name." : null)
        setOpen(true)
        setActive(-1)
      } catch (err) {
        // Surface the backend's message (e.g. "Routing is not configured") —
        // a silently empty dropdown reads as a broken input.
        setHits([])
        setError(err instanceof Error ? err.message : "Search failed. Try again.")
        setOpen(true)
      } finally {
        setLoading(false)
      }
      // 650ms rather than 400. Long enough that an ordinary typing rhythm
      // produces one request for a word instead of three or four, short enough
      // that the list still feels like it is keeping up. This is the cheapest
      // lever there is on the quota: it multiplies down every search anyone
      // makes, without changing what they can find.
    }, 650)
  }

  function select(hit: GeocodeHit) {
    onChange({ label: hit.label, lat: hit.lat, lon: hit.lon })
    setText(hit.label)
    setOpen(false)
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open || hits.length === 0) return
    if (e.key === "ArrowDown") {
      e.preventDefault()
      setActive((a) => (a + 1) % hits.length)
    } else if (e.key === "ArrowUp") {
      e.preventDefault()
      setActive((a) => (a - 1 + hits.length) % hits.length)
    } else if (e.key === "Enter" && active >= 0) {
      e.preventDefault()
      select(hits[active])
    } else if (e.key === "Escape") {
      setOpen(false)
    }
  }

  return (
    <div ref={rootRef} className="relative">
      {/* Label inside the box, same as the number fields — see the note in
          fields.tsx. Unlike those this one CAN be empty, so it keeps a
          placeholder underneath the label rather than relying on it. */}
      <div
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("button")) return
          inputRef.current?.focus()
        }}
        className={`cursor-text rounded-xl border bg-white px-3.5 pb-2.5 pt-2 transition-colors focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-200 ${
          value ? "border-brand-300" : "border-ink-200"
        } ${loading ? "animate-pulse-soft" : ""}`}
      >
        <label
          htmlFor={id}
          className="cursor-text text-[10px] font-semibold uppercase tracking-wider text-ink-400"
        >
          {label}
        </label>
        <div className="flex items-center gap-2">
          <MapPin
            className={`h-4 w-4 shrink-0 ${value ? "text-brand-500" : "text-ink-300"}`}
          />
          <input
            id={id}
            ref={inputRef}
            value={text}
            onChange={(e) => handleInput(e.target.value)}
            onKeyDown={onKeyDown}
            onFocus={() => hits.length > 0 && setOpen(true)}
            placeholder={placeholder}
            role="combobox"
            aria-expanded={open}
            // Belt and braces. The <label for> above is correctly associated
            // (input.labels is 1), but an explicit role="combobox" moves this
            // off the plain-textbox naming path, and at least one a11y tree
            // reads the placeholder instead. Same string, so nothing conflicts.
            aria-label={label}
            className="min-w-0 flex-1 bg-transparent text-base text-ink-900 outline-none placeholder:text-ink-300 sm:text-sm"
          />
        </div>
      </div>
      {open && hits.length === 0 && error && (
        <div className="absolute z-30 mt-1.5 w-full rounded-xl border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs leading-relaxed text-amber-900 shadow-lg shadow-ink-900/5">
          {error}
        </div>
      )}
      {open && hits.length > 0 && (
        <ul
          role="listbox"
          className="absolute z-30 mt-1.5 w-full overflow-hidden rounded-xl border border-ink-100 bg-white shadow-lg shadow-ink-900/5"
        >
          {hits.map((hit, i) => (
            <li key={`${hit.lat},${hit.lon}`}>
              <button
                type="button"
                role="option"
                aria-selected={i === active}
                onMouseEnter={() => setActive(i)}
                onClick={() => select(hit)}
                className={`flex w-full items-center gap-2 px-3 py-2.5 text-left text-sm ${
                  i === active ? "bg-brand-50 text-ink-900" : "text-ink-700"
                }`}
              >
                <MapPin className="h-3.5 w-3.5 shrink-0 text-ink-300" />
                <span className="truncate">{hit.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
