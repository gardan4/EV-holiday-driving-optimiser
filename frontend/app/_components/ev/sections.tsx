/**
 * The catalog page, in the pieces it is actually made of.
 *
 * `/ev/[slug]/page.tsx` was one 275-line render covering eight distinct
 * sections, which made the interesting part — what the page *claims*, and
 * which of those claims is derived from the catalog rather than typed — hard
 * to see past the Tailwind. Each section is now a named component, so the page
 * file reads as an outline and a diff to one section touches one component.
 *
 * These are presentational and server-rendered: no state, no effects, no
 * "use client". Every number arrives already computed, from `lib/vehicles.ts`,
 * which is the point — nothing on these pages is hand-typed, so nothing can
 * quietly go stale or contradict the planner.
 */

import Link from "next/link"
import type { Vehicle } from "@/lib/client"
import {
  averageKw,
  chargeMinutes,
  consumptionWhKm,
  kwhRange,
  nameplateShortName,
  peakKw,
  rangeKm,
  vehicleName,
  vehicleShortName,
} from "@/lib/vehicles"
import ChargeCurveChart from "./ChargeCurveChart"

/** The windows people actually plan around: the standard benchmark, the one the
 *  planner tends to pick, and the tail that shows why it does not go to 100. */
const CHARGE_WINDOWS: [number, number][] = [
  [10, 80],
  [20, 80],
  [10, 60],
  [20, 70],
  [80, 100],
]

const H2 = "mt-12 font-display text-2xl font-semibold text-ink-900"
const H3 = "mt-8 font-display text-lg font-semibold text-ink-900"
const LEAD = "mt-3 text-sm leading-relaxed text-ink-500"

export function Table({
  caption,
  head,
  rows,
}: {
  caption: string
  head: string[]
  rows: string[][]
}) {
  return (
    <div className="mt-4 overflow-x-auto rounded-2xl border border-ink-100 bg-white shadow-sm">
      <table className="w-full min-w-[28rem] text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead>
          <tr className="border-b border-ink-100 text-left text-xs uppercase tracking-wide text-ink-400">
            {head.map((h) => (
              <th key={h} scope="col" className="px-4 py-2.5 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-ink-100">
          {rows.map((row) => (
            <tr key={row[0]}>
              {row.map((cell, i) => (
                <td
                  key={i}
                  className={
                    i === 0
                      ? "px-4 py-2.5 font-medium text-ink-900"
                      : "px-4 py-2.5 font-mono text-ink-600"
                  }
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function KeyNumbers({ vehicle }: { vehicle: Vehicle }) {
  const items = [
    { label: "Usable battery", value: `${vehicle.usable_kwh} kWh` },
    { label: "Peak DC power", value: `${Math.round(peakKw(vehicle))} kW` },
    { label: "10-80% charge", value: `${Math.round(chargeMinutes(vehicle, 10, 80))} min` },
    { label: "Average kW 10-80%", value: `${Math.round(averageKw(vehicle, 10, 80))} kW` },
    // Not kerb mass, which is what this tile used to claim: the catalog's
    // `mass_kg` is kerb plus a nominal 180 kg of people and luggage, because
    // that is the load every other figure on this page already assumes.
    { label: "Mass, loaded", value: `${Math.round(vehicle.mass_kg)} kg` },
    { label: "Top speed", value: `${Math.round(vehicle.top_speed_kph)} km/h` },
  ]
  return (
    <dl className="mt-8 grid grid-cols-2 gap-px overflow-hidden rounded-2xl border border-ink-100 bg-ink-100 sm:grid-cols-3">
      {items.map((item) => (
        <div key={item.label} className="bg-white p-4">
          <dt className="text-xs uppercase tracking-wide text-ink-400">{item.label}</dt>
          <dd className="mt-1 font-display text-xl font-semibold text-ink-900">{item.value}</dd>
        </div>
      ))}
    </dl>
  )
}

/** Why two cars sharing a name are two cars to a trip planner. Only rendered
 *  when the nameplate actually has more than one variant — which is the whole
 *  reason variants are grouped onto one page rather than published separately. */
export function VariantComparison({ variants, name }: { variants: Vehicle[]; name: string }) {
  return (
    <>
      <h2 className={H2}>Which version you have matters</h2>
      <p className={LEAD}>
        These share a name and not much else that a trip planner cares about. Pack size sets how far
        you go between stops, and the curve sets how long each one takes.
      </p>
      <Table
        caption={`Modelled figures for each version of the ${name}.`}
        head={["Version", "Usable battery", "Peak DC", "10-80%", "At 130 km/h", "Range at 130"]}
        rows={variants.map((x) => [
          vehicleShortName(x),
          `${x.usable_kwh} kWh`,
          `${Math.round(peakKw(x))} kW`,
          `${Math.round(chargeMinutes(x, 10, 80))} min`,
          `${Math.round(consumptionWhKm(x, 130))} Wh/km`,
          `${Math.round(rangeKm(x, 130))} km`,
        ])}
      />
    </>
  )
}

export function ChargingSection({
  variants,
  solo,
  peak,
  taper,
}: {
  variants: Vehicle[]
  solo: boolean
  peak: number
  taper: number | null
}) {
  return (
    <>
      <h2 className={H2}>DC charging curve</h2>
      <p className={LEAD}>
        Charging power against state of charge, on a charger fast enough not to be the limit. Peak
        power is {Math.round(peak)} kW
        {taper !== null
          ? `, but it has halved by about ${taper}% state of charge. That is why a short stop low
             down the curve usually beats a long one near the top.`
          : "."}
      </p>

      <div className="mt-5 rounded-2xl border border-ink-100 bg-white p-4 shadow-sm">
        <ChargeCurveChart vehicles={variants} />
      </div>

      {variants.map((x) => (
        <div key={x.slug}>
          <h3 className={H3}>
            How long a stop takes{solo ? "" : `, ${vehicleShortName(x)}`}
          </h3>
          <Table
            caption={`Modelled DC charging times for the ${vehicleName(x)}, on an unrestricted charger.`}
            head={["Charge from", "Time", "Energy added", "Average power"]}
            rows={CHARGE_WINDOWS.map(([from, to]) => [
              `${from}% to ${to}%`,
              `${Math.round(chargeMinutes(x, from, to))} min`,
              `${((x.usable_kwh * (to - from)) / 100).toFixed(1)} kWh`,
              `${Math.round(averageKw(x, from, to))} kW`,
            ])}
          />
        </div>
      ))}

      {variants.map((x) => (
        <div key={x.slug}>
          <h3 className={H3}>
            The curve, point by point{solo ? "" : `, ${vehicleShortName(x)}`}
          </h3>
          <Table
            caption={`Charging power at each state of charge for the ${vehicleName(x)}.`}
            head={["State of charge", "Power"]}
            rows={x.charge_curve.map(([soc, kw]) => [`${soc}%`, `${Math.round(kw)} kW`])}
          />
        </div>
      ))}
    </>
  )
}

export function ConsumptionSection({
  vehicle,
  speeds,
  solo,
}: {
  vehicle: Vehicle
  speeds: number[]
  solo: boolean
}) {
  return (
    <>
      <h2 className={H2}>Consumption and range by cruise speed</h2>
      <p className={LEAD}>
        Steady motorway cruising on flat road in mild weather, from the fitted model{" "}
        <span className="whitespace-nowrap font-mono text-ink-700">
          Wh/km = {vehicle.consumption.a_wh_km} + {vehicle.consumption.b_wh_km_per_kph2} · v²
        </span>
        {solo ? "" : ` for the ${vehicleShortName(vehicle)}`}. The &ldquo;usable range&rdquo; column
        is the honest one for trip planning: it is 100% down to 10%, because nobody arrives on empty.
      </p>
      <Table
        caption={`Modelled motorway consumption and range for the ${vehicleName(vehicle)} at each cruise speed.`}
        head={["Cruise speed", "Consumption", "Range (full pack)", "Usable range (100 to 10%)"]}
        rows={speeds.map((kph) => [
          `${kph} km/h`,
          `${Math.round(consumptionWhKm(vehicle, kph))} Wh/km`,
          `${Math.round(rangeKm(vehicle, kph))} km`,
          `${Math.round(rangeKm(vehicle, kph, 90))} km`,
        ])}
      />
    </>
  )
}

/** The section that makes the rest of the page trustworthy. Keep it honest —
 *  flat road, mild weather, no charger-side limits — it is why the numbers
 *  above are worth citing. */
export function ModelCaveats() {
  return (
    <>
      <h2 className={H2}>What the model does not include</h2>
      <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-relaxed text-ink-500">
        <li>
          Elevation. The trip planner does account for climbs and descents; the range figures on this
          page are flat-road, so an alpine route will come out worse than the table.
        </li>
        <li>
          Temperature. These are mild-weather numbers. Cold raises consumption and cuts the charging
          curve. The planner models both. The numbers on this page do not.
        </li>
        <li>
          Wind, rain, roof boxes, payload and tyre choice, all of which move real consumption more
          than most people expect.
        </li>
        <li>
          Charger-side limits. The charging times above assume the site can deliver whatever the car
          asks for. A 150 kW cabinet shared with the car next to you will not.
        </li>
      </ul>
    </>
  )
}

/** Marks a curve rebuilt from the CC BY measured-profile dataset; see
 *  `scripts/refit_curves.py`. Matching on the DOI rather than on prose means a
 *  reworded note cannot silently turn a modelled curve into a measured claim. */
const MEASURED_MARK = "doi:10.1184/R1/30570653"

export function Provenance({ variants }: { variants: Vehicle[] }) {
  // Deduped: variants on one platform share the opening paragraph, and printing
  // it three times reads as padding rather than provenance.
  const notes = [...new Set(variants.map((x) => x.source_note).filter(Boolean))]
  if (notes.length === 0) return null
  // Some curves are digitised from measured charging sessions and some are
  // still fitted by hand to published tests. The page states every other
  // assumption it makes, so staying quiet about which kind you are reading
  // would be the one place it overclaims.
  const measured = notes.some((note) => note?.includes(MEASURED_MARK))
  return (
    <>
      <h2 className={H2}>Where these numbers come from</h2>
      {notes.map((note) => (
        <p key={note} className={LEAD}>
          {note}
        </p>
      ))}
      {!measured && (
        <p className={LEAD}>
          This curve is modelled from published charging tests rather than
          digitised from a measured session, so read the stop times as an
          estimate.
        </p>
      )}
    </>
  )
}

export function PlanCta({ variants }: { variants: Vehicle[] }) {
  return (
    <div className="mt-12 rounded-2xl border border-brand-200 bg-brand-50 p-6">
      <h2 className="font-display text-xl font-semibold text-ink-900">
        What speed should you actually drive?
      </h2>
      <p className="mt-2 text-sm leading-relaxed text-ink-600">
        It depends on your route, on where the fast chargers happen to be and how the legal limits
        fall. Plan the real trip and the simulator will sweep every cruise speed against this
        car&apos;s curves and tell you which one arrives first.
      </p>
      <Link
        href="/"
        className="mt-4 inline-block rounded-xl bg-brand-500 px-5 py-2.5 font-semibold text-white transition hover:bg-brand-600"
      >
        Plan a trip in the {nameplateShortName(variants)}
      </Link>
    </div>
  )
}

export function RelatedModels({
  make,
  related,
}: {
  make: string
  related: [string, Vehicle[]][]
}) {
  if (related.length === 0) return null
  return (
    <section className="mt-12">
      <h2 className="font-display text-xl font-semibold text-ink-900">Other {make} models</h2>
      <ul className="mt-4 grid gap-3 sm:grid-cols-2">
        {related.map(([key, group]) => (
          <li key={key}>
            <Link
              href={`/ev/${key}`}
              className="block rounded-xl border border-ink-100 bg-white p-3 text-sm shadow-sm transition hover:border-brand-300"
            >
              <span className="font-semibold text-ink-900">{nameplateShortName(group)}</span>
              <span className="ml-2 text-ink-400">
                {kwhRange(group)} kWh · {Math.round(Math.max(...group.map(peakKw)))} kW
              </span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  )
}
