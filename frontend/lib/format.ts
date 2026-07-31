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

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

/** "Fri 14 Aug, 22:00" — hand-formatted for SSR/client consistency. */
export function fmtDeparture(departureIso: string): string {
  const d = new Date(departureIso)
  return `${DAYS[d.getDay()]} ${d.getDate()} ${MONTHS[d.getMonth()]}, ${pad2(d.getHours())}:${pad2(d.getMinutes())}`
}

export function fmtKm(meters: number): string {
  return `${Math.round(meters / 1000)} km`
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
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
