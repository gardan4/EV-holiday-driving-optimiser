import type { Metadata } from "next"
import Link from "next/link"
import { notFound, permanentRedirect } from "next/navigation"
import AppHeader from "@/app/_components/AppHeader"
import ChargeCurveChart from "@/app/_components/ev/ChargeCurveChart"
import JsonLd from "@/app/_components/JsonLd"
import SiteFooter from "@/app/_components/SiteFooter"
import { breadcrumbLd, graph, organizationLd, vehicleLd } from "@/lib/seo"
import { abs } from "@/lib/site"
import {
  averageKw,
  chargeMinutes,
  consumptionWhKm,
  cruiseSpeedsFor,
  fetchVehicles,
  groupByNameplate,
  headlineVariant,
  nameplateKey,
  nameplateName,
  nameplateShortName,
  rangeKm,
  taperSoc,
  vehicleName,
  vehicleShortName,
} from "@/lib/vehicles"
import type { Vehicle } from "@/lib/client"

export const revalidate = 3600

interface Params {
  params: Promise<{ slug: string }>
}

/**
 * Resolve a URL slug to the variants published on that page.
 *
 * Returns the nameplate's variants, or the slug of the page a legacy variant
 * URL now lives on. Every `/ev/<variant-slug>` was indexed and pushed to
 * IndexNow before nameplate pages existed, so those URLs have to keep landing
 * somewhere — and because the redirect is derived from the catalog rather than
 * a hand-kept map, it also covers every variant added from here on.
 */
async function resolvePage(slug: string): Promise<Vehicle[]> {
  const all = await fetchVehicles()

  const variants = groupByNameplate(all).get(slug)
  if (variants) return variants

  // Both of these throw, so the caller only ever sees the happy path.
  const legacy = all.find((v) => v.slug === slug)
  if (legacy) permanentRedirect(`/ev/${nameplateKey(legacy)}`)
  notFound()
}

export async function generateMetadata({ params }: Params): Promise<Metadata> {
  const { slug } = await params
  const all = await fetchVehicles()
  const variants = groupByNameplate(all).get(slug)
  // A legacy variant URL redirects before it renders, so its metadata is never
  // the canonical anything — say nothing rather than describe the wrong page.
  if (!variants) return { title: "Car not found" }

  const name = nameplateName(variants)
  const title = `${name} charging curve, consumption and motorway range`
  const packs = [...new Set(variants.map((v) => v.usable_kwh))]
  const peak = Math.max(...variants.map(peakKw))
  const fastest = variants.reduce((a, b) =>
    chargeMinutes(a, 10, 80) <= chargeMinutes(b, 10, 80) ? a : b
  )
  const topSpeed = Math.max(...variants.flatMap((v) => cruiseSpeedsFor(v)))

  const description = `${name}: ${
    packs.length > 1 ? `${packs.length} versions, ${Math.min(...packs)}-${Math.max(...packs)}` : packs[0]
  } kWh usable, up to ${Math.round(peak)} kW peak DC, from about ${Math.round(
    chargeMinutes(fastest, 10, 80)
  )} minutes for 10 to 80%. Modelled consumption and range at every cruise speed from 90 to ${topSpeed} km/h.`

  return {
    title,
    description,
    alternates: { canonical: `/ev/${slug}` },
    openGraph: {
      type: "article",
      url: abs(`/ev/${slug}`),
      title: `${title} · EV Trip Optimizer`,
      description,
    },
  }
}

export default async function VehiclePage({ params }: Params) {
  const { slug } = await params
  const variants = await resolvePage(slug)
  // One fetch, deduped with `resolvePage`'s by Next's request cache — and the
  // related strip needs the whole catalog anyway.
  const all = await fetchVehicles()
  const groups = groupByNameplate(all)

  // The headline variant is the one most people mean: the biggest pack, and
  // among equals the one that charges fastest.
  const v = headlineVariant(variants)
  const name = nameplateName(variants)
  const solo = variants.length === 1
  const peak = Math.max(...variants.map(peakKw))
  const speeds = cruiseSpeedsFor(v)
  const taper = taperSoc(v, 0.5)
  const c100 = consumptionWhKm(v, 100)
  const c130 = consumptionWhKm(v, 130)
  const penalty = Math.round(((c130 - c100) / c100) * 100)
  const related = [...groups.entries()]
    .filter(([key, group]) => key !== slug && group[0].make === v.make)
    .slice(0, 4)

  return (
    <main className="min-h-screen">
      <AppHeader subtitle={name} />
      <JsonLd
        data={graph(
          organizationLd(),
          breadcrumbLd([
            { name: "Home", path: "/" },
            { name: "Electric cars", path: "/ev" },
            { name, path: `/ev/${slug}` },
          ]),
          // One node per variant. They are distinct products that happen to
          // share a page, and collapsing them would throw away exactly the
          // per-variant numbers that make the page worth citing.
          ...variants.map((variant) => vehicleLd(variant, slug))
        )}
      />

      <article className="mx-auto max-w-3xl px-4 pb-16 pt-8 sm:px-6">
        <nav aria-label="Breadcrumb" className="text-sm text-ink-400">
          <Link href="/" className="hover:text-ink-700">
            Home
          </Link>
          <span className="mx-2 text-ink-300">/</span>
          <Link href="/ev" className="hover:text-ink-700">
            Electric cars
          </Link>
          <span className="mx-2 text-ink-300">/</span>
          <span className="text-ink-700">{nameplateShortName(variants)}</span>
        </nav>

        <h1 className="mt-4 font-display text-3xl font-bold tracking-tight text-ink-900 sm:text-4xl">
          {name}
        </h1>
        <p className="mt-1 font-mono text-xs uppercase tracking-[0.18em] text-brand-600">
          Charging curve · consumption · motorway range
        </p>

        <p className="mt-5 text-base leading-relaxed text-ink-500">
          {solo ? (
            <>The {name} has</>
          ) : (
            <>
              We model {variants.length} versions of the {name}. The{" "}
              {vehicleShortName(v)} has
            </>
          )}{" "}
          {v.usable_kwh} kWh of usable battery and peaks at {Math.round(peakKw(v))}
          {" kW"} on a DC charger. In this app&apos;s model it uses about {Math.round(c100)} Wh/km at a steady
          100 km/h and {Math.round(c130)} Wh/km at 130, so {penalty}% more energy per kilometre buys
          you 30 km/h more speed. Weigh that against a {Math.round(chargeMinutes(v, 10, 80))}-minute
          10-80% charging stop and you have the whole question of how fast to drive.
        </p>

        <KeyNumbers vehicle={v} />

        {!solo && (
          <>
            <h2 className="mt-12 font-display text-2xl font-semibold text-ink-900">
              Which version you have matters
            </h2>
            <p className="mt-3 text-sm leading-relaxed text-ink-500">
              These share a name and not much else that a trip planner cares about. Pack size sets
              how far you go between stops, and the curve sets how long each one takes.
            </p>
            <Table
              caption={`Modelled figures for each version of the ${name}.`}
              head={[
                "Version",
                "Usable battery",
                "Peak DC",
                "10-80%",
                "At 130 km/h",
                "Range at 130",
              ]}
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
        )}

        {/* ---------------------------------------------------------------- */}
        <h2 className="mt-12 font-display text-2xl font-semibold text-ink-900">
          DC charging curve
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-ink-500">
          Charging power against state of charge, on a charger fast enough not to be the limit.
          Peak power is {Math.round(peak)} kW
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
            <h3 className="mt-8 font-display text-lg font-semibold text-ink-900">
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
            <h3 className="mt-8 font-display text-lg font-semibold text-ink-900">
              The curve, point by point{solo ? "" : `, ${vehicleShortName(x)}`}
            </h3>
            <Table
              caption={`Charging power at each state of charge for the ${vehicleName(x)}.`}
              head={["State of charge", "Power"]}
              rows={x.charge_curve.map(([soc, kw]) => [`${soc}%`, `${Math.round(kw)} kW`])}
            />
          </div>
        ))}

        {/* ---------------------------------------------------------------- */}
        <h2 className="mt-12 font-display text-2xl font-semibold text-ink-900">
          Consumption and range by cruise speed
        </h2>
        <p className="mt-3 text-sm leading-relaxed text-ink-500">
          Steady motorway cruising on flat road in mild weather, from the fitted model{" "}
          <span className="whitespace-nowrap font-mono text-ink-700">
            Wh/km = {v.consumption.a_wh_km} + {v.consumption.b_wh_km_per_kph2} · v²
          </span>
          {solo ? "" : ` for the ${vehicleShortName(v)}`}. The &ldquo;usable range&rdquo; column is
          the honest one for trip planning: it is 100% down to 10%, because nobody arrives on empty.
        </p>
        <Table
          caption={`Modelled motorway consumption and range for the ${vehicleName(v)} at each cruise speed.`}
          head={["Cruise speed", "Consumption", "Range (full pack)", "Usable range (100 to 10%)"]}
          rows={speeds.map((kph) => [
            `${kph} km/h`,
            `${Math.round(consumptionWhKm(v, kph))} Wh/km`,
            `${Math.round(rangeKm(v, kph))} km`,
            `${Math.round(rangeKm(v, kph, 90))} km`,
          ])}
        />

        {/* ---------------------------------------------------------------- */}
        <h2 className="mt-12 font-display text-2xl font-semibold text-ink-900">
          What the model does not include
        </h2>
        <ul className="mt-3 list-disc space-y-2 pl-5 text-sm leading-relaxed text-ink-500">
          <li>
            Elevation. The trip planner does account for climbs and descents; the range figures on
            this page are flat-road, so an alpine route will come out worse than the table.
          </li>
          <li>
            Temperature. These are mild-weather numbers. Cold raises consumption and cuts the
            charging curve. The planner models both. The numbers on this page do not.
          </li>
          <li>
            Wind, rain, roof boxes, payload and tyre choice, all of which move real consumption more
            than most people expect.
          </li>
          <li>
            Charger-side limits. The charging times above assume the site can deliver whatever the
            car asks for. A 150 kW cabinet shared with the car next to you will not.
          </li>
        </ul>

        {variants.some((x) => x.source_note) && (
          <>
            <h2 className="mt-12 font-display text-2xl font-semibold text-ink-900">
              Where these numbers come from
            </h2>
            {/* Deduped: variants on one platform share the opening paragraph,
                and printing it three times reads as padding rather than
                provenance. */}
            {[...new Set(variants.map((x) => x.source_note).filter(Boolean))].map((note) => (
              <p key={note} className="mt-3 text-sm leading-relaxed text-ink-500">
                {note}
              </p>
            ))}
          </>
        )}

        {/* ---------------------------------------------------------------- */}
        <div className="mt-12 rounded-2xl border border-brand-200 bg-brand-50 p-6">
          <h2 className="font-display text-xl font-semibold text-ink-900">
            What speed should you actually drive?
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-ink-600">
            It depends on your route, on where the fast chargers happen to be and how the legal
            limits fall. Plan the real trip and the simulator will sweep every cruise speed against
            this car&apos;s curves and tell you which one arrives first.
          </p>
          <Link
            href="/"
            className="mt-4 inline-block rounded-xl bg-brand-500 px-5 py-2.5 font-semibold text-white transition hover:bg-brand-600"
          >
            Plan a trip in the {nameplateShortName(variants)}
          </Link>
        </div>

        {related.length > 0 && (
          <section className="mt-12">
            <h2 className="font-display text-xl font-semibold text-ink-900">
              Other {v.make} models
            </h2>
            <ul className="mt-4 grid gap-3 sm:grid-cols-2">
              {related.map(([key, group]) => (
                <li key={key}>
                  <Link
                    href={`/ev/${key}`}
                    className="block rounded-xl border border-ink-100 bg-white p-3 text-sm shadow-sm transition hover:border-brand-300"
                  >
                    <span className="font-semibold text-ink-900">
                      {nameplateShortName(group)}
                    </span>
                    <span className="ml-2 text-ink-400">
                      {kwhRange(group)} kWh · {Math.round(Math.max(...group.map(peakKw)))} kW
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        )}

        <p className="mt-10 text-sm">
          <Link href="/ev" className="text-brand-600 underline underline-offset-2">
            ← All {all.length} cars in the catalog
          </Link>
        </p>
      </article>

      <SiteFooter />
    </main>
  )
}

/** The windows people actually plan around: the standard benchmark, the one the
 *  planner tends to pick, and the tail that shows why it does not go to 100. */
const CHARGE_WINDOWS: [number, number][] = [
  [10, 80],
  [20, 80],
  [10, 60],
  [20, 70],
  [80, 100],
]

function peakKw(v: Vehicle): number {
  return Math.max(...v.charge_curve.map(([, kw]) => kw))
}

/** "58-77" across a nameplate's variants, or "77" when they all share a pack. */
export function kwhRange(group: Vehicle[]): string {
  const packs = group.map((v) => v.usable_kwh)
  const lo = Math.min(...packs)
  const hi = Math.max(...packs)
  return lo === hi ? `${lo}` : `${lo}-${hi}`
}

function KeyNumbers({ vehicle }: { vehicle: Vehicle }) {
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

function Table({
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
