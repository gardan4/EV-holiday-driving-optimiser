/**
 * API configuration + fetch helper for the frontend. The app is fully public —
 * there is no auth and no tokens.
 *
 * Base URL priority:
 * 1. NEXT_PUBLIC_API_URL environment variable (set at build time)
 * 2. Runtime detection based on window.location for production domains
 * 3. Localhost fallback for local development
 */
function getApiUrl(): string {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL
  }

  if (typeof window !== "undefined") {
    const hostname = window.location.hostname

    // Derive the API host from the web host. This branch is a safety net —
    // NEXT_PUBLIC_API_URL is set at build time by the deploy workflow — and it
    // used to hardcode a custom domain that was never registered to us, which
    // would have sent every API call to a stranger's server had it ever run.
    if (hostname === "evtrip.dev" || hostname === "www.evtrip.dev") {
      return "https://api.evtrip.dev"
    }
    if (hostname.endsWith(".azurewebsites.net")) {
      const apiHostname = hostname.replace("evtrip-web", "evtrip-api")
      return `https://${apiHostname}`
    }
  }

  // Local backend runs on 8100 (8000 commonly taken by other local projects).
  return "http://localhost:8100"
}

export const API_URL = getApiUrl()

/**
 * Normalize a FastAPI error `detail` into a displayable string.
 *
 * App errors use `raise HTTPException(detail="...")` → a string. FastAPI's
 * automatic request-validation failures (422) return `detail` as an array of
 * `{type, loc, msg, ...}` objects; rendering that array in JSX throws, so funnel
 * `data.detail` through this before putting it in state.
 */
export function getErrorMessage(detail: unknown, fallback = "Something went wrong"): string {
  if (typeof detail === "string") return detail
  if (Array.isArray(detail)) {
    const msgs = [...new Set(detail.map(msgOf).filter(Boolean) as string[])]
    if (msgs.length) return msgs.join("; ")
  }
  const single = msgOf(detail)
  if (single) return single
  return fallback
}

/** Pydantic prefixes a custom validator's message with "Value error, " — the
 *  message behind it is the one written for a human, so show only that. */
function msgOf(d: unknown): string | null {
  if (!d || typeof d !== "object" || !("msg" in d)) return null
  return String((d as { msg: unknown }).msg).replace(/^Value error,\s*/, "")
}

/** Make an API request against the FastAPI backend. */
export async function apiFetch(endpoint: string, options: RequestInit = {}): Promise<Response> {
  // FormData bodies must NOT get a JSON Content-Type — the browser sets the
  // multipart boundary itself.
  const headers: HeadersInit = {
    ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...options.headers,
  }
  return fetch(`${API_URL}${endpoint}`, { ...options, headers })
}
