import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts"
import type { Upstream } from "../lib/api"
import { shortDate } from "../lib/format"
import { Empty, Panel } from "./ui"

const NAME: Record<string, string> = {
  ors: "OpenRouteService",
  ocm: "OpenChargeMap",
}

/** Provider health as a trend.
 *
 * `quota_report` answers "how much is left today", which is the right question
 * at 22:00 on launch night and the wrong one every other time. The cache hit
 * rate's *direction* is what says whether a spike will cost one upstream call
 * or a thousand — a single today-figure cannot tell you it has been sliding.
 *
 * The latency table is p50/p95 rather than a mean because the population is
 * bimodal: warm operations take milliseconds, cold corridor sweeps take
 * seconds, and an average lands in the gap and describes neither.
 */
export function Upstream({ upstream }: { upstream: Upstream }) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      {Object.entries(upstream.daily).map(([provider, days]) => {
        const data = days.map((d) => ({
          ...d,
          hit_pct: d.hit_rate === null ? null : d.hit_rate * 100,
        }))
        const anything = data.some((d) => d.operations > 0)
        return (
          <Panel
            key={provider}
            title={NAME[provider] ?? provider}
            hint="Calls billed against the quota, with the cache hit rate over them."
          >
            {anything ? (
              <div className="h-[180px] w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <ComposedChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: -22 }}>
                    <CartesianGrid stroke="#26344a" strokeDasharray="2 4" vertical={false} />
                    <XAxis
                      dataKey="day"
                      tickFormatter={shortDate}
                      tick={{ fill: "#7d8ca0", fontSize: 10 }}
                      axisLine={{ stroke: "#26344a" }}
                      tickLine={false}
                      interval="preserveStartEnd"
                      minTickGap={24}
                    />
                    <YAxis
                      yAxisId="calls"
                      tick={{ fill: "#7d8ca0", fontSize: 10 }}
                      axisLine={false}
                      tickLine={false}
                      width={44}
                    />
                    {/* A second axis, and the one place it is justified: a rate
                        and a count share no unit, and the rate is bounded 0-100
                        so it cannot be made to say anything by rescaling. */}
                    <YAxis
                      yAxisId="rate"
                      orientation="right"
                      domain={[0, 100]}
                      hide
                    />
                    <Tooltip
                      contentStyle={{
                        background: "#141d2c",
                        border: "1px solid #26344a",
                        borderRadius: 10,
                        fontSize: 12,
                      }}
                      labelFormatter={(d) => shortDate(String(d))}
                    />
                    <Bar
                      yAxisId="calls"
                      dataKey="calls"
                      fill="#5b8ad4"
                      name="billed calls"
                      radius={[3, 3, 0, 0]}
                      isAnimationActive={false}
                    />
                    <Bar
                      yAxisId="calls"
                      dataKey="failures"
                      fill="#d9534f"
                      name="failures"
                      radius={[3, 3, 0, 0]}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="rate"
                      type="monotone"
                      dataKey="hit_pct"
                      stroke="#43c389"
                      strokeWidth={2}
                      dot={false}
                      connectNulls
                      name="cache hit %"
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            ) : (
              <Empty>No calls to {NAME[provider] ?? provider} in this window.</Empty>
            )}
          </Panel>
        )
      })}

      <Panel title="latency" hint="Nearest-rank percentiles, split by operation.">
        {upstream.latency.length ? (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left text-ink-mute">
                <th className="pb-2 font-normal">operation</th>
                <th className="pb-2 text-right font-normal">p50</th>
                <th className="pb-2 text-right font-normal">p95</th>
                <th className="pb-2 text-right font-normal">n</th>
              </tr>
            </thead>
            <tbody className="text-ink-soft">
              {upstream.latency.map((l) => (
                <tr key={`${l.provider}-${l.kind}`} className="border-t border-deck-line-soft">
                  <td className="py-1.5">
                    {l.provider} <span className="text-ink-mute">{l.kind}</span>
                  </td>
                  <td className="figure py-1.5 text-right">{fmt(l.p50_ms)}</td>
                  <td
                    className="figure py-1.5 text-right"
                    style={{
                      color:
                        (l.p95_ms ?? 0) > 4000 ? "var(--color-s-rose)" : undefined,
                    }}
                  >
                    {fmt(l.p95_ms)}
                  </td>
                  <td className="figure py-1.5 text-right text-ink-mute">{l.operations}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <Empty>No upstream calls recorded yet.</Empty>
        )}
      </Panel>
    </div>
  )
}

function fmt(ms: number | null): string {
  if (ms === null) return "—"
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
