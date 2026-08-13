/**
 * One v4 UUID generator, shared by everything that needs one.
 *
 * Both identifiers this app stores in the browser — the counting id and the
 * owner secret — are checked against a strict v4 pattern server-side and
 * discarded if they do not match. Two copies of this function is two chances
 * for one of them to drift into a shape the server throws away, which would
 * look like "the feature silently does nothing" rather than like a bug.
 */

/** RFC-4122 v4, from `crypto.randomUUID` where it exists.
 *
 *  `randomUUID` needs a secure context and is missing on older Safari, hence
 *  the fallback. The fallback is not a security boundary for the counting id —
 *  that one only has to avoid collisions — but it IS one for the owner secret,
 *  which is why it uses `crypto.getRandomValues` and only degrades to
 *  `Math.random` in a browser that has neither. */
export function uuidV4(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  const bytes = new Uint8Array(16)
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    crypto.getRandomValues(bytes)
  } else {
    for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256)
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40 // version 4
  bytes[8] = (bytes[8] & 0x3f) | 0x80 // variant 10xx
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(
    16,
    20
  )}-${hex.slice(20)}`
}

/** The same shape the server accepts (`core.visitor._CLIENT_ID_RE`). Used to
 *  reject a pasted device code before it is stored, so a typo fails in the form
 *  rather than silently becoming a secret that authorises nothing. */
export const UUID_V4_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
