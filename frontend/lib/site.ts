/**
 * Canonical site identity — one copy, imported by everything that needs to emit
 * an absolute URL (metadata, robots, sitemap, JSON-LD).
 *
 * `NEXT_PUBLIC_SITE_URL` is set at build time by the deploy workflow; the
 * fallback is what you get if that ever goes missing again. It has to be a host
 * we actually serve — the original placeholder was a domain registered to
 * somebody else, so every og:image and canonical URL pointed at a stranger.
 */
export const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://evtrip.dev"

export const SITE_NAME = "EV Trip Optimizer"

/** Absolute URL for `path` (which must start with "/"). */
export function abs(path: string): string {
  return new URL(path, SITE_URL).toString()
}
