import type { Provider } from "../lib/api"
import { group, pct } from "../lib/format"

const NAME: Record<string, string> = {
  ors: "OpenRouteService",
  ocm: "OpenChargeMap",
}

/** Free-tier headroom, today.
 *
 * "Today" is the unit because the ceilings are daily and reset at midnight UTC
 * — a weekly total cannot tell you whether tonight's post will run the tank
 * dry.
 *
 * Where a provider publishes no daily cap, the gauge is withheld entirely
 * rather than drawn against an invented denominator. A reassuring bar with
 * nothing behind it is worse than no bar, because you would believe it.
 */
export function Quota({ providers }: { providers: Provider[] }) {
  return (
    <div className="flex flex-col gap-4">
      {providers.map((p) => {
        const used = p.daily_quota ? Math.min(1, p.calls_today / p.daily_quota) : null
        const tone =
          used === null
            ? "#5b8ad4"
            : used < 0.6
              ? "#43c389"
              : used < 0.85
                ? "#b8842a"
                : "#d9534f"
        return (
          <div key={`${p.provider}-${p.kind}`}>
            <div className="mb-1.5 flex items-baseline justify-between gap-3">
              <span className="text-[12.5px] text-ink-soft">
                {NAME[p.provider] ?? p.provider}
                {/* The service is named because the ceiling belongs to it, not
                    to the provider. One gauge per provider once showed 96%
                    headroom while geocoding was refusing every request. */}
                {p.kind ? <span className="text-ink-mute"> · {p.kind}</span> : null}
              </span>
              <span className="figure text-[12px] text-ink-mute">
                {group(p.calls_today)}
                {p.daily_quota ? ` / ${group(p.daily_quota)}` : " calls"}
              </span>
            </div>

            {used === null ? (
              <p className="text-[11.5px] text-ink-mute">
                No published daily ceiling — calls are counted, headroom is not invented.
              </p>
            ) : (
              <div className="h-2 overflow-hidden rounded-full bg-deck-line-soft">
                <div
                  className="h-2 rounded-full transition-[width] duration-700"
                  style={{ width: `${Math.max(1, used * 100)}%`, background: tone }}
                />
              </div>
            )}

            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-mute">
              {/* The hit rate is the number that predicts whether a launch
                  survives its own front page: it decides whether a spike costs
                  one upstream call or a thousand. */}
              <span>
                cache hit rate{" "}
                <span className="figure" style={{ color: "var(--color-s-mint)" }}>
                  {pct(p.hit_rate)}
                </span>
              </span>
              <span>
                avg <span className="figure">{p.avg_ms}</span> ms
              </span>
              {p.failures_today > 0 && (
                <span style={{ color: "#d9534f" }}>
                  <span className="figure">{p.failures_today}</span> failed today
                </span>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
