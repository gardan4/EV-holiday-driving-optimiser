import type { Metadata } from "next"
import Link from "next/link"
import { notFound, permanentRedirect } from "next/navigation"
import AppHeader from "@/app/_components/AppHeader"
import {
  ChargingSection,
  ConsumptionSection,
  KeyNumbers,
  ModelCaveats,
  PlanCta,
  Provenance,
  RelatedModels,
  VariantComparison,
} from "@/app/_components/ev/sections"
import JsonLd from "@/app/_components/JsonLd"
import SiteFooter from "@/app/_components/SiteFooter"
import { breadcrumbLd, graph, organizationLd, vehicleLd } from "@/lib/seo"
import { abs } from "@/lib/site"
import {
  chargeMinutes,
  consumptionWhKm,
  cruiseSpeedsFor,
  fetchVehicles,
  groupByNameplate,
  headlineVariant,
  nameplateKey,
  nameplateName,
  nameplateShortName,
  peakKw,
  taperSoc,
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
  const c100 = consumptionWhKm(v, 100)
  const c130 = consumptionWhKm(v, 130)
  const stopMinutes = Math.round(chargeMinutes(v, 10, 80))
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
          100 km/h and {Math.round(c130)} Wh/km at 130, so {Math.round(((c130 - c100) / c100) * 100)}% more energy per kilometre buys
          you 30 km/h more speed. Weigh that against {article(stopMinutes)} {stopMinutes}-minute
          10-80% charging stop and you have the whole question of how fast to drive.
        </p>

        <KeyNumbers vehicle={v} />

        {!solo && <VariantComparison variants={variants} name={name} />}

        <ChargingSection
          variants={variants}
          solo={solo}
          peak={Math.max(...variants.map(peakKw))}
          taper={taperSoc(v, 0.5)}
        />

        <ConsumptionSection vehicle={v} speeds={cruiseSpeedsFor(v)} solo={solo} />

        <ModelCaveats />

        <Provenance variants={variants} />

        <PlanCta variants={variants} />

        <RelatedModels make={v.make} related={related} />

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

/** "a" or "an" for a number that is about to be read aloud.
 *
 *  Every charge time on this page is computed, so the sentence around it has
 *  to be too — the fastest cars land on 18 minutes and were reading "a
 *  18-minute stop". Vowel *sound*, not vowel letter: eight, eleven, eighteen
 *  and the eighties take "an", and nothing else in this range does. */
function article(n: number): string {
  const s = String(n)
  return n === 8 || n === 11 || n === 18 || s.startsWith("8") ? "an" : "a"
}
