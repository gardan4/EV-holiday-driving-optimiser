import type { Metadata } from "next"
import Link from "next/link"
import AppHeader from "@/app/_components/AppHeader"
import JsonLd from "@/app/_components/JsonLd"
import SiteFooter from "@/app/_components/SiteFooter"
import { breadcrumbLd, graph, organizationLd } from "@/lib/seo"
import { abs } from "@/lib/site"
import {
  chargeMinutes,
  consumptionWhKm,
  fetchVehicles,
  groupByNameplate,
  nameplateName,
  nameplateShortName,
  rangeKm,
  vehicleShortName,
} from "@/lib/vehicles"
import type { Vehicle } from "@/lib/client"

/** One nameplate: its page slug and the variants published on it. */
type Nameplate = [string, Vehicle[]]

/** "58-77" when a nameplate's versions differ, "77" when they agree. */
function range(group: Vehicle[], of: (v: Vehicle) => number): string {
  const values = group.map(of)
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  return lo === hi ? `${lo}` : `${lo}-${hi}`
}

// The catalog only changes on deploy, so render once an hour and serve the
// cached HTML to everything else — including crawlers, which is most of the
// traffic a page like this gets.
export const revalidate = 3600

const TITLE = "EV charging curves and motorway consumption"
const DESCRIPTION =
  "Usable battery, peak DC power, modelled 10-80% charge time and motorway consumption at 100 and 130 km/h for every electric car in the EV Trip Optimizer catalog."

export const metadata: Metadata = {
  title: TITLE,
  description: DESCRIPTION,
  alternates: { canonical: "/ev" },
  openGraph: {
    type: "website",
    url: abs("/ev"),
    title: `${TITLE} · EV Trip Optimizer`,
    description: DESCRIPTION,
  },
}

export default async function VehicleIndexPage() {
  const vehicles = await fetchVehicles()
  const nameplates = [...groupByNameplate(vehicles).entries()]
  const byMake = groupByMake(nameplates)

  return (
    <main className="min-h-screen">
      <AppHeader />
      <JsonLd
        data={graph(
          organizationLd(),
          breadcrumbLd([
            { name: "Home", path: "/" },
            { name: "Electric cars", path: "/ev" },
          ]),
          {
            "@type": "CollectionPage",
            name: TITLE,
            description: DESCRIPTION,
            url: abs("/ev"),
            mainEntity: {
              "@type": "ItemList",
              numberOfItems: nameplates.length,
              itemListElement: nameplates.map(([slug, group], i) => ({
                "@type": "ListItem",
                position: i + 1,
                url: abs(`/ev/${slug}`),
                name: nameplateName(group),
              })),
            },
          }
        )}
      />

      <div className="mx-auto max-w-5xl px-4 pb-16 pt-8 sm:px-6">
        <nav aria-label="Breadcrumb" className="text-sm text-ink-400">
          <Link href="/" className="hover:text-ink-700">
            Home
          </Link>
          <span className="mx-2 text-ink-300">/</span>
          <span className="text-ink-700">Electric cars</span>
        </nav>

        <h1 className="mt-4 font-display text-3xl font-bold tracking-tight text-ink-900 sm:text-4xl">
          Charging curves and motorway consumption
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-ink-500">
          These are the {vehicles.length} cars the trip planner can simulate, across{" "}
          {nameplates.length} models. Each version carries a hand-curated DC charging curve and a
          consumption model fitted to motorway speeds, and each page shows what those mean in
          practice: how long a 10-80% stop really takes, what the car uses per kilometre at 100
          versus 130 km/h, and how far a full pack goes at each cruise speed.
        </p>
        <p className="mt-3 max-w-2xl text-sm leading-relaxed text-ink-400">
          The figures are modelled, not measured by us. Charging curves are curated from published
          fast-charge tests and consumption coefficients are fitted to steady motorway cruising.
          Every page names its own sources.
        </p>

        {byMake.map(([make, cars]) => (
          <section key={make} className="mt-10">
            <h2 className="font-display text-xl font-semibold text-ink-900">{make}</h2>
            <ul className="mt-4 grid gap-3 sm:grid-cols-2">
              {cars.map(([slug, group]) => {
                // Ranges where the versions differ, a single figure where they
                // agree — a card that says "58-77 kWh" is telling the reader
                // something a card that silently picks one version is not.
                const best = group.reduce((a, b) => (a.usable_kwh >= b.usable_kwh ? a : b))
                return (
                  <li key={slug}>
                    <Link
                      href={`/ev/${slug}`}
                      className="block rounded-2xl border border-ink-100 bg-white p-4 shadow-sm transition hover:border-brand-300 hover:shadow-md"
                    >
                      <span className="font-display font-semibold text-ink-900">
                        {nameplateShortName(group)}
                      </span>
                      {group.length > 1 && (
                        <span className="ml-2 text-xs text-ink-400">
                          {group.length} versions
                        </span>
                      )}
                      <dl className="mt-2 grid grid-cols-3 gap-2 text-xs text-ink-500">
                        <Stat label="Usable" value={`${range(group, (v) => v.usable_kwh)} kWh`} />
                        <Stat
                          label="10-80%"
                          value={`${range(group, (v) => Math.round(chargeMinutes(v, 10, 80)))} min`}
                        />
                        <Stat
                          label="At 130"
                          value={`${range(group, (v) => Math.round(consumptionWhKm(v, 130)))} Wh/km`}
                        />
                      </dl>
                      <p className="mt-2 text-xs text-ink-400">
                        About {Math.round(rangeKm(best, 120))} km at a steady 120 km/h on a full
                        pack{group.length > 1 ? `, in the ${vehicleShortName(best)}` : ""}.
                      </p>
                    </Link>
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>

      <SiteFooter />
    </main>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-ink-400">{label}</dt>
      <dd className="font-mono text-ink-700">{value}</dd>
    </div>
  )
}

/** Group by make, keeping the catalog's own ordering within each make (the API
 *  sorts by `sort_order`, which is the curated "show the Born first" order). */
function groupByMake(nameplates: Nameplate[]): [string, Nameplate[]][] {
  const groups = new Map<string, Nameplate[]>()
  for (const nameplate of nameplates) {
    const make = nameplate[1][0].make
    const list = groups.get(make)
    if (list) list.push(nameplate)
    else groups.set(make, [nameplate])
  }
  return [...groups.entries()]
}
