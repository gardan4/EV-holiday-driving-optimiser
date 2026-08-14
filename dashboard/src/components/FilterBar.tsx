import type { Filters, Meta } from "../lib/api"
import type { Facet } from "../lib/useFilters"
import { LivePip } from "./ui"

const RANGES = [
  { days: 1, label: "24h" },
  { days: 7, label: "7d" },
  { days: 30, label: "30d" },
  { days: 90, label: "90d" },
]

const FACET_LABEL: Record<string, string> = {
  device: "device",
  browser: "browser",
  os: "os",
  country: "country",
  campaign: "campaign",
  viewport: "screen",
  path: "page",
  referrer: "from",
  referrer_path: "thread",
}

/** Which database answered, said out loud.
 *
 * The console reads whatever `DATABASE_URL` the process was given, and since
 * `--prod` that can be the deployed one. Two tabs of the same layout showing
 * different populations is how somebody ends up reasoning about live traffic
 * while believing they are looking at seed data, so a live connection is
 * labelled and coloured rather than merely implied by the port.
 *
 * Rendered from the server's own answer, and absent until `/api/meta` lands —
 * a placeholder reading "local" while the real value is in flight would be the
 * one wrong state that matters.
 */
function DatabaseChip({ database }: { database: Meta["database"] }) {
  const live = !database.local
  return (
    <span
      className="chip"
      title={`These numbers come from ${database.host}/${database.name}`}
      style={
        live
          ? {
              borderColor: "var(--color-s-rose)",
              color: "var(--color-s-rose)",
              background: "color-mix(in oklab, var(--color-s-rose) 12%, transparent)",
            }
          : undefined
      }
    >
      <span className={live ? undefined : "text-ink-mute"}>
        {live ? "live db" : "local db"}
      </span>
      <span className="truncate max-w-[14rem]">{database.host}</span>
    </span>
  )
}

export function FilterBar({
  filters,
  active,
  status,
  database,
  onDays,
  onClear,
  onDrop,
  onLogout,
}: {
  filters: Filters
  active: { facet: Facet; value: string }[]
  status: "connecting" | "live" | "retrying"
  database?: Meta["database"]
  onDays: (d: number) => void
  onClear: () => void
  onDrop: (facet: Facet, value: string) => void
  onLogout: () => void
}) {
  return (
    <div className="sticky top-0 z-20 -mx-4 mb-6 border-b border-deck-line bg-deck-bg/85 px-4 py-3 backdrop-blur-md sm:-mx-6 sm:px-6">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-3 gap-y-2">
        <h1 className="display mr-1 text-[15px] font-semibold">
          EV Trip <span className="text-ink-mute">mission control</span>
        </h1>

        <div className="flex overflow-hidden rounded-full border border-deck-line">
          {RANGES.map((r) => (
            <button
              key={r.days}
              type="button"
              onClick={() => onDays(r.days)}
              className="px-3 py-1 text-[12px] transition-colors"
              style={{
                background:
                  filters.days === r.days
                    ? "color-mix(in oklab, var(--color-s-mint) 16%, transparent)"
                    : "transparent",
                color:
                  filters.days === r.days ? "var(--color-ink)" : "var(--color-ink-mute)",
              }}
            >
              {r.label}
            </button>
          ))}
        </div>

        {/* Every chip here narrows every panel below at once. Clicking one off
            is the same gesture that put it on. */}
        {active.map(({ facet, value }) => (
          <button
            key={`${facet}:${value}`}
            type="button"
            data-on="true"
            className="chip"
            onClick={() => onDrop(facet, value)}
            title={`Remove the ${FACET_LABEL[facet] ?? facet} filter`}
          >
            <span className="text-ink-mute">{FACET_LABEL[facet] ?? facet}</span>
            <span>{value}</span>
            <span aria-hidden className="text-ink-mute">
              ×
            </span>
          </button>
        ))}

        {active.length > 0 && (
          <button type="button" className="chip" onClick={onClear}>
            clear all
          </button>
        )}

        <div className="ml-auto flex items-center gap-3">
          {database && <DatabaseChip database={database} />}
          <LivePip status={status} />
          <button type="button" className="chip" onClick={onLogout}>
            sign out
          </button>
        </div>
      </div>
    </div>
  )
}
