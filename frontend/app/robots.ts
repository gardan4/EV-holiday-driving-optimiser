import type { MetadataRoute } from "next"

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip.app"

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
