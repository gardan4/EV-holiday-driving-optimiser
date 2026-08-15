/**
 * Handing a planned journey — stops and all — to Google Maps.
 *
 * There is no in-app map by design, and the itinerary has always linked each
 * charging stop out to Maps one at a time. That is the right thing on the sofa
 * and useless in the car: nobody wants to re-enter the next charger at every
 * stop, and doing it while driving is exactly the moment a plan gets abandoned.
 * So this builds the whole thing as ONE Maps route — origin, every stop in
 * order, destination — as a plain link out. Still no map here, still no
 * external origin in the CSP, because a link is not a request.
 *
 * Two things about Google's cross-platform Maps URL shape the code:
 *
 * - **Nine waypoints, hard.** A plan with more stops than that cannot be one
 *   route, so it is split into consecutive legs that each end where the next
 *   begins. That is not a workaround to hide — a 24-stop US interstate run
 *   genuinely is three navigations — so the legs are named and counted rather
 *   than silently truncated to the first nine, which would send somebody down
 *   a road that stops short of where they are going.
 * - **Omitting `origin` means "from where you are".** That is what the live
 *   drive wants: a phone that has been moving for four hours should start the
 *   navigation from the car, not from a GPS fix taken a minute ago or from the
 *   town it set off from this morning.
 *
 * Stops go in as coordinates, not names. A charger's name in this catalog is
 * whatever OpenChargeMap calls the site, which is frequently a hotel or a
 * supermarket and occasionally ambiguous across a country; the coordinate is
 * the thing the plan was actually computed against.
 */

import { PlacePoint, Stop, Trip } from "./client"

/** A point on the route as Maps needs it: a coordinate, and something to call
 *  it in the UI (never sent — see the module note on names). */
export interface MapsPoint {
  lat: number
  lon: number
  label: string
}

/** Google's cross-platform Maps URL accepts at most nine intermediate points. */
export const MAX_WAYPOINTS = 9

/** What we call the start of a route whose origin is left to the device. */
export const CURRENT_LOCATION = "Where you are"

export interface MapsLeg {
  url: string
  /** 1-based. "Part 2" only means anything alongside `total`. */
  index: number
  total: number
  from: string
  to: string
  /** Charging stops passed through *inside* this leg — its endpoints excluded,
   *  since those are named by `from`/`to`. */
  waypoints: number
}

/** ~1 m, which is finer than a charger's own coordinate and keeps the URL short. */
function coord(p: MapsPoint): string {
  return `${round5(p.lat)},${round5(p.lon)}`
}

function round5(n: number): number {
  return Number(n.toFixed(5))
}

/**
 * One Maps directions URL.
 *
 * Built by hand rather than with `URLSearchParams`, which percent-encodes the
 * comma inside every coordinate and the pipe between waypoints. Google accepts
 * the encoded forms, but its own documented shape is a bare `lat,lon` and a
 * `%7C` separator, and this is a link we hand to another product — matching
 * what they document is worth more than the tidier constructor.
 */
function dirUrl(origin: MapsPoint | null, dest: MapsPoint, via: MapsPoint[]): string {
  const parts = ["api=1", `destination=${coord(dest)}`, "travelmode=driving"]
  if (origin) parts.splice(1, 0, `origin=${coord(origin)}`)
  if (via.length) parts.push(`waypoints=${via.map(coord).join("%7C")}`)
  return `https://www.google.com/maps/dir/?${parts.join("&")}`
}

/**
 * The journey as one or more Maps routes.
 *
 * `origin: null` leaves the starting point to the device. Legs share their
 * endpoints: leg 2 begins at the stop leg 1 ended on, so following them in
 * order covers the whole route with nothing skipped between them.
 */
export function mapsRouteLegs({
  origin,
  stops,
  dest,
}: {
  origin: MapsPoint | null
  stops: MapsPoint[]
  dest: MapsPoint
}): MapsLeg[] {
  // `null` appears at index 0 only, and only when the caller wants the device's
  // own location — every later leg has a real point to start from.
  const chain: (MapsPoint | null)[] = [origin, ...stops, dest]
  const legs: Omit<MapsLeg, "total">[] = []

  let i = 0
  while (i < chain.length - 1) {
    const end = Math.min(i + MAX_WAYPOINTS + 1, chain.length - 1)
    const from = chain[i]
    const to = chain[end] as MapsPoint
    const via = chain.slice(i + 1, end) as MapsPoint[]
    legs.push({
      url: dirUrl(from, to, via),
      index: legs.length + 1,
      from: from ? from.label : CURRENT_LOCATION,
      to: to.label,
      waypoints: via.length,
    })
    i = end
  }

  return legs.map((leg) => ({ ...leg, total: legs.length }))
}

/** "Born, Limburg, Netherlands" → "Born". The itinerary shortens labels the
 *  same way; a leg name that wraps twice is not a leg name. */
function place(p: PlacePoint): MapsPoint {
  return { lat: p.lat, lon: p.lon, label: p.label.split(",")[0] }
}

function stopPoint(s: Stop): MapsPoint {
  return { lat: s.lat, lon: s.lon, label: s.name }
}

/** The whole plan: origin → every charging stop → destination. */
export function plannedRouteLegs(trip: Trip, stops: Stop[]): MapsLeg[] {
  return mapsRouteLegs({
    origin: place(trip.request.origin),
    stops: stops.map(stopPoint),
    dest: place(trip.request.dest),
  })
}

/** The rest of the way, from wherever the phone is now. `stopsAhead` is the
 *  stops still in front of the car on whichever plan is in force — after a
 *  re-plan that is the revised list, not the one printed this morning. */
export function remainingRouteLegs(trip: Trip, stopsAhead: Stop[]): MapsLeg[] {
  return mapsRouteLegs({
    origin: null,
    stops: stopsAhead.map(stopPoint),
    dest: place(trip.request.dest),
  })
}
