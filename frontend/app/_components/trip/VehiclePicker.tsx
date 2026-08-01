"use client"

import { BatteryCharging, Gauge, Zap } from "lucide-react"
import { Vehicle } from "@/lib/client"

interface VehiclePickerProps {
  vehicles: Vehicle[]
  value: string | null
  onChange: (vehicleId: string) => void
}

/** Card-per-car picker (radio semantics). */
export default function VehiclePicker({ vehicles, value, onChange }: VehiclePickerProps) {
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
            peak), how much it uses at speed, and its top speed — the plan never simulates
            faster than the car can actually go.
          </span>
        </span>
      </span>
      {vehicles.length === 0 && (
        <div className="rounded-xl border border-dashed border-ink-200 px-3 py-4 text-center text-xs text-ink-400">
          Loading the car list… if this stays empty the API isn&apos;t reachable.
        </div>
      )}
      <div
        role="radiogroup"
        aria-label="Your car"
        className="grid max-h-56 grid-cols-1 gap-2 overflow-y-auto pr-1 sm:grid-cols-2"
      >
        {vehicles.map((v) => {
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
    </div>
  )
}
