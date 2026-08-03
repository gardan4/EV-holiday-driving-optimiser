import { NextRequest, NextResponse } from "next/server"

// Indexable routes: the public planner surface plus SEO plumbing. Trip
// permalinks (/trip/*) carry unguessable ids — they are shareable but
// deliberately noindexed so shared links never end up in search results.
//
// /privacy has to be in here. It was noindexed by omission, and since nothing
// linked to it either, the only way to reach the notice was to guess the URL —
// which is not "providing" it to anyone.
const INDEXABLE = [
  /^\/$/,
  /^\/privacy$/,
  /^\/robots\.txt$/,
  /^\/sitemap\.xml$/,
  /^\/opengraph-image/,
]

const isDev = process.env.NODE_ENV === "development"

// Single source of truth for the CSP. Fully self-hosted: the 3D scene is
// procedural (no tile servers, no CDNs) and all API traffic goes to our own
// backend, so connect-src stays locked to our origins.
//
// `*.azurewebsites.net` used to be in here, which is a hostname anyone can
// register for free — i.e. a ready-made exfiltration target for any XSS that
// ever lands. Name the one host we actually call instead. Add a custom domain
// here when there is one; the placeholder that used to sit here belonged to
// someone else.
const connectExtra = isDev
  ? "http://localhost:* ws://localhost:*"
  : "https://api.evtrip.dev https://evtrip-api-dev.azurewebsites.net"

const csp = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  // 'unsafe-inline' is load-bearing: the App Router emits inline bootstrap and
  // RSC flight scripts. 'unsafe-eval' is not — the production three.js/R3F
  // bundle contains no eval() or new Function(), so it is gone.
  "script-src 'self' 'unsafe-inline'",
  // next/font/google self-hosts the woff2 files at build time, so the Google
  // origins that used to be allowed here were never actually requested.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
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
  // Geolocation is allowed for this origin only — /trip/[id]/live follows the
  // drive by snapping GPS fixes to the route. The browser still asks the user;
  // this header only stops the feature being blocked before they're asked.
  // Camera and microphone stay off entirely.
  res.headers.set("Permissions-Policy", "camera=(), microphone=(), geolocation=(self)")
  res.headers.set("Content-Security-Policy", csp)
  // The API tier sets this; the site people actually load did not. httpsOnly on
  // the App Service redirects http→https, but the redirected first request has
  // already crossed the network in the clear.
  if (!isDev) {
    res.headers.set(
      "Strict-Transport-Security",
      "max-age=31536000; includeSubDomains"
    )
  }
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
