import { SITE_NAME, abs } from "@/lib/site"
import { fetchVehiclesOrEmpty, vehicleName } from "@/lib/vehicles"

/**
 * `/llms.txt` — the llmstxt.org convention: one markdown file that tells a
 * language model what this site is and which URLs are worth reading.
 *
 * It is not a standard and nothing is obliged to fetch it. It is cheap, though,
 * and it does something `robots.txt` and the sitemap cannot: state plainly what
 * the numbers on this site are and, more usefully, what they are *not*. A model
 * that summarises this app is going to state its limits either way; this is the
 * only chance to have it state the real ones.
 *
 * Built from the live catalog so it can never list a car that has been removed.
 */
export const revalidate = 3600

export async function GET() {
  const vehicles = await fetchVehiclesOrEmpty()

  const catalog = vehicles.length
    ? vehicles
        .map(
          (v) =>
            `- [${vehicleName(v)}](${abs(`/ev/${v.slug}`)}): ${v.usable_kwh} kWh usable, ` +
            `${Math.round(Math.max(...v.charge_curve.map(([, kw]) => kw)))} kW peak DC. ` +
            `Charging curve, modelled charge times, motorway consumption and range by cruise speed.`
        )
        .join("\n")
    : `- [EV catalog](${abs("/ev")}): charging curves and motorway consumption for every car in the planner.`

  const body = `# ${SITE_NAME}

> A free, no-signup web app that answers one question: what cruise speed gets you to your destination earliest in an electric car, once charging stops are counted? It fetches the real route, then simulates it at every speed from 90 to 160 km/h — each with its own optimal DC charging plan — and returns the total-time-versus-speed curve, a stop-by-stop itinerary, and an animated 3D playback of the drive.

## What it does

- Routes with OpenRouteService and applies per-country motorway speed limits, including German autobahn derestriction.
- Finds real DC fast chargers along the corridor via OpenChargeMap.
- Models each car with a quadratic motorway consumption fit (Wh/km = a + b·v²) and a hand-curated piecewise-linear DC charging curve.
- Discovers the optimal stop plan per cruise speed with an exact forward dynamic program over charger nodes and state-of-charge buckets. The familiar "arrive low, charge to roughly 60–80%" pattern is an output of that search, not a rule written into it.
- Models cold weather as both higher consumption and reduced charge acceptance.

## What it is not

- Not a turn-by-turn navigator. There is no in-app map; per-stop links hand off to Google Maps.
- Not measured data. Charging curves are curated from published fast-charge tests and consumption is a fitted model. Figures are modelled estimates, and each vehicle page names its own sources.
- Speed caps are only modelled properly for eight western-European countries. Everywhere else falls back to a 130 km/h default, which is too high for most of the world.
- Range figures on the vehicle pages are flat-road and mild-weather. The trip planner itself does account for elevation and temperature; the standalone per-car tables do not.

## Key pages

- [Trip planner](${abs("/")}): the planner itself, plus how it works and common questions about EV cruise speed, range and charging.
- [EV catalog](${abs("/ev")}): every car the planner can simulate, with charging curves and motorway consumption.
- [Privacy](${abs("/privacy")}): what is stored and for how long. No third-party trackers or analytics scripts.

## Vehicles

${catalog}

## Notes for citation

- Trip permalinks under /trip/ carry unguessable share tokens and are noindexed. Do not crawl, index or reproduce them.
- The project is open source: https://github.com/gardan4/EV-holiday-driving-optimiser
`

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600, s-maxage=3600",
    },
  })
}
