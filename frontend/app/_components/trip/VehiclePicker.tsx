"use client"

import { useMemo, useState } from "react"
import { BatteryCharging, Gauge, Search, Zap } from "lucide-react"
import { Vehicle } from "@/lib/client"

interface VehiclePickerProps {
  vehicles: Vehicle[]
  value: string | null
  onChange: (vehicleId: string) => void
}

/**
 * One folded spelling of a car's printed name, with every separator removed.
 *
 * Two things people type were unreachable before this. Accents: the catalog
 * says "Škoda" and "Mégane", nobody reaches for the caron, and a plain
 * lower-cased `includes` never matched. Punctuation: the catalog says "ID.3"
 * and people type "id3" or "id 3". Stripping the separators from both sides
 * collapses all three spellings onto one string, so each of them hits.
 *
 * The name is still the only thing searched — see the note in the component.
 */
function fold(text: string): string {
  return text
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .replace(/[^a-z0-9]/g, "")
}

interface SearchResult {
  shown: Vehicle[]
  /** True when nothing matched every term and these are the near misses. */
  approximate: boolean
}

/**
 * Filter the catalog, and fall back to the closest cars rather than to nothing.
 *
 * An empty result is nearly always someone whose exact variant we do not carry
 * — they type "id4 gtx" or "enyaq 60" and the make and model are right. So when
 * requiring every term matches no car, keep the cars that match the most terms
 * instead. That surfaces the rest of the ID.4 range, which is the useful answer.
 *
 * When no term matches anything at all the brand genuinely is not in the
 * catalog, and there is no nearest car worth showing. That case still returns
 * empty, and the component says so plainly.
 */
function search(vehicles: Vehicle[], query: string): SearchResult {
  const terms = query.trim().split(/\s+/).map(fold).filter(Boolean)
  if (terms.length === 0) return { shown: vehicles, approximate: false }

  const scored = vehicles.map((v) => {
    const hay = fold(`${v.make} ${v.model} ${v.variant ?? ""}`)
    return { v, hits: terms.filter((t) => hay.includes(t)).length }
  })

  const exact = scored.filter((s) => s.hits === terms.length)
  if (exact.length > 0) return { shown: exact.map((s) => s.v), approximate: false }

  // Only the joint-best near misses: for "tesla model 9" that is every Tesla,
  // not every car whose name happens to contain a 9.
  const best = Math.max(...scored.map((s) => s.hits))
  if (best === 0) return { shown: [], approximate: false }
  return { shown: scored.filter((s) => s.hits === best).map((s) => s.v), approximate: true }
}

/** Card-per-car picker (radio semantics), filterable once the catalog is long. */
export default function VehiclePicker({ vehicles, value, onChange }: VehiclePickerProps) {
  const [query, setQuery] = useState("")

  // Match the printed name only. Folding the spec numbers in as well seemed
  // helpful until "ioniq 6" matched the Ioniq 5, whose 72.6 kWh contains a 6 —
  // people search for their car by name, so the name is what we search.
  const { shown, approximate } = useMemo(
    () => search(vehicles, query),
    [vehicles, query]
  )

  return (
    <div>
      <span className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500">
        Your car
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
            Sets the battery size, how quickly it charges (the taper matters more than the
            peak), how much it uses at speed, and its top speed. The plan never simulates
            faster than the car can actually go.
          </span>
        </span>
      </span>
      {vehicles.length === 0 && (
        <div className="rounded-xl border border-dashed border-ink-200 px-3 py-4 text-center text-xs text-ink-400">
          Loading the car list… if this stays empty the API isn&apos;t reachable.
        </div>
      )}

      {/* Below about a dozen cars the list is faster to scan than to type into. */}
      {vehicles.length > 12 && (
        <div className="relative mb-2">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-400" />
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Filter ${vehicles.length} cars, try "volvo" or "ioniq"`}
            aria-label="Filter cars"
            className="w-full rounded-xl border border-ink-200 bg-white py-2 pl-9 pr-3 text-sm text-ink-900 placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-200"
          />
        </div>
      )}

      {approximate && (
        <p className="mb-2 text-xs text-ink-500" role="status">
          Nothing matches “{query}” exactly. The closest cars we model:
        </p>
      )}

      <div
        role="radiogroup"
        aria-label="Your car"
        className="grid max-h-56 grid-cols-1 gap-2 overflow-y-auto pr-1 sm:grid-cols-2"
      >
        {shown.map((v) => {
          const selected = v.id === value
          return (
            <button
              key={v.id}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(v.id)}
              className={`rounded-xl border px-3 py-2.5 text-left transition-all ${
                selected
                  ? "border-brand-400 bg-brand-50 ring-2 ring-brand-200"
                  : "border-ink-200 bg-white hover:border-ink-300"
              }`}
            >
              <div className="text-sm font-semibold text-ink-900">
                {v.make} {v.model}
              </div>
              <div className="mt-0.5 flex items-center gap-3 text-xs text-ink-500">
                {v.variant?.includes("kWh") ? (
                  <span className="inline-flex items-center gap-1">
                    <BatteryCharging className="h-3 w-3" />
                    {v.variant}
                  </span>
                ) : (
                  <>
                    <span>{v.variant}</span>
                    <span className="inline-flex items-center gap-1">
                      <BatteryCharging className="h-3 w-3" />
                      {v.usable_kwh} kWh
                    </span>
                  </>
                )}
                <span className="inline-flex items-center gap-1">
                  <Zap className="h-3 w-3" />
                  {Math.round(v.max_dc_kw)} kW DC
                </span>
                <span className="inline-flex items-center gap-1">
                  <Gauge className="h-3 w-3" />
                  max {Math.round(v.top_speed_kph)}
                </span>
              </div>
            </button>
          )
        })}
      </div>

      {vehicles.length > 0 && shown.length === 0 && (
        <p className="mt-2 text-xs text-ink-400">
          We don&apos;t model any car like “{query}” yet.{" "}
          <button
            type="button"
            onClick={() => setQuery("")}
            className="font-semibold text-brand-700 underline underline-offset-2"
          >
            Show all {vehicles.length}
          </button>
        </p>
      )}
    </div>
  )
}
