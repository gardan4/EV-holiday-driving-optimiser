"use client"

import { BatteryCharging, Zap } from "lucide-react"
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
      <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-ink-500">
        Your car
      </span>
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
              </div>
            </button>
          )
        })}
      </div>
    </div>
  )
}
