import type { MetadataRoute } from "next"

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip.app"

// The authenticated product and API sit behind login — nothing useful (or safe)
// for a crawler to index. /login and /register are deliberately NOT listed: they
// carry an X-Robots-Tag: noindex header (proxy.ts), and a robots.txt disallow
// would stop crawlers from ever seeing that directive.
const PRODUCT_ROUTES = ["/dashboard", "/businesses", "/api"]

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [{ userAgent: "*", allow: "/", disallow: PRODUCT_ROUTES }],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  }
}
