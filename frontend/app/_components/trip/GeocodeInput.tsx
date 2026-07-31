"use client"

import { useEffect, useRef, useState } from "react"
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
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

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
    if (next.trim().length < 2) {
      setHits([])
      setOpen(false)
      return
    }
    timer.current = setTimeout(async () => {
      setLoading(true)
      try {
        const results = await geocode(next.trim())
        setHits(results)
        setOpen(true)
        setActive(-1)
      } catch {
        setHits([])
      } finally {
        setLoading(false)
      }
    }, 400)
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
      <label className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-ink-500">
        {label}
      </label>
      <div className="relative">
        <MapPin
          className={`pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 ${
            value ? "text-brand-500" : "text-ink-300"
          }`}
        />
        <input
          value={text}
          onChange={(e) => handleInput(e.target.value)}
          onKeyDown={onKeyDown}
          onFocus={() => hits.length > 0 && setOpen(true)}
          placeholder={placeholder}
          role="combobox"
          aria-expanded={open}
          aria-label={label}
          className={`w-full rounded-xl border bg-white py-2.5 pl-9 pr-3 text-sm text-ink-900 outline-none transition-colors placeholder:text-ink-300 focus:border-brand-400 focus:ring-2 focus:ring-brand-200 ${
            value ? "border-brand-300" : "border-ink-200"
          } ${loading ? "animate-pulse-soft" : ""}`}
        />
      </div>
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
