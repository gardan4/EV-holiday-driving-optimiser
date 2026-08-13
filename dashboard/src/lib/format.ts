/** Number and date formatting.
 *
 * Hand-rolled for the same reason `frontend/lib/format.ts` is: `toLocale*`
 * differs between runtimes and locales, and a dashboard whose thousands
 * separator depends on the reader's machine is one whose screenshots cannot be
 * compared.
 */

export function compact(n: number): string {
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${trim(n / 1_000_000)}M`
  if (abs >= 10_000) return `${trim(n / 1000)}k`
  return group(n)
}

export function group(n: number): string {
  const neg = n < 0
  const s = Math.round(Math.abs(n)).toString()
  const out = s.replace(/\B(?=(\d{3})+(?!\d))/g, " ")
  return neg ? `-${out}` : out
}

function trim(n: number): string {
  return (Math.round(n * 10) / 10).toString()
}

export function pct(x: number | null | undefined, digits = 0): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "—"
  return `${(x * 100).toFixed(digits)}%`
}

/** A signed change, or an em dash when there was no baseline to change from.
 *
 * A ratio against zero is withheld rather than shown as +100%: "up 100% from
 * nothing" reads as growth and means "it started". */
export function delta(change: number | null | undefined): string {
  if (change === null || change === undefined || !Number.isFinite(change)) return "—"
  const sign = change > 0 ? "+" : ""
  return `${sign}${(change * 100).toFixed(0)}%`
}

export function minutes(m: number | null | undefined): string {
  if (m === null || m === undefined || !Number.isFinite(m)) return "—"
  const h = Math.floor(m / 60)
  const r = Math.round(m % 60)
  return h ? `${h}h ${r.toString().padStart(2, "0")}m` : `${r}m`
}

/** "12 Aug" / "12 Aug 14:00" — short, unambiguous, and locale-independent. */
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

export function shortDate(bucket: string): string {
  const [date, time] = bucket.split("T")
  const [, m, d] = date.split("-")
  const label = `${Number(d)} ${MONTHS[Number(m) - 1] ?? ""}`
  return time ? `${label} ${time.slice(0, 5)}` : label
}

export function clock(iso: string): string {
  const t = iso.split("T")[1] ?? ""
  return t.slice(0, 8)
}
