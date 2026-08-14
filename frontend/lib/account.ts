/**
 * The owner secret, and the username it holds.
 *
 * This is the browser half of "an account without identity". There is no
 * password and nothing to sign in to: the browser makes up a v4 UUID, keeps it
 * here, and sends it as `X-Owner-Secret` when it writes. The server stores only
 * a hash, so what is in this browser is the only usable copy — which is the
 * whole security model and also the whole recovery story.
 *
 * Three rules, and each of them is doing a job:
 *
 *  - **Nothing is minted on page load.** `ownerSecret()` only creates a secret
 *    when `create` is passed, which happens at exactly two moments: claiming a
 *    username, and pasting a code from another device. Someone who never opts
 *    in never has one, so a browser that only reads the site stores nothing.
 *  - **It never throws.** Storage is unavailable in some private windows, and a
 *    trip planner must not break because a list of trips could not be
 *    remembered. Every read returns a safe default.
 *  - **It is independent of the counting toggle.** `evtrip.cid` exists to count
 *    visits and is deleted when somebody opts out of counting; this secret is a
 *    key to a thing the user asked for, and deleting it as a side effect of an
 *    analytics preference would silently take away their username. The privacy
 *    page says which control ends which.
 */

import { UUID_V4_RE, uuidV4 } from "./uuid"

const OWNER_KEY = "evtrip.owner"
const USERNAME_KEY = "evtrip.owner.name"
const COUNT_KEY = "evtrip.trips.n"

/**
 * The secret for this browser.
 *
 * `create` is deliberately explicit rather than defaulted: reading it (to send
 * a header, to decide what the drawer shows) must never have the side effect of
 * bringing an identity into existence for somebody who has not asked for one.
 */
export function ownerSecret(create = false): string | null {
  if (typeof window === "undefined") return null
  try {
    const existing = window.localStorage.getItem(OWNER_KEY)
    if (existing && UUID_V4_RE.test(existing)) return existing
    if (!create) return null
    const fresh = uuidV4()
    window.localStorage.setItem(OWNER_KEY, fresh)
    return fresh
  } catch {
    return null
  }
}

/** The header that authorises writing under a username, when there is one.
 *  Absent is the normal case, not an error — planning a trip anonymously is
 *  what almost everybody does. */
export function ownerHeaders(): Record<string, string> {
  const secret = ownerSecret(false)
  return secret ? { "X-Owner-Secret": secret } : {}
}

/** The username this browser last knew it had.
 *
 *  A cache of a server fact, kept so the drawer can render its list on the
 *  first paint instead of after a round trip. `whoAmI()` is the authority when
 *  they disagree. */
export function storedUsername(): string | null {
  if (typeof window === "undefined") return null
  try {
    return window.localStorage.getItem(USERNAME_KEY)
  } catch {
    return null
  }
}

export function setStoredUsername(name: string | null): void {
  if (typeof window === "undefined") return
  try {
    if (name) window.localStorage.setItem(USERNAME_KEY, name)
    else window.localStorage.removeItem(USERNAME_KEY)
  } catch {
    /* a browser that cannot remember the name still works; the drawer just
       asks the server for it again */
  }
}

/**
 * A pasted device code, in its stored form, or null if it is not one.
 *
 * Validated against the same pattern the server enforces, so a truncated paste
 * fails visibly in the form rather than being sent as a key that authorises
 * nothing.
 *
 * Separate from `adoptSecret` on purpose: the shape of a code and the question
 * of whether it holds a username are two different checks, and the flow has to
 * pass the second one before it is allowed to act on the first.
 */
export function readDeviceCode(raw: string): string | null {
  const secret = (raw || "").trim().toLowerCase()
  return UUID_V4_RE.test(secret) ? secret : null
}

/**
 * Adopt a secret copied from another device.
 *
 * Overwrites whatever this browser had — that is the intent, you are telling it
 * "this is the same person as over there" — and that is exactly why it must be
 * called ONLY after `whoAmI(code)` has confirmed the code resolves to a name.
 * It used to be called first and asked afterwards, which meant a bad paste on a
 * phone that already held a username replaced the only copy of the secret
 * behind it: the code was rejected, the message said so, and the username was
 * gone with nothing on screen admitting it.
 */
export function adoptSecret(secret: string): boolean {
  if (typeof window === "undefined") return false
  if (!UUID_V4_RE.test(secret)) return false
  try {
    window.localStorage.setItem(OWNER_KEY, secret)
    window.localStorage.removeItem(USERNAME_KEY)
    window.localStorage.removeItem(COUNT_KEY)
    return true
  } catch {
    return false
  }
}

/**
 * How many trips the list held the last time we looked.
 *
 * A cache of a number, and the only reason it exists is the badge on the header
 * button: "alpine-driver · 3" is a reason to open the panel and a bare glyph is
 * not, so the number has to be there on the first paint. The honest alternative
 * — fetching the list on every page load to render one integer — is a request
 * per page view for a value that changes at exactly one moment, when a trip is
 * planned. So this is written whenever the real list loads, nudged by
 * `noteTripPlanned` when a trip is added, and corrected the next time the panel
 * is opened. It is never sent anywhere; nothing server-side knows it exists.
 */
export function storedTripCount(): number | null {
  if (typeof window === "undefined") return null
  try {
    const raw = window.localStorage.getItem(COUNT_KEY)
    if (raw === null) return null
    const n = Number.parseInt(raw, 10)
    return Number.isFinite(n) && n >= 0 ? n : null
  } catch {
    return null
  }
}

export function setStoredTripCount(n: number | null): void {
  if (typeof window === "undefined") return
  try {
    if (n === null) window.localStorage.removeItem(COUNT_KEY)
    else window.localStorage.setItem(COUNT_KEY, String(n))
  } catch {
    /* the badge just starts empty */
  }
}

/** Forget the username but KEEP the secret.
 *
 *  What a release leaves behind: the name is gone server-side, and this browser
 *  can claim another one without becoming a new person first. */
export function forgetUsername(): void {
  setStoredUsername(null)
  // The cached count belongs to the name, not to the browser. Leaving it behind
  // puts a badge on a button whose panel is back to asking you to pick a name.
  setStoredTripCount(null)
}
