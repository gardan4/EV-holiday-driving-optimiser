"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { ArrowRight, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { getVehicles, planTrip, PlacePoint, Vehicle } from "@/lib/client"
import { defaultDepartureIso } from "@/lib/format"
import GeocodeInput from "./GeocodeInput"
import VehiclePicker from "./VehiclePicker"

const CONDITIONS = [
  { label: "Mild", factor: 1.0, hint: "≥ 10 °C" },
  { label: "Cold", factor: 1.15, hint: "around 0 °C" },
  { label: "Freezing", factor: 1.3, hint: "well below 0 °C" },
]

/** The planner form. On submit: POST /api/trips → navigate to the permalink. */
export default function TripForm() {
  const router = useRouter()
  const [vehicles, setVehicles] = useState<Vehicle[]>([])
  const [origin, setOrigin] = useState<PlacePoint | null>(null)
  const [dest, setDest] = useState<PlacePoint | null>(null)
  const [vehicleId, setVehicleId] = useState<string | null>(null)
  const [departure, setDeparture] = useState(defaultDepartureIso())
  const [departSoc, setDepartSoc] = useState(100)
  const [targetSoc, setTargetSoc] = useState(10)
  const [factor, setFactor] = useState(1.0)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    getVehicles()
      .then((vs) => {
        setVehicles(vs)
        if (vs.length > 0) setVehicleId((prev) => prev ?? vs[0].id)
      })
      .catch(() => toast.error("Could not load the car list — is the API running?"))
  }, [])

  const ready = origin && dest && vehicleId && !submitting

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!origin || !dest || !vehicleId) return
    setSubmitting(true)
    try {
      const trip = await planTrip({
        origin,
        dest,
        vehicle_id: vehicleId,
        departure_iso: departure,
        depart_soc: departSoc,
        target_soc: targetSoc,
        conditions_factor: factor,
      })
      router.push(`/trip/${trip.id}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Planning failed")
      setSubmitting(false)
    }
  }

  return (
    <form
      onSubmit={submit}
      className="rounded-2xl border border-ink-100 bg-white/90 p-5 shadow-xl shadow-ink-900/5 backdrop-blur sm:p-6"
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <GeocodeInput label="From" placeholder="Utrecht…" value={origin} onChange={setOrigin} />
        <GeocodeInput label="To" placeholder="Innsbruck…" value={dest} onChange={setDest} />
      </div>

      <div className="mt-4">
        <VehiclePicker vehicles={vehicles} value={vehicleId} onChange={setVehicleId} />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <div>
          <label
            htmlFor="departure"
            className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-ink-500"
          >
            Departure
          </label>
          <input
            id="departure"
            type="datetime-local"
            value={departure}
            onChange={(e) => setDeparture(e.target.value)}
            className="w-full rounded-xl border border-ink-200 bg-white px-3 py-2.5 text-sm text-ink-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-200"
          />
        </div>
        <div>
          <span className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-ink-500">
            Conditions
          </span>
          <div className="grid grid-cols-3 gap-1.5" role="radiogroup" aria-label="Conditions">
            {CONDITIONS.map((c) => (
              <button
                key={c.label}
                type="button"
                role="radio"
                aria-checked={factor === c.factor}
                onClick={() => setFactor(c.factor)}
                title={c.hint}
                className={`rounded-xl border px-2 py-2.5 text-sm font-medium transition-colors ${
                  factor === c.factor
                    ? "border-brand-400 bg-brand-50 text-ink-900 ring-2 ring-brand-200"
                    : "border-ink-200 bg-white text-ink-500 hover:border-ink-300"
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <SocSlider
          id="depart-soc"
          label="Battery at departure"
          value={departSoc}
          min={30}
          max={100}
          onChange={setDepartSoc}
        />
        <SocSlider
          id="target-soc"
          label="Battery on arrival (at least)"
          value={targetSoc}
          min={5}
          max={80}
          onChange={setTargetSoc}
        />
      </div>

      <button
        type="submit"
        disabled={!ready}
        className="mt-6 flex w-full items-center justify-center gap-2 rounded-xl bg-ink-900 px-6 py-3.5 font-display text-base font-semibold text-white transition-all hover:bg-ink-700 disabled:cursor-not-allowed disabled:bg-ink-200 disabled:text-ink-400"
      >
        {submitting ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            Simulating every speed…
          </>
        ) : (
          <>
            Find my fastest speed
            <ArrowRight className="h-4 w-4" />
          </>
        )}
      </button>
    </form>
  )
}

function SocSlider({
  id,
  label,
  value,
  min,
  max,
  onChange,
}: {
  id: string
  label: string
  value: number
  min: number
  max: number
  onChange: (v: number) => void
}) {
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-ink-500"
      >
        {label}
        <span className="rounded-md bg-brand-50 px-1.5 py-0.5 font-mono text-xs font-bold text-brand-700">
          {value}%
        </span>
      </label>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={5}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-brand-500"
      />
    </div>
  )
}
