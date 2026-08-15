/** Time/number formatting helpers shared by chart, itinerary, and 3D HUD. */

/** 605.3 → "10h05" */
export function fmtHm(minutes: number): string {
  const total = Math.round(minutes)
  const h = Math.floor(total / 60)
  const m = total % 60
  return `${h}h${String(m).padStart(2, "0")}`
}

/** 605.3 → "10 h 05 min", 42 → "42 min" */
export function fmtDuration(minutes: number): string {
  const total = Math.round(minutes)
  if (total < 60) return `${total} min`
  const h = Math.floor(total / 60)
  const m = total % 60
  return m === 0 ? `${h} h` : `${h} h ${String(m).padStart(2, "0")} min`
}

const pad2 = (n: number) => String(n).padStart(2, "0")

/** Clock time `minutes` after the departure timestamp → "23:41".
 * Formatted by hand: toLocaleTimeString differs between Node and browsers
 * (24:12 vs 00:12 at midnight) and breaks hydration. */
export function clockAt(departureIso: string, minutes: number): string {
  const d = new Date(departureIso)
  d.setMinutes(d.getMinutes() + Math.round(minutes))
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

export const WEEKDAYS_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
export const MONTHS_SHORT = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

/** "Fri 14 Aug, 22:00" — hand-formatted for SSR/client consistency. */
export function fmtDeparture(departureIso: string): string {
  const d = new Date(departureIso)
  return `${WEEKDAYS_SHORT[d.getDay()]} ${d.getDate()} ${MONTHS_SHORT[d.getMonth()]}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

/**
 * "2026-08-14T22:00" → a local Date.
 *
 * Split by hand rather than handed to `new Date(s)`: a value that has lost its
 * time part ("2026-08-14") is read as UTC by the parser, which west of
 * Greenwich lands on the previous evening — so the day you picked and the day
 * you get differ by one for half the planet.
 */
export function parseLocalIso(iso: string): Date {
  const [date, time = "00:00"] = iso.split("T")
  const [y, mo, d] = date.split("-").map(Number)
  const [h, mi] = time.split(":").map(Number)
  return new Date(y, (mo || 1) - 1, d || 1, h || 0, mi || 0, 0, 0)
}

/**
 * A server timestamp → milliseconds since the epoch.
 *
 * The API serialises `datetime.utcnow()`, which is naive: "2026-08-15T08:36:17"
 * with no zone on it. JavaScript reads a date-TIME form with no offset as LOCAL
 * time, so on a phone in CEST that value parses two hours into the future and
 * anything measuring elapsed time against it gets a negative answer. The bug
 * hides, too — code that guards against a nonsense gap simply stops doing its
 * job, silently, for everyone outside UTC.
 *
 * Which is the opposite convention from `parseLocalIso` above, deliberately:
 * that one reads a departure the user TYPED, which is naive local by design;
 * this one reads an instant the server RECORDED, which is naive UTC. Same
 * shape, different meaning, so they cannot share an implementation.
 */
export function parseServerTime(iso: string): number {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso)
  return Date.parse(hasZone ? iso : `${iso}Z`)
}

/** A local Date → "2026-08-14T22:00", the naive local string the API stores. */
export function toLocalIso(d: Date): string {
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

export function fmtKm(meters: number): string {
  return `${Math.round(meters / 1000)} km`
}

/**
 * The live battery, as text: "46%" once a human has confirmed it, a RANGE
 * while it is inferred.
 *
 * A range reads as an estimate in a way "46% ± 2" doesn't — the latter looks
 * like a measurement with a footnote, which is exactly the impression this
 * number has not earned. Lives here rather than in the drive screen because
 * the HUD and the read-only row a watcher sees must not describe the same
 * battery two different ways.
 */
export function socLabel(soc: number, measured: boolean, band: number): string {
  if (measured || band < 1) return `${Math.round(soc)}%`
  return `${Math.max(0, Math.round(soc - band))}-${Math.min(100, Math.round(soc + band))}%`
}

/**
 * "· 4 bays", or nothing at all.
 *
 * OpenChargeMap's bay count is the only capacity signal we have, and it is
 * absent or 1 often enough that printing "1 bay" would assert something the
 * data does not support — a site with an unknown count and a genuine single
 * plug look identical here. So one bay and no bays both render as silence, and
 * the number only appears when it is telling you something: this is a hub.
 *
 * It is NOT availability. Nothing in this app knows whether a stall is free;
 * see the note in `AlternativeStops`.
 */
export function fmtBays(n: number | null | undefined): string {
  return n && n > 1 ? `${n} bays` : ""
}

/** Google Maps deep link for a charging stop (no in-app map in v1). */
export function mapsLink(lat: number, lon: number): string {
  return `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`
}

/** Default departure: next Friday 22:00 local (the overnight-drive story). */
export function defaultDepartureIso(): string {
  const d = new Date()
  const day = d.getDay()
  const daysUntilFriday = (5 - day + 7) % 7 || 7
  d.setDate(d.getDate() + daysUntilFriday)
  d.setHours(22, 0, 0, 0)
  return toLocalIso(d)
}
