"use client"

import { useMemo, useState } from "react"
import { useRouter } from "next/navigation"
import { ChevronRight, Loader2, RotateCw } from "lucide-react"
import { toast } from "sonner"
import { PlanRequest, Trip, Vehicle, planTrip } from "@/lib/client"
import { freeflowFactorFor } from "@/lib/driving"
import { auxKwForTemp, consumptionFactorForTemp, describeTemp } from "@/lib/weather"
import { describePayload, extraWhPerKm, payloadExtraKg } from "@/lib/payload"
import { NumberField, useNumberFieldValidity } from "./fields"

/**
 * Everything the answer rests on, in one place: what we assume about the car,
 * what you told us about the trip, and what the model can't see. A plan that
 * says "drive 145" is only worth as much as the figures behind it, so they
 * shouldn't be buried in the form you already left.
 */
export default function Assumptions({ trip }: { trip: Trip }) {
  const router = useRouter()
  const v = trip.result.vehicle

  // Editing an assumption re-runs the plan, which produces a new trip and a
  // new permalink — a trip is a question plus its answer, so changing the
  // question can't overwrite the old answer someone may already have been sent.
  const [draft, setDraft] = useState<PlanRequest>(trip.request)
  const [replanning, setReplanning] = useState(false)
  // An emptied box keeps whatever it typed and blocks the re-plan, rather than
  // quietly snapping back to a number nobody chose.
  const { allValid, onValidity } = useNumberFieldValidity()
  const r = draft
  const tempC = r.temperature_c ?? null

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(trip.request),
    [draft, trip.request]
  )

  function set<K extends keyof PlanRequest>(key: K, value: PlanRequest[K]) {
    setDraft((d) => ({ ...d, [key]: value }))
  }

  async function replan() {
    setReplanning(true)
    try {
      const next = await planTrip(draft)
      router.push(`/trip/${next.id}`)
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Could not re-plan")
      setReplanning(false)
    }
  }
  // Which of the route's countries have their real limits modelled, and
  // whether the derestriction rules can apply at all.
  const routeCountries = trip.result.countries ?? []
  const MODELLED = ["DE", "NL", "BE", "LU", "FR", "CH", "AT", "IT"]
  const modelled = routeCountries.filter((c) => MODELLED.includes(c))
  const crossesGermany = routeCountries.includes("DE")

  const occupants = r.occupants ?? 2
  const luggageKg = r.luggage_kg ?? 30

  // Three terms, combined the way the simulator combines them. Weather scales
  // the car's own curve; payload is ADDED, because rolling drag doesn't grow
  // with v²; and conditioning is a constant kW converted at this speed, so its
  // per-km cost falls the faster you go. Multiplying the last two in would
  // make both of them lie in opposite directions.
  const wh = (kph: number) =>
    Math.round(
      (v.consumption.a_wh_km + v.consumption.b_wh_km_per_kph2 * kph * kph) *
        (tempC != null ? consumptionFactorForTemp(tempC) : (r.conditions_factor ?? 1)) +
        extraWhPerKm(occupants, luggageKg) +
        (tempC != null ? (auxKwForTemp(tempC) / kph) * 1000 : 0)
    )

  return (
    <details className="group mt-6 rounded-2xl border border-ink-100 bg-white px-4 py-3 transition-colors hover:border-ink-200 sm:px-5">
      <summary className="flex cursor-pointer select-none list-none items-center gap-3 marker:content-['']">
        <ChevronRight className="h-4 w-4 shrink-0 text-brand-700 transition-transform group-open:rotate-90" />
        <span className="min-w-0 flex-1">
          <span className="block font-display text-base font-semibold text-ink-900">
            What this answer assumes
          </span>
          <span className="block text-xs text-ink-400">
            the car, every number you set, how the route is modelled, and what the
            model can&apos;t see, all of it editable
          </span>
        </span>
        <span className="shrink-0 rounded-lg border border-ink-200 px-2.5 py-1 text-xs font-semibold text-ink-600 group-hover:border-brand-300 group-hover:text-brand-700">
          <span className="group-open:hidden">Show</span>
          <span className="hidden group-open:inline">Hide</span>
        </span>
      </summary>

      <div className="mt-4 grid gap-6 lg:grid-cols-2">
        {/* --- The car ------------------------------------------------- */}
        <section>
          <h3 className="mb-2 font-display text-sm font-semibold text-ink-900">
            {v.make} {v.model} {v.variant ?? ""}
          </h3>
          <dl className="space-y-1.5 text-sm">
            <Row k="Usable battery" val={`${v.usable_kwh} kWh`} />
            <Row k="Peak DC charging" val={`${Math.round(v.max_dc_kw)} kW`} />
            <Row k="Top speed" val={`${Math.round(v.top_speed_kph)} km/h`} />
            <Row
              k="Mass as modelled"
              val={`${Math.round(v.mass_kg + payloadExtraKg(occupants, luggageKg))} kg`}
            />
            <Row
              k="Energy use"
              val={`${wh(100)} · ${wh(130)} · ${wh(160)} Wh/km at 100 · 130 · 160`}
            />
          </dl>

          <p className="mt-3 mb-1 text-xs font-semibold uppercase tracking-wider text-ink-400">
            Charging curve
          </p>
          <ChargeCurve curve={v.charge_curve} maxKw={v.max_dc_kw} />
          <p className="mt-1 text-xs leading-relaxed text-ink-500">
            Power the car accepts as the battery fills. The taper is why the plan
            arrives low and leaves before it&apos;s full.
          </p>
          {v.source_note && (
            <p className="mt-2 text-xs leading-relaxed text-ink-400">{v.source_note}</p>
          )}
        </section>

        {/* --- Your settings ------------------------------------------- */}
        <section>
          <h3 className="mb-2 font-display text-sm font-semibold text-ink-900">
            What you told us
          </h3>
          <div className="grid grid-cols-2 gap-x-3 gap-y-3">
            <NumberField
              id="a-depart-soc" label="Battery at departure" unit="%"
              onValidity={onValidity}
              value={r.depart_soc ?? 100} min={10} max={100} step={5}
              onChange={(x) => set("depart_soc", x)}
            />
            <NumberField
              id="a-target-soc" label="Arrive with at least" unit="%"
              onValidity={onValidity}
              value={r.target_soc ?? 10} min={5} max={80} step={5}
              onChange={(x) => set("target_soc", x)}
            />
            <NumberField
              id="a-occupants" label="People aboard" unit="people"
              onValidity={onValidity}
              value={occupants} min={1} max={9} step={1}
              onChange={(x) => set("occupants", x)}
            />
            <NumberField
              id="a-luggage" label="Luggage" unit="kg"
              onValidity={onValidity}
              value={luggageKg} min={0} max={400} step={5}
              onChange={(x) => set("luggage_kg", x)}
            />
            <div className="col-span-2 -mt-1">
              <p className="text-xs leading-relaxed text-ink-500">
                {describePayload(occupants, luggageKg, v.usable_kwh, wh(130))}
              </p>
            </div>
            <div className="col-span-2">
              <NumberField
                id="a-temp" label="Temperature" unit="°C"
                onValidity={onValidity}
                value={Math.round(tempC ?? 20)} min={-30} max={45}
                onChange={(x) => set("temperature_c", x)}
                readout={describeTemp(tempC ?? 20)}
              />
            </div>
            <div className="col-span-2">
              <label className="flex cursor-pointer items-start gap-2.5 rounded-xl border border-ink-200 bg-white px-3 py-2.5">
                <input
                  type="checkbox"
                  checked={r.ignore_speed_limits ?? false}
                  onChange={(e) => set("ignore_speed_limits", e.target.checked)}
                  className="mt-0.5 h-4 w-4 shrink-0 accent-brand-500"
                />
                <span className="min-w-0">
                  <span className="block text-sm font-semibold text-ink-900">
                    Ignore speed limits entirely
                  </span>
                  <span className="block text-xs leading-relaxed text-ink-500">
                    Every motorway derestricted, so what the drive costs if the law
                    isn&apos;t the constraint. Ordinary roads still go at their own pace.
                  </span>
                </span>
              </label>
            </div>
            <div className={r.ignore_speed_limits ? "hidden" : "col-span-2"}>
              <NumberField
                id="a-motorway-cap" label="Motorway limit" unit="km/h"
                onValidity={onValidity}
                value={Math.round(r.motorway_cap_kph ?? 130)} min={80} max={200} step={5}
                onChange={(x) => set("motorway_cap_kph", x)}
                readout={
                  modelled.length > 0
                    ? `used outside ${modelled.join("/")}, whose own limits are real`
                    : `${Math.round((r.motorway_cap_kph ?? 130) / 1.609)} mph`
                }
              />
            </div>
            {/* Germany is the only country with derestricted stretches, so on a
                route that never enters it this control does nothing at all —
                and a dead control reads as a broken one. */}
            {crossesGermany && !r.ignore_speed_limits && (
              <div className="col-span-2">
                <NumberField
                  id="a-autobahn" label="German autobahn actually open" unit="%"
                  onValidity={onValidity}
                  value={Math.round((r.autobahn_open_share ?? 0.3) * 100)} min={0} max={100} step={5}
                  onChange={(x) => set("autobahn_open_share", x / 100)}
                  readout="the rest is roadworks, traffic and posted limits"
                />
              </div>
            )}
            <div className="col-span-2">
              <NumberField
                id="a-over-cap" label="Over the posted limit" unit="km/h"
                onValidity={onValidity}
                value={Math.round(r.over_cap_kph ?? 0)} min={0} max={30}
                onChange={(x) => {
                  set("over_cap_kph", x)
                  set("over_freeflow_factor", freeflowFactorFor(x))
                }}
                readout={
                  (r.over_cap_kph ?? 0) === 0
                    ? "sitting on the limit"
                    : `about +${Math.round((freeflowFactorFor(r.over_cap_kph ?? 0) - 1) * 100)}% on ordinary roads`
                }
              />
            </div>
            <NumberField
              id="a-stop" label="Time per stop" unit="min"
              onValidity={onValidity}
              value={Math.round(r.stop_overhead_min ?? 5)} min={0} max={30}
              onChange={(x) => set("stop_overhead_min", x)}
            />
            <NumberField
              id="a-queue" label="Queue for a charger" unit="min"
              onValidity={onValidity}
              value={Math.round(r.queue_min ?? 0)} min={0} max={30}
              onChange={(x) => set("queue_min", x)}
            />
            <div className="col-span-2">
              <NumberField
                id="a-site-power" label="Charger power you get" unit="%"
                onValidity={onValidity}
                value={Math.round((r.site_power_factor ?? 1) * 100)} min={30} max={100} step={5}
                onChange={(x) => set("site_power_factor", x / 100)}
                readout={
                  (r.site_power_factor ?? 1) < 1
                    ? `a 350 kW site treated as ${Math.round(350 * (r.site_power_factor ?? 1))} kW`
                    : "every site delivers its full rating"
                }
              />
            </div>
            <NumberField
              id="a-rest-every" label="Break every" unit="h"
              onValidity={onValidity}
              value={Math.round((r.rest_interval_min ?? 0) / 60)} min={0} max={8}
              onChange={(x) => set("rest_interval_min", x * 60)}
            />
            <NumberField
              id="a-rest-min" label="Break length" unit="min"
              onValidity={onValidity}
              value={Math.round(r.rest_min ?? 20)} min={0} max={120} step={5}
              onChange={(x) => set("rest_min", x)}
            />
            <div className="col-span-2">
              <NumberField
                id="a-price" label="Charging price" unit="€/kWh"
                onValidity={onValidity}
                value={r.price_per_kwh ?? 0.59} min={0} max={3} step={0.01}
                onChange={(x) => set("price_per_kwh", x)}
                readout={
                  (r.rest_interval_min ?? 0) > 0
                    ? "breaks are covered by time already spent at chargers"
                    : "no breaks modelled"
                }
              />
            </div>
          </div>

          <button
            type="button"
            onClick={replan}
            disabled={!dirty || !allValid || replanning}
            className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-ink-900 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-ink-700 disabled:cursor-not-allowed disabled:bg-ink-100 disabled:text-ink-400"
          >
            {replanning ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Re-running every speed…
              </>
            ) : (
              <>
                <RotateCw className="h-4 w-4" />
                {dirty ? "Re-plan with these" : "Change something to re-plan"}
              </>
            )}
          </button>
          <p className="mt-1.5 text-xs leading-relaxed text-ink-400">
            Re-planning creates a new trip with its own link. This one stays exactly
            as it is, so a link you&apos;ve already shared keeps working.
          </p>
        </section>
      </div>

      {/* --- How the route is modelled ---------------------------------- */}
      <div className="mt-5 grid gap-5 border-t border-ink-100 pt-3 lg:grid-cols-2">
        <div>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400">
            How this route is modelled
          </p>
          <ul className="space-y-1 text-xs leading-relaxed text-ink-500">
            <li>
              <b className="font-semibold text-ink-700">Speed.</b>{" "}
              Your cruise speed applies on motorway stretches up to each country&apos;s legal cap (130 in
              AT/NL). German autobahn counts as derestricted only for the{" "}
              {Math.round((r.autobahn_open_share ?? 0.3) * 100)}% you set above.
              roadworks, traffic and posted limits take back the rest. Everywhere else
              the road&apos;s own driving speed governs.
            </li>
            <li>
              {/* The planner routes worldwide, but only these eight countries have
                  their real limits modelled. Saying so matters most exactly where
                  it's wrong: a US interstate is 105-120, so an unflagged 130 would
                  quietly recommend a cruise speed that isn't legal there. */}
              <b className="font-semibold text-ink-700">Which limits are real.</b>{" "}
              {r.ignore_speed_limits ? (
                `None. You asked for every motorway derestricted, so the cruise speed ` +
                `itself is the only limit. This is a what-if, not a plan you can legally ` +
                `drive outside the open stretches of German autobahn.`
              ) : modelled.length > 0 ? (
                <>
                  Legal motorway limits are modelled for{" "}
                  <b className="font-semibold text-ink-700">{modelled.join(", ")}</b> on
                  this route.{" "}
                </>
              ) : (
                <>None of the countries on this route have their own limits modelled. </>
              )}
              {/* One expression, not prose interleaved with {…}: JSX trims the
                  whitespace at each line's edges, which silently glued "113" to
                  "km/h" and then "above" to the dash. */}
              {`Everywhere else uses the ${Math.round(r.motorway_cap_kph ?? 130)} km/h ` +
                `you set above. Check it against the local limit, because it's the ` +
                `number the recommended speed is built on.`}
            </li>
            <li>
              <b className="font-semibold text-ink-700">Elevation.</b>{" "}
              {(trip.result.climb_m / 1000).toFixed(1)} km of cumulative climb on this
              route, costing energy on the way up and giving about two-thirds of it back
              through regen on the way down.
            </li>
            <li>
              <b className="font-semibold text-ink-700">Weather.</b> Consumption is a
              per-car fit from public range tests, scaled by the temperature above. Cold
              also slows <em>charging</em>, the bigger of the two effects on a winter
              trip, so it derates the charge curve as well. Winter tyres, if you tick
              them, add a further 5-10%. The cold model is fitted to motorway cruising,
              where the heater is spread over a lot of kilometres — on short hops in the
              same weather it will read optimistic, and drivers report we are a little
              light below freezing generally.
            </li>
            <li>
              <b className="font-semibold text-ink-700">Cost.</b>{" "}
              Energy is priced at the plug, including charging losses, so the figure is what you&apos;d pay, not
              the energy that reaches the battery.
            </li>
          </ul>
        </div>

        {/* --- Caveats -------------------------------------------------- */}
        <div>
          <p className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-ink-400">
            What the model can&apos;t see
          </p>
          <ul className="space-y-1 text-xs leading-relaxed text-ink-500">
            <li>
              <b className="font-semibold text-ink-700">Which autobahn is open.</b>{" "}
              Road data doesn&apos;t mark derestricted stretches, so the share above is a
              guess you get to make, not a measurement.
            </li>
            <li>
              <b className="font-semibold text-ink-700">Traffic.</b> Not modelled at all.
              the times assume a clear road, which is close enough overnight and
              optimistic in daylight. Departure time shifts the clock, not the plan.
            </li>
            <li>
              <b className="font-semibold text-ink-700">Charger detours.</b> Estimated from
              how far the site sits off the route, not routed door to door, so arrival
              times at chargers are ±5 minutes.
            </li>
            <li>
              <b className="font-semibold text-ink-700">Whether the charger works.</b> Sites
              come from OpenChargeMap and are filtered to operational CCS above 100 kW, but
              a broken or occupied stall is exactly the thing a crowdsourced database is
              worst at. Glance at the operator and Maps link before counting on one.
            </li>
          </ul>
        </div>
      </div>
    </details>
  )
}

function Row({ k, val, note }: { k: string; val: string; note?: string }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-x-3 border-b border-ink-50 pb-1.5">
      <dt className="text-ink-500">{k}</dt>
      <dd className="text-right">
        <span className="font-medium text-ink-900">{val}</span>
        {note && <span className="block text-xs text-ink-400">{note}</span>}
      </dd>
    </div>
  )
}

/**
 * The car's power-vs-SoC curve. Laid out with real margins so the axis labels
 * sit outside the plot area — drawn edge-to-edge, the peak label landed on top
 * of the line it was labelling.
 */
function ChargeCurve({ curve, maxKw }: { curve: Vehicle["charge_curve"]; maxKw: number }) {
  if (!curve?.length) return null

  const W = 268
  const H = 96
  const M = { top: 10, right: 8, bottom: 18, left: 34 }
  const pw = W - M.left - M.right
  const ph = H - M.top - M.bottom

  const peak = Math.max(maxKw, ...curve.map(([, kw]) => kw))
  // Round the top of the scale up to a clean number so the label reads well.
  const top = Math.ceil(peak / 25) * 25
  const x = (soc: number) => M.left + (soc / 100) * pw
  const y = (kw: number) => M.top + (1 - kw / top) * ph

  const line = curve
    .map(([soc, kw], i) => `${i ? "L" : "M"}${x(soc).toFixed(1)},${y(kw).toFixed(1)}`)
    .join(" ")
  const area = `${line} L${x(100).toFixed(1)},${y(0).toFixed(1)} L${x(0).toFixed(1)},${y(0).toFixed(1)} Z`

  const peakPt = curve.reduce((a, b) => (b[1] > a[1] ? b : a))

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full max-w-[280px]"
      role="img"
      aria-label={`Charging power against state of charge, peaking at ${Math.round(peak)} kilowatts and tapering as the battery fills`}
    >
      {/* horizontal guides, labelled outside the plot */}
      {[top, top / 2, 0].map((kw) => (
        <g key={kw}>
          <line
            x1={M.left}
            x2={W - M.right}
            y1={y(kw)}
            y2={y(kw)}
            stroke="#e8edf1"
            strokeWidth={1}
          />
          <text x={M.left - 5} y={y(kw) + 3} fontSize={8.5} fill="#8496a5" textAnchor="end">
            {Math.round(kw)}
          </text>
        </g>
      ))}
      <text
        x={2}
        y={M.top - 2}
        fontSize={8}
        fill="#8496a5"
        style={{ textTransform: "uppercase", letterSpacing: "0.05em" }}
      >
        kW
      </text>

      <path d={area} fill="#17a56b" fillOpacity={0.12} />
      <path d={line} fill="none" stroke="#17a56b" strokeWidth={2} strokeLinejoin="round" />

      {/* the peak, called out where the line actually is */}
      <circle cx={x(peakPt[0])} cy={y(peakPt[1])} r={2.5} fill="#17a56b" />
      <text
        x={x(peakPt[0]) + 5}
        y={y(peakPt[1]) - 3}
        fontSize={8.5}
        fill="#0d6f48"
        fontWeight={600}
      >
        {Math.round(peakPt[1])} kW
      </text>

      {[0, 50, 100].map((soc) => (
        <text
          key={soc}
          x={x(soc)}
          y={H - 5}
          fontSize={8.5}
          fill="#8496a5"
          textAnchor={soc === 0 ? "start" : soc === 100 ? "end" : "middle"}
        >
          {soc}%
        </text>
      ))}
    </svg>
  )
}
