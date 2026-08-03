import type { MetadataRoute } from "next"

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip.app"

/** Public, indexable routes only. Add your marketing pages here as you build them.
 *  Keep in step with `INDEXABLE` in `proxy.ts` — a page listed here but noindexed
 *  there just tells crawlers to fetch something they are then told to discard. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE_URL}/`, changeFrequency: "weekly", priority: 1.0 },
    { url: `${SITE_URL}/privacy`, changeFrequency: "yearly", priority: 0.3 },
  ]
}
