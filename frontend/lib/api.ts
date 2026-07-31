/**
 * API configuration + authenticated fetch for the frontend.
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

    // Production: go through the custom domain (api.evtrip.app) so any
    // WAF / rate-limit / access restrictions in front of the App Service are
    // enforced. Hitting the *.azurewebsites.net host directly bypasses them.
    if (
      hostname === "evtrip.app" ||
      hostname === "www.evtrip.app" ||
      hostname === "evtrip-web-prod.azurewebsites.net"
    ) {
      return "https://api.evtrip.app"
    }
    // Dev / staging Azure Web Apps domains — no custom domain in front.
    if (hostname.endsWith(".azurewebsites.net")) {
      const apiHostname = hostname.replace("evtrip-web", "evtrip-api")
      return `https://${apiHostname}`
    }
  }

  return "http://localhost:8000"
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
    const msgs = detail
      .map((d) =>
        d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : null
      )
      .filter(Boolean) as string[]
    if (msgs.length) return msgs.join("; ")
  }
  if (detail && typeof detail === "object" && "msg" in detail) {
    return String((detail as { msg: unknown }).msg)
  }
  return fallback
}

// Authentication is owned by Clerk. ClerkProvider exposes `window.Clerk` once
// loaded; we read the session token from it for our (cross-origin) FastAPI API.
type ClerkGlobal = {
  session?: { getToken: () => Promise<string | null> }
  user?: unknown
  signOut?: (opts?: { redirectUrl?: string }) => Promise<void>
}
function clerk(): ClerkGlobal | undefined {
  if (typeof window === "undefined") return undefined
  return (window as unknown as { Clerk?: ClerkGlobal }).Clerk
}

/**
 * Resolve the bearer token for an API call: the live (short-lived,
 * auto-refreshed) Clerk session JWT — minted with the email/name/email_verified
 * claims the backend reads. Central choke point for fetchWithAuth + lib/client.
 */
export async function getAuthToken(): Promise<string | null> {
  if (typeof window === "undefined") return null
  try {
    return (await clerk()?.session?.getToken()) ?? null
  } catch {
    return null
  }
}

/** Make an authenticated API request against the FastAPI backend. */
export async function fetchWithAuth(
  endpoint: string,
  options: RequestInit = {}
): Promise<Response> {
  const token = await getAuthToken()

  // FormData bodies must NOT get a JSON Content-Type — the browser sets the
  // multipart boundary itself.
  const headers: HeadersInit = {
    ...(options.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
    ...options.headers,
  }
  if (token) {
    (headers as Record<string, string>)["Authorization"] = `Bearer ${token}`
  }

  return fetch(`${API_URL}${endpoint}`, { ...options, headers })
}

/**
 * Best-effort "is there a session?" check. Route protection is enforced
 * server-side by clerkMiddleware; this is only a client-side UI hint. Components
 * that need reactive state should use Clerk's `useAuth()` instead.
 */
export function isAuthenticated(): boolean {
  if (typeof window === "undefined") return false
  return !!clerk()?.user
}

/** Log out — clears the Clerk session (and cookie) and lands on /login. */
export function logout(): void {
  clerk()
    ?.signOut?.({ redirectUrl: "/login" })
    .catch(() => {})
}
