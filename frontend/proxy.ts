import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server"
import { NextResponse } from "next/server"

// Public routes: the landing page and the auth screens (Clerk renders sign-in/up
// + its recovery flows under /login and /register), plus the SEO plumbing.
// The SEO files must be listed: .txt/.xml/extension-less routes are NOT in the
// config matcher's static-asset skip list, so without an entry here Clerk
// auth-gates them and crawlers get a sign-in redirect instead of robots.txt.
const isPublicRoute = createRouteMatcher([
  "/",
  "/login(.*)",
  "/register(.*)",
  "/robots.txt",
  "/sitemap.xml",
  "/opengraph-image(.*)",
])

// Indexable routes: the public marketing surface plus SEO plumbing. Everything
// else — the authenticated product AND the auth screens — gets an
// X-Robots-Tag: noindex header. robots.txt alone doesn't stop Google indexing a
// bare URL it can't crawl; the header does, and for /login and /register
// (crawlable, since they're linked from the landing CTA) it's the only signal.
const isIndexableRoute = createRouteMatcher([
  "/",
  "/sitemap.xml",
  "/robots.txt",
  "/opengraph-image(.*)",
])

const isDev = process.env.NODE_ENV === "development"

// Single source of truth for the CSP. The locked connect-src is the real defense
// for the auth token; Clerk's widget needs its FAPI + Cloudflare Turnstile + img.clerk.com.
const connectExtra = isDev
  ? "http://localhost:* ws://localhost:*"
  : "https://*.azurewebsites.net https://*.evtrip.app https://api.evtrip.app"

const csp = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  // clerk.evtrip.app = the PRODUCTION Clerk Frontend API (serves clerk-js
  // + ui bundles). Without it the prod CSP blocks Clerk's script → <SignIn> never
  // renders and login hangs. *.clerk.accounts.dev covers the dev instance; both
  // are listed so one CSP works in every environment.
  "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://*.clerk.accounts.dev https://clerk.evtrip.app https://challenges.cloudflare.com",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "img-src 'self' data: blob: https://img.clerk.com https://*.clerk.accounts.dev https://clerk.evtrip.app",
  "font-src 'self' data: https://fonts.gstatic.com",
  `connect-src 'self' ${connectExtra} https://*.clerk.accounts.dev https://clerk-telemetry.com`,
  "worker-src 'self' blob:",
  "frame-src 'self' https://challenges.cloudflare.com",
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

export default clerkMiddleware(async (auth, req) => {
  const host = req.headers.get("host") || ""

  // Redirect www → non-www (prod canonicalization).
  if (host.startsWith("www.")) {
    const url = req.nextUrl.clone()
    url.host = host.replace("www.", "")
    url.protocol = "https"
    url.port = ""
    return NextResponse.redirect(url, 301)
  }

  // Gate everything that isn't explicitly public.
  if (!isPublicRoute(req)) {
    await auth.protect()
  }

  const res = withSecurityHeaders(NextResponse.next())
  if (!isIndexableRoute(req)) {
    res.headers.set("X-Robots-Tag", "noindex, nofollow")
  }
  return res
})

export const config = {
  matcher: [
    // Run on everything except Next internals + static assets…
    "/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)",
    // …and always on API/trpc routes (future-proof).
    "/(api|trpc)(.*)",
  ],
}
