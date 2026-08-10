/**
 * schema.org JSON-LD builders.
 *
 * Structured data is doing double duty here. Classic search engines use it for
 * rich results; retrieval-augmented assistants use it because it is the one
 * part of the page that states a fact without any layout around it. Every value
 * below is computed from the same catalog the planner runs on — nothing here is
 * hand-written marketing copy dressed up as data.
 */

import { FAQ } from "./faq"
import { SITE_NAME, SITE_URL, abs } from "./site"
import { averageKw, chargeMinutes, consumptionWhKm, rangeKm, vehicleName } from "./vehicles"
import type { Vehicle } from "./client"

const ORG_ID = `${SITE_URL}/#organization`
const SITE_ID = `${SITE_URL}/#website`

const DESCRIPTION =
  "Simulates an EV road trip at every cruise speed from 90 to 160 km/h, plans the optimal DC charging stops for each, and shows which speed gets you there earliest."

/** Publisher identity, referenced by `@id` from every other block so the graph
 *  has one organisation rather than a copy per page. */
export function organizationLd() {
  return {
    "@type": "Organization",
    "@id": ORG_ID,
    name: SITE_NAME,
    url: SITE_URL,
    description: DESCRIPTION,
    sameAs: ["https://github.com/gardan4/EV-holiday-driving-optimiser"],
  }
}

export function websiteLd() {
  return {
    "@type": "WebSite",
    "@id": SITE_ID,
    url: SITE_URL,
    name: SITE_NAME,
    description: DESCRIPTION,
    inLanguage: "en",
    publisher: { "@id": ORG_ID },
  }
}

/** What the thing *is*. `WebApplication` rather than `SoftwareApplication`:
 *  there is nothing to install, and the free-and-no-signup facts are the two
 *  an assistant most often needs before recommending a tool. */
export function webApplicationLd() {
  return {
    "@type": "WebApplication",
    "@id": `${SITE_URL}/#app`,
    name: SITE_NAME,
    url: SITE_URL,
    description: DESCRIPTION,
    applicationCategory: "TravelApplication",
    operatingSystem: "Any (web browser)",
    browserRequirements: "Requires JavaScript",
    isAccessibleForFree: true,
    offers: { "@type": "Offer", price: "0", priceCurrency: "EUR" },
    featureList: [
      "Total journey time simulated at every cruise speed from 90 to 160 km/h",
      "Optimal DC charging stop plan discovered per speed by dynamic programming",
      "Real fast-charger locations along the route from OpenChargeMap",
      "Per-car consumption model and measured DC charging curves",
      "Cold-weather consumption and charge-power derating",
      "Stop-by-stop itinerary with arrival and departure state of charge",
      "Animated 3D journey playback and race mode",
    ],
    publisher: { "@id": ORG_ID },
  }
}

export function faqLd() {
  return {
    "@type": "FAQPage",
    "@id": `${SITE_URL}/#faq`,
    mainEntity: FAQ.map((item) => ({
      "@type": "Question",
      name: item.q,
      acceptedAnswer: { "@type": "Answer", text: item.a },
    })),
  }
}

export function breadcrumbLd(trail: { name: string; path: string }[]) {
  return {
    "@type": "BreadcrumbList",
    itemListElement: trail.map((crumb, i) => ({
      "@type": "ListItem",
      position: i + 1,
      name: crumb.name,
      item: abs(crumb.path),
    })),
  }
}

/**
 * One variant, as `Vehicle` (a schema.org subtype of Product).
 *
 * The `additionalProperty` bag carries the numbers that make this page worth
 * citing at all — the fitted consumption coefficients, the modelled 10→80%
 * time, the average power that stop really delivers. Vocabulary has no terms
 * for those, and `PropertyValue` is how you say so without inventing any.
 *
 * `pageSlug` is the nameplate page the variant is published on, which is not
 * its own slug once several variants share a page. The `@id` still keys on the
 * variant, so three ID.4 nodes on one page stay three distinct things rather
 * than three conflicting descriptions of one.
 */
export function vehicleLd(v: Vehicle, pageSlug: string = v.slug) {
  const name = vehicleName(v)
  const peakKw = Math.max(...v.charge_curve.map(([, kw]) => kw))

  return {
    "@type": "Vehicle",
    "@id": abs(`/ev/${pageSlug}`) + `#${v.slug}`,
    name,
    url: abs(`/ev/${pageSlug}`),
    manufacturer: { "@type": "Organization", name: v.make },
    model: v.model,
    vehicleConfiguration: v.variant ?? undefined,
    fuelType: "Electric",
    vehicleEngine: {
      "@type": "EngineSpecification",
      engineType: "Electric motor",
      fuelType: "Electric",
    },
    weightTotal: {
      "@type": "QuantitativeValue",
      value: Math.round(v.mass_kg),
      unitCode: "KGM",
    },
    speed: {
      "@type": "QuantitativeValue",
      value: Math.round(v.top_speed_kph),
      unitCode: "KMH",
    },
    additionalProperty: [
      prop("Usable battery capacity", v.usable_kwh, "kWh"),
      prop("Peak DC charging power", peakKw, "kW"),
      prop("Average DC power 10-80%", Math.round(averageKw(v, 10, 80)), "kW"),
      prop("DC charge time 10-80%", Math.round(chargeMinutes(v, 10, 80)), "min"),
      prop("Motorway consumption at 100 km/h", Math.round(consumptionWhKm(v, 100)), "Wh/km"),
      prop("Motorway consumption at 130 km/h", Math.round(consumptionWhKm(v, 130)), "Wh/km"),
      prop("Modelled motorway range at 100 km/h", Math.round(rangeKm(v, 100)), "km"),
      prop("Modelled motorway range at 130 km/h", Math.round(rangeKm(v, 130)), "km"),
    ],
    isPartOf: { "@id": SITE_ID },
  }
}

function prop(name: string, value: number, unitText: string) {
  return { "@type": "PropertyValue", name, value, unitText }
}

/** Wrap blocks in a single `@graph` so one `<script>` carries the whole page. */
export function graph(...nodes: object[]) {
  return { "@context": "https://schema.org", "@graph": nodes }
}
