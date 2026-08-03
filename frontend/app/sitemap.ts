import type { MetadataRoute } from "next"

// Fallback only. `NEXT_PUBLIC_SITE_URL` is set at build time by the deploy
// workflow; this is what you get if that ever goes missing again. It has to be
// a host we actually serve — the previous placeholder was a domain registered
// to somebody else, so every og:image and canonical URL pointed at a stranger.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip.dev"

/** Public, indexable routes only. Add your marketing pages here as you build them.
 *  Keep in step with `INDEXABLE` in `proxy.ts` — a page listed here but noindexed
 *  there just tells crawlers to fetch something they are then told to discard. */
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    { url: `${SITE_URL}/`, changeFrequency: "weekly", priority: 1.0 },
    { url: `${SITE_URL}/privacy`, changeFrequency: "yearly", priority: 0.3 },
  ]
}
