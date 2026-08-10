"use client"

import { useMemo, useState } from "react"
import { BatteryCharging, Gauge, Search, Zap } from "lucide-react"
import { Vehicle } from "@/lib/client"

interface VehiclePickerProps {
  vehicles: Vehicle[]
  value: string | null
  onChange: (vehicleId: string) => void
}

/** Card-per-car picker (radio semantics), filterable once the catalog is long. */
export default function VehiclePicker({ vehicles, value, onChange }: VehiclePickerProps) {
  const [query, setQuery] = useState("")

  // Match the printed name only. Folding the spec numbers in as well seemed
  // helpful until "ioniq 6" matched the Ioniq 5, whose 72.6 kWh contains a 6 —
  // people search for their car by name, so the name is what we search.
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return vehicles
    const terms = q.split(/\s+/)
    return vehicles.filter((v) => {
      const hay = `${v.make} ${v.model} ${v.variant ?? ""}`.toLowerCase()
      return terms.every((t) => hay.includes(t))
    })
  }, [vehicles, query])

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
          No car matches “{query}”.{" "}
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
