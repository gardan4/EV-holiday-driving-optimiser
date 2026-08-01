"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { ArrowRight, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { getVehicles, planTrip, PlacePoint, Vehicle } from "@/lib/client"
import { defaultDepartureIso } from "@/lib/format"
import { chargePowerFactorForTemp, consumptionFactorForTemp } from "@/lib/weather"
import GeocodeInput from "./GeocodeInput"
import VehiclePicker from "./VehiclePicker"

/**
 * An ordinary road's flow speed lifts more gently than a motorway's legal cap
 * when you push: +20 km/h on the autobahn is not +20 through a village. Mirrors
 * the old Legal/Brisk/Assertive presets (0 -> 1.00, 10 -> 1.08, 20 -> 1.15).
 */
function freeflowFactorFor(overCapKph: number): number {
  return Math.min(1.15, 1 + overCapKph * 0.0075)
}

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
  const [tempC, setTempC] = useState(12)
  const [overCap, setOverCap] = useState(0)
  const [autobahnShare, setAutobahnShare] = useState(30)
  const [stopOverhead, setStopOverhead] = useState(8)
  const [queue, setQueue] = useState(0)
  const [restEveryH, setRestEveryH] = useState(0)
  const [restMin, setRestMin] = useState(20)
  const [price, setPrice] = useState(0.59)
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
        temperature_c: tempC,
        autobahn_open_share: autobahnShare / 100,
        over_cap_kph: overCap,
        over_freeflow_factor: freeflowFactorFor(overCap),
        stop_overhead_min: stopOverhead,
        queue_min: queue,
        rest_interval_min: restEveryH > 0 ? restEveryH * 60 : 0,
        rest_min: restEveryH > 0 ? restMin : 0,
        price_per_kwh: price,
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
        <NumberField
          id="temp"
          label="Temperature"
          unit="°C"
          value={tempC}
          min={-30}
          max={45}
          step={1}
          onChange={setTempC}
          explain="The temperature you expect for most of the drive. Cold hits twice: the car uses more energy AND the battery accepts charge more slowly. The second effect is the bigger one — it can move the best cruise speed down by 30 km/h."
          readout={describeTemp(tempC)}
        />
      </div>

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <SocSlider
          id="depart-soc"
          label="Battery at departure"
          explain="How full the car is when you set off — charge at home the night before and this is 100%."
          value={departSoc}
          min={30}
          max={100}
          onChange={setDepartSoc}
        />
        <SocSlider
          id="target-soc"
          label="Battery on arrival (at least)"
          explain="Leave enough to reach the chalet, the shops and a charger. Low means you arrive nearly empty, which is the fastest plan."
          value={targetSoc}
          min={5}
          max={80}
          onChange={setTargetSoc}
        />
      </div>

      <details className="group mt-4 rounded-xl border border-ink-200 bg-white px-4 py-3">
        <summary className="cursor-pointer select-none text-xs font-semibold uppercase tracking-wider text-ink-500 marker:content-['']">
          Fine-tune the assumptions
          <span className="ml-2 font-normal normal-case tracking-normal text-ink-400">
            these change the answer more than you&apos;d think
          </span>
        </summary>

        <div className="mt-4 space-y-4">
          <div>
            <label
              htmlFor="autobahn"
              className="mb-1.5 flex items-center justify-between text-xs font-semibold uppercase tracking-wider text-ink-500"
            >
              <span className="flex items-center gap-1.5">
                Autobahn actually open
                <InfoTip>
                  How much of the derestricted-looking autobahn you&apos;ll really get to use —
                  the rest is roadworks, traffic and signposted limits. About 30% is realistic.
                  Set it to 100% and fast driving looks free, which is the assumption that
                  flatters high speeds most.
                </InfoTip>
              </span>
              <span className="rounded-md bg-brand-50 px-1.5 py-0.5 font-mono text-xs font-bold text-brand-700">
                {autobahnShare}%
              </span>
            </label>
            <input
              id="autobahn"
              type="range"
              min={0}
              max={100}
              step={5}
              value={autobahnShare}
              onChange={(e) => setAutobahnShare(Number(e.target.value))}
              className="w-full accent-brand-500"
            />
          </div>

          <NumberField
            id="over-cap"
            label="Over the limit"
            unit="km/h"
            value={overCap}
            min={0}
            max={30}
            step={1}
            onChange={setOverCap}
            explain="How far above the posted limit you actually sit. Saves driving time but burns more energy, so past a point it simply buys you another charging stop. Ordinary roads lift more gently than the autobahn does."
            readout={
              overCap === 0
                ? "sitting on the limit"
                : `+${overCap} on limited roads, about +${Math.round((freeflowFactorFor(overCap) - 1) * 100)}% elsewhere`
            }
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <NumberField
              id="stop-overhead"
              label="Time per stop"
              unit="min"
              value={stopOverhead}
              min={0}
              max={30}
              onChange={setStopOverhead}
              explain="Everything around the charging itself: leaving the motorway, parking, plugging in, paying, getting back on. Count about 5 minutes if you plug and go, 12 if there's a detour and payment faff. Higher values punish plans with many short stops."
            />
            <NumberField
              id="queue"
              label="Queue for a charger"
              unit="min"
              value={queue}
              min={0}
              max={30}
              onChange={setQueue}
              explain="Waiting for a free stall. Nil in the middle of the night; 10 or more on a holiday getaway weekend. The more stops a plan makes, the more times you risk it — which is the honest cost of driving fast."
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="grid grid-cols-2 gap-3">
              <NumberField
                id="rest-every"
                label="Break every"
                unit="h"
                value={restEveryH}
                min={0}
                max={8}
                onChange={setRestEveryH}
                explain="How long you'll drive before stopping to rest. Time already spent standing at a charger counts towards it, so a fast plan with many stops usually gets its breaks for free — a slow plan has to add them. Set 0 if you'd rather not model breaks."
                readout={restEveryH === 0 ? "no breaks modelled" : undefined}
              />
              <NumberField
                id="rest-min"
                label="Break length"
                unit="min"
                value={restMin}
                min={0}
                max={120}
                step={5}
                onChange={setRestMin}
                explain="How long each of those breaks lasts."
              />
            </div>
            <NumberField
              id="price"
              label="Charging price"
              unit="€/kWh"
              value={price}
              min={0}
              max={3}
              step={0.01}
              onChange={setPrice}
              explain="What you pay at a public fast charger — about €0.59 on most European networks without a subscription. Priced at the plug, so it already includes charging losses."
            />
          </div>
        </div>
      </details>

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
  explain,
  value,
  min,
  max,
  onChange,
}: {
  id: string
  label: string
  explain?: string
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
        <span className="flex items-center gap-1.5">
          {label}
          {explain && <InfoTip>{explain}</InfoTip>}
        </span>
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

/** Hover/focus explainer. A real <button> so it also works from the keyboard
 * and on touch, where a title attribute shows nothing. */
function InfoTip({ children }: { children: React.ReactNode }) {
  return (
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
        {children}
      </span>
    </span>
  )
}

/** Small segmented control used by the fine-tuning panel. */


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
}

/**
 * A plain number with its unit. Preferred over preset chips wherever the reader
 * plausibly knows the real figure — "12 °C" or "10 min" says what it means,
 * where "Cold" or "typical" hides a multiplier they can't see or argue with.
 */
function NumberField({
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
}: NumberFieldProps) {
  return (
    <div>
      <label
        htmlFor={id}
        className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500"
      >
        {label}
        {explain && <InfoTip>{explain}</InfoTip>}
      </label>
      <div className="relative">
        <input
          id={id}
          type="number"
          inputMode="numeric"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => {
            const n = Number(e.target.value)
            if (!Number.isNaN(n)) onChange(Math.min(max, Math.max(min, n)))
          }}
          className="w-full rounded-xl border border-ink-200 bg-white py-2.5 pl-3 pr-12 text-sm text-ink-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-200"
        />
        <span className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 font-mono text-xs text-ink-400">
          {unit}
        </span>
      </div>
      {readout && <p className="mt-1 text-xs leading-relaxed text-ink-500">{readout}</p>}
    </div>
  )
}


/** Puts the temperature multipliers into words, including "no penalty". */
function describeTemp(tempC: number): string {
  const extra = Math.round((consumptionFactorForTemp(tempC) - 1) * 100)
  const charge = Math.round(chargePowerFactorForTemp(tempC) * 100)
  if (extra === 0 && charge === 100) return "no weather penalty — full range and full charging speed"
  if (extra === 0) return `normal consumption · charging at ${charge}% of full speed`
  return `${extra}% more energy · charging at ${charge}% of full speed`
}
