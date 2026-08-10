import type { MetadataRoute } from "next"
import { SITE_URL } from "@/lib/site"
import { fetchVehiclesOrEmpty, groupByNameplate } from "@/lib/vehicles"

/** Rebuilt hourly alongside the catalog pages it lists. */
export const revalidate = 3600

/** Public, indexable routes only. Keep in step with `INDEXABLE` in `proxy.ts` —
 *  a page listed here but noindexed there just tells crawlers to fetch
 *  something they are then told to discard.
 *
 *  Vehicle pages come from the live catalog rather than a hand-kept list, so
 *  adding a car to `seed_vehicles.py` is all it takes to get it indexed. When
 *  the API is unreachable this degrades to the static routes: a short sitemap
 *  costs a crawl cycle, a 500 costs the whole file.
 *
 *  One URL per nameplate, not per variant: a second ID.4 pack size is a row on
 *  a page that already exists, and listing the variant slug would advertise a
 *  URL that 308s straight back here. */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const vehicles = await fetchVehiclesOrEmpty()
  const nameplates = [...groupByNameplate(vehicles).keys()]

  return [
    { url: `${SITE_URL}/`, changeFrequency: "weekly", priority: 1.0 },
    { url: `${SITE_URL}/ev`, changeFrequency: "monthly", priority: 0.8 },
    ...nameplates.map((slug) => ({
      url: `${SITE_URL}/ev/${slug}`,
      changeFrequency: "monthly" as const,
      priority: 0.6,
    })),
    { url: `${SITE_URL}/privacy`, changeFrequency: "yearly", priority: 0.3 },
  ]
}
