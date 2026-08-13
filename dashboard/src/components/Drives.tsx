import {
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from "recharts"
import type { Journeys } from "../lib/api"
import { group, minutes, pct } from "../lib/format"
import { Bars, Empty, Panel } from "./ui"

/** What the drives actually did.
 *
 * Every number here comes from `trip_runs` and `trip_events`, which have been
 * recorded since the live-drive feature shipped and read by nothing until now.
 * Two caveats belong on the panel rather than in a comment, because they
 * change how the numbers should be read: runs are purged at 90 days, so this
 * is always a rolling window; and `abandoned` means the phone stopped
 * reporting, not that the drive failed.
 */
export function Drives({ journeys }: { journeys: Journeys }) {
  const d = journeys.drives

  const stops = d.charge_stops
  // Bin the real charge-stop durations. The planner's own stop lengths come
  // out of the DP; these are what the clock actually showed.
  const bins = [
    { label: "under 10m", lo: 0, hi: 10 },
    { label: "10-20m", lo: 10, hi: 20 },
    { label: "20-30m", lo: 20, hi: 30 },
    { label: "30-45m", lo: 30, hi: 45 },
    { label: "45-60m", lo: 45, hi: 60 },
    { label: "over 60m", lo: 60, hi: Infinity },
  ].map((b) => ({
    label: b.label,
    value: stops.filter((s) => s.minutes >= b.lo && s.minutes < b.hi).length,
  }))

  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Panel
        title="drives"
        hint="Runs are purged at 90 days, so this is always a rolling window."
      >
        <div className="grid grid-cols-2 gap-x-4 gap-y-3">
          <Figure label="completed" value={pct(d.completion_rate, 0)} tone="var(--color-s-mint)" />
          <Figure label="median drive" value={minutes(d.median_minutes)} />
          <Figure label="replanned" value={pct(d.replan_rate, 0)} />
          <Figure label="went off route" value={pct(d.off_route_rate, 0)} tone="var(--color-s-charge)" />
        </div>
        <p className="mt-3 text-[11.5px] text-ink-mute">
          <span className="figure">{group(d.finished)}</span> finished,{" "}
          <span className="figure">{group(d.active)}</span> still going,{" "}
          <span className="figure">{group(d.abandoned)}</span> stopped reporting. Active runs are
          left out of the completion rate — a drive in progress has not failed to complete.
        </p>
      </Panel>

      <Panel
        title="how long people actually charged"
        hint="Paired charge_start / charge_end events. A run that ended mid-charge is dropped, not closed."
      >
        {stops.length ? (
          <Bars rows={bins} empty="No completed charge stops." />
        ) : (
          <Empty>No completed charge stops in this window.</Empty>
        )}
      </Panel>

      <Panel
        title="planned vs achieved"
        hint="Journey average including charging — so it sits below the cruise speed by design. The gap is what the model has to get right."
      >
        {d.plan_vs_actual.length ? (
          <div className="h-[200px] w-full">
            <ResponsiveContainer width="100%" height="100%">
              <ScatterChart margin={{ top: 8, right: 10, bottom: 4, left: -16 }}>
                <CartesianGrid stroke="#26344a" strokeDasharray="2 4" />
                {/* The unit lives in the tooltip, not on the ticks: appended
                    to every tick label it doubles their width, and the axis
                    then clips the numbers and leaves a column of bare "km/h". */}
                <XAxis
                  type="number"
                  dataKey="planned_kph"
                  name="planned"
                  domain={[80, 170]}
                  tick={{ fill: "#7d8ca0", fontSize: 11 }}
                  axisLine={{ stroke: "#26344a" }}
                  tickLine={false}
                />
                <YAxis
                  type="number"
                  dataKey="achieved_kph"
                  name="achieved"
                  tick={{ fill: "#7d8ca0", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                  width={40}
                />
                <ZAxis range={[42, 42]} />
                {/* Parity: anything on this line drove its planned speed with
                    no stopped time at all, which nothing real will do. */}
                <ReferenceLine
                  segment={[
                    { x: 80, y: 80 },
                    { x: 170, y: 170 },
                  ]}
                  stroke="#26344a"
                  strokeDasharray="4 4"
                />
                <Tooltip
                  cursor={{ stroke: "#26344a" }}
                  contentStyle={{
                    background: "#141d2c",
                    border: "1px solid #26344a",
                    borderRadius: 10,
                    fontSize: 12,
                  }}
                  formatter={(v) => `${v ?? "—"} km/h`}
                />
                <Scatter data={d.plan_vs_actual} fill="#9b7ec0" isAnimationActive={false} />
              </ScatterChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <Empty>No finished drives with a recorded distance yet.</Empty>
        )}
      </Panel>
    </div>
  )
}

function Figure({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div>
      <p className="panel-title !text-[10px]">{label}</p>
      <p className="figure mt-1 text-[20px] leading-none" style={{ color: tone }}>
        {value}
      </p>
    </div>
  )
}
