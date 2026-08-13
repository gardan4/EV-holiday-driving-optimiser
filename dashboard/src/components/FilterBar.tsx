import type { Filters } from "../lib/api"
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

export function FilterBar({
  filters,
  active,
  status,
  onDays,
  onClear,
  onDrop,
  onLogout,
}: {
  filters: Filters
  active: { facet: Facet; value: string }[]
  status: "connecting" | "live" | "retrying"
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
          <LivePip status={status} />
          <button type="button" className="chip" onClick={onLogout}>
            sign out
          </button>
        </div>
      </div>
    </div>
  )
}
