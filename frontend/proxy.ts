import { NextRequest, NextResponse } from "next/server"

// Indexable routes: the public planner surface plus SEO plumbing. Trip
// permalinks (/trip/*) carry unguessable ids — they are shareable but
// deliberately noindexed so shared links never end up in search results.
const INDEXABLE = [/^\/$/, /^\/robots\.txt$/, /^\/sitemap\.xml$/, /^\/opengraph-image/]

const isDev = process.env.NODE_ENV === "development"

// Single source of truth for the CSP. Fully self-hosted: the 3D scene is
// procedural (no tile servers, no CDNs) and all API traffic goes to our own
// backend, so connect-src stays locked to our origins.
const connectExtra = isDev
  ? "http://localhost:* ws://localhost:*"
  : "https://*.azurewebsites.net https://*.evtrip.app https://api.evtrip.app"

const csp = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "img-src 'self' data: blob:",
  "font-src 'self' data: https://fonts.gstatic.com",
  `connect-src 'self' ${connectExtra}`,
  // three.js / R3F may spin up blob workers (e.g. for texture decoding).
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "form-action 'self'",
].join("; ")

function withSecurityHeaders(res: NextResponse): NextResponse {
  res.headers.set("X-Frame-Options", "DENY")
  res.headers.set("X-Content-Type-Options", "nosniff")
  res.headers.set("Referrer-Policy", "strict-origin-when-cross-origin")
  res.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
  res.headers.set("Content-Security-Policy", csp)
  return res
}

export default function proxy(req: NextRequest) {
  const host = req.headers.get("host") || ""

  // Redirect www → non-www (prod canonicalization).
  if (host.startsWith("www.")) {
    const url = req.nextUrl.clone()
    url.host = host.replace("www.", "")
    url.protocol = "https"
    url.port = ""
    return NextResponse.redirect(url, 301)
  }

  const res = withSecurityHeaders(NextResponse.next())
  const path = req.nextUrl.pathname
  if (!INDEXABLE.some((re) => re.test(path))) {
    res.headers.set("X-Robots-Tag", "noindex, nofollow")
  }
  return res
}

export const config = {
  matcher: [
    // Run on everything except Next internals + static assets…
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // …and always on API/trpc routes (future-proof).
    "/(api|trpc)(.*)",
  ],
}
