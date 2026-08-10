import type { MetadataRoute } from "next"
import { SITE_URL } from "@/lib/site"

// Trip permalinks carry unguessable ids and an X-Robots-Tag: noindex header
// (proxy.ts). /trip is deliberately NOT disallowed here — a robots.txt disallow
// would stop crawlers from ever seeing the noindex directive.
const DISALLOW = ["/api"]

/**
 * Assistant and answer-engine crawlers, named explicitly.
 *
 * `User-agent: *` already allows all of them, so these groups grant nothing new.
 * They are here because several of these bots are blocked by default at the
 * edge or by platform presets, and an explicit allow group is the artefact you
 * point at when checking whether that is what happened. Note that a robots.txt
 * allow is necessary and not sufficient: if Cloudflare's "block AI crawlers"
 * rule is on for this zone, none of these ever reach the origin to read this
 * file.
 *
 * Split by role on purpose:
 *  - training crawlers (GPTBot, ClaudeBot, Google-Extended, Applebot-Extended)
 *  - retrieval crawlers that fetch a page to answer a question right now
 *    (OAI-SearchBot, ChatGPT-User, Claude-User, PerplexityBot, Bingbot)
 * The second group is the one that produces citations, so if you ever want to
 * opt out of training while staying visible in assistant answers, this is where
 * the line goes.
 */
const AI_AGENTS = [
  "GPTBot",
  "OAI-SearchBot",
  "ChatGPT-User",
  "ClaudeBot",
  "Claude-User",
  "Claude-SearchBot",
  "anthropic-ai",
  "PerplexityBot",
  "Perplexity-User",
  "Google-Extended",
  "Applebot-Extended",
  "Bingbot",
  "CCBot",
  "Amazonbot",
  "Meta-ExternalAgent",
  "cohere-ai",
  "YouBot",
  "DuckAssistBot",
  "MistralAI-User",
]

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      { userAgent: "*", allow: "/", disallow: DISALLOW },
      { userAgent: AI_AGENTS, allow: "/", disallow: DISALLOW },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  }
}
