"use client"

import { useEffect, useState } from "react"
import { useRouter } from "next/navigation"
import { ArrowRight, Loader2 } from "lucide-react"
import { toast } from "sonner"
import { getVehicles, planTrip, PlacePoint, Vehicle } from "@/lib/client"
import { defaultDepartureIso } from "@/lib/format"
import GeocodeInput from "./GeocodeInput"
import VehiclePicker from "./VehiclePicker"

// Cold hits twice: the car uses more, AND the pack accepts charge more slowly.
// The second effect is the bigger one on a long winter trip.
const CONDITIONS = [
  { label: "Mild", factor: 1.0, charge: 1.0, hint: "≥ 10 °C" },
  { label: "Cold", factor: 1.15, charge: 0.8, hint: "around 0 °C — slower charging too" },
  { label: "Freezing", factor: 1.3, charge: 0.55, hint: "below 0 °C — much slower charging" },
]

// How hard you push where the road allows it.
const STYLES = [
  { label: "Legal", cap: 0, flow: 1.0, hint: "sit on the limit" },
  { label: "Brisk", cap: 10, flow: 1.08, hint: "a little over, everywhere" },
  { label: "Assertive", cap: 20, flow: 1.15, hint: "press on wherever you can" },
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
  const [conditions, setConditions] = useState(CONDITIONS[0])
  const [style, setStyle] = useState(STYLES[0])
  const [autobahnShare, setAutobahnShare] = useState(30)
  const [stopOverhead, setStopOverhead] = useState(5)
  const [queue, setQueue] = useState(0)
  const [restBreaks, setRestBreaks] = useState(false)
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
        conditions_factor: conditions.factor,
        charge_power_factor: conditions.charge,
        autobahn_open_share: autobahnShare / 100,
        over_cap_kph: style.cap,
        over_freeflow_factor: style.flow,
        stop_overhead_min: stopOverhead,
        queue_min: queue,
        rest_interval_min: restBreaks ? 180 : 0,
        rest_min: restBreaks ? 20 : 0,
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
        <div>
          <span className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500">
            Conditions
            <InfoTip>
              Cold hits twice: the car uses more energy <em>and</em> the battery accepts charge
              more slowly. The second effect is the bigger one on a long winter drive — it can
              move the best cruise speed down by 30&nbsp;km/h.
            </InfoTip>
          </span>
          <div className="grid grid-cols-3 gap-1.5" role="radiogroup" aria-label="Conditions">
            {CONDITIONS.map((c) => (
              <button
                key={c.label}
                type="button"
                role="radio"
                aria-checked={conditions.label === c.label}
                onClick={() => setConditions(c)}
                title={c.hint}
                className={`rounded-xl border px-2 py-2.5 text-sm font-medium transition-colors ${
                  conditions.label === c.label
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

          <Choice
            label="Driving style"
            explain="How far above the posted limit you actually sit — on the autobahn and on ordinary roads alike. Pushing harder saves driving time but burns more energy, so it can buy you an extra charging stop."
            options={STYLES.map((x) => ({ label: x.label, hint: x.hint }))}
            selected={style.label}
            onSelect={(l) => setStyle(STYLES.find((x) => x.label === l)!)}
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Choice
              label="Time per stop"
              explain="Everything around the charging itself: leaving the motorway, parking, plugging in, paying, getting back on. Higher values punish plans with many short stops."
              options={[
                { label: "5 min", hint: "plug and go — optimistic" },
                { label: "8 min", hint: "typical for a motorway services" },
                { label: "12 min", hint: "realistic with a detour and payment faff" },
              ]}
              selected={`${stopOverhead} min`}
              onSelect={(l) => setStopOverhead(parseInt(l))}
            />
            <Choice
              label="Queue for a charger"
              explain="Waiting for a free stall. The more stops a plan makes, the more times you risk it — which is the honest cost of driving fast."
              options={[
                { label: "0 min", hint: "middle of the night, nobody about" },
                { label: "5 min", hint: "a normal evening" },
                { label: "10 min", hint: "holiday getaway weekend" },
              ]}
              selected={`${queue} min`}
              onSelect={(l) => setQueue(parseInt(l))}
            />
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-ink-200 px-3 py-2.5">
              <input
                type="checkbox"
                checked={restBreaks}
                onChange={(e) => setRestBreaks(e.target.checked)}
                className="mt-0.5 accent-brand-500"
              />
              <span className="text-xs leading-relaxed text-ink-600">
                <span className="inline-flex items-center gap-1.5 font-semibold text-ink-900">
                  Rest breaks
                  <InfoTip>
                    20 minutes off every 3 hours of driving. Time already spent standing at a
                    charger counts towards it, so a fast plan with many stops usually gets its
                    breaks for free — a slow plan with few stops has to add them.
                  </InfoTip>
                </span>
                <br />
                20 min every 3 h of driving
              </span>
            </label>
            <div>
              <label
                htmlFor="price"
                className="mb-1.5 block text-xs font-semibold uppercase tracking-wider text-ink-500"
              >
                <span className="flex items-center gap-1.5">
                  Charging price (€/kWh)
                  <InfoTip>
                    What you pay at a public fast charger — about €0.59 on most European
                    networks without a subscription. Priced at the plug, so it includes
                    charging losses.
                  </InfoTip>
                </span>
              </label>
              <input
                id="price"
                type="number"
                min={0}
                max={3}
                step={0.01}
                value={price}
                onChange={(e) => setPrice(Number(e.target.value))}
                className="w-full rounded-xl border border-ink-200 bg-white px-3 py-2.5 text-sm text-ink-900 outline-none focus:border-brand-400 focus:ring-2 focus:ring-brand-200"
              />
            </div>
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
function Choice({
  label,
  explain,
  options,
  selected,
  onSelect,
}: {
  label: string
  /** One line saying what the setting actually does. Always visible — a
   * tooltip is invisible on touch and easy to miss. */
  explain?: string
  options: { label: string; hint?: string }[]
  selected: string
  onSelect: (label: string) => void
}) {
  return (
    <div>
      <span className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-ink-500">
        {label}
        {explain && (
          <InfoTip>
            {explain}
            <span className="mt-1.5 block space-y-0.5">
              {options.map((o) => (
                <span key={o.label} className="block">
                  <span className="font-semibold text-ink-900">{o.label}</span>
                  {o.hint ? ` — ${o.hint}` : ""}
                </span>
              ))}
            </span>
          </InfoTip>
        )}
      </span>
      <div className="grid grid-cols-3 gap-1.5" role="radiogroup" aria-label={label}>
        {options.map((o) => (
          <button
            key={o.label}
            type="button"
            role="radio"
            aria-checked={selected === o.label}
            onClick={() => onSelect(o.label)}
            title={o.hint}
            className={`rounded-xl border px-2 py-2 text-xs font-medium transition-colors ${
              selected === o.label
                ? "border-brand-400 bg-brand-50 text-ink-900 ring-2 ring-brand-200"
                : "border-ink-200 bg-white text-ink-500 hover:border-ink-300"
            }`}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
  )
}
