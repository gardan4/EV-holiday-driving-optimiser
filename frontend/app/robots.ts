import type { MetadataRoute } from "next"

// Fallback only. `NEXT_PUBLIC_SITE_URL` is set at build time by the deploy
// workflow; this is what you get if that ever goes missing again. It has to be
// a host we actually serve — the previous placeholder was a domain registered
// to somebody else, so every og:image and canonical URL pointed at a stranger.
const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip.dev"

// Trip permalinks carry unguessable ids and an X-Robots-Tag: noindex header
// (proxy.ts). /trip is deliberately NOT disallowed here — a robots.txt disallow
// would stop crawlers from ever seeing the noindex directive.
const PRODUCT_ROUTES = ["/api"]

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: PRODUCT_ROUTES }],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  }
}
