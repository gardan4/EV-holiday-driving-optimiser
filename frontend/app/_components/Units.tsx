"use client"

/**
 * Units as elements, for surfaces that are rendered on the server.
 *
 * `useUnits` needs a client component, but the trip card and the drive review
 * are deliberately server-rendered — the card says so in its own docstring, and
 * the review page is a route. Wrapping either one in `"use client"` to reach a
 * formatter would move a whole page into the client bundle for the sake of two
 * numbers.
 *
 * So the leaf is the client component instead: a server page renders
 * `<Distance meters={…} />` and only that span re-renders when the reader
 * switches units. Outside the provider these fall back to metric like every
 * other units call site, which is what keeps them safe in a static render.
 */

import { useUnits } from "@/lib/useUnits"

/** A speed in km/h, shown in the reader's units: "130 km/h" / "81 mph". */
export function Speed({ kph }: { kph: number }) {
  const { speed } = useUnits()
  return <>{speed(kph)}</>
}

/** A distance in metres: "912 km" / "567 mi". */
export function Distance({ meters }: { meters: number }) {
  const { dist } = useUnits()
  return <>{dist(meters)}</>
}

/** A distance already expressed in kilometres. */
export function DistanceKm({ km }: { km: number }) {
  const { rangeKm } = useUnits()
  return <>{rangeKm(km)}</>
}
