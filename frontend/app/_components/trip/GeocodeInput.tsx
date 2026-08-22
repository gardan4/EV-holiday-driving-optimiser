"use client"

import { useEffect, useId, useRef, useState } from "react"
import { Crosshair, Loader2, MapPin } from "lucide-react"
import { geocode, reverseGeocode, GeocodeHit, PlacePoint } from "@/lib/client"

interface GeocodeInputProps {
  label: string
  placeholder: string
  value: PlacePoint | null
  onChange: (place: PlacePoint | null) => void
  /** Offer "use my location". On the origin only: a destination you are
   *  already standing at is not a journey, and a button that plans a trip to
   *  here is a button nobody wants. */
  allowHere?: boolean
}

/** Debounced place autocomplete backed by GET /api/geocode. */
export default function GeocodeInput({
  label,
  placeholder,
  value,
  onChange,
  allowHere = false,
}: GeocodeInputProps) {
  const [text, setText] = useState(value?.label ?? "")
  const [hits, setHits] = useState<GeocodeHit[]>([])
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [locating, setLocating] = useState(false)
  const [hereError, setHereError] = useState<string | null>(null)
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
    setHereError(null)
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

  /** "Start from where I am."
   *
   *  Two steps, and the second is the one that matters: the browser gives
   *  coordinates, and a trip whose origin reads "52.09, 5.12" is one nobody can
   *  check before planning it or recognise afterwards in their own list. So the
   *  fix is NAMED server-side and the result goes through `select`, exactly as
   *  a picked suggestion does — same shape, same "a place is only chosen when
   *  it resolves" rule, no second way for `origin` to get set.
   *
   *  Every failure lands as text under the box rather than a toast: this is a
   *  field, the reader is looking at it, and the answer to all three failures
   *  (permission refused, no fix, nowhere nameable) is the same — type it. */
  async function locateMe() {
    if (locating) return
    setLocating(true)
    setError(null)
    setOpen(false)
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) => {
        if (typeof navigator === "undefined" || !navigator.geolocation) {
          reject(new Error("no geolocation"))
          return
        }
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: true,
          timeout: 10_000,
          maximumAge: 60_000,
        })
      })
      const hit = await reverseGeocode(pos.coords.latitude, pos.coords.longitude)
      select(hit)
    } catch (err) {
      const denied =
        typeof GeolocationPositionError !== "undefined" &&
        err instanceof GeolocationPositionError &&
        err.code === err.PERMISSION_DENIED
      setHereError(
        denied
          ? "Location is blocked for this site. Type where you're starting from."
          : err instanceof Error && err.message !== "no geolocation"
            ? err.message
            : "Couldn't get your location. Type where you're starting from."
      )
    } finally {
      setLocating(false)
    }
  }

  function select(hit: GeocodeHit) {
    setHereError(null)
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
    } else if (e.key === "Enter") {
      // Nothing arrow-highlighted takes the top hit rather than doing nothing.
      // The form cannot be submitted from here anyway — its button is disabled
      // until both places resolve — so an Enter that fell through was a
      // keystroke that appeared to do nothing on the one control standing
      // between the reader and the answer.
      e.preventDefault()
      select(hits[active >= 0 ? active : 0])
    } else if (e.key === "Escape") {
      setOpen(false)
    }
  }

  // Typed something, picked nothing. The box LOOKS filled — the text is right
  // there — while `value` is null and the submit button is grey, which is the
  // single most reported way this form reads as broken. Only once the dropdown
  // is out of the way, so it is not shouted while the reader is still choosing.
  const unresolved = text.trim().length > 0 && !value && !open

  return (
    <div ref={rootRef} className="relative min-w-0">
      {/* `min-w-0` is load-bearing, not tidiness. A grid item defaults to
          `min-width: auto`, so an auto-sized track takes the item's MAX-content
          width — and a text input carries an intrinsic ~207 px from its default
          `size`, which `min-w-0 flex-1` on the input itself cannot undo while
          nothing upstream is squeezing the row. The field therefore rendered
          wider than the card that holds it and was clipped on a 320 px phone.
          It was 15 px over before this box gained a button and 80 px over
          after, which is the only reason anybody noticed. Fixed here rather
          than by adding `grid-cols-1` at each call site, because that is a list
          somebody has to remember to extend. */}
      {/* Label inside the box, same as the number fields — see the note in
          fields.tsx. Unlike those this one CAN be empty, so it keeps a
          placeholder underneath the label rather than relying on it. */}
      <div
        onClick={(e) => {
          if ((e.target as HTMLElement).closest("button")) return
          inputRef.current?.focus()
        }}
        className={`cursor-text rounded-xl border bg-white px-3.5 pb-2.5 pt-2 transition-colors focus-within:border-brand-400 focus-within:ring-2 focus-within:ring-brand-200 ${
          value ? "border-brand-300" : unresolved ? "border-amber-400" : "border-ink-200"
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
          {/* Inside the box, not beside it: the field is already full width on
              a phone and a control alongside would halve it. A <button> so the
              wrapper's click-to-focus handler steps over it — that handler
              already skips anything inside a button, which is why the tap does
              not shove the keyboard up over the answer.

              Sized for a thumb. It renders at ~62 × 34 and the `before`
              pseudo-element carries the hit area to 44 high, which is what a
              phone needs and what the visible chip cannot be without making
              this field taller than the one beside it. Vertical only: the text
              of the input runs up to its left edge, and stealing taps from
              there would cost somebody their cursor position.

              The label shows at EVERY width, deliberately — it was hidden
              below `sm`, which is backwards. A phone is where "start from
              where I am" is most likely to be the true answer, and a lone
              crosshair beside a text field is a guess about what it does. */}
          {allowHere && (
            <button
              type="button"
              onClick={() => void locateMe()}
              disabled={locating}
              title="Use my current location"
              aria-label="Use my current location"
              className="relative -mr-1 flex shrink-0 items-center gap-1 rounded-lg px-2 py-2 text-xs font-semibold text-brand-700 transition-colors before:absolute before:inset-x-0 before:-inset-y-1.5 before:content-[''] hover:bg-brand-50 disabled:text-ink-400"
            >
              {locating ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Crosshair className="h-3.5 w-3.5" />
              )}
              {locating ? "Locating…" : "Here"}
            </button>
          )}
        </div>
      </div>
      {hereError ? (
        <p className="mt-1 text-xs text-amber-700">{hereError}</p>
      ) : (
        unresolved && (
          <p className="mt-1 text-xs text-amber-700">
            Pick a place from the list to use it.
          </p>
        )
      )}
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
                className={`flex min-h-11 w-full items-center gap-2 px-3 py-2.5 text-left text-sm ${
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
